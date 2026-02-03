# igor_knowledge.py
import os
import re
import json
import glob
import time
import requests
import datetime
import shutil
import subprocess
import threading
import urllib.parse
import wikipedia
import unicodedata
from difflib import SequenceMatcher
from sympy import symbols, solve, Eq, sympify
from sympy.parsing.sympy_parser import parse_expr, standard_transformations, implicit_multiplication_application

# Modules audio optionnels
try:
    import soundcard as sc
    import soundfile as sf
    import numpy as np
    import speech_recognition as sr
except ImportError:
    pass # Géré dans tool_listen_system

# Nécessaire pour lire l'interface des applis sans les toucher (URL extraction)
try:
    import gi
    gi.require_version('Atspi', '2.0')
    from gi.repository import Atspi
except:
    Atspi = None

try:
    import pyperclip
except ImportError:
    pyperclip = None

# Imports Configuration & Système
import igor_config
from igor_config import (
    MEMORY, save_memory, smart_summarize, remove_accents,
    KNOWLEDGE_DIR, SEARCH_LOG_FILE, LLM_TEXT_API_URL,
    ALARM_LOCK, SHORTCUTS_FILE, PLAY_ALARM_CALLBACK,
    TASK_QUEUE, ABORT_FLAG, ALARM_STYLES, USER_HOME,
    LAST_WIKI_OPTIONS, AUTO_LEARN_MODE
)
from igor_system import bash_exec, tool_launch, INSTALLED_APPS

# --- AJOUT GEMINI AUDIO ---
try:
    import google.generativeai as genai
    # Clé API (La même que dans igor_vision.py)
    GEMINI_API_KEY = "AIzaSyAhVW2TyB84IOuX8d-ybmcQ2jded6vxLmU"
    
    if "METS_TA_CLE" not in GEMINI_API_KEY:
        genai.configure(api_key=GEMINI_API_KEY)
        GEMINI_AUDIO_AVAILABLE = True
    else:
        GEMINI_AUDIO_AVAILABLE = False
except ImportError:
    GEMINI_AUDIO_AVAILABLE = False

# Config Wikipedia en français
wikipedia.set_lang("fr")

# --- RECHERCHE WEB (MANUELLE & ROBUSTE) ---

def tool_search_web(arg):
    """
    Fait une requête HTTP POST standard vers DuckDuckGo.
    FALLBACK : Si DDG échoue (timeout), bascule sur Google Search (version légère).
    """
    raw_query = str(arg).strip()
    if len(raw_query) < 2: return "Recherche trop courte."

    print(f"  [WEB] Requête : [{raw_query}]", flush=True)

    final_response = ""
    raw_log_buffer = ""
    valid_results = []

    # --- TENTATIVE 1 : DUCKDUCKGO ---
    try:
        url = "https://html.duckduckgo.com/html/"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/115.0",
            "Referer": "https://html.duckduckgo.com/",
            "Content-Type": "application/x-www-form-urlencoded"
        }
        data = {'q': raw_query, 'kl': 'fr-fr'}

        # Timeout réduit à 5s pour basculer vite sur Google en cas de blocage
        res = requests.post(url, data=data, headers=headers, timeout=5)
        html_content = res.text
        
        pattern = r'<a[^>]*class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</a>'
        snippets = re.findall(pattern, html_content, re.DOTALL)
        pattern_titles = r'<a[^>]*class="[^"]*result__a[^"]*"[^>]*>(.*?)</a>'
        titles = re.findall(pattern_titles, html_content, re.DOTALL)

        count = min(len(snippets), len(titles), 5)
        for i in range(count):
            clean_title = re.sub(r'<[^>]+>', '', titles[i]).strip()
            clean_body = re.sub(r'<[^>]+>', '', snippets[i]).strip()
            clean_body = clean_body.replace("&nbsp;", " ")
            valid_results.append(f"{clean_title}: {clean_body}")
            
    except Exception as e:
        print(f"  [WEB] DDG échoué ({e}), bascule sur Google Fallback...", flush=True)
        raw_log_buffer += f"DDG ERROR: {e}\n"

    # --- TENTATIVE 2 : GOOGLE (FALLBACK) ---
    if not valid_results:
        try:
            # On utilise l'interface Google Mobile/Basic qui est plus facile à parser
            google_url = f"https://www.google.com/search?q={urllib.parse.quote(raw_query)}&hl=fr&gl=fr"
            # User-Agent générique pour éviter le blocage bot strict
            g_headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"}
            
            res_g = requests.get(google_url, headers=g_headers, timeout=8)
            
            # Regex robuste pour extraire les descriptions Google (souvent dans des div BNeawe ou span st)
            # On cherche des blocs de texte significatifs
            # Cette regex cherche les textes longs dans les balises div/span
            raw_text = re.sub(r'<script.*?>.*?</script>', '', res_g.text, flags=re.DOTALL)
            raw_text = re.sub(r'<style.*?>.*?</style>', '', raw_text, flags=re.DOTALL)
            
            # Extraction brute des bouts de texte qui ressemblent à des phrases
            text_parts = re.findall(r'>([^<]{30,})<', raw_text)
            
            # Nettoyage
            for part in text_parts[:6]: # On prend les 6 premiers "gros" textes
                clean = part.replace("\n", " ").strip()
                if "Google" not in clean and "Connexion" not in clean and len(clean) > 40:
                    valid_results.append(clean)
                    
        except Exception as e_g:
            print(f"  [WEB] Google échoué aussi : {e_g}", flush=True)
            raw_log_buffer += f"GOOGLE ERROR: {e_g}\n"

    # --- RÉSUMÉ FINAL ---
    if not valid_results:
        final_response = f"Recherche impossible (Connexions bloquées). Essayez plus tard."
    else:
        full_raw_text = " \n".join(valid_results)
        # On demande à l'IA de résumer le tout proprement
        summary = smart_summarize(full_raw_text, source_name="recherche web")
        final_response = f"Web : {summary}"

    # --- ECRITURE LOG (inchangé) ---
    try:
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(SEARCH_LOG_FILE, 'a', encoding='utf-8') as f_log:
            f_log.write(f"[{timestamp}] REQUÊTE: {raw_query}\n")
            f_log.write(raw_log_buffer if raw_log_buffer else "LOG OK.\n")
            f_log.write(f"RÉPONSE: {final_response}\n{'='*50}\n")
    except Exception: pass

    # --- ECRITURE LOG ---
    try:
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(SEARCH_LOG_FILE, 'a', encoding='utf-8') as f_log:
            f_log.write(f"[{timestamp}] REQUÊTE: {raw_query}\n")
            f_log.write(raw_log_buffer if raw_log_buffer else "AUCUNE DONNÉE.\n")
            f_log.write(f"RÉPONSE: {final_response}\n{'='*50}\n")
    except Exception: pass

    return final_response

def tool_weather(arg):
    location = str(arg).strip().replace('"', '').replace("'", "")
    
    # Nettoyage des prépositions
    for prefix in ["à ", "en ", "au ", "in "]:
        if location.lower().startswith(prefix):
            location = location[len(prefix):].strip()
            
    # DÉTECTION AUTOMATIQUE (Si vide ou mots clés)
    if location.lower() in ["", "ici", "now", "local", "maison"]:
        location = ""
        print("  [WEATHER] Auto-détection de la position...", flush=True)
        
        # 1. API IP (Très précis pour Montréal)
        try:
            # ip-api retourne du JSON avec la ville exacte
            res = requests.get("http://ip-api.com/json", timeout=2)
            if res.status_code == 200:
                data = res.json()
                if data.get("status") == "success":
                    location = data.get("city", "")
                    print(f"  [WEATHER] Ville détectée (IP) : {location}", flush=True)
        except: pass

        # 2. API FALLBACK
        if not location:
            try:
                res = requests.get("https://ipinfo.io/city", timeout=2)
                if res.status_code == 200:
                    location = res.text.strip()
            except: pass
            
        # 3. SYSTÈME (Timedatectl)
        if not location:
            try:
                # Cherche "America/Montreal" dans la config
                out = subprocess.check_output("timedatectl", shell=True).decode()
                match = re.search(r"Time zone: ([^ ]+)", out)
                if match:
                    # Extrait "Montreal" de "America/Montreal"
                    zone = match.group(1).split("/")[-1].replace("_", " ")
                    location = zone
                    print(f"  [WEATHER] Ville détectée (OS) : {location}", flush=True)
            except: pass

    # Si vraiment tout échoue, on ne laisse pas vide (sinon wttr.in peut deviner Paris)
    # On peut mettre une valeur par défaut de sécurité si vous le souhaitez
    if not location:
        # location = "Montreal" # Décommentez pour forcer Montréal en dernier recours absolu
        pass

    try:
        # Requête Wttr.in
        headers = {"User-Agent": "curl/7.68.0"}
        safe_loc = location if location else ""
        url = f"https://wttr.in/{safe_loc}?format=%C+et+%t&lang=fr"
        
        res = requests.get(url, headers=headers, timeout=10)
        
        if res.status_code == 200:
            weather_text = res.text.strip().replace("+", "")
            # Nettoyage des codes ANSI (couleurs)
            weather_text = re.sub(r'\x1b\[[0-9;]*m', '', weather_text)
            
            loc_display = location if location else "ici"
            return f"Météo pour {loc_display} : {weather_text}"
            
        return f"Erreur météo ({res.status_code})."
    except Exception as e: return f"Erreur technique : {e}"

