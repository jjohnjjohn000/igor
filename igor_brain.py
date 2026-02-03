# igor_brain.py
import os
import re
import json
import hashlib
import requests
import threading
from functools import lru_cache
import unicodedata
import string
import time
import subprocess
import igor_skills as skills
import igor_config
import igor_globals
from igor_system import INSTALLED_APPS, APP_METADATA

def call_llm_api(prompt, n_predict=150, stop=None, temperature=0.1, grammar=None):
    """
    Fonction unifiée pour appeler le LLM avec gestion de fallback automatique.
    Si le premier modèle échoue, passe au suivant dans la liste configurée.
    """
    if stop is None:
        stop = ["User:", "\n\n"]

    # 1. Construction de la liste des candidats (Config actuelle + Instances enregistrées)
    candidates = []
    
    # A. Configuration actuelle (Prioritaire)
    candidates.append({
        'type': skills.MEMORY.get('llm_backend', 'llamacpp'),
        'url': skills.MEMORY.get('llm_api_url', igor_globals.API_URL),
        'model_name': skills.MEMORY.get('llm_model_name', 'mistral-small'),
        'is_current': True
    })

    # B. Autres instances disponibles (Fallback)
    instances = skills.MEMORY.get('llm_instances', [])
    for inst in instances:
        if inst.get('enabled', True):
            # On évite d'ajouter le doublon de la config actuelle
            if inst.get('url') == candidates[0]['url'] and \
               inst.get('model_name') == candidates[0]['model_name']:
                continue
            inst_copy = inst.copy()
            inst_copy['is_current'] = False
            candidates.append(inst_copy)

    # 2. Boucle de tentative
    for i, candidate in enumerate(candidates):
        backend = candidate.get('type', 'llamacpp')
        url = candidate.get('url')
        model_name = candidate.get('model_name')
        
        # Log discret sauf si switch
        if i > 0:
            print(f"  [LLM] 🔄 Tentative fallback sur : {backend} ({model_name or 'Local'})...", flush=True)

        if backend == 'ollama':
            # Format API Ollama (/api/generate)
            payload = {
                "model": model_name,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "num_predict": n_predict,
                    "temperature": temperature,
                    "stop": stop
                }
            }
        else:
            # Format API Llama.cpp Server (/completion)
            payload = {
                "prompt": prompt,
                "n_predict": n_predict,
                "temperature": temperature,
                "stop": stop
            }
            if grammar:
                payload["grammar"] = grammar

        try:
            # Utilisation de l'URL configurée (Llama.cpp ou Ollama)
            res = requests.post(url, json=payload, timeout=300.0) 
            
            if res.status_code == 200:
                data = res.json()
                
                # SI SUCCESS SUR UN FALLBACK -> MISE À JOUR DE LA CONFIGURATION
                if not candidate.get('is_current'):
                    print(f"  [LLM] ✅ Nouveau modèle actif : {model_name or backend}", flush=True)
                    skills.MEMORY['llm_backend'] = backend
                    skills.MEMORY['llm_api_url'] = url
                    if model_name:
                        skills.MEMORY['llm_model_name'] = model_name
                    skills.save_memory(skills.MEMORY)

                # Normalisation de la réponse
                if backend == 'ollama':
                    return data.get('response', '').strip()
                else:
                    return data.get('content', '').strip()
            else:
                print(f"  [LLM] ⚠️ Erreur {res.status_code} sur {model_name or url}: {res.text}", flush=True)
                # On continue vers le prochain candidat
        except Exception as e:
            print(f"  [LLM] ⚠️ Exception sur {model_name or url}: {e}", flush=True)
            # On continue vers le prochain candidat

    print("  [LLM] ❌ CRITIQUE : Tous les modèles ont échoué.", flush=True)
    return None
    
def check_llama_status():
    """Vérifie si le serveur local llama.cpp répond (Port 8080)."""
    try:
        requests.get("http://localhost:8080/health", timeout=0.2)
        return True
    except:
        return False

def check_ollama_status():
    """Vérifie si Ollama répond (Port 11434)."""
    try:
        requests.get("http://localhost:11434/", timeout=0.2)
        return True
    except:
        return False

def manage_local_server(action):
    """Démarre ou arrête le serveur llama.cpp local."""
    if action == "stop":
        if igor_globals.LLM_SERVER_PROCESS:
            print("  [LLM-SRV] Arrêt du serveur...", flush=True)
            igor_globals.LLM_SERVER_PROCESS.terminate()
            try:
                igor_globals.LLM_SERVER_PROCESS.wait(timeout=2)
            except:
                igor_globals.LLM_SERVER_PROCESS.kill()
            igor_globals.LLM_SERVER_PROCESS = None
        return False

    elif action == "start":
        if igor_globals.LLM_SERVER_PROCESS:
            return True # Déjà lancé

        binary = skills.MEMORY.get('llm_binary_path')
        model = skills.MEMORY.get('llm_gguf_path')
        
        if not binary or not model or not os.path.exists(binary) or not os.path.exists(model):
            print("  [LLM-SRV] Erreur : Chemins binaire ou modèle invalides.", flush=True)
            return False

        try:
            print(f"  [LLM-SRV] Démarrage : {os.path.basename(binary)} sur {os.path.basename(model)}", flush=True)
            
            # Commande de démarrage standard
            cmd = [
                binary,
                "-m", model,
                "-c", "4096",      # Contexte
                "--port", "8080",  # Port
                "-ngl", "99",      # GPU Layers (tout sur GPU si possible)
                "--host", "0.0.0.0"
            ]
            
            # Lancement non-bloquant
            igor_globals.LLM_SERVER_PROCESS = subprocess.Popen(
                cmd, 
                stdout=subprocess.DEVNULL, # On cache les logs pour ne pas polluer
                stderr=subprocess.DEVNULL
            )
            return True
        except Exception as e:
            print(f"  [LLM-SRV] Exception démarrage : {e}", flush=True)
            return False

def check_intent_category(words, actions, objects, category, focus = 0, inquiries = {}, states = {}, points_needed = 1):
    found_actions = words.intersection(actions)
    found_objects = words.intersection(objects)
    found_inquiries = set()
    found_states = set()
    if(inquiries):
        found_inquiries = words.intersection(inquiries)
    if(states):
        found_states = words.intersection(states)
    
    agent_name = set([skills.MEMORY['agent_name'].lower()])

    trust_level = 1.0

    noise_words = words - found_actions - found_objects - found_inquiries - found_states - igor_globals.STOP_WORDS - agent_name

    noise_words = {word for word in noise_words if len(word) >= 3}

    words_without_stop = words - igor_globals.STOP_WORDS - agent_name #Met la sonnerie en mode douceur > met sonnerie mode douceur

    words_diff = words_without_stop - noise_words

    #print(f"  [INTENT CATEGORY] actions: {actions}", flush=True)
    print(f"  [INTENT CATEGORY] {category}\nwords: {words}\nnoise_words: {noise_words}\nfound_actions: {len(found_actions)}\nfound_objects: {len(found_objects)}\nfound_inquiries: {len(found_inquiries)}\nfound_states: {len(found_states)}\nwords diff: {words_diff}", flush=True)

    has_action = bool(found_actions)
    has_object = bool(found_objects)
    has_inquiries = bool(found_inquiries)
    has_states = bool(found_states)
    action_count = len(found_actions)
    object_count = len(found_objects)
    inquiries_count = len(found_inquiries)
    states_count = len(found_states)
    action_points = action_count * 5
    object_points = object_count * 2
    total_points = action_points + object_points
    #inquiries_count =
    #states_count = 

    is_focused_command = len(words_diff) <= focus and (len(found_objects) + len(found_inquiries) + len(found_states)) >= points_needed

    print(f"  [INTENT POINTS] {total_points}", flush=True)
    #print(f"  [INTENT CATEGORY] has action: {has_action}", flush=True)
    #print(f"  [INTENT CATEGORY] is_focused_command: {is_focused_command}", flush=True)

    if has_action:
        total_points += inquiries_count + states_count

    #if total_points >= 7.0:
        #return category

    #if has_action:
        #if not has_object and is_focused_command:
        #    return category
        #if has_object:
        #    return category
    #return
    return total_points
    

