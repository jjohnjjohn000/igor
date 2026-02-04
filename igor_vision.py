import cv2
import threading
import time
import base64
import requests
import pyautogui
import json
import datetime
import os
import shutil
import subprocess
# --- IMPORTS GEMINI (CLOUD FALLBACK) ---
print("  [DEBUG-INIT] Tentative chargement modules Google...", flush=True)
try:
    import google.generativeai as genai
    from PIL import Image
    GEMINI_LIB_AVAILABLE = True
    print("  [DEBUG-INIT] Modules Google OK.", flush=True)
except ImportError as e:
    GEMINI_LIB_AVAILABLE = False
    print(f"  [WARN] 'google-generativeai' ou 'Pillow' manquant : {e}", flush=True)

# 🔑 CLÉ API GEMINI
# Chargement depuis le fichier .env pour la sécurité
import os
try:
    from dotenv import load_dotenv
    load_dotenv() # Charge les variables du fichier .env
except ImportError:
    pass # Si python-dotenv n'est pas installé, on espère que la variable est dans le système

GEMINI_API_KEY = os.getenv("GOOGLE_API_KEY")

GEMINI_READY = False

if GEMINI_LIB_AVAILABLE:
    if not GEMINI_API_KEY or "METS_TA_CLE" in GEMINI_API_KEY:
        print("  [DEBUG-INIT] Clé Gemini absente (Vérifiez votre fichier .env).", flush=True)
    else:
        try:
            genai.configure(api_key=GEMINI_API_KEY)
            GEMINI_READY = True
            print(f"  [DEBUG-INIT] Gemini configuré et PRÊT (Clé: ...{GEMINI_API_KEY[-5:]})", flush=True)
        except Exception as e:
            print(f"  [ERR] Erreur configuration Gemini : {e}", flush=True)
else:
    print("  [DEBUG-INIT] Gemini désactivé (Libs manquantes).", flush=True)

# Import de la configuration partagée
import igor_config
from igor_config import (
    OLLAMA_API_URL, 
    VISION_MODEL, 
    FAST_VISION_MODEL, 
    USER_HOME,
    SHARED_FRAME, 
    SHARED_FRAME_LOCK, 
    ABORT_FLAG, 
    ON_FRAME_CALLBACK,
    CURRENT_VISION_SESSION,
    WATCH_RUNNING,
    WATCH_THREAD,
    LAST_SEEN_LABELS,
    TASK_QUEUE  # AJOUTÉ : Pour envoyer les commandes gestuelles
)

# Import de l'utilitaire système pour trouver les fenêtres
from igor_system import get_window_geometry

# --- DIAGNOSTIC ET IMPORT DES LIBRAIRIES LOURDES ---
print("\n--- DIAGNOSTIC VISION DÉMARRAGE ---")

VISION_LIBS_AVAILABLE = False
YOLO_AVAILABLE = False
GESTURES_AVAILABLE = False
mp = None
fast_model = None
hands_detector = None

# 1. OpenCV
try:
    import cv2
    VISION_LIBS_AVAILABLE = True
    print("  [DEBUG] OpenCV: OK")
except Exception as e:
    print(f"  [DEBUG] OpenCV: ÉCHEC ({e})")

# 2. YOLO
try:
    from ultralytics import YOLO
    YOLO_AVAILABLE = True
    print("  [DEBUG] YOLO: OK")
except Exception as e:
    print(f"  [DEBUG] YOLO: ÉCHEC ({e})")

# 3. MediaPipe
try:
    import mediapipe as mp
    print(f"  [DEBUG] Import 'mediapipe' de base: OK")
    GESTURES_AVAILABLE = True
except Exception as e:
    print(f"  [DEBUG] Import 'mediapipe' TOTALEMENT PLANTÉ: {e}")
    mp = None

print("-----------------------------------\n")

# --- ANALYSE DES GESTES (MEDIAPIPE) ---