def tool_time(arg):
    # 1. Gestion "Maintenant / Ici"
    if not arg or str(arg).lower() in ["now", "ici", "local", ""]: 
        return bash_exec("date '+%H heures %M'")
    
    arg_clean = str(arg).strip().replace("'", "").replace('"', '')
    
    # 2. NETTOYAGE (Prépositions)
    for prefix in ["à ", "a ", "en ", "au ", "in ", "pour ", "vers "]:
        if arg_clean.lower().startswith(prefix):
            arg_clean = arg_clean[len(prefix):].strip()

    # 3. MAPPING ÉTENDU (Villes -> Fuseaux IANA Linux)
    mapping = {
        # USA (Les villes manquantes)
        "salt lake city": "America/Denver", "slc": "America/Denver", "utah": "America/Denver",
        "denver": "America/Denver",
        "san francisco": "America/Los_Angeles", "las vegas": "America/Los_Angeles", "seattle": "America/Los_Angeles",
        "boston": "America/New_York", "washington": "America/New_York", "dc": "America/New_York", "miami": "America/New_York", "atlanta": "America/New_York", "detroit": "America/New_York",
        "chicago": "America/Chicago", "dallas": "America/Chicago", "houston": "America/Chicago",
        
        # Afrique (Tombouctou est au Mali -> Bamako ou GMT)
        "tombouctou": "Africa/Bamako", "timbuktu": "Africa/Bamako", "mali": "Africa/Bamako", "bamako": "Africa/Bamako",
        "dakar": "Africa/Dakar", "senegal": "Africa/Dakar",
        "marrakech": "Africa/Casablanca", "casablanca": "Africa/Casablanca", "rabat": "Africa/Casablanca", "maroc": "Africa/Casablanca",
        "tunis": "Africa/Tunis", "alger": "Africa/Algiers", "le caire": "Africa/Cairo",
        
        # Canada (Rappel)
        "calgary": "America/Edmonton", "edmonton": "America/Edmonton", "vancouver": "America/Vancouver",
        "toronto": "America/Toronto", "montreal": "America/Montreal", "quebec": "America/Montreal",
        
        # Asie
        "tokyo": "Asia/Tokyo", "kyoto": "Asia/Tokyo", "osaka": "Asia/Tokyo",
        "pekin": "Asia/Shanghai", "shanghai": "Asia/Shanghai", "hong kong": "Asia/Hong_Kong",
        "bangkok": "Asia/Bangkok", "singapour": "Asia/Singapore",
        "new delhi": "Asia/Kolkata", "mumbai": "Asia/Kolkata", "bombay": "Asia/Kolkata",
        
        # Europe
        "londres": "Europe/London", "dublin": "Europe/Dublin",
        "paris": "Europe/Paris", "berlin": "Europe/Berlin", "rome": "Europe/Rome", "madrid": "Europe/Madrid",
        "moscou": "Europe/Moscow", "kiev": "Europe/Kiev"
    }
    
    normalized = remove_accents(arg_clean).lower()
    search_term = mapping.get(normalized, normalized.replace(" ", "_"))

    # 4. RECHERCHE LOCALE (Zoneinfo)
    zone_path = ""
    if os.path.exists(f"/usr/share/zoneinfo/{search_term}"):
        zone_path = search_term
    else:
        try:
            cmd = f"find /usr/share/zoneinfo -iname '*{search_term}*' | grep -v 'posix' | grep -v 'right' | head -n 1"
            found = subprocess.check_output(cmd, shell=True, stderr=subprocess.DEVNULL).decode().strip()
            if found: zone_path = found.replace("/usr/share/zoneinfo/", "")
        except: pass

    # 5. RÉSULTAT OU FALLBACK WEB
    if zone_path:
        time_str = bash_exec(f"TZ='{zone_path}' date '+%H heures %M'")
        return f"Il est {time_str} à {arg_clean.capitalize()}."
    else:
        # SI ECHEC LOCAL -> ON DEMANDE AU WEB (Beaucoup plus robuste)
        print(f"  [TIME] Zone locale introuvable pour '{arg_clean}'. Recherche Web...", flush=True)
        return tool_search_web(f"heure actuelle à {arg_clean}")

# --- WIKIPEDIA & APPRENTISSAGE ---

def tool_learn(arg):
    # Utilisation de la liste globale définie dans igor_config
    arg_str = str(arg).strip()
    
    # --- Vérification si on connait déjà le sujet ---
    query_clean = remove_accents(arg_str).lower()
    files = glob.glob(os.path.join(KNOWLEDGE_DIR, "*.txt"))
    
    for f in files:
        fname = os.path.basename(f).lower().replace(".txt", "")
        fname_clean = fname.replace("_", " ")
        if query_clean == fname_clean or query_clean == fname:
            print(f"  [LEARN] Sujet '{arg_str}' déjà connu localement. Redirection.", flush=True)
            return f"(Je connais déjà ce sujet) \n" + tool_consult(fname_clean)

    selected_title = None

    # GESTION SÉLECTION VOCALE
    if igor_config.LAST_WIKI_OPTIONS:
        lower_arg = arg_str.lower()
        idx = -1
        
        if "premier" in lower_arg or "1er" in lower_arg: idx = 0
        elif "deuxième" in lower_arg or "2ème" in lower_arg: idx = 1
        elif "troisième" in lower_arg or "3ème" in lower_arg: idx = 2
        elif "quatrième" in lower_arg or "4ème" in lower_arg: idx = 3
        
        if idx == -1:
            nums = re.findall(r'\d+', arg_str)
            if nums:
                val = int(nums[0])
                if 0 < val <= len(igor_config.LAST_WIKI_OPTIONS): idx = val - 1

        if 0 <= idx < len(igor_config.LAST_WIKI_OPTIONS):
            selected_title = igor_config.LAST_WIKI_OPTIONS[idx]
            igor_config.LAST_WIKI_OPTIONS = [] 

    query = selected_title if selected_title else arg_str
    
    # --- MODIFICATION 1 : Nettoyage plus robuste via Regex ---
    # Gère "apprend" (sans s), "le sujet", "c'est quoi", etc.
    query = re.sub(r"^(apprends?|cherche|le sujet|sur|à propos de|c'est quoi|parle moi de)\s+", "", query, flags=re.IGNORECASE).strip()

    try:
        # --- MODIFICATION 2 : Recherche préalable du titre exact ---
        # Au lieu de deviner l'ID de la page, on demande à Wikipedia de chercher
        # Cela corrige les erreurs de singulier/pluriel et majuscules
        search_results = wikipedia.search(query)
        
        if not search_results:
            return f"Je n'ai rien trouvé sur Wikipedia pour '{query}'."

        # On prend le premier résultat (le plus pertinent) comme titre cible
        best_match = search_results[0]
        
        # On charge la page avec le titre VALIDÉ par la recherche
        page = wikipedia.page(best_match, auto_suggest=False)
        
        content = page.content.split("== Voir aussi ==")[0]
        
        safe_name = re.sub(r'[^\w\-_\. ]', '_', page.title.lower()) + ".txt"
        filepath = os.path.join(KNOWLEDGE_DIR, safe_name)
        
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(f"Sujet: {page.title}\n\n{content[:10000]}")
            
        igor_config.LAST_WIKI_OPTIONS = [] 
        summary = smart_summarize(content[:3000], source_name=f"article {page.title}")
        return f"Appris : '{page.title}'. {summary}"

    except wikipedia.exceptions.DisambiguationError as e:
        print(f"  [DEBUG WIKI] Ambiguité sur '{query}'.", flush=True)
        # Gestion ambiguïté (reste inchangée, sauf qu'on utilise e.options directement)
        igor_config.LAST_WIKI_OPTIONS = e.options
        links = [f"{i+1}. {o}" for i, o in enumerate(e.options[:10])]
        return f"Ambiguïté. Précisez : \n" + "\n".join(links)

    except Exception as ex:
        return f"Erreur lors de l'apprentissage : {ex}"