@lru_cache(maxsize=256)
def classify_query_intent(user_input):
    """
    Classification ultra-rapide de l'intention avec pré-filtres robustes.
    """
    current_timestamp = time.time()
    print(f"    [TIMESTAMP START] {current_timestamp}",flush=True)

    lower = user_input.lower().strip()
    cleaned = remove_accents_and_special_chars(lower)
    ranking = dict()
    
    # === PRÉ-FILTRE ABSOLU : COMMANDES MULTIPLES ===
    multi_keywords = [" et ", " puis ", " ensuite ", " apres "]
    multi_count = sum(1 for multi in multi_keywords if multi in cleaned)
    
    print(f"   [MULTIACTION] Conjonctions: {multi_count}", flush=True)

    if multi_count > 0:
        # Vérifier qu'il y a bien 2 verbes d'action
        verb_count = sum(1 for verb in igor_globals.MULTI_ACTIONS if verb in cleaned)
        
        print(f"   [MULTIACTION] Actions: {verb_count}", flush=True)

        if verb_count > multi_count:
            print(f"  [CLASSIFY] 🎯 COMMANDES MULTIPLES détectées", flush=True)
            return "CONTROL"  # CONTROL gère le BATCH

    # === PRÉ-FILTRE ===
    split_words = cleaned.split()
    unique_words = set(split_words)
    apps_list = set(INSTALLED_APPS)
    
    # === PRÉ-FILTRE : EXIT ===
    #if (check_intent_category(unique_words, igor_globals.EXIT_ACTIONS, igor_globals.EXIT_OBJECTS, "IDENTITY", 0, set(), set(), 0)):
    #print(f"  [CLASSIFY] PRÉ-FILTRE: IDENTITY (EXIT) - Regex: {lower}", flush=True)
    points = check_intent_category(unique_words, igor_globals.EXIT_ACTIONS, igor_globals.EXIT_OBJECTS, "IDENTITY", 0, set(), set(), 0)
    ranking["IDENTITY"] = points
    print(f"  [CLASSIFY] PRÉ-FILTRE: IDENTITY (EXIT) - Regex: {lower} Points: "+str(points), flush=True)
    
    # === PRÉ-FILTRE : IDENTITY (BASE) ===
    points = check_intent_category(unique_words, igor_globals.BASE_ACTIONS, igor_globals.BASE_OBJECTS, "IDENTITY", 0 , igor_globals.BASE_INQUIRIES)
    if ranking["IDENTITY"] < points:
        ranking["IDENTITY"] = points
    print(f"  [CLASSIFY] PRÉ-FILTRE: IDENTITY (BASE) - Regex: {lower} Points: "+str(points), flush=True)
    
    # === PRÉ-FILTRE : SET FAVORITE ===
    points = check_intent_category(unique_words, igor_globals.SETFAVORITE_ACTIONS, igor_globals.SETFAVORITE_OBJECTS, "CONTROL", 1)
    ranking["CONTROL"] = points
    print(f"  [CLASSIFY] PRÉ-FILTRE: CONTROL (SET_FAVORITE) - Regex: {lower} Points: "+str(points), flush=True)

    # === PRÉ-FILTRE : MEDIA (VOLUME) ===
    points = check_intent_category(unique_words, igor_globals.VOLUME_ACTIONS, igor_globals.VOLUME_OBJECTS, "MEDIA")
    ranking["MEDIA"] = points
    print(f"  [CLASSIFY] ⛔ PRÉ-FILTRE: MEDIA (VOLUME) - Regex: {lower} Points: "+str(points), flush=True)
        
    # === PRÉ-FILTRE : SET_MUTE ===
    points = check_intent_category(unique_words, igor_globals.MUTE_ACTIONS, igor_globals.MUTE_OBJECTS, "MEDIA")
    if ranking["MEDIA"] < points:
        ranking["MEDIA"] = points
    print(f"  [CLASSIFY] ⛔ PRÉ-FILTRE: MEDIA (SET_MUTE) - Regex: {lower} Points: "+str(points), flush=True)
        
    # === PRÉ-FILTRE : MEDIA ===
    points = check_intent_category(unique_words, igor_globals.LISTEN_ACTIONS, igor_globals.LISTEN_OBJECTS, "MEDIA", 0)
    if ranking["MEDIA"] < points:
        ranking["MEDIA"] = points
    print(f"  [CLASSIFY] PRÉ-FILTRE: MEDIA (LISTEN_SYSTEM)- Regex: {lower} Points: "+str(points), flush=True)
    
    # === PRÉ-FILTRE : MEDIA ===
    points = check_intent_category(unique_words, igor_globals.MEDIA_ACTIONS, igor_globals.MEDIA_OBJECTS, "MEDIA", 1)
    if ranking["MEDIA"] < points:
        ranking["MEDIA"] = points
    print(f"  [CLASSIFY] PRÉ-FILTRE: MEDIA - Regex: {lower} Points: "+str(points), flush=True)
    
    # === PRÉ-FILTRE : SYSTEM (FIND) ===
    points = check_intent_category(unique_words, igor_globals.FIND_ACTIONS, igor_globals.FIND_OBJECTS, "SYSTEM", 1)
    ranking["SYSTEM"] = points
    print(f"  [CLASSIFY] PRÉ-FILTRE: SYSTEM (FIND) - Regex: {lower} Points: "+str(points), flush=True)
    
    # === PRÉ-FILTRE : MEMORY ===
    points = check_intent_category(unique_words, igor_globals.NOTES_ACTIONS, igor_globals.NOTES_OBJECTS, "MEMORY")
    ranking["MEMORY"] = points
    print(f"  [CLASSIFY] PRÉ-FILTRE: MEMORY (NOTES)- Regex: {lower} Points: "+str(points), flush=True)
    
    # === PRÉ-FILTRE : LAUNCH (app) ===   
    open_objects = igor_globals.OPEN_OBJECTS | apps_list
    points = check_intent_category(unique_words, igor_globals.OPEN_ACTIONS, open_objects, "LAUNCH", 1)
    ranking["LAUNCH"] = points
    print(f"  [CLASSIFY] PRÉ-FILTRE: LAUNCH (APP)- Regex: {lower} Points: "+str(points), flush=True)
        
    # === PRÉ-FILTRE : LAUNCH (web) ===   
    points = check_intent_category(unique_words, igor_globals.WEB_ACTIONS, igor_globals.WEB_OBJECTS, "LAUNCH")
    if ranking["LAUNCH"] < points:
        ranking["LAUNCH"] = points
    print(f"  [CLASSIFY] PRÉ-FILTRE: LAUNCH (WEB)- Regex: {lower} Points: "+str(points), flush=True)
        
    # === PRÉ-FILTRE : SHELL ===
    points = check_intent_category(unique_words, igor_globals.SHELL_ACTIONS, igor_globals.SHELL_OBJECTS, "SYSTEM", 1)
    if ranking["SYSTEM"] < points:
        ranking["SYSTEM"] = points
    print(f"  [CLASSIFY] PRÉ-FILTRE: SYSTEM (SHELL)- Regex: {lower} Points: "+str(points), flush=True)
        
    # === PRÉ-FILTRE : CONTROL (FULLSCREEN) ===
    fullscreen_objects = igor_globals.FULLSCREEN_OBJECTS | apps_list
    points = check_intent_category(unique_words, igor_globals.FULLSCREEN_ACTIONS, fullscreen_objects, "CONTROL", 0, set(), igor_globals.FULLSCREEN_STATES)
    if ranking["CONTROL"] < points:
        ranking["CONTROL"] = points
    print(f"  [CLASSIFY] PRÉ-FILTRE: CONTROL (FULLSCREEN)- Regex: {lower} Points: "+str(points), flush=True)
        
    # === PRÉ-FILTRE : CONTROL (CLOSE WINDOW)===
    close_object = igor_globals.CLOSE_OBJECTS | apps_list
    points = check_intent_category(unique_words, igor_globals.CLOSE_ACTIONS, close_object, "CONTROL", 0)
    if ranking["CONTROL"] < points:
        ranking["CONTROL"] = points
    print(f"  [CLASSIFY] PRÉ-FILTRE: CONTROL (CLOSE WINDOW)- Regex: {lower} Points: "+str(points), flush=True)
        
    # === PRÉ-FILTRE : CONTROL ===
    points = check_intent_category(unique_words, igor_globals.CONTROL_ACTIONS, igor_globals.CONTROL_OBJECTS, "CONTROL", 0)
    if ranking["CONTROL"] < points:
        ranking["CONTROL"] = points
    print(f"  [CLASSIFY] PRÉ-FILTRE: CONTROL - Regex: {lower} Points: "+str(points), flush=True)
        
    # === PRÉ-FILTRE : CONTROL (WINDOW) ===
    points = check_intent_category(unique_words, igor_globals.WINDOWSTATS_ACTIONS, igor_globals.WINDOWSTATS_OBJECTS,
                               "CONTROL", 1, igor_globals.WINDOWSTATS_INQUIRIES, igor_globals.WINDOWSTATS_STATES)
    if ranking["CONTROL"] < points:
        ranking["CONTROL"] = points
    print(f"  [CLASSIFY] PRÉ-FILTRE: CONTROL (WINDOW)- Regex: {lower} Points: "+str(points), flush=True)
        
    # === PRÉ-FILTRE : CONTROL (FOCUS) ===
    focus_objects = igor_globals.FOCUS_OBJECTS | apps_list
    points = check_intent_category(unique_words, igor_globals.FOCUS_ACTIONS, focus_objects, "CONTROL", 1)
    if ranking["CONTROL"] < points:
        ranking["CONTROL"] = points
    print(f"  [CLASSIFY] PRÉ-FILTRE: CONTROL (FOCUS)- Regex: {lower} Points: "+str(points), flush=True)
        
    # === PRÉ-FILTRE : VISION ===
    points = check_intent_category(unique_words, igor_globals.VISION_ACTIONS, igor_globals.VISION_OBJECTS, "VISION")
    ranking["VISION"] = points
    print(f"  [CLASSIFY] PRÉ-FILTRE: VISION - Regex: {lower} Points: "+str(points), flush=True)
    
    # === PRÉ-FILTRE : SHORTCUT ===
    points = check_intent_category(unique_words, igor_globals.SHORTCUTLIST_ACTIONS, igor_globals.SHORTCUTLIST_OBJECTS, "SHORTCUT", 0)
    ranking["SHORTCUT"] = points
    print(f"  [CLASSIFY] PRÉ-FILTRE: SHORTCUT (SHORTCUT_LIST)- Regex: {lower} Points: "+str(points), flush=True)
    
    # === PRÉ-FILTRE : SHORTCUT ===
    points = check_intent_category(unique_words, igor_globals.SHORTCUT_ACTIONS, igor_globals.SHORTCUT_OBJECTS, "SHORTCUT", 2)
    if ranking["SHORTCUT"] < points:
        ranking["SHORTCUT"] = points
    print(f"  [CLASSIFY] PRÉ-FILTRE: SHORTCUT - Regex: {lower} Points: "+str(points), flush=True)
    
    # === PRÉ-FILTRE : MEMORY ===
    points = check_intent_category(unique_words, igor_globals.READMEM_ACTIONS, igor_globals.READMEM_OBJECTS, "MEMORY")
    if ranking["MEMORY"] < points:
        ranking["MEMORY"] = points
    print(f"  [CLASSIFY] PRÉ-FILTRE: MEMORY (READ_MEM)- Regex: {lower} Points: "+str(points), flush=True)
    
    # === PRÉ-FILTRE : SEARCH (TIME) ===
    points = check_intent_category(unique_words, igor_globals.TIME_ACTIONS, igor_globals.TIME_OBJECTS, "SEARCH", 1, igor_globals.TIME_INQUIRIES)
    ranking["SEARCH"] = points
    print(f"  [CLASSIFY] PRÉ-FILTRE: SEARCH (TIME)- Regex: {lower} Points: "+str(points), flush=True)
    
    # === PRÉ-FILTRE : SEARCH (METEO) ===
    points = check_intent_category(unique_words, igor_globals.METEO_ACTIONS, igor_globals.METEO_OBJECTS, "SEARCH", 1, igor_globals.METEO_INQUIRIES)
    if ranking["SEARCH"] < points:
        ranking["SEARCH"] = points
    print(f"  [CLASSIFY] PRÉ-FILTRE: SEARCH (METEO)- Regex: {lower} Points: "+str(points), flush=True)
    
    # === PRÉ-FILTRE : KNOWLEDGE ===
    points = check_intent_category(unique_words, igor_globals.LEARN_ACTIONS, igor_globals.LEARN_OBJECTS, "KNOWLEDGE", 3)
    ranking["KNOWLEDGE"] = points
    print(f"  [CLASSIFY] PRÉ-FILTRE: KNOWLEDGE (LEARN)- Regex: {lower} Points: "+str(points), flush=True)
    
    # === PRÉ-FILTRE : SEARCH ===
    points = check_intent_category(unique_words, igor_globals.SEARCH_ACTIONS, igor_globals.SEARCH_OBJECTS, "SEARCH", 4)
    if ranking["SEARCH"] < points:
        ranking["SEARCH"] = points
    print(f"  [CLASSIFY] PRÉ-FILTRE: SEARCH - Regex: {lower} Points: "+str(points), flush=True)
    
    # === PRÉ-FILTRE : CONTROL OPEN_FILE ===
    points = check_intent_category(unique_words, igor_globals.OPEN_ACTIONS, igor_globals.OPEN_OBJECTS, "CONTROL", 2)
    if ranking["CONTROL"] < points:
        ranking["CONTROL"] = points
    print(f"  [CLASSIFY] PRÉ-FILTRE: CONTROL (OPEN_FILE) - Regex: {lower} Points: "+str(points), flush=True)
    
    # === PRÉ-FILTRE : MEMORY ===
    points = check_intent_category(unique_words, igor_globals.MEMORY_ACTIONS, igor_globals.MEMORY_OBJECTS, "MEMORY", 10)
    if ranking["MEMORY"] < points:
        ranking["MEMORY"] = points
    print(f"  [CLASSIFY] PRÉ-FILTRE: MEMORY - Regex: {lower} Points: "+str(points), flush=True)
    
    # === PRÉ-FILTRE : LAUNCH ===
    launch_keywords = ["ouvre", "lance", "demarre", "va sur", "open", "start", "affiche", 
                       "mets", "met", "joue", "regarde", "montre"]
    video_keywords = ["video", "youtube", "clip", "film"]
    
    has_launch = any(k in cleaned for k in launch_keywords)
    has_video = any(k in cleaned for k in video_keywords)
    
    if has_launch:
        if not any(calc in cleaned for calc in ["calcul de", "resultat de", "combien"]):
            print(f"  [CLASSIFY] PRÉ-FILTRE: LAUNCH", flush=True)
            if ranking["LAUNCH"] < 7:
                ranking["LAUNCH"] = 7
    
    if has_video:
        print(f"  [CLASSIFY] PRÉ-FILTRE: LAUNCH (VIDEO)", flush=True)
        if ranking["LAUNCH"] < 7:
            ranking["LAUNCH"] = 7
    
    # === PRÉ-FILTRE : MATH (calculs) ===
    has_operators = any(op in user_input for op in ['+', '-', '*', '/', '=', '^'])
    has_calc_words = any(w in cleaned for w in ["calcule", "combien fait", "résultat de"])
    is_app_launch = any(f"{launch} " in cleaned for launch in ["lance", "ouvre", "démarre"])
    
    if (has_operators or has_calc_words) and not is_app_launch:
        print(f"  [CLASSIFY] PRÉ-FILTRE: KNOWLEDGE (MATH)", flush=True)
        if ranking["KNOWLEDGE"] < 7:
            ranking["KNOWLEDGE"] = 7
    
    current_timestamp = time.time() - current_timestamp
    print(f"    [TIMESTAMP END] {current_timestamp*0.0001}ms",flush=True)

    # === PRÉ-FILTRE : PROJECT ===
    if any(k in cleaned for k in ["projet", "code", "fichier", "todo", "sauve"]):
        print(f"  [CLASSIFY] PRÉ-FILTRE: PROJECT", flush=True)
        ranking["PROJECT"] = 7
    
    # === PRÉ-FILTRE : IDENTITY (noms) ===
    # On rend la détection plus agressive pour capturer les affirmations "Tu es..."
    identity_keywords = ["appelle", "nom", "prénom", "suis", "es tu", "tu es", "t'es", "qui es", "qui suis"]
    if any(w in cleaned for w in identity_keywords):
        # On vérifie qu'on parle bien de personnes (je/tu/mon/ton)
        if any(pron in cleaned for pron in ["je", "j'", "mon", "ma", "mes", "tu", "te", "ton", "ta", "tes", "moi", "toi", "t'", "m'"]):
            print(f"  [CLASSIFY] PRÉ-FILTRE: IDENTITY", flush=True)
            if ranking["IDENTITY"] < 7:
                ranking["IDENTITY"] = 7

    # === PRÉ-FILTRE : ALARM ===
    # AJOUT des versions sans accents (reveil, reveille)
    alarm_keywords = ["alarme", "reveil", "reveille", "sonnerie", "debout"]
    if any(k in cleaned for k in alarm_keywords):
        # Si on détecte une notion de temps (chiffres, "dans", "à", "h", "min")
        # ET qu'on ne parle pas de configuration ("change", "style", "son")
        time_triggers = ["dans", "à", "pour", "minutes", "heures", "h", "min", "sec"]
        has_time = any(t in cleaned for t in time_triggers) or any(char.isdigit() for char in cleaned)
        
        is_config = any(s in cleaned for s in ["change", "règle", "définit", "style", "type", "bruit", "son"])
        
        if has_time and not is_config:
            print(f"  [QUICK] ⏰ Pose d'alarme détectée -> ALARM: '{user_input}'", flush=True)
            return ("ALARM", user_input)

        print(f"  [CLASSIFY] PRÉ-FILTRE: ALARM (Intent)", flush=True)
        return "ALARM"
        
    sorted_ranking = dict(sorted(ranking.items(), key=lambda item: item[1], reverse=True))

    print(f"    [DEBUG RANKING] {sorted_ranking}",flush=True)

    value = list(sorted_ranking.values())[0]
    key = list(sorted_ranking.keys())[0]
    if value >= 7:
        print(f"CATEGORIE: {key} VALEUR: {value}",flush=True)
        return key

    # === APPEL IA (Seulement si aucun pré-filtre) ===
    prompt = f"""Classifie en 1 MOT parmi: IDENTITY MEDIA SHORTCUT VISION PROJECT ALARM SEARCH KNOWLEDGE LAUNCH CONTROL MEMORY CHAT

Phrase: "{user_input}"
Réponse (1 mot uniquement):"""
    
    #prompt = f"""Répond à QUOI? 

