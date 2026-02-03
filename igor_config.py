# igor_config.py
import sys
import site
# FORCE la priorité aux paquets utilisateur (pip install --user) vs système
# Cela permet de charger TON protobuf récent au lieu de celui obsolète de Linux
sys.path.insert(0, site.getusersitepackages())

import os
import glob
import shutil
import json
import urllib.parse
import subprocess
import re
import datetime
import time
import unicodedata
import requests
import queue
import threading
from difflib import SequenceMatcher

# --- CONFIGURATION & PATHS ---
KNOWLEDGE_DIR = "knowledge"
PROJECTS_DIR = "projects"
MEMORY_FILE = "memory.json"
SEARCH_LOG_FILE = "search_history.log"  # Fichier de log
USER_HOME = os.path.expanduser("~")
SHORTCUTS_FILE = "shortcuts.json"

# --- CONFIGURATION API ---
OLLAMA_API_URL = "http://localhost:11434/api/generate"
VISION_MODEL = "llama3.2-vision"  # Modèle haute précision (lent)
FAST_VISION_MODEL = "llava-llama3" # Modèle rapide (moins précis mais véloce)
LLM_TEXT_API_URL = "http://localhost:8080/completion"

# --- CRÉATION DES DOSSIERS ---
if not os.path.exists(KNOWLEDGE_DIR):
    os.makedirs(KNOWLEDGE_DIR)

if not os.path.exists(PROJECTS_DIR):
    os.makedirs(PROJECTS_DIR)

# --- VARIABLES PARTAGÉES (GLOBALS) ---
# File d'attente des tâches (accessible par tous les modules)
TASK_QUEUE = queue.Queue()

# Variable pour stocker temporairement les choix d'ambiguïté Wikipedia
# (Utilisé par igor_knowledge.py, mais doit être accessible globalement)
LAST_WIKI_OPTIONS = []

# Verrou pour protéger la mémoire des alarmes
ALARM_LOCK = threading.Lock() 

# Variables globales pour la Vision et le Threading
SHARED_FRAME = None
SHARED_FRAME_LOCK = threading.Lock()
WATCH_RUNNING = False
WATCH_THREAD = None
LAST_SEEN_LABELS = set()

# Variables pour la gestion des gestes
LAST_GESTURE_TIME = 0
GESTURE_COOLDOWN = 2.5 # Secondes d'attente entre deux actions gestuelles

# Drapeau d'annulation global (STOP)
ABORT_FLAG = False

# Variable pour tuer la requête HTTP Vision en cours
CURRENT_VISION_SESSION = None 

# Callbacks (Seront définis par main.py)
PLAY_ALARM_CALLBACK = None
SET_MUTE_CALLBACK = None
ON_FRAME_CALLBACK = None
ON_GESTURE_CALLBACK = None

# Styles d'alarme (Définis ici pour être accessibles par config et skills)
ALARM_STYLES = {
    "classique": "Bip bip bip ! Bip bip bip ! Il est l'heure.",
    "douceur": "Bonjour, c'est l'heure de se réveiller en douceur. Le soleil se lève.",
    "alerte": "ALERTE ROUGE ! DEBOUT ! ALERTE ROUGE ! DEBOUT !",
    "gong": "GONG" # Mot clé spécial pour son synthétisé
}

# --- GESTION MÉMOIRE ---
def load_memory():
    """Charge la mémoire depuis le fichier JSON ou crée les défauts."""
    default_mem = {
        "agent_name": "Igor", 
        "user_name": "Utilisateur", 
        "facts": [],
        "notebook": [],
        "alarms": [],
        "alarm_sound": None,
        "fav_music_app": None,
        "fav_browser": None,
        "fav_email": None,
        "fav_voip": None,
        "fav_terminal": None,
        "fav_filemanager": None,
        "voice_speed": 1.1,
        "auto_learn": False,
        "pinned": True,
        "muted": False,
        "window_x": None,
        "window_y": None,
        "current_project": None
    }
    if not os.path.exists(MEMORY_FILE): return default_mem
    try:
        with open(MEMORY_FILE, 'r', encoding='utf-8') as f: 
            mem = json.load(f)
            # Fusion avec les clés par défaut manquantes
            for k, v in default_mem.items():
                if k not in mem: mem[k] = v
            return mem
    except: return default_mem