def tool_consult(arg):
    """Lit un fichier de connaissance local ou lance LEARN si inconnu et mode auto."""
    # Accès explicite à la config globale
    global AUTO_LEARN_MODE 
    
    query = str(arg).lower().strip()
    files = glob.glob(os.path.join(KNOWLEDGE_DIR, "*.txt"))
    best_match = None
    
    # 1. Recherche Nom
    for f in files:
        fname = os.path.basename(f).lower()
        if query in fname.replace("_", " ") or query in fname:
            best_match = f; break
            
    # 2. Recherche Contenu
    if not best_match:
        for f in files:
            try:
                if query in open(f, "r", encoding="utf-8").read(2000).lower():
                    best_match = f; break
            except: pass

    if not best_match:
        if igor_config.AUTO_LEARN_MODE: # Utilise la variable du module config
            return tool_learn(arg)
        known = [os.path.basename(f).replace(".txt", "") for f in files]
        return f"Rien trouvé sur '{query}'. Je connais : {', '.join(known[:5])}..."

    try:
        content = open(best_match, "r", encoding="utf-8").read()
        clean_title = os.path.basename(best_match).replace(".txt", "").replace("_", " ").title()
        return f"Savoir sur {clean_title} : " + smart_summarize(content, f"article {clean_title}")
    except Exception as e: return f"Erreur lecture : {e}"

# --- CARNET DE NOTES (SIMILAIRE A LA MEMOIRE) ---

def tool_note_write(text):
    text = text.strip()
    if not text: return "Note vide."
    
    notes = MEMORY.get('notebook', [])
    # Analyse similarité
    for i, old in enumerate(notes):
        sim = SequenceMatcher(None, old.lower(), text.lower()).ratio()
        if sim > 0.65:
            if len(text) > len(old):
                notes[i] = text
                save_memory(MEMORY)
                return f"Note mise à jour : '{text}'."
            return f"Note déjà existante : '{old}'."

    MEMORY['notebook'].append(text)
    save_memory(MEMORY)
    return f"Ajouté au carnet : {text}"

def tool_note_read(arg):
    notes = MEMORY.get('notebook', [])
    if not notes: return "Carnet vide."
    
    # 1. Analyse si un numéro spécifique est demandé
    arg_str = str(arg).lower()
    
    # Gestion "Dernière note"
    if "dernière" in arg_str or "last" in arg_str:
        return f"Dernière note : {notes[-1]}"

    # Gestion par numéro (#1, n°1, 1)
    nums = re.findall(r'\d+', arg_str)
    if nums:
        try:
            # On prend le dernier chiffre trouvé (souvent le plus pertinent dans "Note numéro 2")
            idx = int(nums[-1]) - 1
            if 0 <= idx < len(notes):
                return f"Note #{idx+1} : {notes[idx]}"
            else:
                return f"La note #{idx+1} n'existe pas. Vous avez {len(notes)} notes."
        except: pass

    # 2. Sinon, on liste tout
    # On limite l'affichage pour éviter de saturer le TTS si le carnet est gros
    if len(notes) > 5:
        return f"Vous avez {len(notes)} notes. Les 5 dernières : " + "; ".join([f"{i+1}. {n}" for i,n in enumerate(notes[-5:], start=len(notes)-4)])
    
    return "Voici votre carnet : " + "; ".join([f"{i+1}. {n}" for i,n in enumerate(notes)])

def tool_delete_note(arg):
    arg = str(arg).lower().strip()
    notes = MEMORY.get('notebook', [])
    if not notes: return "Carnet déjà vide."

    # Par numéro
    nums = re.findall(r'\d+', arg)
    if nums:
        try:
            idx = int(nums[0]) - 1
            if 0 <= idx < len(notes):
                rm = notes.pop(idx)
                save_memory(MEMORY)
                return f"Note supprimée : {rm}"
        except: pass
    
    # Par texte
    for i, note in enumerate(notes):
        if arg in note.lower():
            rm = notes.pop(i)
            save_memory(MEMORY)
            return f"Note effacée : {rm}"
    return "Note introuvable."

def tool_note_clear(arg):
    MEMORY['notebook'] = []
    save_memory(MEMORY)
    return "Carnet effacé."

def tool_remember(text):
    text = text.strip()
    if not text: return "Vide."
    if text not in MEMORY['facts']:
        MEMORY['facts'].append(text)
        save_memory(MEMORY)
        return f"Noté : {text}"
    return "Déjà connu."

def tool_read_memory(arg):
    facts = MEMORY.get('facts', [])
    if not facts: return "Je n'ai aucun fait mémorisé sur vous pour l'instant."
    
    # Amélioration du formatage pour une lecture plus naturelle
    header = f"Voici les {len(facts)} faits que je connais sur vous :"
    bullet_list = "\n- ".join(facts)
    return f"{header}\n- {bullet_list}"

# --- ALARMES (ROBUSTE) ---

def parse_alarm_args(text):
    text = str(text).lower().strip()
    now = datetime.datetime.now()
    is_relative = False
    
    if "dans " in text: is_relative = True
    elif " à " in f" {text} " or any(x in text for x in ["tous", "chaque", "matin", "soir"]): is_relative = False
    elif any(x in text for x in ["min", "sec", " m ", " s "]): is_relative = True
    elif any(x in text for x in ["heure", " h "]) or text.endswith("h"): is_relative = False
    elif text.isdigit(): is_relative = True

    if is_relative:
        match = re.search(r"(\d+)\s*(h|m|s|min|sec|heure)?", text)
        if match:
            val = int(match.group(1))
            unit = match.group(2) if match.group(2) else "min"
            delta = val * 3600 if 'h' in unit else (val if 's' in unit else val * 60)
            return {"type": "oneshot", "timestamp": (now + datetime.timedelta(seconds=delta)).timestamp(), "raw": text}
    else:
        h = -1; m = 0
        if "midi" in text: h = 12
        elif "minuit" in text: h = 0
        else:
            match = re.search(r"(\d{1,2})(?:[:h]|\s+heures?\s+)?(\d{1,2})?", text)
            if match: h = int(match.group(1)); m = int(match.group(2)) if match.group(2) else 0
        
        if h != -1:
            days = []
            if "semaine" in text: days = [0,1,2,3,4]
            elif "weekend" in text: days = [5,6]
            elif any(x in text for x in ["tous", "chaque", "quotidien"]): days = list(range(7))
            else:
                map_days = {"lundi":0, "mardi":1, "mercredi":2, "jeudi":3, "vendredi":4, "samedi":5, "dimanche":6}
                for k,v in map_days.items(): 
                    if k in text: days.append(v)
            
            if not days:
                target = now.replace(hour=h, minute=m, second=0)
                if target <= now: target += datetime.timedelta(days=1)
                return {"type": "oneshot", "timestamp": target.timestamp(), "raw": text}
            else:
                return {"type": "recurring", "time": f"{h:02d}:{m:02d}", "days": days, "raw": text}
    return None

def tool_set_alarm(arg):
    data = parse_alarm_args(str(arg))
    if not data: return "Heure incomprise."
    
    with ALARM_LOCK:
        MEMORY['alarms'].append(data)
        save_memory(MEMORY)
    
    if MEMORY.get('alarm_sound') is None:
        MEMORY['alarm_sound'] = 'classique'; save_memory(MEMORY)
    return "Alarme réglée."

def tool_list_alarms(arg):
    with ALARM_LOCK: alarms = list(MEMORY.get('alarms', []))
    if not alarms: return "Aucune alarme."
    res = []
    for i, a in enumerate(alarms):
        if a['type'] == 'oneshot':
            dt = datetime.datetime.fromtimestamp(float(a['timestamp']))
            res.append(f"{i+1}: Une fois à {dt.strftime('%H:%M')}")
        else: res.append(f"{i+1}: Récurrente à {a['time']}")
    return " ; ".join(res)