#Phrase: "{user_input}"
#Réponse (1 mot uniquement):"""

    # Appel unifié
    raw_intent = call_llm_api(prompt, n_predict=10, temperature=0.0)
    
    if not raw_intent:
         print(f"  [CLASSIFY] API Error ou Vide → CHAT", flush=True)
         return "CHAT"
    
    raw_intent = raw_intent.strip().upper()
        
    print(f" [RAW INTENT] {raw_intent}",flush=True)

    if not raw_intent:
        print(f"  [CLASSIFY] Réponse vide → CHAT", flush=True)
        return "CHAT"
    
    intent = re.sub(r'[^A-Z]', '', raw_intent)
    
    valid_intents = {
        "IDENTITY", "MEDIA", "SHORTCUT", "VISION", "PROJECT", 
        "ALARM", "SEARCH", "KNOWLEDGE", "LAUNCH", "CONTROL", 
        "MEMORY", "CHAT"
    }
    
    if intent not in valid_intents:
        print(f"  [CLASSIFY] Intent IA inconnu '{intent}' (raw:'{raw_intent}') → CHAT", flush=True)
        return "CHAT"
    
    print(f"  [CLASSIFY] Intent IA: {intent}", flush=True)
    return intent

def remove_accents_and_special_chars(input_str):
    # Normalize to NFD (Normalization Form D) - decomposes accents into base characters and combining marks
    nfkd_form = unicodedata.normalize('NFD', input_str)
    
    # Filter out combining characters (Unicode category 'Mn' is for Mark, Nonspacing)
    # and keep only printable ASCII characters (optional, to remove other special symbols)
    cleaned_chars = [c for c in nfkd_form if not unicodedata.combining(c) and c in string.printable]
    
    # Join the characters back into a string
    cleaned_str = ''.join(cleaned_chars)
    
    # Optional: use regex to keep only alphanumeric characters and spaces
    import re
    cleaned_str = re.sub(r'[^a-zA-Z0-9\s]', '', cleaned_str)
    
    return cleaned_str