def _analyze_hand_gesture(hand_landmarks):
    """
    Déduit un geste parmi 7 classes (Version Robuste pour commande système).
    """
    # Points clés
    thumb_tip = hand_landmarks.landmark[4]
    thumb_ip  = hand_landmarks.landmark[3]
    index_mcp = hand_landmarks.landmark[2] # Base de l'index
    
    # Doigts (Index, Majeur, Annulaire, Auriculaire)
    finger_tips = [8, 12, 16, 20]
    finger_pips = [6, 10, 14, 18]
    
    fingers_extended = []

    # 1. Analyse des 4 doigts longs (Haut/Bas)
    # Note : En image, Y=0 est en haut. Donc Tip < Pip signifie "Levé".
    for tip, pip in zip(finger_tips, finger_pips):
        if hand_landmarks.landmark[tip].y < hand_landmarks.landmark[pip].y:
            fingers_extended.append(True)
        else:
            fingers_extended.append(False)

    # Décomposition
    i, m, r, p = fingers_extended
    fingers_count = fingers_extended.count(True)

    # 2. Analyse du Pouce (Simplifiée)
    # On regarde l'écart horizontal (X) par rapport à la base de l'index
    thumb_is_out = abs(thumb_tip.x - index_mcp.x) > 0.04
    
    thumb_is_up = thumb_tip.y < thumb_ip.y
    thumb_is_down = thumb_tip.y > thumb_ip.y

    # --- CLASSIFICATION ---

    # 1. MAIN OUVERTE (OPEN_HAND) -> VISUEL UNIQUEMENT
    if i and m and r and p:
        return "OPEN_HAND"

    # 2. POING (FIST) -> PAUSE
    if fingers_count == 0 and not thumb_is_out:
        return "FIST"

    # 3. POUCE HAUT (THUMB_UP) -> NON ASSOCIÉ
    if fingers_count == 0 and thumb_is_out and thumb_is_up:
        return "THUMB_UP"

    # 4. POUCE BAS (THUMB_DOWN) -> NON ASSOCIÉ
    if fingers_count == 0 and thumb_is_out and thumb_is_down:
        return "THUMB_DOWN"

    # 5. VICTOIRE (VICTORY) -> (Gardé en réserve ou Non Associé)
    if i and m and not r and not p:
        return "VICTORY"

    # 6. POINTÉ (POINTING_UP) -> VOLUME 50%
    if i and not m and not r and not p:
        return "POINTING_UP"

    # 7. ROCK / CORNES (ROCK) -> PLAY (Reprendre lecture)
    if i and not m and not r and p:
        return "ROCK"

    return None

# --- WORKER D'ANALYSE (YOLO + GESTES) ---