def tool_delete_alarm(arg):
    arg = str(arg).lower()
    with ALARM_LOCK:
        alarms = MEMORY.get('alarms', [])
        if "tout" in arg: MEMORY['alarms'] = []; save_memory(MEMORY); return "Tout supprimé."
        
        # Par index
        nums = re.findall(r'\d+', arg)
        if nums and "h" not in arg:
            try:
                idx = int(nums[-1]) - 1
                if 0 <= idx < len(alarms):
                    alarms.pop(idx); save_memory(MEMORY); return f"Alarme {idx+1} supprimée."
            except: pass
            
        # Par heure
        m = re.search(r"(\d{1,2})\s*(?:[:h])\s*(\d{1,2})?", arg)
        if m:
            th, tm = int(m.group(1)), int(m.group(2) or 0)
            tstr = f"{th:02d}:{tm:02d}"
            kept = [a for a in alarms if (a.get('time') != tstr and 
                    datetime.datetime.fromtimestamp(a.get('timestamp',0)).strftime("%H:%M") != tstr)]
            if len(kept) < len(alarms):
                MEMORY['alarms'] = kept; save_memory(MEMORY); return f"Alarmes de {tstr} supprimées."
    return "Précisez quelle alarme supprimer."

def tool_set_alarm_sound(arg):
    arg = str(arg).lower()
    chosen = None
    if "douceur" in arg: chosen = "douceur"
    elif "alerte" in arg: chosen = "alerte"
    elif "gong" in arg: chosen = "gong"
    else: chosen = "classique"
    
    MEMORY['alarm_sound'] = chosen
    save_memory(MEMORY)
    if PLAY_ALARM_CALLBACK: threading.Thread(target=PLAY_ALARM_CALLBACK).start()
    return f"Sonnerie : {chosen}."

# --- AUDIO & MUSIQUE (AVANCÉ) ---

def get_active_audio_streams():
    """Récupère les applications qui font du bruit via pactl."""
    streams = []
    try:
        output = subprocess.check_output("pactl list sink-inputs", shell=True, stderr=subprocess.DEVNULL).decode()
        curr_id = None
        for line in output.split('\n'):
            line = line.strip()
            if line.startswith("Sink Input #"): curr_id = line.split("#")[1]
            elif "application.name = " in line and curr_id:
                name = line.split('"', 1)[1].strip('"')
                streams.append({"id": curr_id, "name": name})
                curr_id = None
    except: pass
    return streams

def classify_audio_with_llm(text_info):
    """Demande à l'IA locale si le titre ressemble à de la musique."""
    print(f"  [AI-AUDIO] Analyse : '{text_info}'", flush=True)
    
    # Config dynamique
    backend = MEMORY.get('llm_backend', 'llamacpp')
    url = MEMORY.get('llm_api_url', "http://localhost:8080/completion")
    model_name = MEMORY.get('llm_model_name', 'mistral-small')

    try:
        prompt = (f"Analyse ce titre : \"{text_info}\". Est-ce de la musique ? "
                  f"Réponds UNIQUEMENT par 'OUI' ou 'NON'.")
        
        if backend == 'ollama':
            payload = {
                "model": model_name,
                "prompt": prompt,
                "stream": False,
                "options": {"num_predict": 10, "temperature": 0.0, "stop": ["\n"]}
            }
        else:
            payload = {"prompt": prompt, "n_predict": 10, "temperature": 0.0, "stop": ["\n"]}
            
        res = requests.post(url, json=payload, timeout=5)
        
        if res.status_code == 200:
            data = res.json()
            if backend == 'ollama':
                ans = data.get('response', '').strip().upper()
            else:
                ans = data.get('content', '').strip().upper()
            return "OUI" in ans or "YES" in ans
            
    except: return None

def is_likely_music(player_name, meta_json):
    """Détermine si le média est de la musique (Heuristique + IA)."""
    player_name = player_name.lower()
    pure_music = ["spotify", "rhythmbox", "lollypop", "audacious", "deezer", "mpd"]
    if any(app in player_name for app in pure_music): return True

    title = meta_json.get("xesam:title", "").lower()
    non_music = ["tuto", "gameplay", "review", "vlog", "news", "cours", "recette"]
    if any(kw in title for kw in non_music): return False

    music_kw = ["official", "lyrics", " remix", " feat", "clip", "ost", "album"]
    if any(kw in title for kw in music_kw): return True
    
    # Appel IA si navigateur
    if any(b in player_name for b in ["chrome", "firefox", "brave"]):
        ai = classify_audio_with_llm(title)
        if ai is not None: return ai
        if " - " in title: return True
        
    return False

def tool_music_checkup(arg):
    """Vérifie l'environnement sonore. Si calme, lance favori. Enregistre un échantillon audio."""
    # Mode passif : Si "status" est demandé, on ne met rien en pause et on ne lance rien automatiquement
    is_passive_mode = "status" in str(arg).lower()
    
    status_msg = ""
    playing = False
    
    # === ENREGISTREMENT AUDIO (3 secondes) ===
    audio_recorded = False
    audio_filename = None
    
    if 'sc' in globals() and 'sf' in globals() and 'np' in globals():
        try:
            # Timestamp pour nom de fichier unique
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            audio_filename = f"/tmp/music_check_{timestamp}.wav"
            
            print(f"  [MUSIC_CHECK] Enregistrement audio 3s...", flush=True)
            
            sr_rate = 44100
            duration_sec = 3
            total_frames = duration_sec * sr_rate
            curr = 0
            all_data = []
            
            # Enregistrement du loopback audio (sortie système)
            with sc.get_microphone(id=str(sc.default_speaker().name), include_loopback=True).recorder(samplerate=sr_rate) as mic:
                while curr < total_frames:
                    if igor_config.ABORT_FLAG: 
                        break
                    chunk = mic.record(numframes=4096)
                    all_data.append(chunk)
                    curr += 4096
            
            # Sauvegarde du fichier WAV
            full = np.concatenate(all_data, axis=0)
            sf.write(audio_filename, full, sr_rate)
            audio_recorded = True
            print(f"  [MUSIC_CHECK] ✅ Audio sauvegardé: {audio_filename}", flush=True)
            
        except Exception as e:
            print(f"  [MUSIC_CHECK] ⚠️ Erreur enregistrement audio: {e}", flush=True)
    else:
        print(f"  [MUSIC_CHECK] ⚠️ Modules audio non disponibles (soundcard/soundfile/numpy)", flush=True)
    
    # === VÉRIFICATION PLAYERCTL ===
    if shutil.which("playerctl"):
        try:
            cmd = "playerctl -a metadata --format '{{playerName}};;;{{xesam:title}};;;{{xesam:artist}}'"
            out = subprocess.check_output(cmd, shell=True, stderr=subprocess.DEVNULL).decode().strip()
            if out:
                for line in out.split('\n'):
                    if ";;;" not in line: continue
                    parts = line.split(";;;")
                    if len(parts)<3: continue
                    
                    p, t, a = parts[0].strip(), parts[1].strip(), parts[2].strip()
                    meta = {"xesam:title": t, "xesam:artist": [a]}
                    
                    try:
                        pb = subprocess.check_output(f"playerctl -p {p} status", shell=True).decode().strip()
                        if pb == "Playing":
                            if is_likely_music(p, meta):
                                playing = True
                                audio_msg = f"\n🎵 Audio capturé: {audio_filename}" if audio_recorded else ""
                                return f"De la musique joue sur {p} ({t}).{audio_msg}"
                            else:
                                # MODIFICATION : On ne met en pause que si on n'est PAS en mode passif
                                if not is_passive_mode:
                                    subprocess.run(f"playerctl -p {p} pause", shell=True)
                                    status_msg += f"Pause de {p}. "
                                else:
                                    # En mode passif, on signale juste qu'une vidéo joue
                                    return f"Il y a une vidéo ou un média actif sur {p} ({t})."
                    except: pass
        except: pass

    # === PAS DE MUSIQUE DÉTECTÉE ===
    if not playing:
        # MODIFICATION : En mode passif, on ne lance rien
        if is_passive_mode:
            audio_msg = f"\n🎵 Audio capturé: {audio_filename}" if audio_recorded else ""
            return f"Tout est calme. Aucune musique détectée.{audio_msg}"

        fav = MEMORY.get('fav_music_app')
        audio_msg = f"\n🎵 Audio capturé: {audio_filename}" if audio_recorded else ""
        
        if fav:
            res = tool_launch(fav)
            return f"{status_msg}Tout est calme. Je lance : {fav} ({res}).{audio_msg}"
        return f"{status_msg}Tout est calme. Pas de favori défini.{audio_msg}"
    
    return status_msg

