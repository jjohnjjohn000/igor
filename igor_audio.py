# igor_audio.py
import os
import sys
import json
import time
import subprocess
import threading
import speech_recognition as sr
from vosk import Model, KaldiRecognizer
from ctypes import *
from contextlib import contextmanager
import numpy as np
import pyaudio
import noisereduce as nr
from scipy import signal as scipy_signal
import webrtcvad
import igor_skills as skills
import igor_globals # Import des globals

# --- GESTION ERREUR ALSA ---
ERROR_HANDLER_FUNC = CFUNCTYPE(None, c_char_p, c_int, c_char_p, c_int, c_char_p)
def py_error_handler(filename, line, function, err, fmt):
    pass
c_error_handler = ERROR_HANDLER_FUNC(py_error_handler)

try:
    ASOUND_LIB = cdll.LoadLibrary('libasound.so')
    ASOUND_LIB.snd_lib_error_set_handler(c_error_handler)
except:
    ASOUND_LIB = None

@contextmanager
def no_alsa_err():
    yield

# --- INITIALISATION VOSK ---
VOSK_MODEL = None
if os.path.exists(igor_globals.MODEL_PATH):
    print(f"  [INIT] Chargement modèle Vosk...", flush=True)
    with no_alsa_err():
        try: VOSK_MODEL = Model(igor_globals.MODEL_PATH); print("  [INIT] Vosk prêt.", flush=True)
        except: pass

# --- CLASSES ET FONCTIONS ---

class FrenchWakeWordDetector:
    """
    Détecteur de mots-clés en français avec filtrage avancé du bruit.
    Utilise Vosk pour la reconnaissance continue + VAD WebRTC + Réduction de bruit.
    """
    
    def __init__(self, wake_words=None, model_path="model"):
        self.wake_words = wake_words or ["igor", "assistant", "ordinateur"]
        self.model_path = model_path
        
        # Initialisation Vosk
        if not os.path.exists(model_path):
            print(f"  [FR-KWS] ⚠️ Modèle Vosk introuvable : {model_path}")
            print(f"  [FR-KWS] Téléchargez-le : https://alphacephei.com/vosk/models")
            self.vosk_model = None
            self.recognizer = None
        else:
            print(f"  [FR-KWS] 📚 Chargement modèle Vosk français...", flush=True)
            with no_alsa_err():
                self.vosk_model = Model(model_path)
                self.recognizer = KaldiRecognizer(self.vosk_model, 16000)
                self.recognizer.SetWords(True)
            print(f"  [FR-KWS] ✅ Prêt. Mots-clés : {self.wake_words}", flush=True)
        
        self.vad = webrtcvad.Vad(3)
        self.noise_sample = None
        self.calibration_frames = 0
        self.MAX_CALIBRATION = 30
        self.RATE = 16000
        self.CHUNK = 480
        self.last_detection_time = 0
        self.COOLDOWN = 2.0
        
    def _reduce_noise(self, audio_data, sample_rate=16000):
        try:
            audio_float = audio_data.astype(np.float32)
            if self.noise_sample is not None:
                reduced = nr.reduce_noise(
                    y=audio_float,
                    y_noise=self.noise_sample,
                    sr=sample_rate,
                    stationary=True,
                    prop_decrease=1.0
                )
            else:
                reduced = nr.reduce_noise(y=audio_float, sr=sample_rate, stationary=False)
            return reduced.astype(np.int16)
        except:
            return audio_data
    
    def _apply_bandpass(self, audio_data, sample_rate=16000):
        try:
            nyquist = sample_rate / 2
            low = 300 / nyquist
            high = 3400 / nyquist
            b, a = scipy_signal.butter(4, [low, high], btype='band')
            filtered = scipy_signal.filtfilt(b, a, audio_data.astype(np.float32))
            return filtered.astype(np.int16)
        except:
            return audio_data
    
    def calibrate_noise(self, audio_chunk):
        if self.calibration_frames < self.MAX_CALIBRATION:
            if self.noise_sample is None:
                self.noise_sample = audio_chunk.astype(np.float32)
            else:
                self.noise_sample = (self.noise_sample * 0.9 + audio_chunk.astype(np.float32) * 0.1)
            self.calibration_frames += 1
            if self.calibration_frames == self.MAX_CALIBRATION:
                print(f"  [FR-KWS] 🎚️ Calibration terminée.", flush=True)
    
    def process_audio(self, audio_chunk):
        if self.calibration_frames < self.MAX_CALIBRATION:
            self.calibrate_noise(audio_chunk)
            return None
        
        if time.time() - self.last_detection_time < self.COOLDOWN:
            return None
        
        if not self.recognizer:
            return None
        
        try:
            audio_bytes = audio_chunk.tobytes()
            is_speech = self.vad.is_speech(audio_bytes, self.RATE)
            if not is_speech:
                return None
        except:
            pass
        
        audio_clean = self._reduce_noise(audio_chunk, self.RATE)
        audio_filtered = self._apply_bandpass(audio_clean, self.RATE)
        
        try:
            audio_bytes = audio_filtered.tobytes()
            
            if self.recognizer.AcceptWaveform(audio_bytes):
                result = json.loads(self.recognizer.Result())
                text = result.get("text", "").lower().strip()
            else:
                partial = json.loads(self.recognizer.PartialResult())
                text = partial.get("partial", "").lower().strip()
            
            if not text:
                return None
            
            if len(text) > 2:
                print(f"  [FR-KWS] 🎤 '{text}'", end='\r', flush=True)
            
            for wake_word in self.wake_words:
                if wake_word in text:
                    print(f"\n  [FR-KWS] 🟢 DÉTECTION : '{wake_word}' dans '{text}'", flush=True)
                    self.recognizer = KaldiRecognizer(self.vosk_model, 16000)
                    self.recognizer.SetWords(True)
                    self.last_detection_time = time.time()
                    return wake_word
            
            return None
        except Exception as e:
            print(f"  [FR-KWS] Erreur : {e}")
            return None