def extract_json_from_response(raw_text):
    """
    Extrait le JSON d'une réponse LLM même si elle contient du texte parasite.
    Essaie plusieurs stratégies dans l'ordre.
    VERSION CORRIGÉE : Prend le PREMIER objet JSON valide trouvé.
    """
    import re
    
    # 🆕 STRATÉGIE 0 : Extraction ultra-précoce (avant même les regex)
    # Si le texte commence directement par { ou [, on essaie de parser jusqu'au premier objet/array complet
    if raw_text.strip().startswith('{'):
        # On cherche l'accolade fermante qui correspond
        brace_count = 0
        for i, char in enumerate(raw_text):
            if char == '{': brace_count += 1
            elif char == '}': brace_count -= 1
            
            if brace_count == 0 and i > 0:
                # On a trouvé la fin du premier objet
                candidate = raw_text[:i+1]
                try:
                    parsed = json.loads(candidate)
                    # ✅ VALIDATION : Doit avoir "tool"
                    if isinstance(parsed, dict) and 'tool' in parsed:
                        print(f"  [JSON-EXTRACT] ✅ Stratégie 0 (Early-cut) : {candidate[:80]}...", flush=True)
                        return parsed, None
                except:
                    pass
                break
    
    elif raw_text.strip().startswith('['):
        # Même logique pour les tableaux
        bracket_count = 0
        for i, char in enumerate(raw_text):
            if char == '[': bracket_count += 1
            elif char == ']': bracket_count -= 1
            
            if bracket_count == 0 and i > 0:
                candidate = raw_text[:i+1]
                try:
                    parsed = json.loads(candidate)
                    # ✅ VALIDATION : Doit être une liste de dicts avec "tool"
                    if isinstance(parsed, list) and all(isinstance(x, dict) and 'tool' in x for x in parsed):
                        print(f"  [JSON-EXTRACT] ✅ Stratégie 0 (Early-cut) : {candidate[:80]}...", flush=True)
                        return parsed, None
                except:
                    pass
                break
    
    # Stratégie 1 : Regex pour trouver {...} ou [...]
    json_patterns = [
        r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}',  # Objet JSON
        r'\[[^\[\]]*(?:\[[^\[\]]*\][^\[\]]*)*\]'  # Liste JSON
    ]
    
    for pattern in json_patterns:
        matches = re.findall(pattern, raw_text, re.DOTALL)
        if matches:
            # 🆕 MODIFICATION : Au lieu de prendre le plus long, on essaie TOUS les matches
            # et on prend le PREMIER qui est valide et contient "tool"
            for candidate in matches:
                try:
                    parsed = json.loads(candidate)
                    
                    # Validation : Doit avoir "tool" (dict) ou être une liste de dicts avec "tool"
                    if isinstance(parsed, dict) and 'tool' in parsed:
                        print(f"  [JSON-EXTRACT] ✅ Stratégie 1 (Regex) : {candidate[:80]}...", flush=True)
                        return parsed, None
                    elif isinstance(parsed, list) and all(isinstance(x, dict) and 'tool' in x for x in parsed):
                        print(f"  [JSON-EXTRACT] ✅ Stratégie 1 (Regex-List) : {candidate[:80]}...", flush=True)
                        return parsed, None
                except:
                    continue
    
    # Stratégie 2 : Cherche entre marqueurs ```json ou ```
    code_block = re.search(r'```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```', raw_text, re.DOTALL)
    if code_block:
        try:
            parsed = json.loads(code_block.group(1))
            if isinstance(parsed, dict) and 'tool' in parsed:
                print(f"  [JSON-EXTRACT] ✅ Stratégie 2 (Code block)", flush=True)
                return parsed, None
            elif isinstance(parsed, list):
                print(f"  [JSON-EXTRACT] ✅ Stratégie 2 (Code block list)", flush=True)
                return parsed, None
        except:
            pass
    
    # Stratégie 3 : Split sur newlines et garde la ligne la plus longue qui parse
    for line in raw_text.split('\n'):
        line = line.strip()
        if (line.startswith('{') or line.startswith('[')) and len(line) > 10:
            try:
                parsed = json.loads(line)
                if isinstance(parsed, dict) and 'tool' in parsed:
                    print(f"  [JSON-EXTRACT] ✅ Stratégie 3 (Line) : {line[:80]}...", flush=True)
                    return parsed, None
                elif isinstance(parsed, list):
                    print(f"  [JSON-EXTRACT] ✅ Stratégie 3 (Line list)", flush=True)
                    return parsed, None
            except:
                continue
    
    # 🆕 Stratégie 4 : Nettoyage de la flèche (→) et tout ce qui suit
    if '→' in raw_text:
        clean_text = raw_text.split('→')[0].strip()
        print(f"  [JSON-EXTRACT] 🧹 Nettoyage flèche détecté. Avant: {len(raw_text)} chars, Après: {len(clean_text)} chars", flush=True)
        try:
            parsed = json.loads(clean_text)
            if isinstance(parsed, dict) and 'tool' in parsed:
                print(f"  [JSON-EXTRACT] ✅ Stratégie 4 (Arrow-clean)", flush=True)
                return parsed, None
        except:
            pass
    
    return None, f"Aucun JSON valide trouvé dans : {raw_text[:200]}"

def fallback_intent_detection(user_input):
    """
    Si le parsing JSON échoue totalement, on devine l'intention par mots-clés.
    Version simplifiée mais robuste.
    """
    lower = user_input.lower().strip()
    
    # Dictionnaire de patterns simples
    fallback_map = {
        # Format: (mots_clés, tool, args_extractor)
        ("quelle heure", "heure", "time"): ("TIME", ""),
        ("météo", "temps qu'il fait"): ("WEATHER", ""),
        ("cherche", "recherche", "google"): ("SEARCH", user_input),
        ("ouvre", "lance", "démarre"): ("LAUNCH", user_input),
        ("calcul", "combien", "résultat"): ("MATH", user_input),
        ("note", "retiens"): ("NOTE", user_input),
        ("alarme", "réveille"): ("ALARM", user_input),
    }
    
    for keywords, action in fallback_map.items():
        if any(kw in lower for kw in keywords):
            tool, args = action if isinstance(action, tuple) else (action, user_input)
            print(f"  [FALLBACK] Détection par mots-clés : {tool}", flush=True)
            return tool, args
    
    # Si vraiment rien ne matche
    return "CHAT", f"Je n'ai pas compris '{user_input}'. Peux-tu reformuler avec d'autres mots ?"

def force_batch_if_needed(user_input, parsed_result):
    """
    Vérifie si la réponse de l'IA devrait être un BATCH mais ne l'est pas.
    Corrige automatiquement si besoin avec héritage du verbe précédent.
    VERSION CORRIGÉE : Nettoyage "propre" via Regex (ne casse pas "calcu-la-trice").
    """
    lower = user_input.lower()
    
    # Détection des mots de liaison
    multi_keywords = [" et ", " puis ", " ensuite ", " après "]
    has_multi = any(kw in lower for kw in multi_keywords)
    
    if not has_multi:
        return parsed_result
    
    # Si c'est déjà un tableau, OK
    if isinstance(parsed_result, list):
        return parsed_result
    
    # Si c'est un dict simple, on tente la réparation
    if isinstance(parsed_result, dict):
        # Liste des verbes d'action
        action_map = {
            "ferme": "CLOSE_WINDOW", "éteins": "CLOSE_WINDOW", "arrête": "CLOSE_WINDOW",
            "ouvre": "LAUNCH", "lance": "LAUNCH", "démarre": "LAUNCH", "mets": "LAUNCH", "met": "LAUNCH"
        }
        
        # On vérifie si au moins un verbe est présent
        if any(v in lower for v in action_map):
            print(f"  [BATCH-FIX] ⚠️ Tentative de découpage intelligent...", flush=True)
            
            # Découpage par "et", "puis", etc.
            parts = re.split(r'\s+(?:et|puis|ensuite|après)\s+', lower)
            
            if len(parts) >= 2:
                actions = []
                last_tool = None 
                
                for part in parts:
                    part = part.strip()
                    current_tool = None
                    
                    # 1. On cherche un verbe explicite dans ce segment
                    found_verb = False
                    for verb, tool in action_map.items():
                        if verb in part:
                            current_tool = tool
                            last_tool = tool 
                            # NETTOYAGE SÉCURISÉ DU VERBE (1ère occurrence seulement)
                            part = part.replace(verb, "", 1).strip()
                            found_verb = True
                            break
                    
                    # 2. Si pas de verbe, on utilise le dernier outil connu (Héritage)
                    if not found_verb and last_tool:
                        current_tool = last_tool

                    # 3. NETTOYAGE DES DETERMINANTS (Regex pour mots entiers uniquement)
                    # \b = limite de mot. Évite de supprimer "la" dans "calculatrice"
                    if current_tool:
                        part = re.sub(r'\b(le|la|les|un|une|du|de)\b', '', part).strip()
                        part = part.replace("l'", "").replace("d'", "").strip()
                        
                        # Si on a trouvé un outil et qu'il reste du texte
                        if len(part) > 1:
                            actions.append({"tool": current_tool, "args": part})
                
                # Si on a réussi à extraire au moins 2 actions valides
                if len(actions) >= 2:
                    print(f"  [BATCH-FIX] ✅ Réparation réussie (Regex) : {actions}", flush=True)
                    return actions
    
    return parsed_result