def tool_media_control(arg):
    if not shutil.which("playerctl"): return "Installez playerctl."
    arg = str(arg).lower()
    cmd = "play-pause"
    if any(x in arg for x in ["pause", "stop", "tais"]): cmd = "pause"
    elif any(x in arg for x in ["play", "lecture", "reprend"]): cmd = "play"
    # MODIFICATION : Ajout des mots-clés français pour la navigation
    elif any(x in arg for x in ["next", "suivant", "après", "avance"]): cmd = "next"
    elif any(x in arg for x in ["prev", "précédent", "avant", "arrière", "back", "retour"]): cmd = "previous"
    
    try:
        subprocess.call(f"playerctl -a {cmd}", shell=True)
        return f"Média : {cmd}"
    except Exception as e: return f"Erreur : {e}"

def tool_listen_system(arg):
    """Enregistre l'audio système et l'analyse (Gemini ou STT)."""
    if 'sc' not in globals(): return "Module soundcard manquant."
    
    try:
        # Analyse de la durée demandée
        dur = 10
        try: dur = int(re.search(r'\d+', str(arg)).group())
        except: pass
        
        fname = "/tmp/igor_system_audio.wav"
        print(f"  [LISTEN] Enregistrement {dur}s...", flush=True)
        
        # Enregistrement par blocs (Code inchangé pour la capture)
        sr_rate = 44100
        total_frames = dur * sr_rate
        curr = 0
        all_data = []
        
        with sc.get_microphone(id=str(sc.default_speaker().name), include_loopback=True).recorder(samplerate=sr_rate) as mic:
            while curr < total_frames:
                if igor_config.ABORT_FLAG: break
                chunk = mic.record(numframes=4096)
                all_data.append(chunk)
                curr += 4096
                
        full = np.concatenate(all_data, axis=0)
        sf.write(fname, full, sr_rate)
        
        # --- TENTATIVE 1 : ANALYSE INTELLIGENTE VIA GEMINI ---
        if GEMINI_AUDIO_AVAILABLE:
            print("  [LISTEN] Analyse audio via Gemini...", flush=True)
            try:
                # 1. Upload du fichier vers Google
                myfile = genai.upload_file(fname)
                
                # 2. Configuration du modèle (Flash est rapide et gère l'audio)
                model = genai.GenerativeModel("gemini-2.5-flash")
                
                # 3. Prompt contextuel
                prompt = "Écoute cet audio. S'il y a de la parole, transcris-la. Si c'est de la musique ou un bruit, décris ce que c'est brièvement en français."
                
                # 4. Génération
                result = model.generate_content([myfile, prompt])
                
                # 5. Nettoyage (optionnel mais recommandé pour ne pas saturer le stockage cloud temporaire)
                # myfile.delete() 
                
                if result.text:
                    return f"Analyse Audio : {result.text}"
            except Exception as e:
                print(f"  [LISTEN] Erreur Gemini ({e}), passage au mode classique.", flush=True)

        # --- TENTATIVE 2 : FALLBACK CLASSIQUE (Transcription seule) ---
        print("  [LISTEN] Transcription classique...", flush=True)
        rec = sr.Recognizer()
        with sr.AudioFile(fname) as src:
            aud = rec.record(src)
            try: return f"Entendu : \"{rec.recognize_google(aud, language='fr-FR')}\""
            except: return "Rien entendu (ou son non vocal)."
            
    except Exception as e: return f"Erreur technique : {e}"

# --- MATHS ---

def tool_calculate(arg):
    expression = str(arg).strip().lower()
    
    # Nettoyage et conversion langage naturel -> maths Python
    rep = {
        " fois ": "*", " divisé par ": "/", " plus ": "+", " moins ": "-", " égale ": "=", 
        ",": ".", 
        "% de ": "*0.01*",  # Ex: 15% de 200 -> 15*0.01*200
        "% ": "*0.01",      # Ex: 15% -> 15*0.01
        "%": "*0.01",
        " de ": "*"         # Ex: quart de 100 -> quart * 100
    }
    for k, v in rep.items(): expression = expression.replace(k, v)

    try:
        tr = (standard_transformations + (implicit_multiplication_application,))
        if "=" in expression:
            parts = expression.split("=")
            lhs = parse_expr(parts[0], transformations=tr)
            rhs = parse_expr(parts[1], transformations=tr)
            sol = solve(Eq(lhs, rhs), dict=True)
            return f"Solution : {sol}"
        else:
            expr = parse_expr(expression, transformations=tr)
            return f"Résultat : {expr.evalf() if not expr.free_symbols else expr.simplify()}"
    except Exception as e: return f"Erreur math : {e}"

# --- RACCOURCIS (HELPERS COMPLEXES) ---

def load_shortcuts():
    if not os.path.exists(SHORTCUTS_FILE): return {}
    try: return json.load(open(SHORTCUTS_FILE))
    except: return {}

def save_shortcuts(data):
    with open(SHORTCUTS_FILE, 'w') as f: json.dump(data, f, indent=4)

def _get_url_from_playerctl(require_playing=False):
    if not shutil.which("playerctl"): return None
    try:
        # On récupère la liste des lecteurs
        out = subprocess.check_output("playerctl -l", shell=True).decode()
        for p in out.splitlines():
            if not p.strip(): continue
            try:
                # FILTRE CRITIQUE : Si on demande 'playing', on vérifie le statut
                if require_playing:
                    status = subprocess.check_output(f"playerctl -p {p} status", shell=True).decode().strip().lower()
                    if "playing" not in status:
                        continue 

                u = subprocess.check_output(f"playerctl -p {p} metadata xesam:url", shell=True).decode().strip()
                t = subprocess.check_output(f"playerctl -p {p} metadata xesam:title", shell=True).decode().strip()
                
                # On ignore les URLs vides ou locales bizarres
                if "http" in u: 
                    return (u, t)
            except: continue
    except: pass
    return None

def _get_url_via_atspi():
    if not Atspi: return None
    try:
        d = Atspi.get_desktop(0)
        for i in range(d.get_child_count()):
            app = d.get_child_at_index(i)
            if not app: continue
            if any(b in app.get_name().lower() for b in ["firefox", "chrome", "brave", "edge"]):
                # Recherche récursive limitée
                def find(obj, depth=0):
                    if depth > 8: return None
                    try:
                        role = obj.get_role()
                        if role in [Atspi.Role.ENTRY, Atspi.Role.TEXT]:
                            txt = obj.get_text().get_text(0, -1).strip()
                            if "http" in txt or "www." in txt: return txt
                        for k in range(min(obj.get_child_count(), 50)):
                            res = find(obj.get_child_at_index(k), depth+1)
                            if res: return res
                    except: pass
                    return None
                
                for j in range(app.get_child_count()):
                    u = find(app.get_child_at_index(j))
                    if u: return "https://"+u if "://" not in u else u
    except: pass
    return None

def _get_url_from_history_forensics():
    import sqlite3
    temp = "/tmp/igor_hist.db"
    targets = [
        {"glob": os.path.join(USER_HOME, ".config", "*", "*", "History"), "sql": "SELECT url, title FROM urls ORDER BY last_visit_time DESC LIMIT 1"},
        {"glob": os.path.join(USER_HOME, ".mozilla", "firefox", "*", "places.sqlite"), "sql": "SELECT url, title FROM moz_places ORDER BY last_visit_date DESC LIMIT 1"}
    ]
    for t in targets:
        for db in glob.glob(t["glob"]):
            try:
                shutil.copy2(db, temp)
                conn = sqlite3.connect(temp)
                row = conn.cursor().execute(t["sql"]).fetchone()
                conn.close()
                if row: return row
            except: pass
    return None

def _get_url_via_clipboard_hack():
    if not shutil.which("xdotool"): return None
    try:
        if pyperclip: pyperclip.copy("VIDE")
        for k in ["ctrl+l", "alt+d", "F6"]:
            subprocess.call(["xdotool", "key", "--clearmodifiers", k])
            time.sleep(0.2)
            subprocess.call(["xdotool", "key", "--clearmodifiers", "ctrl+c"])
            time.sleep(0.2)
            c = pyperclip.paste().strip() if pyperclip else subprocess.check_output("xclip -o", shell=True).decode()
            if "http" in c or "www." in c: 
                subprocess.call(["xdotool", "key", "Escape"])
                return c
    except: pass
    return None