# --- CONFIGURATION SONNERIES ---
def play_actual_alarm_sound():
    
    old_vol = skills.get_system_volume()
    if old_vol < 10: skills.set_raw_volume(30)
    
    sound_name = skills.MEMORY.get('alarm_sound', 'classique')
    text_pattern = skills.ALARM_STYLES.get(sound_name, skills.ALARM_STYLES['classique'])
    print(f"  [ALARM] Playing style: {sound_name}", flush=True)
    
    # On réinitialise le drapeau pour permettre l'arrêt manuel via le bouton STOP
    skills.ABORT_FLAG = False

    try:
        if text_pattern == "GONG":
            cmd = ["mpv", "--no-terminal", "av://lavfi:sine=f=440:b=4:d=1"]
            for _ in range(5): 
                # MODIFICATION : On ne coupe plus sur IS_MUTED, mais sur demande d'arrêt explicite (STOP)
                if skills.ABORT_FLAG: break 
                
                # On utilise igor_globals.CURRENT_PLAYER_PROCESS pour permettre l'interruption via stop_speaking()
                with igor_globals.SPEAK_LOCK:
                    igor_globals.CURRENT_PLAYER_PROCESS = subprocess.Popen(cmd, stderr=subprocess.DEVNULL)
                
                # On attend la fin du son (ou l'interruption)
                try:
                    if igor_globals.CURRENT_PLAYER_PROCESS: igor_globals.CURRENT_PLAYER_PROCESS.wait()
                except: break
                
                time.sleep(0.5)
        else:
            for _ in range(3):
                # MODIFICATION : Arrêt uniquement si bouton STOP pressé
                if skills.ABORT_FLAG: break
                # MODIFICATION : On force la parole même si l'agent est en mode muet
                speak_logic(text_pattern, ignore_mute=True)
                time.sleep(1)
    finally: pass

# Liaison du callback (important de le faire après la définition)
skills.PLAY_ALARM_CALLBACK = play_actual_alarm_sound