def brain_query(user_input):
    print(f"\n{'='*20} [DEBUG] ENTRÉE CHAT {'='*20}\n>>> {user_input}\n{'='*55}", flush=True) # <--- LOG AJOUTÉ

    """
    Cerveau de l'agent - Version 2.0 avec Classification + Micro-Prompts.
    
    Pipeline:
    1. Pré-filtre heuristique (< 1ms)
    2. Classification intention (< 2s)
    3. Prompt contextuel minimal (< 3s)
    
    Total: ~5s max au lieu de 8-10s avec le gros prompt.
    """
    # === ÉTAPE 0 : PRÉ-FILTRE HEURISTIQUE ===
    #quick_result = quick_heuristic_check(user_input)
    #if quick_result:
    #    return quick_result
    
    # === ÉTAPE 1 : CLASSIFICATION ===
    intent = classify_query_intent(user_input)
    
    # === ÉTAPE 2 : CONSTRUCTION CONTEXTE MINIMAL ===
    current_name = skills.MEMORY['agent_name']
    user_name = skills.MEMORY['user_name']
    current_proj = skills.MEMORY.get('current_project', 'Aucun')
    
    context_parts = []
    context_parts.append(f"Agent: {current_name}, User: {user_name}")
    
    # Historique (seulement pour CHAT)
    if intent == "CHAT":
        last_msgs = igor_globals.CHAT_HISTORY[-2:] if igor_globals.CHAT_HISTORY else []
        if last_msgs:
            context_parts.append(f"Historique: {' | '.join(last_msgs)}")
    
    # Contexte spécifique selon l'intent
    local_files_str = ""
    facts_str = ""
    
    if intent == "PROJECT":
        context_parts.append(f"Projet actif: {current_proj}")
    
    if intent == "KNOWLEDGE":
        try:
            files = glob.glob(os.path.join(skills.KNOWLEDGE_DIR, "*.txt"))
            local_files = [os.path.basename(f).replace(".txt", "").replace("_", " ") for f in files[:8]]
            local_files_str = ", ".join(local_files) if local_files else "Aucun"
        except:
            local_files_str = "Aucun"
    
    if intent in ["MEMORY", "CHAT"]:
        facts = skills.MEMORY.get('facts', [])
        if facts:
            facts_str = "; ".join(facts[:3])
    
    context_str = "\n".join(context_parts)
    
    # === ÉTAPE 3 : RÉCUPÉRATION MICRO-PROMPT ===
    micro_prompt_template = igor_globals.MICRO_PROMPTS.get(intent, igor_globals.MICRO_PROMPTS["CHAT"])
    
    # === INJECTION VARIABLES DYNAMIQUES ===
    # On prépare TOUTES les variables possibles pour éviter les KeyError
    format_vars = {
        'current_proj': current_proj,
        'local_files': local_files_str if local_files_str else "Aucun",
        'facts': facts_str if facts_str else "Aucun"
    }
    
    # Méthode sécurisée : on remplace chaque {var} individuellement
    micro_prompt = micro_prompt_template
    for key, value in format_vars.items():
        placeholder = "{" + key + "}"
        if placeholder in micro_prompt:
            micro_prompt = micro_prompt.replace(placeholder, str(value))
    
    # === ÉTAPE 4 : SÉLECTION OUTILS (Charge SEULEMENT le groupe concerné) ===
    relevant_groups = igor_globals.INTENT_TOOL_GROUPS.get(intent, ["BASE"])
    
    print(f"\n{'='*20} [DEBUG] RAISON MICROPROMPT {'='*16}\nINTENTION DÉTECTÉE : {intent}\nGROUPES ASSOCIÉS   : {relevant_groups}\nRAISON             : L'intention '{intent}' force l'inclusion des outils {relevant_groups} et du guide spécifique '{intent}'.\n{'='*58}", flush=True) # <--- LOG AJOUTÉ

    tools_list = []
    
    for group_name in relevant_groups:
        if group_name in igor_globals.TOOLS_GROUPS:
            tools_list.extend(igor_globals.TOOLS_GROUPS[group_name])
    
    # Dédoublonnage
    tools_list = list(set(tools_list))
    tools_str = "\n".join([f"- {t}" for t in tools_list])
    
    # === ÉTAPE 5 : PROMPT FINAL (COURT ET CIBLÉ) ===
    prompt = f"""Tu es {current_name} (l'agent IA). Tu DOIS répondre UNIQUEMENT avec du JSON valide.

CONTEXTE:
{context_str}

GUIDE SPÉCIFIQUE:
{micro_prompt}

OUTILS:
{tools_str}

RÈGLES ABSOLUES:
1. UNE action : {{"tool": "NOM", "args": "valeur"}}
2. PLUSIEURS actions (si "et", "puis") : [{{"tool":...}}, {{"tool":...}}]
3. PAS de texte avant/après le JSON
4. PAS de markdown (```json)
5. Si doute → utilise CHAT

EXEMPLES:
User: "Quelle heure ?" → {{"tool": "TIME", "args": ""}}
User: "Ferme Chrome et ouvre Firefox" → [{{"tool": "CLOSE_WINDOW", "args": "Chrome"}}, {{"tool": "LAUNCH", "args": "Firefox"}}]
User: "Cherche Python" → {{"tool": "SEARCH", "args": "Python"}}

User: "{user_input}"
JSON:"""
    
    # === ÉTAPE 6 : APPEL API ===
    grammar_json = "root ::= object | list\nobject ::= \"{\" pair (\",\" pair)* \"}\"\npair ::= string \":\" value\nstring ::= '\"' [^\"]* '\"'\nvalue ::= string | number | object | list\nlist ::= \"[\" (object (\",\" object)*)? \"]\"\nnumber ::= [0-9]+"
    
    # Appel via la nouvelle fonction unifiée
    raw = call_llm_api(prompt, n_predict=300, temperature=0.1, grammar=grammar_json)

    try:
        if not raw:
             return fallback_intent_detection(user_input)
        
        print(f"\n{'='*20} [DEBUG] SORTIE AGENT (BRUT) {'='*15}\n{raw}", flush=True) # <--- LOG AJOUTÉ

        parsed, error = extract_json_from_response(raw)
        
        if parsed: # <--- LOG AJOUTÉ
             print(f"{'-'*20} [DEBUG] JSON PARSÉ {'-'*20}\n{json.dumps(parsed, indent=2, ensure_ascii=False)}\n{'='*58}", flush=True)

        if error:
            print(f"  [BRAIN] Échec parsing : {error}", flush=True)
            return fallback_intent_detection(user_input)
        
        # === FILTRE ANTI-HALLUCINATION SPÉCIAL MÉTÉO ===
        # C'est ici qu'on empêche l'IA d'inventer "Paris"
        if isinstance(parsed, dict) and parsed.get('tool') == 'WEATHER':
            arg_city = str(parsed.get('args', '')).strip()
            
            # Si l'IA propose une ville, mais que cette ville n'est PAS dans ce que vous avez dit
            # (Comparaison souple : on vérifie si le mot clé est dans l'input utilisateur)
            if arg_city and len(arg_city) > 2:
                # Nettoyage pour comparaison (minuscules, sans accents)
                # CORRECTION : Utilisation de igor_config au lieu de skills
                user_clean = igor_config.remove_accents(user_input.lower())
                city_clean = igor_config.remove_accents(arg_city.lower())
                
                # Si la ville inventée (ex: "paris") n'est pas dans la phrase user (ex: "quel temps fait-il ?")
                if city_clean not in user_clean:
                    print(f"  [ANTI-HALLUCINATION] Suppression de la ville inventée : '{arg_city}'", flush=True)
                    parsed['args'] = "" # On force l'auto-détection

        # === VALIDATION FINALE ===
        # Si c'est une liste (BATCH)
        if isinstance(parsed, list):
            # Vérification que tous les éléments sont des dicts avec "tool"
            for item in parsed:
                if not isinstance(item, dict) or 'tool' not in item:
                    print(f"  [BRAIN] Item BATCH invalide: {item}", flush=True)
                    return fallback_intent_detection(user_input)
            
            print(f"  [BRAIN] ✅ BATCH détecté ({len(parsed)} actions)", flush=True)
            return "BATCH", parsed
        
        # Si c'est un dict simple
        elif isinstance(parsed, dict) and 'tool' in parsed:
            parsed = force_batch_if_needed(user_input, parsed)

            # Re-vérification après correction
            if isinstance(parsed, list):
                print(f"  [BRAIN] ✅ BATCH corrigé automatiquement ({len(parsed)} actions)", flush=True)
                return "BATCH", parsed

            tool_name = str(parsed['tool'])
            args_val = str(parsed.get('args', ''))
            
            print(f"  [BRAIN] ✅ Action unique: {tool_name}", flush=True)
            return tool_name, args_val
        
        else:
            print(f"  [BRAIN] Format JSON invalide: {type(parsed)}", flush=True)
            return fallback_intent_detection(user_input)
    
    except Exception as e:
        print(f"  [BRAIN] Exception : {e}")
        return "CHAT", "Je bugue un peu là."