def _yolo_analysis_loop():
    """
    Thread dédié qui analyse l'image (YOLO pour objets + MediaPipe pour gestes).
    Gère le cooldown de 2 secondes et l'envoi des commandes via TASK_QUEUE.
    """
    global fast_model, hands_detector
    
    print("  [WATCH-AI] Thread d'analyse démarré.", flush=True)

    # --- 1. INIT YOLO ---
    if YOLO_AVAILABLE:
        try:
            fast_model = YOLO("yolov8n.pt")
        except Exception as e:
            print(f"  [ERR] Erreur chargement YOLO : {e}", flush=True)

    # --- 2. INIT MEDIAPIPE ---
    if mp:
        try:
            hands_detector = mp.solutions.hands.Hands(
                static_image_mode=False,
                max_num_hands=1,
                min_detection_confidence=0.7,
                min_tracking_confidence=0.75
            )
        except:
            # Fallback pour compatibilité versions
            try:
                hands_detector = mp.python.solutions.hands.Hands(
                    static_image_mode=False,
                    max_num_hands=1,
                    min_detection_confidence=0.7,
                    min_tracking_confidence=0.75
                )
            except:
                hands_detector = None

    # --- 3. CONFIGURATION ACTIONS & COOLDOWN ---
    GESTURE_COOLDOWN = 2.0  # Temps de pause après une détection
    last_gesture_time = 0   # Timestamp de la dernière action

    # Mapping Gestes -> Outils Igor
    GESTURE_ACTIONS = {
        # "OPEN_HAND": Non associé à un outil (Traitement spécial pour visuel)
        "FIST":        {"tool": "MEDIA", "args": "pause"},       # Pause uniquement
        "ROCK":        {"tool": "MEDIA", "args": "play"},        # Reprend la lecture
        "POINTING_UP": {"tool": "VOLUME", "args": "50"},         # Volume 50%
        # "THUMB_UP":    Non associé
        # "THUMB_DOWN":  Non associé
        "VICTORY":     {"tool": "VOLUME", "args": "30"},         # Volume 30%
    }

    # --- 4. BOUCLE D'ANALYSE ---
    while igor_config.WATCH_RUNNING:
        
        # A. GESTION DU COOLDOWN (Arrêt temporaire du détecteur)
        if time.time() - last_gesture_time < GESTURE_COOLDOWN:
            time.sleep(0.1) # On dort pour laisser le temps à l'action de se faire
            continue

        img_to_process = None
        
        # Récupération thread-safe de l'image
        with igor_config.SHARED_FRAME_LOCK:
            if igor_config.SHARED_FRAME is not None:
                img_to_process = igor_config.SHARED_FRAME.copy()
        
        if img_to_process is None:
            time.sleep(0.1)
            continue

        # B. ANALYSE GESTES (MediaPipe) - Prioritaire sur YOLO
        if hands_detector:
            try:
                img_rgb = cv2.cvtColor(img_to_process, cv2.COLOR_BGR2RGB)
                mp_results = hands_detector.process(img_rgb)
                
                if mp_results.multi_hand_landmarks:
                    gesture = _analyze_hand_gesture(mp_results.multi_hand_landmarks[0])
                    
                    if gesture:
                        # Cas Spécial : OPEN_HAND (Juste un log visuel, pas d'action système)
                        if gesture == "OPEN_HAND":
                            print(f"  [GESTURE] 🖐️ Je te vois (En attente...)", flush=True)
                            if igor_config.ON_GESTURE_CALLBACK:
                                igor_config.ON_GESTURE_CALLBACK("OPEN_HAND")
                            # On ne met pas de cooldown ici pour permettre d'enchainer
                            # avec un vrai geste rapidement, ou on peut en mettre un petit.
                            time.sleep(0.5) 
                            continue

                        # Cas Commandes Actives
                        if gesture in GESTURE_ACTIONS:
                            action = GESTURE_ACTIONS[gesture]
                            print(f"  [GESTURE] 👉 Détecté : {gesture} -> Commande : {action['tool']}", flush=True)
                            
                            # 1. Envoi de la commande
                            igor_config.TASK_QUEUE.put(action)
                            
                            # 2. Activation du cooldown (Arrêt du détecteur)
                            last_gesture_time = time.time()
                            
                            # On saute YOLO pour ce cycle afin d'être réactif
                            continue 
            except Exception:
                pass

        # C. ANALYSE OBJETS (YOLO)
        # Ne s'exécute que si aucun geste n'a été détecté ce cycle-ci
        if fast_model:
            try:
                results = fast_model(img_to_process, verbose=False, conf=0.75)
                current_labels = set()
                for r in results:
                    for box in r.boxes:
                        try:
                            cls_id = int(box.cls[0])
                            if cls_id < len(fast_model.names):
                                current_labels.add(fast_model.names[cls_id])
                        except: pass
                
                # Mise à jour mémoire visuelle
                new_items = current_labels - igor_config.LAST_SEEN_LABELS
                if new_items:
                    print(f"  [WATCH] Vu : {', '.join(list(new_items))}", flush=True)
                
                igor_config.LAST_SEEN_LABELS = current_labels
            except Exception: pass
        
        time.sleep(0.1)

    if hands_detector:
        hands_detector.close()
    print("  [WATCH-AI] Arrêt du moteur d'analyse.", flush=True)