# --- FONCTIONS AUDIO ---
def listen_hybrid_logic():
    r = sr.Recognizer()
    
    # 1. RÉGLAGE DE LA SENSIBILITÉ
    # On laisse l'algo décider du seuil de bruit (plus fiable que 300 fixe)
    r.dynamic_energy_threshold = True  
    
    # 2. RÉGLAGE DE LA PATIENCE
    # C'est ici que ça se joue : on passe de 1.0 à 1.5 secondes de silence
    # nécessaire pour valider la fin de la phrase.
    r.pause_threshold = 1.5 
    
    # Évite de couper si on parle doucement
    r.phrase_threshold = 0.3
    
    # Ducking (Baisse le son du système pour mieux entendre)
    saved_vol = skills.get_system_volume()
    skills.set_raw_volume(max(10, saved_vol - 40)) 
    
    text_out = ""
    try:
        with no_alsa_err():
            with sr.Microphone(sample_rate=16000) as source:
                # 3. DÉMARRAGE PLUS RAPIDE
                # On réduit le temps de calibration de 0.5 à 0.2 pour ne pas rater le début
                r.adjust_for_ambient_noise(source, duration=0.2)
                
                try: 
                    # On augmente un peu le timeout global
                    audio = r.listen(source, timeout=5, phrase_time_limit=15)
                    
                    try: 
                        text_out = r.recognize_google(audio, language="fr-FR")
                    except:
                        if VOSK_MODEL:
                            rec = KaldiRecognizer(VOSK_MODEL, 16000)
                            if rec.AcceptWaveform(audio.get_raw_data(convert_rate=16000, convert_width=2)):
                                text_out = json.loads(rec.Result())['text']
                            else:
                                text_out = json.loads(rec.FinalResult())['text']
                except sr.WaitTimeoutError: pass
    finally:
        skills.set_raw_volume(saved_vol) # Restaure le son
    
    return text_out

def stop_speaking():
    """Arrêt immédiat, forcé et nettoyage des tâches."""
    igor_globals.CURRENT_PLAYER_PROCESS
    
    # 1. On vide la file d'attente via la nouvelle fonction
    skills.abort_tasks()

    # 2. On tue le processus audio
    acquired = igor_globals.SPEAK_LOCK.acquire(timeout=0.1)
    try:
        if igor_globals.CURRENT_PLAYER_PROCESS:
            try:
                igor_globals.CURRENT_PLAYER_PROCESS.kill()
            except: pass
        igor_globals.CURRENT_PLAYER_PROCESS = None
    finally:
        if acquired:
            igor_globals.SPEAK_LOCK.release()

def speak_logic(text, ignore_mute=False):
    # MODIFICATION : On vérifie si on doit ignorer le mode muet
    if igor_globals.IS_MUTED and not ignore_mute: return
    
    if not text: return
    clean_text = text.replace(f"{skills.MEMORY['agent_name']}:", "").strip()
    if not clean_text: return
    
    with igor_globals.SPEAK_LOCK:
        try:
            if igor_globals.CURRENT_PLAYER_PROCESS:
                try: igor_globals.CURRENT_PLAYER_PROCESS.terminate()
                except: pass
            
            # --- FIX SEGFAULT: ISOLATION DU TTS ---
            # On exécute gTTS dans un processus séparé (python -c ...)
            # Cela évite le crash "getaddrinfo" qui arrive quand on fait du réseau
            # dans un thread secondaire avec une interface GTK active.
            try:
                # On utilise repr() pour passer le texte proprement (avec guillemets)
                cmd_gen = [
                    sys.executable, 
                    "-c", 
                    f"from gtts import gTTS; gTTS(text={repr(clean_text)}, lang='fr').save('response.mp3')"
                ]
                # On attend que la génération soit finie (timeout 10s pour pas bloquer)
                subprocess.check_call(cmd_gen, stderr=subprocess.DEVNULL, timeout=10)
            except Exception as e:
                print(f"  [ERR] Génération TTS échouée (Pas de réseau ?): {e}", flush=True)
                return
            # --------------------------------------

            speed = skills.MEMORY.get('voice_speed', 1.1)
            cmd = ["mpv", f"--speed={speed}", "--af=scaletempo", "--no-terminal", "response.mp3"]
            
            igor_globals.CURRENT_PLAYER_PROCESS = subprocess.Popen(cmd, stderr=subprocess.DEVNULL)
            igor_globals.CURRENT_PLAYER_PROCESS.wait() 
        except Exception as e: print(f"TTS ERR: {e}", flush=True)
        finally: igor_globals.CURRENT_PLAYER_PROCESS = None

def set_mute_wrapper(state):
    
    # Gestion du basculement ou de l'état forcé
    if state == "toggle":
        igor_globals.IS_MUTED = not igor_globals.IS_MUTED
    else:
        igor_globals.IS_MUTED = (str(state).lower() == "true" or state is True)

    # MODIFICATION ICI : Sauvegarde immédiate
    skills.MEMORY['muted'] = igor_globals.IS_MUTED
    skills.save_memory(skills.MEMORY)
        
    # Action immédiate
    if igor_globals.IS_MUTED:
        stop_speaking() # Coupe la parole en cours
        
    return "Mode silencieux activé." if igor_globals.IS_MUTED else "Je peux parler à nouveau."

skills.SET_MUTE_CALLBACK = set_mute_wrapper