def _get_url_from_browser_session(window_id, window_class):
    """
    Extrait l'URL via l'historique récent (MÉTHODE FIABLE).
    """
    
    print(f"  [API] 🔍 Détection navigateur : '{window_class}'", flush=True)
    
    # === MÉTHODE CHROME : HISTORIQUE RÉCENT (GARANTI) ===
    if any(b in window_class for b in ["chrome", "chromium", "brave", "edge"]):
        print(f"  [API] 🎯 Chrome détecté", flush=True)
        
        try:
            import sqlite3
            
            chrome_dirs = [
                os.path.join(USER_HOME, ".config/google-chrome/Default"),
                os.path.join(USER_HOME, ".config/google-chrome/Profile 1"),
                os.path.join(USER_HOME, ".config/google-chrome/Profile 2"),
                os.path.join(USER_HOME, ".config/chromium/Default"),
                os.path.join(USER_HOME, ".config/BraveSoftware/Brave-Browser/Default"),
                os.path.join(USER_HOME, ".config/microsoft-edge/Default")
            ]
            
            for chrome_dir in chrome_dirs:
                # ✅ ON LIT L'HISTORIQUE (fichier SQLite standard)
                history_file = os.path.join(chrome_dir, "History")
                
                print(f"  [API] 📂 Test : {chrome_dir}", flush=True)
                
                if os.path.exists(history_file):
                    print(f"  [API] ✅ Fichier 'History' trouvé", flush=True)
                    
                    try:
                        # Copie temporaire (Chrome verrouille le fichier original)
                        temp_history = "/tmp/chrome_history_tmp.db"
                        shutil.copy2(history_file, temp_history)
                        
                        conn = sqlite3.connect(temp_history)
                        cursor = conn.cursor()
                        
                        # Requête : Les 10 URLs les plus récentes
                        cursor.execute("""
                            SELECT url, title, last_visit_time 
                            FROM urls 
                            ORDER BY last_visit_time DESC 
                            LIMIT 10
                        """)
                        
                        results = cursor.fetchall()
                        print(f"  [API] 📊 URLs récentes : {len(results)}", flush=True)
                        
                        if results:
                            # On affiche les résultats pour debug
                            for i, (url, title, timestamp) in enumerate(results):
                                print(f"  [API]   #{i+1}: {title[:40] if title else 'Sans titre'}", flush=True)
                                print(f"  [API]        {url[:80]}", flush=True)
                                
                                # Priorité aux médias
                                if any(k in url.lower() for k in ["youtube.com", "youtu.be", "twitch.tv", "netflix.com", "vimeo.com"]):
                                    conn.close()
                                    try:
                                        os.remove(temp_history)
                                    except:
                                        pass
                                    print(f"  [API] ✅ URL média trouvée : {url[:80]}", flush=True)
                                    return url
                            
                            # Si pas de média, on prend le premier (le plus récent)
                            url, title, _ = results[0]
                            conn.close()
                            try:
                                os.remove(temp_history)
                            except:
                                pass
                            
                            if url and "http" in url:
                                print(f"  [API] ✅ URL (plus récente) : {url[:80]}", flush=True)
                                return url
                        
                        conn.close()
                        try:
                            os.remove(temp_history)
                        except:
                            pass
                            
                    except Exception as e:
                        print(f"  [API] ⚠️ Erreur lecture History : {e}", flush=True)
                        
        except Exception as e:
            print(f"  [API] ❌ Erreur globale Chrome : {e}", flush=True)