# --- WORKER PRINCIPAL (WEBCAM) ---

def _hybrid_watch_worker():
    """
    Thread Principal Vidéo (Yeux).
    Gère la caméra et l'affichage fluide. Délègue l'analyse au thread _yolo_analysis_loop.
    """
    print(f"  [WATCH] Démarrage du flux vidéo fluide...", flush=True)

    # 1. DÉTECTION ROBUSTE DE LA CAMÉRA
    cap = None
    potential_indexes = [0, 1, 2, 3, 4]
    
    for idx in potential_indexes:
        try:
            temp_cap = cv2.VideoCapture(idx, cv2.CAP_V4L2)
            # Paramètres vitaux pour Linux/USB
            fourcc = cv2.VideoWriter_fourcc(*'MJPG')
            temp_cap.set(cv2.CAP_PROP_FOURCC, fourcc)
            temp_cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            temp_cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
            
            if not temp_cap.isOpened():
                temp_cap.release()
                continue
                
            # Test de lecture réel
            ret, frame = temp_cap.read()
            if ret and frame is not None and frame.size > 0:
                print(f"  [WATCH] Caméra OK sur index {idx}", flush=True)
                cap = temp_cap
                break
            
            temp_cap.release()
        except: pass

    if cap is None:
        print("  [WATCH] ERREUR FATALE : Aucune caméra trouvée.", flush=True)
        igor_config.WATCH_RUNNING = False
        return

    # 2. LANCEMENT DU CERVEAU (Thread d'analyse)
    ai_thread = threading.Thread(target=_yolo_analysis_loop, daemon=True)
    ai_thread.start()

    # 3. BOUCLE PRINCIPALE (Affichage pur)
    last_ollama_check = time.time()
    
    while igor_config.WATCH_RUNNING:
        ret, frame = cap.read()
        if not ret:
            print("  [WATCH] Perte du flux caméra.")
            break

        # A. Mise à jour de l'image partagée (pour le thread YOLO)
        with igor_config.SHARED_FRAME_LOCK:
            igor_config.SHARED_FRAME = frame # Assignation directe (rapide)

        # B. Envoi à l'interface GTK (Fluide) via le callback défini dans main.py
        if igor_config.ON_FRAME_CALLBACK:
            try:
                # Redimensionnement léger pour l'UI
                mini = cv2.resize(frame, (200, 150))
                rgb = cv2.cvtColor(mini, cv2.COLOR_BGR2RGB)
                igor_config.ON_FRAME_CALLBACK(rgb)
            except Exception: pass

        # C. Analyse Lente (Ollama Vision) - Toutes les 20 secondes
        if time.time() - last_ollama_check > 20:
            last_ollama_check = time.time()
            
            def _async_ollama(img_snp):
                try:
                    # Compression et encodage
                    _, buf = cv2.imencode('.jpg', img_snp, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
                    b64_str = base64.b64encode(buf).decode('utf-8')
                    
                    prompt = "Describe briefly what you see (1 sentence)."
                    payload = {
                        "model": VISION_MODEL,
                        "prompt": prompt,
                        "stream": False,
                        "images": [b64_str]
                    }
                    # Requête bloquante mais dans son propre thread, donc pas grave
                    requests.post(OLLAMA_API_URL, json=payload, timeout=20)
                except: pass

            # On envoie une COPIE de l'image au thread Ollama
            threading.Thread(target=_async_ollama, args=(frame.copy(),), daemon=True).start()

        # D. Cadencement (30 FPS environ)
        time.sleep(0.03)

    # Nettoyage
    cap.release()
    igor_config.WATCH_RUNNING = False
    print("  [WATCH] Arrêt du flux vidéo.")

# --- OUTILS EXPOSÉS (TOOLS) ---

def tool_surveillance(arg):
    """Active ou désactive la surveillance hybride."""
    
    if not VISION_LIBS_AVAILABLE:
        return f"Erreur chargement modules Vision. Vérifiez l'installation (pip)."

    arg = str(arg).lower()
    
    if "stop" in arg or "arrêt" in arg or "off" in arg:
        if not igor_config.WATCH_RUNNING: return "La surveillance est déjà éteinte."
        igor_config.WATCH_RUNNING = False
        return "Je désactive ma vue."
    
    if igor_config.WATCH_RUNNING:
        return "Je surveille déjà votre environnement."
        
    igor_config.WATCH_RUNNING = True
    # On lance le worker dans un thread séparé
    igor_config.WATCH_THREAD = threading.Thread(target=_hybrid_watch_worker, daemon=True)
    igor_config.WATCH_THREAD.start()
    
    return "Surveillance activée. Je détecte les objets et les gestes."

def tool_vision_look(arg):
    """
    1. Capture (Webcam/Écran).
    2. Tente analyse via Gemini 1.5 Flash (Cloud, Rapide, Intelligent).
    3. Si échec/offline, bascule sur Ollama (Local).
    """
    # 1. Check immédiat
    if igor_config.ABORT_FLAG: return "Analyse annulée."

    arg_str = str(arg).strip()
    arg_lower = arg_str.lower()
    
    # 2. Configuration Modèle LOCAL (Fallback)
    # Détection mode "Vite" pour le fallback Ollama
    is_fast_mode = any(k in arg_lower for k in ["vite", "rapide", "fast", "speed", "éclair", "flash"])
    selected_local_model = FAST_VISION_MODEL if is_fast_mode else VISION_MODEL
    
    # Mode sauvegarde (ne fait pas d'IA, juste enregistre l'image)
    save_mode = any(k in arg_lower for k in ["sauvegarde", "enregistre", "garde", "cliché"])
    
    # --- DÉTECTION SOURCE ---
    use_webcam = False
    webcam_keywords = ["photo", "webcam", "caméra", "camera", "selfie", "tête", "visage", "moi"]
    if any(k in arg_lower for k in webcam_keywords): use_webcam = True
    
    screen_keywords = ["écran", "screen", "fenêtre", "window", "capture", "site", "page", "app", "logiciel"]
    if any(k in arg_lower for k in screen_keywords): use_webcam = False

    target_desc = "la caméra" if use_webcam else "l'écran"
    region = None 

    # 3. Détection Fenêtre (Mode Écran uniquement)
    if not use_webcam:
        target_window_name = None
        clean = arg_str
        # Nettoyage des mots parasites
        for prefix in ["regarde ", "vois ", "analyse ", "décris ", "prends ", "fais ", "le ", "la ", "l'", "les ", "un ", "une ", "vite ", "rapide ", "capture ", "d'", "de ", "du "]:
            if clean.lower().startswith(prefix): clean = clean[len(prefix):].strip()
        
        ignored_words = ["ca", "ça", "ceci", "tout", "image", "maintenant", "écran", "ecran", "l'écran", "screen", "fenêtre", "bureau", "desktop"]
        if len(clean) > 2 and clean.lower() not in ignored_words:
            target_window_name = clean

        if target_window_name:
            geo_data = get_window_geometry(target_window_name)
            if geo_data:
                # x, y, w, h
                region = (geo_data[0], geo_data[1], geo_data[2], geo_data[3])
                target_desc = f"la fenêtre '{geo_data[4]}'"

    # 4. CAPTURE DE L'IMAGE
    if igor_config.ABORT_FLAG: return "Annulé."
    
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    final_path = f"/tmp/igor_vision_snap_{timestamp}.jpg"
    
    if save_mode:
        sub_folder = "Photos" if use_webcam else "Captures"
        save_dir = os.path.join(USER_HOME, "Images", sub_folder)
        if not os.path.exists(save_dir): os.makedirs(save_dir)
        filename = f"{'Photo' if use_webcam else 'Capture'}_{timestamp}.jpg"
        final_path = os.path.join(save_dir, filename)

    print(f"  [VISION] Capture via {target_desc}...", flush=True)

    try:
        # Petite pause pour l'UI
        time.sleep(0.1)
        if igor_config.ABORT_FLAG: return "Annulé."

        if use_webcam:
            if not VISION_LIBS_AVAILABLE: return "Module OpenCV non disponible."
            
            frame_captured = None
            # Essai via le flux partagé (Si surveillance active)
            with igor_config.SHARED_FRAME_LOCK:
                if igor_config.SHARED_FRAME is not None:
                    frame_captured = igor_config.SHARED_FRAME.copy()
            
            # Sinon, ouverture dédiée
            if frame_captured is None:
                cap = cv2.VideoCapture(0)
                if not cap.isOpened(): return "Impossible d'ouvrir la caméra."
                for _ in range(15): # Warmup
                    ret, tmp_frame = cap.read()
                    if ret: frame_captured = tmp_frame
                    time.sleep(0.05)
                cap.release()
            
            if frame_captured is None: return "Échec capture caméra."
            cv2.imwrite(final_path, frame_captured)

        else:
            # Capture écran (avec ou sans région)
            myScreenshot = pyautogui.screenshot(region=region)
            myScreenshot.save(final_path)

    except Exception as e:
        return f"Erreur capture : {e}"

    if save_mode: return f"📸 Image enregistrée : {final_path}"

    # 5. PRÉPARATION DU PROMPT
    if "décris" in arg_lower or arg_str == "None" or len(arg_str) < 4:
        final_prompt = "Describe this image in detail, but answer in French."
    else:
        final_prompt = arg_str

    # --- 6. WORKER INTELLIGENT (CLOUD -> LOCAL) ---
    # Container pour récupérer le résultat du thread
    result_container = {"text": "", "error": None, "finished": False, "source": ""}

    # === AJOUT : FENÊTRE DE PRÉVISUALISATION (VERSION STABLE) ===
    def _preview_worker():
        """
        Ouvre l'image avec le visionneur par défaut du système.
        Utilise subprocess pour éviter le conflit GTK/OpenCV (Segmentation Fault).
        """
        try:
            # On s'assure que le fichier existe sur le disque
            if not os.path.exists(final_path) and use_webcam and 'frame_captured' in locals():
                # Si c'était en mémoire (webcam), on l'écrit temporairement pour le viewer
                cv2.imwrite(final_path, frame_captured)

            if os.path.exists(final_path):
                print(f"  [VISION-PREVIEW] Ouverture viewer externe : {final_path}", flush=True)
                
                # Lancement non-bloquant du visionneur système
                # xdg-open fonctionne sur la plupart des Linux (Gnome, KDE, etc.)
                viewer_process = subprocess.Popen(
                    ["xdg-open", final_path], 
                    stdout=subprocess.DEVNULL, 
                    stderr=subprocess.DEVNULL
                )
                
                # Optionnel : On peut tuer le viewer quand l'IA a fini, 
                # ou le laisser ouvert pour l'utilisateur.
                # Ici, on le laisse ouvert car c'est plus agréable pour l'utilisateur.
                
        except Exception as e:
            print(f"  [VISION-PREVIEW] Erreur ouverture viewer : {e}")

    # Lancement du thread (Daemon pour ne pas bloquer)
    threading.Thread(target=_preview_worker, daemon=True).start()

    def _smart_vision_worker():
        nonlocal result_container
        gemini_success = False

        # === DEBUG STATUS GEMINI ===
        # Vérifie si la variable globale GEMINI_READY existe et est True
        is_gemini_ready = globals().get("GEMINI_READY", False)
        print(f"  [DEBUG-WORKER] Statut GEMINI_READY: {is_gemini_ready}", flush=True)

        # === TENTATIVE 1 : GEMINI (CLOUD) ===
        if is_gemini_ready:
            try:
                print("  [DEBUG-WORKER] Appel API Google Gemini...", flush=True)
                
                # Correction : On force le français via system_instruction
                model = genai.GenerativeModel(
                    'gemini-2.5-flash',
                    system_instruction="Tu es un assistant utile. Analyse l'image et réponds toujours en Français."
                )
                
                pil_img = Image.open(final_path)
                
                # Pas besoin de stream pour Gemini, c'est très rapide
                response = model.generate_content([final_prompt, pil_img])
                
                if response.text:
                    result_container["text"] = response.text
                    result_container["source"] = "☁️ Gemini"
                    gemini_success = True
                    print("  [DEBUG-WORKER] Succès Gemini.", flush=True)
            except Exception as e:
                print(f"  [DEBUG-WORKER] ❌ ÉCHEC Gemini : {e}", flush=True)
                
                # DIAGNOSTIC : Affiche les modèles disponibles si la clé fonctionne
                try:
                    mods = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
                    print(f"  [DEBUG-INFO] Modèles disponibles pour votre clé : {mods}", flush=True)
                except Exception as ex:
                    print(f"  [DEBUG-INFO] Impossible de lister les modèles : {ex}", flush=True)

                print("  [DEBUG-WORKER] Bascule vers Ollama...", flush=True)
                gemini_success = False
        else:
            print("  [DEBUG-WORKER] Gemini ignoré (Non configuré ou Clé invalide).", flush=True)
        
        # === TENTATIVE 2 : OLLAMA (LOCAL) ===
        # Si Gemini n'est pas configuré OU s'il a planté
        if not gemini_success:
            try:
                print(f"  [VISION] Démarrage Ollama ({selected_local_model})...", flush=True)
                
                # Encodage Base64 pour Ollama
                with open(final_path, "rb") as image_file:
                    encoded_string = base64.b64encode(image_file.read()).decode('utf-8')

                payload = {
                    "model": selected_local_model, 
                    "prompt": final_prompt,
                    "stream": True, 
                    "images": [encoded_string]
                }

                igor_config.CURRENT_VISION_SESSION = requests.Session()
                
                # Requête streamée
                with igor_config.CURRENT_VISION_SESSION.post(OLLAMA_API_URL, json=payload, stream=True, timeout=300) as response:
                    if response.status_code != 200:
                        result_container["error"] = f"Erreur Ollama ({response.status_code})"
                        return

                    for line in response.iter_lines():
                        # Interruption propre
                        if igor_config.ABORT_FLAG: break 
                        if line:
                            try:
                                json_part = json.loads(line.decode('utf-8'))
                                chunk = json_part.get("response", "")
                                result_container["text"] += chunk
                                if json_part.get("done", False): break
                            except: pass
                
                result_container["source"] = f"🏠 {selected_local_model}"
                
            except Exception as e:
                # On ne log l'erreur que si ce n'est pas une annulation volontaire
                if not igor_config.ABORT_FLAG:
                    result_container["error"] = str(e)
            finally:
                igor_config.CURRENT_VISION_SESSION = None

        # Nettoyage final
        result_container["finished"] = True
        
        # Suppression fichier temporaire
        if not save_mode and os.path.exists(final_path):
            try: os.remove(final_path)
            except: pass

    # Lancement du thread (Daemon pour qu'il ne bloque pas la fermeture du programme)
    t = threading.Thread(target=_smart_vision_worker, daemon=True)
    t.start()

    # --- 7. ATTENTE ACTIVE (STOP) ---
    # On attend que le thread finisse tout en surveillant le bouton STOP
    while not result_container["finished"]:
        if igor_config.ABORT_FLAG:
            print("  [VISION] Interruption immédiate demandée (STOP).", flush=True)
            if igor_config.CURRENT_VISION_SESSION:
                try: igor_config.CURRENT_VISION_SESSION.close()
                except: pass
            return "Analyse stoppée."
        time.sleep(0.1)

    # --- 8. RÉSULTAT ---
    if result_container["error"]:
        return f"Erreur Vision : {result_container['error']}"
        
    return f"{result_container['source']} ({target_desc}) : {result_container['text']}"