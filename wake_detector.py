#!/usr/bin/env python3
"""
wake_detector.py - VERSION FINAL (ECHO CANCELLATION + 3 FILES)
MODIFIÉ AVEC :
1. SUPPORT MODÈLES CUSTOM (custom_models/*.onnx)
2. OPTIMISATION VITESSE (VAD + NON-BLOCKING I/O)
"""

import os
import sys
import time
from collections import deque
import signal
import json
import pyaudio
import numpy as np
import wave
import subprocess
import re
import fcntl
import glob       # AJOUT: Pour scanner les modèles
import webrtcvad  # AJOUT: Pour l'optimisation VAD

# ==========================================
# 1. FILTRES DSP (TRAITEMENT SIGNAL)
# ==========================================

def spectral_subtraction(mic_data, sys_data, strength=0.85):
    """
    Soustraction spectrale Améliorée :
    1. Pas de fenêtrage (Hanning) pour éviter l'effet de hachage sans Overlap-Add.
    2. Adaptation automatique du niveau d'énergie (Scaling).
    3. Ajout d'un plancher spectral pour éviter le son "sous-marin/robotique".
    """
    try:
        if len(mic_data) != len(sys_data):
            return mic_data

        # Conversion float
        mic_f = mic_data.astype(np.float32)
        sys_f = sys_data.astype(np.float32)
        
        # FFT sans fenêtrage (Vital ici car pas d'overlap dans la boucle principale)
        mic_spec = np.fft.rfft(mic_f)
        sys_spec = np.fft.rfft(sys_f)
        
        # Magnitudes et Phase
        mic_mag = np.abs(mic_spec)
        sys_mag = np.abs(sys_spec)
        mic_phase = np.angle(mic_spec)
        
        # --- ETAPE CRITIQUE : ADAPTATION D'ÉNERGIE ---
        # On calcule combien le signal système est plus fort que le micro
        # pour éviter de tout soustraire si le volume système est haut.
        energy_mic = np.sum(mic_mag)
        energy_sys = np.sum(sys_mag) + 1e-6 # éviter division par 0
        
        scale_factor = energy_mic / energy_sys
        # On limite le facteur pour ne pas amplifier le bruit système si le micro est silencieux
        # On suppose que le résidu d'écho ne doit pas dépasser le niveau du signal micro global
        scale_factor = min(scale_factor, 1.0) 
        
        # On adapte la magnitude du système au niveau du micro
        effective_sys_mag = sys_mag * scale_factor

        # --- SOUSTRACTION ---
        # On soustrait, mais on garde un "plancher" (0.05 * mic_mag)
        # Cela empêche les trous de silence complets qui sonnent artificiels.
        new_mag = np.maximum(mic_mag - (effective_sys_mag * strength), mic_mag * 0.05)
        
        # Reconstruction (IFFT)
        new_spec = new_mag * np.exp(1j * mic_phase)
        new_audio = np.fft.irfft(new_spec).astype(np.int16)
        
        return new_audio

    except Exception as e:
        return mic_data

# ==========================================
# 2. DÉTECTEURS (VOSK & OPENWAKEWORD)
# ==========================================