def tool_shortcut_add(arg):
    """
    Ajoute un raccourci avec détection intelligente d'URL.
    """
    arg = str(arg).strip()
    
    # --- MODIFICATION : DÉTECTION D'URL EXPLICITE EN LANGAGE NATUREL ---
    # Transforme "mail vers gmail.com" en "mail :: https://gmail.com"
    # Cela empêche de scanner Youtube si l'utilisateur donne déjà l'URL.
    domain_match = re.search(r"^(.*?)\s+(?:vers|sur|pour|à)\s+([a-zA-Z0-9\-\.]+\.(?:com|fr|net|org|io|co|uk|ca|be|ch|info).*)$", arg, re.IGNORECASE)
    
    if domain_match and "::" not in arg:
        name_part = domain_match.group(1).strip()
        url_part = domain_match.group(2).strip()
        
        # Nettoyage des mots parasites du début ("crée un raccourci...")
        for parasite in ["crée", "ajoute", "nouveau", "raccourci", "un", "le", "mon"]:
             name_part = re.sub(f"^{parasite}\\s+", "", name_part, flags=re.IGNORECASE).strip()
        
        if not url_part.startswith("http"):
            url_part = "https://" + url_part
            
        print(f"  [SHORTCUT] 🎯 URL explicite détectée : {url_part}", flush=True)
        # On force le format "::" pour que le bloc suivant le traite comme manuel
        arg = f"{name_part} :: {url_part}"
    # -------------------------------------------------------------------

    data = load_shortcuts()
    
    # ========================================
    # 🧠 VÉRIFICATION INTELLIGENTE (NOUVEAU!)
    # ========================================
    # Si l'utilisateur dit "mets mon raccourci X" et que X existe déjà,
    # c'est probablement qu'il veut l'OUVRIR, pas le recréer
    
    if not "::" in arg:  # Pas un ajout manuel explicite avec URL
        # Extraire les mots-clés de la demande
        def normalize_for_matching(text):
            text = remove_accents(str(text)).lower()
            text = text.replace('-', ' ').replace('_', ' ')
            text = re.sub(r'[^\w\s]', ' ', text)
            text = re.sub(r'\s+', ' ', text).strip()
            return text
        
        def extract_keywords(text, min_length=3):
            words = normalize_for_matching(text).split()
            stop_words = {'mon', 'ma', 'mes', 'le', 'la', 'les', 'un', 'une', 'des', 'du', 'de', 'et', 'raccourci', 'lien', 'site'}
            return [w for w in words if len(w) >= min_length and w not in stop_words]
        
        # Chercher si un raccourci existe déjà avec ces mots-clés
        search_keywords = extract_keywords(arg)
        
        if search_keywords and data:
            print(f"  [SHORTCUT] 🔍 Vérification si '{arg}' existe déjà...", flush=True)
            
            # Calculer des scores comme dans tool_shortcut_open
            best_match = None
            best_score = 0.0
            
            for key in data.keys():
                key_normalized = normalize_for_matching(key)
                key_words = set(key_normalized.split())
                
                matches = 0
                for keyword in search_keywords:
                    if keyword in key_words:
                        matches += 2
                    elif any(keyword in word for word in key_words):
                        matches += 1
                
                score = matches / (len(search_keywords) * 2) if search_keywords else 0
                
                if score > best_score:
                    best_score = score
                    best_match = key
            
            # Si on a un bon match (≥60%), l'ouvrir au lieu de créer un doublon
            if best_match and best_score >= 0.6:
                print(f"  [SHORTCUT] 💡 Raccourci existant trouvé: '{best_match}' (score: {best_score:.0%})", flush=True)
                print(f"  [SHORTCUT] ➡️  Redirection vers SHORTCUT_OPEN", flush=True)
                
                # Appeler directement tool_shortcut_open
                from igor_knowledge import tool_shortcut_open
                return tool_shortcut_open(arg)
    
    # ========================================
    # Cas 1: Ajout manuel explicite (Nom :: URL)
    # ========================================
    if "::" in arg:
        parts = arg.split("::", 1)  # Limiter à 2 parties max
        original_name = parts[0].strip()
        url = parts[1].strip()
        
        # Nettoyer les guillemets potentiels
        url = url.strip('"').strip("'")
        
        # AMÉLIORATION: Stocker comme dict avec métadonnées
        normalized_key = remove_accents(original_name).lower()
        
        # Supprimer les espaces multiples
        normalized_key = re.sub(r'\s+', ' ', normalized_key).strip()
        
        data[normalized_key] = {
            "url": url,
            "original_name": original_name,
            "added": datetime.datetime.now().isoformat(),
            "source": "manual"
        }
        
        save_shortcuts(data)
        print(f"  [SHORTCUT] ✅ Ajouté: '{original_name}' (clé: '{normalized_key}') -> {url}", flush=True)
        return f"✅ Raccourci '{original_name}' ajouté."

    final_url = None
    final_title = None
    source_found = None
    
    # Sauvegarde du focus
    current_window_id = None
    try:
        current_window_id = subprocess.check_output(
            "xdotool getactivewindow",
            shell=True,
            stderr=subprocess.DEVNULL
        ).decode().strip()
        print(f"  [SHORTCUT] 💾 Focus sauvegardé : {current_window_id}", flush=True)
    except:
        pass

    # ========================================
    # 🎯 PRIORITÉ 0 : MÉDIAS (API natives)
    # ========================================
    print(f"  [SHORTCUT] 🔍 Phase 1 : Scan médias (API natives)...", flush=True)
    
    try:
        media_browsers = ["firefox", "chrome", "chromium", "brave", "microsoft-edge", "opera", "vivaldi"]
        
        all_windows = subprocess.check_output(
            "xdotool search --class '.*'",
            shell=True,
            stderr=subprocess.DEVNULL
        ).decode().strip().split('\n')
        
        for window_id in all_windows:
            if not window_id.strip() or source_found:
                continue
                
            try:
                xprop_output = subprocess.check_output(
                    f"xprop -id {window_id} WM_CLASS",
                    shell=True,
                    stderr=subprocess.DEVNULL
                ).decode().strip()
                classes = re.findall(r'"([^"]+)"', xprop_output)
                window_class = " ".join(classes).lower() if classes else ""
                
                try:
                    window_name = subprocess.check_output(
                        f"xdotool getwindowname {window_id}",
                        shell=True,
                        stderr=subprocess.DEVNULL
                    ).decode().strip()
                except:
                    window_name = ""
                
                # Skip Igor
                if "main.py" in window_class or "agent" in window_name.lower():
                    continue
                
                # Détection média
                media_keywords = ["youtube", "twitch", "netflix", "vimeo", "dailymotion", "watch", "video", "stream"]
                
                is_browser = any(b in window_class for b in media_browsers)
                is_media = any(k in window_name.lower() for k in media_keywords)
                
                if is_browser and is_media:
                    print(f"  [SHORTCUT] 🎬 MÉDIA : '{window_name[:60]}'", flush=True)
                    
                    # 🔥 LECTURE NATIVE (sans touches clavier)
                    url = _get_url_from_browser_session(window_id, window_class)
                    
                    if url:
                        # Validation stricte
                        is_valid = any(k in url.lower() for k in ["youtube.com", "youtu.be", "twitch.tv", "netflix.com", "vimeo.com", "dailymotion.com"])
                        
                        if is_valid:
                            final_url = url
                            final_title = window_name
                            source_found = "MEDIA_NATIVE_API"
                            print(f"  [SHORTCUT] ✅ URL capturée via API native", flush=True)
                            break
                        else:
                            print(f"  [SHORTCUT] ⚠️ URL trouvée mais pas un média connu: {url[:60]}", flush=True)
                    else:
                        print(f"  [SHORTCUT] ⚠️ Impossible de lire l'URL via API", flush=True)
                    
            except Exception as e:
                print(f"  [SHORTCUT] Erreur window scan: {e}", flush=True)
                continue
                
    except Exception as e:
        print(f"  [SHORTCUT] Erreur Phase 1: {e}", flush=True)

    # ========================================
    # 🎯 PRIORITÉ 1 : PLAYERCTL
    # ========================================
    if not source_found:
        print(f"  [SHORTCUT] 🔍 Phase 2 : Playerctl...", flush=True)
        res = _get_url_from_playerctl(require_playing=True)
        if res:
            final_url, final_title = res
            source_found = "PLAYERCTL_PLAYING"
            print(f"  [SHORTCUT] ✅ Playerctl OK", flush=True)

    # ========================================
    # 🎯 PRIORITÉ 2 : NAVIGATEUR STANDARD (API natives aussi)
    # ========================================
    if not source_found:
        print(f"  [SHORTCUT] 🔍 Phase 3 : Navigateurs standards (API)...", flush=True)
        try:
            stacking_output = subprocess.check_output(
                "xprop -root _NET_CLIENT_LIST_STACKING",
                shell=True
            ).decode()
            
            stacked_ids = re.findall(r'0x[0-9a-f]+', stacking_output)
            stacked_ids.reverse()
            
            for window_id in stacked_ids:
                if source_found:
                    break
                    
                try:
                    xprop_output = subprocess.check_output(
                        f"xprop -id {window_id} WM_CLASS",
                        shell=True,
                        stderr=subprocess.DEVNULL
                    ).decode().strip()
                    classes = re.findall(r'"([^"]+)"', xprop_output)
                    window_class = " ".join(classes).lower() if classes else ""
                    
                    try:
                        window_name = subprocess.check_output(
                            f"xdotool getwindowname {window_id}",
                            shell=True,
                            stderr=subprocess.DEVNULL
                        ).decode().strip()
                    except:
                        window_name = ""
                    
                    if "main.py" in window_class or "agent" in window_name.lower():
                        continue
                    
                    browser_patterns = ["firefox", "chrome", "chromium", "brave", "edge", "opera", "vivaldi"]
                    is_browser = any(p in window_class for p in browser_patterns)
                    
                    if is_browser:
                        print(f"  [SHORTCUT] 🌐 Nav : '{window_name[:40]}'", flush=True)
                        
                        # Lecture native
                        url = _get_url_from_browser_session(window_id, window_class)
                        
                        if url and "http" in url:
                            final_url = url
                            final_title = window_name
                            source_found = "BROWSER_NATIVE_API"
                            print(f"  [SHORTCUT] ✅ URL capturée", flush=True)
                            break
                            
                except:
                    continue
                    
        except Exception as e:
            print(f"  [SHORTCUT] Erreur Phase 3: {e}", flush=True)

    # ========================================
    # 🎯 PRIORITÉ 3 : PLAYERCTL PAUSE
    # ========================================
    if not source_found:
        print(f"  [SHORTCUT] 🔍 Phase 4 : Playerctl pause...", flush=True)
        res = _get_url_from_playerctl(require_playing=False)
        if res:
            final_url, final_title = res
            source_found = "PLAYERCTL_PAUSED"

    # ========================================
    # 🎯 PRIORITÉ 4 : HISTORIQUE
    # ========================================
    if not source_found:
        print(f"  [SHORTCUT] 🔍 Phase 5 : Historique...", flush=True)
        res = _get_url_from_history_forensics()
        if res:
            final_url, final_title = res
            source_found = "HISTORY"
    
    # Restauration du focus
    if current_window_id:
        try:
            subprocess.call(
                ["xdotool", "windowactivate", current_window_id],
                stderr=subprocess.DEVNULL
            )
            print(f"  [SHORTCUT] ✅ Focus restauré", flush=True)
        except:
            pass
        
    # ========================================
    # ENREGISTREMENT
    # ========================================
    if final_url and source_found:
        print(f"  [SHORTCUT] 💾 Enregistrement (source: {source_found})", flush=True)
        
        name = arg
        
        generic_words = ["raccourci", "site", "lien", "page", "cette", "ce", "sauvegarde", "comme"]
        if not name or any(w in remove_accents(name) for w in generic_words):
            if final_title:
                name = re.sub(r' - (YouTube|Google|Mozilla|Watch|Regarder).*', '', final_title).strip()[:30]
            else:
                try:
                    name = urllib.parse.urlparse(final_url).netloc.replace("www.", "")
                except:
                    name = "Favori"
        
        # AMÉLIORATION: Nouveau format de stockage
        normalized_key = remove_accents(name).lower()
        normalized_key = re.sub(r'\s+', ' ', normalized_key).strip()
        
        base = normalized_key
        key = base
        c = 1
        while key in data:
            key = f"{base}_{c}"
            c += 1
        
        data[key] = {
            "url": final_url,
            "original_name": name,
            "source": source_found,
            "added": datetime.datetime.now().isoformat()
        }
        save_shortcuts(data)
        
        return f"✅ Raccourci '{name}' ajouté.\n📍 Source: {source_found}\n🔗 {final_url}"
    
    return "❌ Aucune URL trouvée malgré les 5 phases."

def tool_shortcut_list(arg):
    d = load_shortcuts()
    if not d: return "Aucun raccourci enregistré."
    
    lines = [f"<b>Mes Raccourcis ({len(d)}) :</b>"]
    for key, val in d.items():
        # On récupère le nom d'affichage (support legacy string vs dict)
        display_name = val.get("original_name", key) if isinstance(val, dict) else key
        # On crée un lien cliquable avec le protocole custom shortcut://
        lines.append(f"• <a href='shortcut://{key}'>{display_name}</a>")
        
    return "\n".join(lines)