def save_memory(mem_data):
    """Sauvegarde la mémoire dans le fichier JSON."""
    with open(MEMORY_FILE, 'w', encoding='utf-8') as f:
        json.dump(mem_data, f, indent=4, ensure_ascii=False)

# Chargement initial de la mémoire
MEMORY = load_memory()
# Variable globale pratique pour le mode auto-apprentissage
AUTO_LEARN_MODE = MEMORY.get('auto_learn', False)

# --- FONCTIONS UTILITAIRES DE BASE ---

def remove_accents(input_str):
    """Retire les accents pour faciliter la comparaison (ex: 'Vidéo' -> 'video')."""
    if not input_str: return ""
    # Normalisation Unicode pour séparer les caractères de leurs accents
    nfkd_form = unicodedata.normalize('NFKD', str(input_str))
    # On garde uniquement les caractères qui ne sont pas des marques d'accent (Mn)
    return "".join([c for c in nfkd_form if not unicodedata.category(c) == 'Mn']).lower()

def smart_summarize(text, source_name="résultat"):
    """
    Si le texte est trop long, demande à l'IA de le résumer.
    Sinon, renvoie le texte tel quel.
    """
    if not text: return f"Aucun {source_name}."
    
    # Nettoyage préventif des caractères nuls/binaires qui font crasher GTK
    text = str(text).replace('\x00', '').strip()
    
    # Seuil de déclenchement (caractères)
    MAX_CHARS = 600
    
    if len(text) <= MAX_CHARS:
        return text

    print(f"  [SUMMARIZE] Texte trop long ({len(text)} chars). Génération du résumé...", flush=True)
    
    try:
        # On coupe quand même pour ne pas saturer le context window de l'IA (ex: 4000 chars)
        input_text = text[:4000]
        
        prompt = (
            f"Tu es un assistant efficace. Voici le contenu d'un {source_name} "
            f"qui est trop long pour être lu à haute voix.\n\n"
            f"CONTENU BRUT :\n{input_text}\n\n"
            f"TACHE : Fais une synthèse courte (2 phrases maximum) et précise en français. "
            f"Ne dis pas 'Voici le résumé', donne juste les faits."
        )

        # Récupération de la config active (Ollama ou Llama.cpp)
        backend = MEMORY.get('llm_backend', 'llamacpp')
        url = MEMORY.get('llm_api_url', LLM_TEXT_API_URL)
        model_name = MEMORY.get('llm_model_name', 'mistral-small')

        if backend == 'ollama':
            payload = {
                "model": model_name,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "num_predict": 150,
                    "temperature": 0.2,
                    "stop": ["\n\n"]
                }
            }
            res = requests.post(url, json=payload, timeout=20)
            summary = res.json().get('response', '').strip()
        else:
            # Format Llama.cpp Server
            payload = {
                "prompt": prompt,
                "n_predict": 150,
                "temperature": 0.2,
                "stop": ["\n\n"]
            }
            res = requests.post(url, json=payload, timeout=20)
            summary = res.json().get('content', '').strip()
        
        return f"(Résumé auto) : {summary}"

    except Exception as e:
        print(f"  [ERR] Echec résumé : {e}")
        # Fallback : on tronque proprement si l'IA échoue
        return text[:MAX_CHARS] + "... (Texte trop long et IA indisponible)"

def abort_tasks():
    """
    Fonction globale pour vider la file d'attente et tuer la session HTTP active (Vision).
    Utilisée par le bouton STOP et la commande vocale 'Stop'.
    """
    global TASK_QUEUE, ABORT_FLAG, CURRENT_VISION_SESSION
    try:
        ABORT_FLAG = True
        
        # 1. Vidage de la queue
        with TASK_QUEUE.mutex:
            TASK_QUEUE.queue.clear()
            
        # 2. Kill Réseau (Vision)
        if CURRENT_VISION_SESSION:
            print("  [SYSTEM] KILL switch activé sur la Vision.", flush=True)
            try: CURRENT_VISION_SESSION.close()
            except: pass
            CURRENT_VISION_SESSION = None

        print("  [SYSTEM] Tâches annulées.", flush=True)
    except Exception as e:
        print(f"  [ERR] Erreur vidage queue: {e}")