class FrenchDetector:
    """Détecteur pour le Français basé sur Vosk (Léger et local)."""
    def __init__(self):
        print("[FR-KWS] 📚 Chargement Vosk...", flush=True)
        from vosk import Model, KaldiRecognizer
        import webrtcvad
        
        MODEL_PATH = "model"
        if not os.path.exists(MODEL_PATH):
            print(f"[ERR] Modèle Vosk manquant dans le dossier : {MODEL_PATH}", flush=True)
            # On ne crash pas, mais la boucle plantera plus tard si on ne gère pas
            sys.exit(1)
        
        # Astuce : On redirige stderr pour éviter le spam de logs ALSA de Vosk
        with open(os.devnull, "w") as devnull:
            old_stderr = os.dup(sys.stderr.fileno())
            os.dup2(devnull.fileno(), sys.stderr.fileno())
            try:
                self.vosk_model = Model(MODEL_PATH)
            finally:
                os.dup2(old_stderr, sys.stderr.fileno())

        self.recognizer = KaldiRecognizer(self.vosk_model, 16000)
        self.recognizer.SetWords(True)
        
        # VAD (Voice Activity Detection) : Filtre les silences avant même de tester le mot
        self.vad = webrtcvad.Vad(3) # Niveau 3 = Très agressif sur le bruit non-vocal
        
        self.wake_words = ["igor", "assistant", "ordinateur", "écoute"]
        self.last_detection = 0
        self.cooldown = 1.0 # RÉDUIT de 1.5 à 1.0 pour réactivité
        print(f"[FR-KWS] ✅ Prêt. Mots-clés : {self.wake_words}", flush=True)

    def process(self, audio_np):
        # Anti-spam
        if time.time() - self.last_detection < self.cooldown:
            return None

        # 1. Vérification VAD (Est-ce de la voix ?)
        try:
            if not self.vad.is_speech(audio_np.tobytes(), 16000):
                return None
        except: pass

        # 2. Reconnaissance Vosk
        if self.recognizer.AcceptWaveform(audio_np.tobytes()):
            res = json.loads(self.recognizer.Result())
            text = res.get("text", "")
        else:
            res = json.loads(self.recognizer.PartialResult())
            text = res.get("partial", "")

        if text:
            clean_text = text.lower().strip()
            # Si on veut debugger ce qu'il entend :
            # if len(clean_text) > 2: print(f"  [DEBUG-FR] Entendu: {clean_text}", flush=True)

            for kw in self.wake_words:
                if kw in clean_text:
                    self.recognizer.Reset()
                    self.last_detection = time.time()
                    return kw
        return None

class EnglishDetector:
    """
    Détecteur pour l'Anglais basé sur OpenWakeWord.
    MODIFIÉ : Supporte les modèles personnalisés dans custom_models/
    """
    def __init__(self):
        print("[OWW-KWS] 📚 Chargement OpenWakeWord...", flush=True)
        from openwakeword.model import Model as OWWModel
        
        # --- AJOUT: Scan des modèles custom ---
        script_dir = os.path.dirname(os.path.abspath(__file__))
        custom_dir = os.path.join(script_dir, "custom_models")
        model_paths = []
        
        if os.path.exists(custom_dir):
            model_paths.extend(glob.glob(os.path.join(custom_dir, "*.onnx")))
            model_paths.extend(glob.glob(os.path.join(custom_dir, "*.tflite")))
        
        if model_paths:
            print(f"[OWW-KWS] 📂 Modèles perso trouvés : {[os.path.basename(p) for p in model_paths]}", flush=True)
            self.oww = OWWModel(wakeword_models=model_paths, inference_framework="onnx")
        else:
            print("[OWW-KWS] ⚠️ Aucun modèle perso trouvé, chargement des défauts...", flush=True)
            self.oww = OWWModel(inference_framework="onnx")
        # -------------------------------------
            
        self.cooldown = 0
        print(f"[OWW-KWS] ✅ Prêt.", flush=True)
    
    def process(self, audio_np):
        if self.cooldown > 0:
            self.cooldown -= 1
            return None
            
        # Prédiction (Score entre 0 et 1)
        prediction = self.oww.predict(audio_np)
        
        for name, score in prediction.items():
            if score > 0.5: # Seuil de confiance
                self.oww.reset()
                self.cooldown = 40 # ~2 secondes de pause (si chunk ~50ms)
                return name
        return None

# ==========================================
# 3. UTILITAIRES SYSTÈME & CONFIG
# ==========================================

def get_pulse_monitor_source(ui_index):
    """
    Traduit l'index UI (souvent 100+ID) en nom de source technique PulseAudio.
    Nécessaire pour l'outil 'parec'.
    """
    if ui_index is None: return None
    try:
        pa_id = int(ui_index) - 100
        # On cherche la ligne correspondant à l'ID dans pactl
        cmd = f"pactl list sources short | grep '^{pa_id}\\s'"
        res = subprocess.check_output(cmd, shell=True).decode().strip()
        name = res.split('\t')[1]
        print(f"[PA-RESOLVER] Source système trouvée : {name}", flush=True)
        return name
    except: return None