def tool_shortcut_delete(arg):
    k = remove_accents(str(arg))
    d = load_shortcuts()
    
    target = None
    for key in d:
        if remove_accents(key) == k or k in remove_accents(key):
            target = key; break
            
    if target:
        del d[target]
        save_shortcuts(d)
        return f"Raccourci '{target}' supprimé."
    return "Inconnu."

def tool_shortcut_open(arg):
    """
    Ouvre un raccourci sauvegardé.
    VERSION ULTRA-AVANCÉE avec matching par mots-clés multiples.
    """
    import shlex
    from difflib import get_close_matches
    
    def normalize_for_matching(text):
        """Normalise le texte pour la comparaison : sans accents, minuscules, tirets -> espaces"""
        text = remove_accents(str(text)).lower()
        text = text.replace('-', ' ').replace('_', ' ')  # Tirets et underscores = espaces
        text = re.sub(r'[^\w\s]', ' ', text)  # Enlever ponctuation
        text = re.sub(r'\s+', ' ', text).strip()
        return text
    
    def extract_keywords(text, min_length=3):
        """Extrait les mots-clés significatifs (longueur >= min_length)"""
        words = normalize_for_matching(text).split()
        # Filtrer les mots courts et les mots vides courants
        stop_words = {'mon', 'ma', 'mes', 'le', 'la', 'les', 'un', 'une', 'des', 'du', 'de', 'et'}
        return [w for w in words if len(w) >= min_length and w not in stop_words]
    
    def keyword_match_score(search_keywords, target_text):
        """Calcule un score de matching basé sur les mots-clés trouvés"""
        target_normalized = normalize_for_matching(target_text)
        target_words = set(target_normalized.split())
        
        matches = 0
        for keyword in search_keywords:
            # Match exact du mot
            if keyword in target_words:
                matches += 2  # Poids fort
            # Match partiel (le mot cible contient le keyword)
            elif any(keyword in word for word in target_words):
                matches += 1  # Poids moyen
        
        # Score normalisé (0.0 à 1.0)
        if len(search_keywords) == 0:
            return 0.0
        return matches / (len(search_keywords) * 2)
    
    # Normaliser la recherche
    raw_input = str(arg).strip()
    search_normalized = normalize_for_matching(raw_input)
    search_keywords = extract_keywords(raw_input)
    
    if not search_normalized:
        return "❌ Nom de raccourci vide."
    
    shortcuts = load_shortcuts()
    
    if not shortcuts:
        return "❌ Aucun raccourci enregistré.\nUtilisez: 'ajoute un raccourci nom :: url'"
    
    # DEBUG: Afficher ce qu'on cherche
    print(f"  [SHORTCUT] 🔍 Recherche: '{search_normalized}'", flush=True)
    print(f"  [SHORTCUT] 🎯 Mots-clés: {search_keywords}", flush=True)
    print(f"  [SHORTCUT] 📋 Disponibles ({len(shortcuts)}): {list(shortcuts.keys())[:10]}", flush=True)
    
    target_url = None
    target_name = None
    matched_key = None
    best_score = 0.0
    match_method = ""
    
    # ========================================
    # ÉTAPE 1: Correspondance EXACTE
    # ========================================
    if search_normalized in [normalize_for_matching(k) for k in shortcuts.keys()]:
        for key in shortcuts.keys():
            if normalize_for_matching(key) == search_normalized:
                matched_key = key
                match_method = "exacte"
                print(f"  [SHORTCUT] ✓ Correspondance exacte", flush=True)
                break
    
    # ========================================
    # ÉTAPE 2: Matching PAR MOTS-CLÉS (NOUVEAU!)
    # ========================================
    if not matched_key and search_keywords:
        candidates = []
        
        for key in shortcuts.keys():
            score = keyword_match_score(search_keywords, key)
            if score > 0:
                candidates.append((key, score))
                print(f"  [SHORTCUT] 📊 '{key[:40]}...' score: {score:.2f}", flush=True)
        
        # Trier par score décroissant
        candidates.sort(key=lambda x: x[1], reverse=True)
        
        # Prendre le meilleur si score > 0.4 (au moins 40% des mots matchent)
        if candidates and candidates[0][1] >= 0.4:
            matched_key = candidates[0][0]
            best_score = candidates[0][1]
            match_method = f"mots-clés ({best_score*100:.0f}%)"
            print(f"  [SHORTCUT] 🎯 Meilleur match par mots-clés: '{matched_key}' ({best_score:.2%})", flush=True)
    
    # ========================================
    # ÉTAPE 3: Fuzzy matching (tolérant aux typos)
    # ========================================
    if not matched_key:
        all_keys_normalized = [normalize_for_matching(k) for k in shortcuts.keys()]
        matches = get_close_matches(search_normalized, all_keys_normalized, n=1, cutoff=0.6)
        
        if matches:
            # Retrouver la clé originale
            for key in shortcuts.keys():
                if normalize_for_matching(key) == matches[0]:
                    matched_key = key
                    match_method = "fuzzy"
                    print(f"  [SHORTCUT] ≈ Correspondance floue: '{matched_key}'", flush=True)
                    break
    
    # ========================================
    # ÉTAPE 4: Correspondance PARTIELLE (substring)
    # ========================================
    if not matched_key:
        for key in shortcuts.keys():
            key_normalized = normalize_for_matching(key)
            if search_normalized in key_normalized:
                matched_key = key
                match_method = "substring"
                print(f"  [SHORTCUT] ⊂ Correspondance partielle", flush=True)
                break
    
    # ========================================
    # Si TOUJOURS pas trouvé: message d'erreur utile
    # ========================================
    if not matched_key:
        all_names = []
        for key, val in shortcuts.items():
            if isinstance(val, dict):
                all_names.append(val.get('original_name', key))
            else:
                all_names.append(key)
        
        available = ", ".join(f"'{n}'" for n in all_names[:5])
        if len(all_names) > 5:
            available += f" (et {len(all_names)-5} autres)"
        
        # Suggestions basées sur les mots-clés
        suggestions = []
        if search_keywords:
            for key in list(shortcuts.keys())[:10]:
                key_normalized = normalize_for_matching(key)
                for kw in search_keywords:
                    if kw in key_normalized:
                        suggestions.append(key)
                        break
        
        error_msg = f"❌ Raccourci '{raw_input}' introuvable.\n\n📋 Disponibles:\n{available}"
        if suggestions:
            error_msg += f"\n\n💡 Suggestions:\n" + "\n".join(f"   • {s}" for s in suggestions[:3])
        error_msg += "\n\nUtilisez 'liste mes raccourcis' pour voir tous."
        
        return error_msg
    
    # ========================================
    # Extraire URL (support anciens formats)
    # ========================================
    shortcut_data = shortcuts[matched_key]
    
    if isinstance(shortcut_data, dict):
        target_url = shortcut_data.get("url")
        target_name = shortcut_data.get("original_name", matched_key)
    else:
        # Format legacy (string directe - ancien système)
        target_url = shortcut_data
        target_name = matched_key.title()
    
    if not target_url:
        return f"❌ URL invalide pour le raccourci '{target_name}'"
    
    # ========================================
    # Ouverture avec le navigateur favori
    # ========================================
    fav_browser = MEMORY.get('fav_browser')
    
    if not fav_browser:
        return "❌ Navigateur favori non défini.\n💡 Dites: 'définis chrome comme navigateur' ou 'définis firefox comme navigateur'"
    
    try:
        print(f"  [SHORTCUT] 🌐 Ouverture: {target_url[:60]}...", flush=True)
        print(f"  [SHORTCUT] 🌐 Navigateur: {fav_browser}", flush=True)
        
        # Parser la commande du navigateur
        browser_parts = shlex.split(fav_browser)
        full_cmd = browser_parts + [target_url]
        
        # Lancer en arrière-plan
        subprocess.Popen(
            full_cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True  # Détacher complètement du processus parent
        )
        
        print(f"  [SHORTCUT] ✅ Commande lancée avec succès", flush=True)
        return f"✅ Ouverture de '{target_name}'"
        
    except Exception as e:
        print(f"  [SHORTCUT] ❌ Erreur subprocess: {e}", flush=True)
        return f"❌ Erreur d'ouverture: {e}\n\nVérifiez que le navigateur '{fav_browser}' est installé."