def log_query_stats(source, intent=None):
    """Log les statistiques d'utilisation (pour optimisation future)"""
    igor_globals.STATS[source] += 1
    if intent:
        igor_globals.STATS["intents"][intent] = igor_globals.STATS["intents"].get(intent, 0) + 1

def print_stats():
    """Affiche les stats de performance (appeler en debug)"""
    total = sum([igor_globals.STATS["heuristic_hits"], igor_globals.STATS["cache_hits"], igor_globals.STATS["ai_calls"]])
    if total == 0:
        return
    
    print("\n=== STATISTIQUES BRAIN ===")
    print(f"Heuristiques: {igor_globals.STATS['heuristic_hits']} ({igor_globals.STATS['heuristic_hits']/total*100:.1f}%)")
    print(f"Cache: {igor_globals.STATS['cache_hits']} ({igor_globals.STATS['cache_hits']/total*100:.1f}%)")
    print(f"Appels IA: {igor_globals.STATS['ai_calls']} ({igor_globals.STATS['ai_calls']/total*100:.1f}%)")
    print(f"\nIntents détectés: {igor_globals.STATS['intents']}")
    print("==========================\n")

def quick_heuristic_check(user_input):
    """
    PRÉ-filtre AVANT brain_query pour les cas ULTRA évidents.
    PRIORITÉ ABSOLUE : Distinction Projets Igor vs Fichiers Système.
    """
    log_query_stats("heuristic_hits")
    
    lower = user_input.lower().strip()
    
    # === PRIORITÉ 0 : PROJETS IGOR (AVANT TOUT) ===
    # Mots-clés qui indiquent clairement qu'on parle d'un PROJET Igor
    project_keywords = [
        "projet", "project", "siteweb", "code", "todo", 
        "sauvegarde", "fichier du projet", "dans le projet",
        "mon projet", "le projet", "projet actif"
    ]
    
    # ✅ NOUVEAU : Détection STRICTE du contexte projet
    has_project_context = any(kw in lower for kw in project_keywords)
    
    # Si on mentionne explicitement un projet, on NE FAIT PAS d'heuristique
    if has_project_context:
        print(f"  [QUICK] 🎯 Contexte PROJET détecté → Laisse l'IA gérer", flush=True)
        return None  # Laisse l'IA décider (elle a le contexte des projets)
    
    # Détection explicite de la demande de vitesse pour la vision
    if ("vite" in lower or "rapide" in lower) and ("regarde" in lower or "vision" in lower):
            if "écran" in lower or "screen" in lower:
                print(f"  [QUICK] Vision Rapide (Écran) détectée", flush=True)
                return ("VISION", "vite screen")
            elif "photo" in lower or "webcam" in lower:
                 print(f"  [QUICK] Vision Rapide (Webcam) détectée", flush=True)
                 return ("VISION", "vite webcam")

    # === PRIORITÉ 0.5 : VISION (QUESTIONS CONTEXTUELLES) ===
    # Capture "Qu'est-ce que tu vois ?", "Que vois-tu ?", "Décris ce que tu vois"
    # Placez ceci AVANT les fichiers pour éviter que "Regarde..." ne soit pris pour OpenFile
    if "vois" in lower or "regarde" in lower:
         # Si combiné avec "tu", "ce que", "qu'est-ce", "que" (Question visuelle)
         if any(w in lower for w in ["tu", "ce que", "qu'est-ce", "que", "ton", "tes"]):
             # Exclusion des fichiers explicites pour éviter "Regarde le fichier X"
             if not any(w in lower for w in ["fichier", "dossier", "document", "projet"]):
                 print(f"  [QUICK] 👁️ Vision contextuelle détectée -> VISION", flush=True)
                 # On passe l'input entier pour que tool_vision_look détecte "moi"/"webcam" ou "écran"
                 return ("VISION", user_input)

    # === PRIORITÉ 1 : RECHERCHE DE FICHIERS (FIND) ===
    # Verbes de recherche (PAS d'ouverture)
    search_verbs = ["trouve", "cherche", "où est", "localise", "locate", "find"]
    
    has_search_verb = any(lower.startswith(v) or f" {v} " in lower for v in search_verbs)
    
    if has_search_verb:
        # Extensions pour détecter qu'on parle d'un fichier
        file_extensions = [".pdf", ".jpg", ".jpeg", ".png", ".doc", ".txt", ".html", 
                          ".py", ".js", ".css", ".mp4", ".zip"]
        
        has_extension = any(ext in lower for ext in file_extensions)
        mentions_file = "fichier" in lower or "file" in lower
        
        # Si on cherche clairement un fichier
        if has_extension or mentions_file:
            # Extraction du nom de fichier
            clean_query = lower
            for verb in search_verbs:
                clean_query = clean_query.replace(verb, "", 1).strip()
            
            # Nettoyage articles
            for article in ["le ", "la ", "les ", "un ", "une ", "mon ", "ma ", "mes "]:
                if clean_query.startswith(article):
                    clean_query = clean_query[len(article):].strip()
            
            # Nettoyage mots parasites
            for noise in ["fichier ", "file ", "nommé ", "appelé "]:
                clean_query = clean_query.replace(noise, "").strip()
            
            print(f"  [QUICK] 🔍 RECHERCHE FICHIER détectée: '{clean_query}'", flush=True)
            return ("FIND", clean_query)

    # === PRIORITÉ 1.5 : LECTURE DE NOTE (INTERCEPTION CRITIQUE) ===
    # Empêche OPEN_FILE de voler "montre la note" en pensant que c'est un fichier
    if "note" in lower:
        note_verbs = ["montre", "lis", "voir", "affiche", "donne", "quelle est"]
        has_note_verb = any(v in lower for v in note_verbs)
        
        # Si on demande de lire une note ET qu'il n'y a pas d'extension (.txt) explicite
        if has_note_verb and not any(ext in lower for ext in [".txt", ".md", ".pdf", ".doc"]):
            print(f"  [QUICK] 📝 LECTURE NOTE détectée (Prioritaire): '{user_input}'", flush=True)
            return ("READ_NOTE", user_input)
    
    # === PRIORITÉ 2 : OUVERTURE DE FICHIERS (OPEN_FILE) ===
    # Verbes d'ouverture
    # AJOUT : "lance", "démarre" pour gérer "lance le fichier X"
    open_verbs = ["ouvre", "affiche", "montre", "lis", "regarde", "open", "show", "voir", "lance", "démarre"]
    
    has_open_verb = any(lower.startswith(v) or f" {v} " in lower for v in open_verbs)
    
    if has_open_verb:
        # Mots-clés qui indiquent qu'on parle d'un DOSSIER SYSTÈME
        system_folders = ["documents", "downloads", "téléchargements", "bureau", 
                         "desktop", "images", "photos", "pictures", "vidéos", 
                         "videos", "musique", "music"]
        
        # Extensions de fichiers courantes
        file_extensions = [".pdf", ".jpg", ".jpeg", ".png", ".gif", ".mp4", ".avi", 
                          ".mkv", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".txt", 
                          ".zip", ".rar", ".odt", ".ods", ".csv"]

        # AJOUT : Mots-clés explicites de type fichier
        explicit_file_types = ["document", "fichier", "file", "image", "photo", "dessin", 
                              "scan", "feuille", "pdf", "texte", "note"]
        
        has_extension = any(ext in lower for ext in file_extensions)
        has_system_folder = any(folder in lower for folder in system_folders)
        has_explicit_type = any(ft in lower for ft in explicit_file_types)
        
        # Vérifie si c'est probablement un fichier de projet (pour éviter les conflits)
        current_project = skills.MEMORY.get('current_project')
        likely_project_file = (
            current_project 
            and has_extension 
            and not has_system_folder
        )
        
        # CONDITION ÉLARGIE : Si dossier système OU extension OU mot clé "document/fichier"
        if has_extension or has_system_folder or has_explicit_type:
            clean_query = lower
            
            # On retire le verbe
            for verb in open_verbs:
                clean_query = clean_query.replace(verb, "", 1).strip()
            
            # On retire les déterminants
            for article in ["le ", "la ", "les ", "un ", "une ", "mon ", "ma ", "mes ", "ton ", "ta ", "tes ", "ce ", "cet ", "cette "]:
                if clean_query.startswith(article):
                    clean_query = clean_query[len(article):].strip()
            
            # Note : On laisse le mot "document" ou "stickman" ici, 
            # car tool_open_file (dans igor_system) fera son propre nettoyage final.
            
            print(f"  [QUICK] 🎯 FICHIER détecté (Heuristique): '{clean_query}' -> OPEN_FILE", flush=True)
            return ("OPEN_FILE", clean_query)
        
        # Cas ambigu : projet actif
        elif likely_project_file:
            print(f"  [QUICK] ⚠️ Ambiguïté (Projet: {current_project}) → IA", flush=True)
            return None
    
    # === PRIORITÉ IDENTITÉ : QUESTIONS (CHAT) ===
    # On gère ici les demandes d'information (ex: "C'est quoi ton nom", "Ton nom est ?")
    
    # 1. QUESTION SUR L'USER ("C'est quoi mon nom ?", "Mon nom est ?")
    regex_question_user = re.compile(
        r"^(?:quel\s+est|c'est\s+quoi|dis(?:[-\s]moi)?|donne(?:[-\s]moi)?|rappell?e(?:[-\s]moi)?)\s+mon\s+(?:nom|prénom)|"
        r"comment\s+je\s+m['\s]appelle|"
        r"qui\s+suis[-\s]je|"
        r"^mon\s+(?:pre)?nom\s+est\s*[?]?$", # Capture "Mon nom est ?" (vide après)
        re.IGNORECASE
    )
    if regex_question_user.search(lower):
        user_n = skills.MEMORY.get('user_name', 'Utilisateur')
        print(f"  [QUICK] ❓ Question identité user -> CHAT", flush=True)
        return ("CHAT", f"Tu t'appelles {user_n}.")

    # 2. QUESTION SUR L'AGENT ("C'est quoi ton nom ?", "Ton nom est ?")
    regex_question_agent = re.compile(
        r"^(?:quel\s+est|c'est\s+quoi|dis(?:[-\s]moi)?|donne(?:[-\s]moi)?)\s+ton\s+(?:nom|prénom)|"
        r"comment\s+tu\s+t['\s]appelles?|"
        r"qui\s+es[-\s]tu|t'es\s+qui|"
        r"^ton\s+(?:pre)?nom\s+est\s*[?]?$", # Capture "Ton nom est ?" (vide après)
        re.IGNORECASE
    )
    if regex_question_agent.search(lower):
        agent_n = skills.MEMORY.get('agent_name', 'Igor')
        print(f"  [QUICK] ❓ Question identité agent -> CHAT", flush=True)
        return ("CHAT", f"Je m'appelle {agent_n}.")

    # === PRIORITÉ IDENTITÉ : COMMANDES (CHANGEMENT DE NOM) ===
    
    # 3. CHANGER LE NOM DE L'UTILISATEUR ("Je m'appelle Jambon", "Mon nom est Jambon")
    # Regex stricte : Doit commencer par JE/MON et avoir du contenu après.
    regex_set_username = re.compile(
        r"^(?:je\s+(?:suis|m['\s]appelle)|mon\s+(?:pre)?nom\s+est|appelle[\s-]moi)\s+(?P<name>.+)$",
        re.IGNORECASE
    )
    match_user = regex_set_username.match(lower)
    if match_user:
        new_name = match_user.group("name").strip()
        # Petit nettoyage si l'user dit "je m'appelle le grand X"
        for noise in ["le ", "la ", "un "]: 
             if new_name.startswith(noise): new_name = new_name[len(noise):]
        
        print(f"  [QUICK] 🆔 Changement nom USER détecté : '{new_name}' -> USERNAME", flush=True)
        return ("USERNAME", new_name)

    # 4. CHANGER LE NOM DE L'AGENT ("Tu t'appelles Igor", "Ton nom est Igor")
    # Regex stricte : Doit commencer par TU/TON et avoir du contenu après.
    regex_set_agentname = re.compile(
        r"^(?:tu\s+(?:es|t['\s]appelles?)|ton\s+(?:pre)?nom\s+est|appelle[\s-]toi|change\s+ton\s+nom\s+(?:en|pour))\s+(?P<name>.+)$",
        re.IGNORECASE
    )
    match_agent = regex_set_agentname.match(lower)
    if match_agent:
        new_name = match_agent.group("name").strip()
        # Petit nettoyage
        for noise in ["le ", "la ", "un "]:
             if new_name.startswith(noise): new_name = new_name[len(noise):]

        print(f"  [QUICK] 🆔 Changement nom AGENT détecté : '{new_name}' -> AGENTNAME", flush=True)
        return ("AGENTNAME", new_name)

    # === PRIORITÉ 2.2 : RAPPELS (Ambiguïté Alarme vs Note) ===
    # Gestion de : "Rappelle-moi à 8h" (Alarme) vs "Rappelle-moi de manger" (Note)
    if "rappel" in lower:
        # 1. Détection temporelle (C'est une alarme)
        time_triggers = ["dans", "à", "pour", "minutes", "heures", "h", "min", "sec", "demain"]
        has_time = any(t in lower for t in time_triggers) or any(char.isdigit() for char in lower)

        if has_time:
             print(f"  [QUICK] ⏰ Rappel temporel détecté -> ALARM", flush=True)
             return ("ALARM", user_input)

        # 2. Sinon, c'est une Note (To-Do)
        # Nettoyage de l'ordre "Rappelle moi de" pour ne garder que le contenu
        clean_text = re.sub(r"^(?:se\s+)?rappell?ez?(?:[-\s]moi)?\s*(?:de|d')?\s*", "", user_input, flags=re.IGNORECASE).strip()
        
        if clean_text:
            print(f"  [QUICK] 📝 Rappel tâche détecté -> NOTE: '{clean_text}'", flush=True)
            return ("NOTE", clean_text)

    # === PRIORITÉ 2.5 : Commandes Muet/Parole (REGEX ROBUSTE) ===
    # Détecte: "parle à nouveau", "tu peux parler", "remets le son", "active la voix", "sors du mode muet", "désactive le silencieux", etc.
    regex_unmute = re.compile(
        r"(?:tu\s+peux\s+|vas[- ]?y\s+|re)parl(?:e|er|es?)\b|"       # Tu peux parler, vas-y parle, reparle
        r"parl(?:e|er|es?)\s+(?:à|de)\s+nouveau|"                    # Parle à nouveau
        r"(?:r?é?activ|re?met|rétabl|allum)\w*\s+(?:le\s+|la\s+|ton\s+|ta\s+)?(?:son|voix|parole|audio)|" # Active/Remets le son/la voix
        r"(?:désactiv|enlève|sor|quitt|coup)\w*\s+(?:le\s+|du\s+)?(?:mode\s+)?(?:muet|silencieux|silence)", # Désactive/Sors du mode muet
        re.IGNORECASE
    )

    if regex_unmute.search(lower):
        print(f"  [QUICK] Unmute détecté (Regex): {lower}", flush=True)
        return ("SET_MUTE", "off")

    # === PRIORITÉ 2.6 : COMMANDES SHELL ===
    if "exécute la commande" in lower or "commande terminal" in lower or "commande shell" in lower:
        for trigger in ["exécute la commande", "execute la commande", "commande terminal", "commande shell"]:
            if trigger in lower:
                # Extraction commande (en gardant la casse)
                idx = lower.find(trigger) + len(trigger)
                cmd = user_input[idx:].strip().lstrip(":").strip()
                if cmd:
                    print(f"  [QUICK] SHELL détecté: '{cmd}'", flush=True)
                    return ("SHELL", cmd)

    # === PRIORITÉ 3 : Commandes système (1 mot) ===
    direct_commands = {
        "stop": ("SET_MUTE", "on"),
        "silence": ("SET_MUTE", "on"),
        "tais-toi": ("SET_MUTE", "on"),
        "parle": ("SET_MUTE", "off"),
        "pause": ("MEDIA", "pause"),
        "play": ("MEDIA", "play"),
        "lecture": ("MEDIA", "play"),
        "next": ("MEDIA", "next"),
        "suivant": ("MEDIA", "next"),
        "piste suivante": ("MEDIA", "next"),
        "précédent": ("MEDIA", "previous"),
        "piste précédente": ("MEDIA", "previous"),
        "prev": ("MEDIA", "previous")
    }
    
    if lower in direct_commands:
        print(f"  [QUICK] Commande directe: {lower}", flush=True)
        return direct_commands[lower]
    
    # === PRIORITÉ 4 : Questions système (REGEX) ===
    # Liste de tuples (Pattern Regex, Action)
    system_regexes = [
        # Apps: "Quelles apps", "Liste mes applications", "Logiciels installés"
        (r"(?:quelles?|liste|mes|voir)\s+(?:toutes\s+les\s+)?(?:applications?|apps?|logiciels?)", ("LIST_APPS", "")),
        
        # Windows: "Quelles fenêtres", "Liste fenêtres", "Qu'est-ce qui est ouvert"
        (r"(?:quelles?|liste)\s+(?:fenêtres?|windows?)|(?:applications?|fenêtres?)\s+ouvertes?|qu'est\s+ce\s+qui\s+est\s+ouvert", ("LIST_WINDOWS", "")),
        
        # Projects: "Mes projets", "Liste projets"
        (r"(?:quels?|liste|mes|voir)\s+projets?", ("PROJECT_LIST", "")),
        
        # Notes: "Lis mes notes", "Note #1"
        # MODIFICATION : On passe user_input pour capturer le numéro éventuel
        (r"(?:mes|liste|lis|voir|montre)\s+(?:mes\s+|la\s+)?notes?", ("READ_NOTE", user_input)),
        
        # Alarms: "Mes alarmes", "Quelles alarmes"
        (r"(?:mes|liste|quelles?|voir)\s+alarmes?", ("SHOW_ALARMS", "")),
        
        # Memory: "Que sais-tu SUR MOI", "Ta mémoire", "Ce que tu sais DE MOI"
        # MODIFICATION : Ajout de (?:sur\s+moi|de\s+moi|me\s+concernant) pour ne pas intercepter "Que sais-tu des serpents"
        (r"(?:qu'est\s+ce\s+que|que|ce\s+que)\s+(?:tu\s+)?sais\s+(?:sur\s+moi|de\s+moi|me\s+concernant)|ta\s+mémoire|faits?\s+mémorisés?", ("READ_MEM", ""))
    ]

    for pattern, action in system_regexes:
        if re.search(pattern, lower):
            print(f"  [QUICK] Question système (Regex): {action[0]}", flush=True)
            return action
        
    # === PRIORITÉ 4.5 : Vitesse de la voix (SPEED) ===
    # Liste élargie pour inclure "vitesse normale", "parle vite", etc.
    speed_keywords = [
        "parle plus vite", "parle moins vite", "parle plus lentement", 
        "vitesse de la voix", "ralentis", "accélère", 
        "vitesse normal", "vitesse normale", "remets la vitesse",
        "parle normalement"
    ]
    
    if any(k in lower for k in speed_keywords):
        print(f"  [QUICK] Vitesse voix détectée : '{user_input}'", flush=True)
        return ("SET_SPEED", user_input)
        
    # Cas spécifique "parle doucement"
    if "parle" in lower and "doucement" in lower:
        return ("SET_SPEED", "doucement")

    # === PRIORITÉ 5 : Musique ===
    # A. Demande de statut / Identification ("Qu'est-ce qui se passe en musique ?", "C'est quoi ce titre ?")
    music_status_triggers = [
        "se passe en musique", "joue en ce moment", "titre de la chanson", 
        "c'est quoi cette musique", "quelle est cette musique", "quelle musique joue",
        "qui chante", "c'est quoi ce son", "quel est ce titre", "niveau musique"
    ]
    
    if any(t in lower for t in music_status_triggers):
        print(f"  [QUICK] 🎵 Statut Musique détecté -> MUSIC_CHECK (Passif)", flush=True)
        # On passe l'argument "status" pour empêcher Igor de lancer/pauser des trucs
        return ("MUSIC_CHECK", "status")

    # B. Commandes de lancement ("Mets de la musique")
    music_launch_triggers = [
        "mets de la musique", "met de la musique", "lance la musique",
        "joue de la musique", "play music", "de la musique",
        "mettre de la musique", "lancer de la musique"
    ]
    
    # Filtres négatifs pour le lancement (éviter les définitions encyclopédiques)
    music_negative = [
        "quelle", "quel", "c'est quoi", "qu'est-ce", "identifie", "reconnaît"
    ]
    
    # Si demande de musique SANS question d'identification
    if any(trigger in lower for trigger in music_launch_triggers):
        if not any(neg in lower for neg in music_negative):
            print(f"  [QUICK] 🎵 Lancement MUSIQUE détecté -> MUSIC_CHECK", flush=True)
            return ("MUSIC_CHECK", "")
    
    # === PRIORITÉ 6 : Contrôle média ===
    control_phrases = [
        "en pause", "en lecture", "en plein écran", "plein écran", 
        "fullscreen", "volume", "son"
    ]
    
    if any(phrase in lower for phrase in control_phrases):
        return None  # Laisse l'IA gérer
    
    # === PRIORITÉ 7 : Recettes de cuisine (SEARCH forcé) ===
    # Force la recherche Web pour les recettes au lieu de la définition (KNOWLEDGE)
    if "recette" in lower:
        print(f"  [QUICK] Recette cuisine détectée -> SEARCH: '{user_input}'", flush=True)
        return ("SEARCH", user_input)

    # === PRIORITÉ 8 : Gestion des Raccourcis (List/Delete) ===
    # On ajoute "raccouric" et "racourci" à la liste de détection
    if any(k in lower for k in ["raccourci", "raccouric", "racourci", "favori"]):
        
        # Cas 1 : Suppression
        if any(w in lower for w in ["supprime", "efface", "retire", "enlever"]):
            # Extraction propre du nom
            target = lower
            # On ajoute aussi les typos dans la liste des mots à nettoyer
            for noise in ["supprime", "efface", "retire", "enlever", 
                          "le raccourci", "mon raccourci", "raccourci", 
                          "le raccouric", "raccouric", "racourci", 
                          "le favori", "favori"]:
                target = target.replace(noise, "")
            target = target.strip()
            
            print(f"  [QUICK] Suppression raccourci détectée: '{target}'", flush=True)
            return ("SHORTCUT_DELETE", target)
            
        # Cas 2 : Liste / Consultation
        if any(w in lower for w in ["quels", "quelles", "liste", "mes", "voir", "montre"]):
            print(f"  [QUICK] Liste raccourcis détectée", flush=True)
            return ("SHORTCUT_LIST", "")

    # === PRIORITÉ 6 : YouTube ===
    if "sur youtube" in lower or "youtube" in lower:
        query = lower
        for verb in ["mets", "met", "lance", "joue", "regarde", "ouvre"]:
            query = query.replace(verb, "").strip()
        
        for noise in ["sur youtube", "youtube", "la", "le", "du", "de", "une", "un"]:
            query = query.replace(noise, "").strip()
        
        if len(query) > 2:
            youtube_arg = f"Youtube {query}"
            print(f"  [QUICK] Youtube détecté: '{youtube_arg}'", flush=True)
            return ("LAUNCH", youtube_arg)
    
    # === PRIORITÉ 7 : Vidéos ===
    video_patterns = ["vidéo de ", "video de ", "clip de ", "musique de "]
    for pattern in video_patterns:
        if pattern in lower:
            idx = lower.find(pattern)
            subject = lower[idx + len(pattern):].strip()
            
            for noise in ["la", "le", "du", "de", "une", "un"]:
                subject = subject.replace(noise, "").strip()
            
            if len(subject) > 2:
                youtube_arg = f"Youtube {subject}"
                print(f"  [QUICK] Vidéo détectée: '{youtube_arg}'", flush=True)
                return ("LAUNCH", youtube_arg)
    
    # === PRIORITÉ 8 : Nombres seuls ===
    if lower.isdigit():
        num = int(lower)
        if skills.LAST_WIKI_OPTIONS:
            print(f"  [QUICK] Sélection Wiki #{num}", flush=True)
            return ("LEARN", lower)
        if 0 <= num <= 100:
            print(f"  [QUICK] Volume {num}", flush=True)
            return ("VOLUME", lower)
    
    # === PRIORITÉ 9 : Sélection Wikipedia ===
    if skills.LAST_WIKI_OPTIONS:
        selection_keywords = ["premier", "1er", "deuxième", "second", "2ème", "troisième", "3ème"]
        if any(k in lower for k in selection_keywords):
            print(f"  [QUICK] Sélection contextuelle Wiki", flush=True)
            return ("LEARN", user_input)

    # === PRIORITÉ 10 : MAXIMISATION / PLEIN ÉCRAN (Force l'outil FULLSCREEN) ===
    # On intercepte ici pour éviter que l'IA n'invente MAXIMIZE_WINDOW
    if any(k in lower for k in ["maximise", "maximize", "agrandis", "plein écran", "fullscreen"]):
        # On vérifie que ce n'est pas une question ("c'est quoi le plein écran")
        if not any(k in lower for k in ["c'est quoi", "comment"]):
            print(f"  [QUICK] Maximisation détectée -> FULLSCREEN", flush=True)
            return ("FULLSCREEN", user_input)

    # === PRIORITÉ 11 : FOCUS FENÊTRE (NOUVEAU) ===
    if "focus" in lower:
        # Nettoyage simple pour extraire la cible
        target = lower.replace("focus", "").strip()
        # On enlève "sur" si présent
        if target.startswith("sur "): target = target[4:].strip()
        
        if target:
            print(f"  [QUICK] Focus détecté -> FOCUS_WINDOW: '{target}'", flush=True)
            return ("FOCUS_WINDOW", target)
    
    return None  # Pas de match → Appel IA nécessaire

def get_cached_or_query(user_input):
    """
    Vérifie le cache avant d'appeler l'IA.
    Utilise un hash MD5 de la requête comme clé.
    """
    # Hash de la requête (insensible à la casse)
    cache_key = hashlib.md5(user_input.lower().encode()).hexdigest()
    
    if cache_key in igor_globals.QUERY_CACHE:
        log_query_stats("cache_hits")
        print(f"  [CACHE HIT] Réponse instantanée", flush=True)
        return igor_globals.QUERY_CACHE[cache_key]
    
    # Appel IA
    result = brain_query(user_input)
    log_query_stats("ai_calls")
    
    # Sauvegarde cache (avec limite de taille FIFO)
    if len(igor_globals.QUERY_CACHE) >= igor_globals.CACHE_MAX_SIZE:
        # Supprime la plus ancienne entrée
        igor_globals.QUERY_CACHE.pop(next(iter(igor_globals.QUERY_CACHE)))
    
    igor_globals.QUERY_CACHE[cache_key] = result
    return result