def find_mic_index(p, config_idx):
    """Valide l'index PyAudio du microphone."""
    if config_idx is None: return None
    try:
        idx = int(config_idx)
        info = p.get_device_info_by_index(idx)
        if info['maxInputChannels'] > 0:
            return idx
    except: pass
    return None

def set_non_blocking(fd):
    """AJOUT: Rend un descripteur de fichier non-bloquant pour la vitesse."""
    fl = fcntl.fcntl(fd, fcntl.F_GETFL)
    fcntl.fcntl(fd, fcntl.F_SETFL, fl | os.O_NONBLOCK)

# ==========================================
# 4. BOUCLE PRINCIPALE (MAIN)
# ==========================================

def main():
    # --- A. CHARGEMENT CONFIG ---
    try:
        with open("memory.json", 'r') as f:
            mem = json.load(f)
            audio_conf = mem.get('audio_config', {})
            lang = mem.get('wake_lang', 'FR')
    except:
        audio_conf = {}; lang = 'FR'

    print(f"[INIT] Démarrage Détecteur... Langue={lang}", flush=True)

    # --- B. INITIALISATION AUDIO ---
    p = pyaudio.PyAudio()
    
    # Récupération des IDs
    mic_idx = find_mic_index(p, audio_conf.get('mic_index'))
    sys_source = get_pulse_monitor_source(audio_conf.get('sys_index'))
    
    # Taille du buffer (Chunk)
    # Vosk aime bien 480 (30ms), OpenWakeWord préfère 1280 (80ms)
    CHUNK = 1280 
    if lang == "EN": CHUNK = 1280 
    
    # AJOUT: Initialisation VAD Global (Optimisation Vitesse)
    vad = webrtcvad.Vad(3)
    
    # 1. Ouverture Micro (PyAudio)
    stream_mic = None
    if audio_conf.get('mic_enabled', True):
        try:
            stream_mic = p.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=16000,
                input=True,
                input_device_index=mic_idx,
                frames_per_buffer=CHUNK
            )
            print(f"[INIT] ✅ Micro ouvert (Index: {mic_idx})")
        except Exception as e:
            print(f"[ERR] Échec ouverture Micro: {e}")

    # 2. Ouverture Système (Parec - Subprocess)
    parec_proc = None
    if audio_conf.get('sys_enabled', False) and sys_source:
        try:
            # --latency-msec est CRUCIAL pour la synchro Micro/Système
            # AJOUT: Réduction latence de 20ms à 10ms
            cmd = ['parec', '--format=s16le', '--rate=16000', '--channels=1', 
                   '--device', sys_source, '--latency-msec=10']
            
            parec_proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            
            # AJOUT: Passage en mode non-bloquant
            set_non_blocking(parec_proc.stdout.fileno())
            
            print(f"[INIT] ✅ Système monitoré sur '{sys_source}'")
            
            # PURGE DU BUFFER (Anti-Lag au démarrage)
            time.sleep(0.2) # Laisser le temps au process de démarrer
            fd = parec_proc.stdout.fileno()
            # Lecture en boucle pour vider
            try:
                while parec_proc.stdout.read(4096): pass 
            except: pass
            print("[INIT] 🧹 Buffer système purgé.")

            # PRÉ-REMPLISSAGE du buffer système (Évite la famine initiale)
            sys_prebuffer = deque(maxlen=3)
            # On remplit avec des zéros au départ
            for _ in range(3):
                sys_prebuffer.append(np.zeros(CHUNK, dtype=np.int16))
            
        except Exception as e:
            print(f"[ERR] Échec Parec: {e}")

    # --- C. PRÉPARATION DÉTECTEUR ---
    if lang == "FR":
        detector = FrenchDetector()
    else:
        detector = EnglishDetector()

    # --- D. FICHIERS DE DEBUG (LES 3 FLUX) ---
    wf_mic = None
    wf_sys = None
    wf_filtered = None

    if audio_conf.get('debug_audio', False):
        try:
            # Fichier 1 : Micro Brut
            wf_mic = wave.open("/tmp/igor_mic.wav", 'wb')
            wf_mic.setnchannels(1); wf_mic.setsampwidth(2); wf_mic.setframerate(16000)
            
            # Fichier 2 : Système Brut
            wf_sys = wave.open("/tmp/igor_sys.wav", 'wb')
            wf_sys.setnchannels(1); wf_sys.setsampwidth(2); wf_sys.setframerate(16000)
            
            # Fichier 3 : RÉSULTAT FILTRÉ (Ce que l'IA écoute)
            wf_filtered = wave.open("/tmp/igor_filtered.wav", 'wb')
            wf_filtered.setnchannels(1); wf_filtered.setsampwidth(2); wf_filtered.setframerate(16000)
            
            print("[DEBUG] Enregistrement actif : /tmp/igor_{mic,sys,filtered}.wav", flush=True)
        except Exception as e:
            print(f"[WARN] Impossible de créer les fichiers WAV: {e}")

    # Récupération PID parent (pour envoyer le signal de réveil)
    parent_pid = None
    if os.path.exists("/tmp/igor_agent.pid"):
        try:
            with open("/tmp/igor_agent.pid", 'r') as f:
                parent_pid = int(f.read().strip())
        except: pass

    # --- CONFIGURATION DU DÉLAI (BUFFER CIRCULAIRE) ---
    sys_delay_ms = audio_conf.get('sys_delay', 0)
    sys_delay_buffer = None
    
    if sys_delay_ms > 0:
        # Calcul : combien de chunks représentent X ms ?
        sec_per_chunk = CHUNK / 16000.0
        ms_per_chunk = sec_per_chunk * 1000.0
        
        # Nombre de chunks à stocker pour atteindre le délai
        buffer_len = max(int(sys_delay_ms / ms_per_chunk), 2)  # Minimum 2 chunks

        print(f"[DELAY] Buffer configuré : {sys_delay_ms}ms (~{buffer_len} chunks)", flush=True)

        # On remplit le buffer avec du silence au départ
        sys_delay_buffer = deque(maxlen=buffer_len)
        for _ in range(buffer_len):
            sys_delay_buffer.append(np.zeros(CHUNK, dtype=np.int16))

    print("[RUN] Boucle de détection active...", flush=True)
    
    # Calcul taille buffer système (16bit = 2 octets)
    BYTES_PER_CHUNK = CHUNK * 2

    # Variables pour la lecture non-bloquante (Zero Order Hold)
    zeros_chunk = np.zeros(CHUNK, dtype=np.int16)
    sys_last_chunk = zeros_chunk.copy()

    # --- E. BOUCLE INFINIE ---
    while True:
        try:
            mic_data_np = None
            sys_data_np = None
            
            # 1. Lecture Micro (BLOQUANTE = SYNCHRO TEMPS RÉEL)
            # C'est la seule lecture bloquante, elle cadence la boucle.
            if stream_mic:
                try:
                    raw_mic = stream_mic.read(CHUNK, exception_on_overflow=False)
                    mic_data_np = np.frombuffer(raw_mic, dtype=np.int16)
                    if wf_mic: wf_mic.writeframes(raw_mic)
                except Exception: pass
            else:
                # Si pas de micro, on dort pour ne pas CPU burn
                time.sleep(0.05)
                continue

            # 2. Lecture Système (NON-BLOQUANTE)
            # Si pas de données, on utilise le dernier chunk ou des zéros
            if parec_proc:
                try:
                    raw_sys = parec_proc.stdout.read(BYTES_PER_CHUNK)
                    if raw_sys and len(raw_sys) == BYTES_PER_CHUNK:
                        sys_data_np = np.frombuffer(raw_sys, dtype=np.int16)
                        if wf_sys: wf_sys.writeframes(raw_sys)
                        
                        # Mise à jour du cache et prebuffer
                        sys_last_chunk = sys_data_np
                        if 'sys_prebuffer' in locals():
                            sys_prebuffer.append(sys_data_np)
                    else:
                        # Pas de données prêtes -> utilisation du dernier chunk valide
                        sys_data_np = sys_last_chunk
                except Exception: 
                    sys_data_np = sys_last_chunk
            else:
                sys_data_np = zeros_chunk

            if mic_data_np is None:
                continue

            # --- AJOUT: GESTION DU VAD (OPTIMISATION) ---
            # On vérifie s'il y a de la voix dans le micro AVANT de faire les calculs lourds.
            # WebrtcVAD veut des trames de 10, 20 ou 30ms. Le chunk est de 80ms.
            # On découpe en 3 pour tester.
            is_speech = False
            try:
                # Test sur 3 segments (début, milieu, fin)
                # 320 bytes = 160 samples = 10ms à 16kHz
                if vad.is_speech(raw_mic[0:320], 16000) or \
                   vad.is_speech(raw_mic[320:640], 16000) or \
                   vad.is_speech(raw_mic[640:960], 16000):
                    is_speech = True
            except: 
                is_speech = True # En cas d'erreur VAD, on traite par défaut
            
            # 3. TRAITEMENT DSP (FILTRAGE ROBUSTE)
            final_audio = mic_data_np 
            
            # --- GESTION DU DÉLAI (Même en silence, il faut avancer le buffer) ---
            reference_chunk = sys_data_np

            # Si le délai est activé (buffer existe)
            if sys_delay_buffer is not None:
                # A. Quoi qu'il arrive, on doit pousser quelque chose dans le buffer
                if sys_data_np is not None:
                    to_push = sys_data_np
                else:
                    to_push = zeros_chunk
                
                # B. Rotation du Buffer (FIFO)
                delayed_chunk = sys_delay_buffer.popleft()
                sys_delay_buffer.append(to_push)
                
                # C. Notre référence devient le son retardé
                reference_chunk = delayed_chunk

            # --- SI SILENCE DÉTECTÉ PAR VAD, ON SAUTE LE RESTE ---
            if not is_speech:
                # On écrit quand même dans le debug si actif
                if wf_filtered: 
                    try: wf_filtered.writeframes(mic_data_np.tobytes())
                    except: pass
                continue # REBOUCLE ICI -> GAIN DE PERF

            # --- SOUSTRACTION (Seulement si parole détectée) ---
            # On ne filtre que si le signal de référence contient du son
            if reference_chunk is not None and np.any(reference_chunk):
                
                # Calcul rapide du volume sur la référence
                sys_vol = np.sum(np.abs(reference_chunk))
                
                # Seuil de bruit : on ne filtre que si le volume est significatif
                if sys_vol > 500000: # Seuil empirique rapide (somme abs)
                    final_audio = spectral_subtraction(mic_data_np, reference_chunk, strength=0.85)
            
            # 4. Écriture WAV 3 (Résultat)
            try:
                if wf_filtered: wf_filtered.writeframes(final_audio.tobytes())
            except: pass

            # 5. DÉTECTION (Sur le signal nettoyé !)
            trigger_word = detector.process(final_audio)
            
            if trigger_word:
                print(f"[WAKE] 🔔 DÉTECTION CONFIRMÉE : {trigger_word}", flush=True)
                
                # Réveil de l'interface graphique
                if parent_pid:
                    os.kill(parent_pid, signal.SIGUSR1)
                
                # Pause pour éviter de s'entendre soi-même
                time.sleep(1.0)
                
                # Vidage des buffers accumulés pendant la pause
                if stream_mic:
                    try:
                        stream_mic.read(stream_mic.get_read_available(), exception_on_overflow=False)
                    except: pass
                    
                if parec_proc:
                    # Lecture non-bloquante pour vider le pipe
                    try: 
                        while parec_proc.stdout.read(4096): pass
                    except: pass

                # Reset buffers
                sys_last_chunk = zeros_chunk.copy()

        except KeyboardInterrupt:
            print("[STOP] Arrêt manuel demandé.")
            break
        except Exception as e:
            print(f"[ERR] Erreur boucle principale: {e}", flush=True)
            time.sleep(1)

    # --- F. NETTOYAGE ---
    try:
        if wf_mic: wf_mic.close()
        if wf_sys: wf_sys.close()
        if wf_filtered: wf_filtered.close()
        
        if parec_proc: parec_proc.terminate()
        if stream_mic: stream_mic.stop_stream(); stream_mic.close()
        p.terminate()
    except: pass
    print("[END] Arrêt propre du détecteur.")

if __name__ == "__main__":
    main()