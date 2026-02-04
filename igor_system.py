# igor_system.py
import os
import glob
import shutil
import json
import subprocess
import re
import datetime
import urllib.parse
import shlex
import time
import requests
import stat
import threading
from gi.repository import GLib
from difflib import SequenceMatcher
from collections import Counter

# Import des configurations et variables partagées
import igor_config
from igor_config import (
    MEMORY, 
    save_memory, 
    smart_summarize, 
    PROJECTS_DIR, 
    USER_HOME, 
    TASK_QUEUE
)

# --- GESTION DES APPLICATIONS (SCAN SYSTEME) ---
INSTALLED_APPS = {} 

# Dictionnaire inverse : Commande → Métadonnées
APP_METADATA = {}  # Format: {cmd: {"names": [...], "categories": [...], "class": "..."}}

def scan_system_apps():
    """
    Scanne les fichiers .desktop avec classification XDG complète.
    Construit à la fois INSTALLED_APPS (recherche) et APP_METADATA (reverse lookup).
    """
    print("  [INIT] Scan des applications (Classification XDG)...", flush=True)
    global INSTALLED_APPS, APP_METADATA
    apps = {}
    metadata = {}
    
    # MAPPING ÉTENDU : Catégorie XDG → Mots-clés français
    CATEGORY_MAP = {
        "Calculator": ["calculatrice", "calc", "maths", "calculette"],
        "WebBrowser": ["internet", "web", "navigateur", "browser", "site"],
        "TextEditor": ["texte", "editeur", "notes", "notepad", "bloc-notes"],
        "FileManager": ["fichiers", "dossiers", "explorateur"],
        "InstantMessaging": ["chat", "message", "messagerie", "sms"],
        "Email": ["mail", "email", "courriel", "boite"],
        "Audio": ["musique", "audio", "son"],
        "Video": ["video", "film"],
        "Player": ["lecteur"], 
        "Game": ["jeu", "jeux"],
        "TerminalEmulator": ["terminal", "console", "shell", "bash"],
        "Development": ["code", "dev", "programmation"],
        "Office": ["bureau", "travail"],
        "Spreadsheet": ["tableur", "excel", "feuille"],
        "Presentation": ["diaporama", "slide", "powerpoint"]
    }

    paths = [
        "/usr/share/applications/**/*.desktop",
        "/usr/local/share/applications/**/*.desktop",
        os.path.expanduser("~/.local/share/applications/**/*.desktop"),
        "/var/lib/flatpak/exports/share/applications/**/*.desktop",
        "/snap/bin/*.desktop"
    ]
    
    for path in paths:
        for filename in glob.glob(path, recursive=True):
            try:
                entry = {
                    'categories': [], 
                    'keywords': [], 
                    'names': [],
                    'class': None  # NOUVEAU : WM_CLASS pour le matching fenêtre
                }
                
                with open(filename, 'r', errors='ignore') as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith("[Desktop Action"): break 
                        
                        # 1. Commande (Exec)
                        if line.startswith("Exec="): 
                            raw = line.split("=", 1)[1]
                            cmd_clean = re.sub(r' %[a-zA-Z].*', '', raw).split(' --')[0]
                            entry['exec'] = cmd_clean.strip().replace('"', '')
                        
                        # 2. Noms (Name & GenericName)
                        elif line.startswith("Name=") or line.startswith("Name[fr]="): 
                            val = line.split("=", 1)[1].lower()
                            entry['names'].append(val)
                        elif line.startswith("GenericName=") or line.startswith("GenericName[fr]="):
                            val = line.split("=", 1)[1].lower()
                            entry['names'].append(val)
                            
                        # 3. Catégories (Le cœur de la classification)
                        elif line.startswith("Categories="):
                            cats = line.split("=", 1)[1].split(";")
                            entry['categories'].extend([c.strip() for c in cats if c.strip()])

                        # 4. Mots clés manuels du fichier
                        elif "eywords=" in line: 
                            kws = line.split("=", 1)[1].lower().split(';')
                            entry['keywords'].extend([k.strip() for k in kws if k.strip()])
                        
                        # 5. NOUVEAU : StartupWMClass (pour le reverse matching)
                        elif line.startswith("StartupWMClass="):
                            entry['class'] = line.split("=", 1)[1].strip().lower()

                if 'exec' in entry and entry['names']:
                    cmd = entry['exec']
                    
                    # A. Indexation par NOMS
                    for name in entry['names']:
                        apps[name] = cmd
                        # Indexation par mots individuels
                        for word in name.split():
                            if len(word) > 2: apps[word] = cmd

                    # B. Indexation par CATEGORIES
                    for cat in entry['categories']:
                        if cat in CATEGORY_MAP:
                            aliases = CATEGORY_MAP[cat]
                            for alias in aliases:
                                apps[alias] = cmd
                    
                    # C. Indexation par Mots Clés
                    for kw in entry['keywords']:
                        apps[kw] = cmd
                    
                    # D. NOUVEAU : Stockage des métadonnées (pour reverse lookup)
                    if cmd not in metadata:
                        metadata[cmd] = {
                            "names": entry['names'],
                            "categories": entry['categories'],
                            "class": entry['class'],
                            "keywords": entry['keywords']
                        }

            except Exception: pass
            
    INSTALLED_APPS = apps
    APP_METADATA = metadata
    
    # Debug
    debug_keys = ["calculatrice", "web", "texte"]
    found_info = [f"{k}: {'OK' if k in apps else 'NON'}" for k in debug_keys]
    print(f"  [INIT] Apps chargées : {len(INSTALLED_APPS)} clés. ({', '.join(found_info)})", flush=True)
    print(f"  [INIT] Métadonnées : {len(APP_METADATA)} commandes uniques.", flush=True)

# Lancement immédiat au chargement du module
scan_system_apps()

# === CLASSIFICATION DES FENÊTRES ===

def classify_window(window_title, window_class=None):
    """
    Détermine la catégorie d'une fenêtre en se basant sur :
    1. Son WM_CLASS (si disponible)
    2. Son titre (fallback)
    
    Retourne: (cmd_matched, app_name, categories)
    """
    title_lower = window_title.lower()
    
    # Méthode 1 : Match par WM_CLASS (le plus fiable)
    if window_class:
        class_lower = window_class.lower()
        for cmd, meta in APP_METADATA.items():
            if meta['class'] and meta['class'] == class_lower:
                return (cmd, meta['names'][0] if meta['names'] else cmd, meta['categories'])
    
    # Méthode 2 : Match par Titre
    # On cherche d'abord une correspondance exacte avec un nom d'app
    for cmd, meta in APP_METADATA.items():
        for name in meta['names']:
            if name in title_lower:
                return (cmd, name, meta['categories'])
    
    # Méthode 3 : Heuristique basique (si aucun match .desktop)
    # Utile pour les apps non-standard ou les terminaux
    heuristic_categories = []
    
    if any(k in title_lower for k in ["terminal", "console", "bash", "zsh"]):
        heuristic_categories.append("TerminalEmulator")
    elif any(k in title_lower for k in ["firefox", "chrome", "brave", "edge"]):
        heuristic_categories.append("WebBrowser")
    elif any(k in title_lower for k in ["files", "fichiers", "nautilus", "dolphin"]):
        heuristic_categories.append("FileManager")
    
    return (None, window_title, heuristic_categories)

# --- SHELL & STATISTIQUES ---

def bash_exec(command):
    command = command.strip()
    if command.startswith('"') and command.endswith('"'): command = command[1:-1]
    elif command.startswith("'") and command.endswith("'"): command = command[1:-1]
    
    # Sécurité basique
    if any(d in command for d in ["rm ", "mkfs", ":(){", "> /dev/sda"]): return "BLOCKED."
    
    try:
        process = subprocess.Popen(
            command, 
            shell=True, 
            stdout=subprocess.PIPE, 
            stderr=subprocess.STDOUT
        )
        out, _ = process.communicate(timeout=10)
        output = out.decode('utf-8', errors='replace').strip()
        output = output.replace('\x00', '')
        
        MAX_LEN = 2000
        if len(output) > MAX_LEN:
             return f"Sortie tronquée ({len(output)} car.) :\n{output[:MAX_LEN]}...\n(Suite ignorée)"
        
        return output if output else "Commande exécutée (Sortie vide)."
        
    except subprocess.TimeoutExpired:
        process.kill()
        return "ERR: Timeout commande."
    except Exception as e: 
        return f"ERR: {e}"

def tool_system_stats(arg):
    """
    Affiche les statistiques système avec un formatage élégant.
    """
    try:
        stats_lines = []
        
        # === 1. RAM ===
        cmd_ram = "free -h | grep Mem | awk '{print $3 \"/\" $2 \" (\" int($3/$2*100) \"%)\" }'"
        ram_usage = subprocess.check_output(cmd_ram, shell=True).decode().strip()
        stats_lines.append(f"💾 **RAM** : {ram_usage}")
        
        # === 2. UPTIME ===
        uptime_raw = subprocess.check_output("uptime -p", shell=True).decode().strip()
        # Nettoyage : "up 1 week, 3 hours" → "1 semaine, 3h"
        uptime_clean = (uptime_raw
                       .replace("up ", "")
                       .replace(" week", " sem")
                       .replace(" day", " jour")
                       .replace(" hour", "h")
                       .replace(" minute", "min")
                       .replace("s", ""))
        stats_lines.append(f"⏱️ **Uptime** : {uptime_clean}")
        
        # === 3. DISQUE (Racine) ===
        cmd_disk = "df -h / | tail -1 | awk '{print $4 \" libre / \" $2 \" (\" $5 \" utilisé)\"}'"
        disk_info = subprocess.check_output(cmd_disk, shell=True).decode().strip()
        stats_lines.append(f"💿 **Disque** : {disk_info}")
        
        # === 4. CPU (Charge moyenne) ===
        try:
            cmd_load = "uptime | awk -F'load average:' '{print $2}' | awk '{print $1}'"
            load_avg = subprocess.check_output(cmd_load, shell=True).decode().strip()
            # Enlève la virgule finale si présente
            load_1min = load_avg.rstrip(',').strip()
            stats_lines.append(f"⚡ **CPU Load (1min)** : {load_1min}")
        except:
            pass
        
        # === 5. TEMPÉRATURE CPU (si disponible) ===
        try:
            # Essai 1 : sensors (le plus courant)
            cmd_temp = "sensors 2>/dev/null | grep -E 'Package id 0:|Tdie:|temp1:' | head -1 | awk '{print $3}'"
            temp = subprocess.check_output(cmd_temp, shell=True).decode().strip()
            if temp and temp != '+0.0°C':
                stats_lines.append(f"🌡️ **CPU Temp** : {temp}")
        except:
            pass
        
        # === 6. PROCESSUS ACTIFS ===
        try:
            cmd_procs = "ps aux --no-headers | wc -l"
            proc_count = subprocess.check_output(cmd_procs, shell=True).decode().strip()
            stats_lines.append(f"🔄 **Processus** : {proc_count}")
        except:
            pass
        
        # === ASSEMBLAGE FINAL ===
        result = "📊 **Rapport Système**\n\n" + "\n".join(stats_lines)
        
        return result

    except Exception as e:
        return f"❌ Erreur lecture stats : {e}"

# --- GESTION DES PROJETS ---

def _resolve_project_context(explicit_name=None):
    """
    Détermine le projet cible :
    1. Si 'explicit_name' est fourni, on l'utilise.
    2. Sinon, on utilise MEMORY['current_project'].
    """
    if explicit_name and str(explicit_name).strip().lower() not in ["", "none", "null"]:
        return str(explicit_name).strip(), None
        
    current = MEMORY.get('current_project')
    if current:
        return current, None
        
    return None, "Aucun projet spécifié et aucun projet actif en cours."

def tool_project_set_active(arg):
    proj_name = str(arg).strip()
    safe_proj = re.sub(r'[^\w\-\. ]', '_', proj_name)
    path = os.path.join(PROJECTS_DIR, safe_proj)
    
    if not os.path.exists(path):
        return f"Le projet '{safe_proj}' n'existe pas."
        
    MEMORY['current_project'] = safe_proj
    save_memory(MEMORY)
    return f"Projet actif basculé sur : {safe_proj}"

def tool_project_new(arg):
    raw_name = str(arg).split('::')[0].strip()
    safe_name = re.sub(r'[^\w\-\. ]', '_', raw_name).strip()
    
    if not safe_name: 
        return "Nom de projet invalide."
        
    path = os.path.join(PROJECTS_DIR, safe_name)
    
    if os.path.exists(path):
        return f"Le projet '{safe_name}' existe déjà."
        
    try:
        os.makedirs(path)
        MEMORY['current_project'] = safe_name
        save_memory(MEMORY)
        print(f"  [PROJ] Création dossier : {path}", flush=True)
        return f"Projet '{safe_name}' créé et défini comme projet actif."
    except Exception as e:
        return f"Erreur création projet: {e}"

def tool_project_list(arg):
    try:
        items = [d for d in os.listdir(PROJECTS_DIR) if os.path.isdir(os.path.join(PROJECTS_DIR, d))]
        if not items:
            return "Aucun projet en cours."
        return "Projets existants : " + ", ".join(items)
    except Exception as e:
        return f"Erreur lecture projets: {e}"

def tool_project_save_file(arg):
    arg_str = str(arg)
    parts = arg_str.split("::")
    
    proj_name = None
    file_name = None
    content = ""

    if len(parts) >= 3:
        proj_name = parts[0].strip()
        file_name = parts[1].strip()
        content = "::".join(parts[2:]).strip()
    elif len(parts) == 2:
        part_a = parts[0].strip()
        part_b = parts[1].strip()
        
        safe_a = re.sub(r'[^\w\-\. ]', '_', part_a)
        potential_path = os.path.join(PROJECTS_DIR, safe_a)
        
        if os.path.exists(potential_path) and os.path.isdir(potential_path):
            proj_name = part_a
            file_name = part_b
            content = ""
        else:
            proj_name = None
            file_name = part_a
            content = part_b
    elif len(parts) == 1:
        proj_name = None
        file_name = parts[0].strip()
        content = ""
    else:
        return "Erreur format. Donnez au moins 'Fichier :: Contenu'."

    target_proj, error = _resolve_project_context(proj_name)
    if error:
        return f"Erreur : {error}"

    safe_proj = re.sub(r'[^\w\-\. ]', '_', target_proj)
    safe_file = re.sub(r'[^\w\-\. ]', '_', file_name)
    
    if not safe_file:
        return "Erreur : Le nom du fichier est vide ou invalide."

    proj_path = os.path.join(PROJECTS_DIR, safe_proj)
    file_path = os.path.join(proj_path, safe_file)
    
    if not os.path.exists(proj_path):
        try:
            os.makedirs(proj_path)
            print(f"  [AUTO-CREATE] Dossier projet créé : {safe_proj}")
        except Exception as e:
            return f"Impossible de créer le dossier projet : {e}"
    
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)
        status = "vide" if not content else "avec contenu"
        return f"✓ Fichier '{safe_file}' sauvegardé ({status}) dans le projet '{safe_proj}'."
    except IsADirectoryError:
        return f"Erreur critique : '{safe_file}' est interprété comme un dossier. Ajoutez une extension."
    except Exception as e:
        return f"Erreur écriture : {e}"

def tool_project_read_files(arg):
    target_proj, error = _resolve_project_context(str(arg))
    if error:
        return f"Erreur : {error}"
        
    safe_proj = re.sub(r'[^\w\-\. ]', '_', target_proj)
    path = os.path.join(PROJECTS_DIR, safe_proj)
    
    if not os.path.exists(path):
        return f"Le projet '{safe_proj}' est introuvable."
        
    try:
        files = os.listdir(path)
        visible_files = [f for f in files if not f.startswith('.')]
        
        if not visible_files: 
            return f"Le projet '{safe_proj}' est vide."
        
        visible_files.sort()
        file_list = ", ".join(visible_files)
        
        return smart_summarize(
            f"Fichiers dans le projet '{safe_proj}' : {file_list}", 
            source_name="liste de fichiers"
        )
        
    except Exception as e:
        return f"Erreur lors de la lecture du projet : {e}"

def tool_project_delete(arg):
    proj_name = str(arg).strip()
    safe_proj = re.sub(r'[^\w\-\. ]', '_', proj_name)
    path = os.path.join(PROJECTS_DIR, safe_proj)
    
    if not os.path.exists(path):
        return f"Le projet '{safe_proj}' n'existe pas."
        
    try:
        shutil.rmtree(path)
        print(f"  [PROJ] Projet supprimé : {path}", flush=True)
        return f"Le projet '{safe_proj}' a été supprimé définitivement."
    except Exception as e:
        return f"Erreur lors de la suppression du projet : {e}"

def tool_project_delete_file(arg):
    arg_str = str(arg)
    parts = arg_str.split("::")
            
    proj_name = parts[0].strip()
    file_name = parts[1].strip()
    
    safe_proj = re.sub(r'[^\w\-\. ]', '_', proj_name)
    safe_file = re.sub(r'[^\w\-\. ]', '_', file_name)
    
    file_path = os.path.join(PROJECTS_DIR, safe_proj, safe_file)
    
    if not os.path.exists(file_path):
        return f"Le fichier '{safe_file}' est introuvable dans le projet '{safe_proj}'."
        
    try:
        os.remove(file_path)
        print(f"  [PROJ] Suppression fichier : {file_path}", flush=True)
        return f"Le fichier '{safe_file}' a été supprimé du projet '{safe_proj}'."
    except Exception as e:
        return f"Erreur lors de la suppression du fichier : {e}"

def _get_project_todo_path(proj_name):
    safe_proj = re.sub(r'[^\w\-\. ]', '_', proj_name.strip())
    path = os.path.join(PROJECTS_DIR, safe_proj)
    if not os.path.exists(path):
        return None, f"Le projet '{safe_proj}' n'existe pas."
    return os.path.join(path, "todo.json"), None

def tool_project_todo_add(arg):
    arg_str = str(arg)
    parts = arg_str.split("::")
    
    proj_name = None
    task_desc = ""
    
    if len(parts) >= 2:
        proj_name = parts[0].strip()
        task_desc = "::".join(parts[1:]).strip()
    else:
        proj_name = None
        task_desc = parts[0].strip()
        
    if not task_desc:
        return "Erreur : La description de la tâche est vide."
        
    target_proj, error = _resolve_project_context(proj_name)
    if error: return f"Erreur : {error}"
    
    file_path, error = _get_project_todo_path(target_proj)
    if error: return error
    
    tasks = []
    if os.path.exists(file_path):
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                tasks = json.load(f)
        except: pass 
        
    new_task = {
        "desc": task_desc, 
        "done": False, 
        "date": datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    }
    tasks.append(new_task)
    
    try:
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(tasks, f, indent=4, ensure_ascii=False)
        return f"Tâche ajoutée au projet '{target_proj}' : {task_desc}"
    except Exception as e:
        return f"Erreur écriture ToDo : {e}"

def tool_project_todo_list(arg):
    target_proj, error = _resolve_project_context(str(arg))
    if error: return f"Erreur : {error}"
    
    file_path, error = _get_project_todo_path(target_proj)
    if error: return error
    
    if not os.path.exists(file_path):
        return f"Aucune To-Do list n'a été créée pour le projet '{target_proj}'."
        
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            tasks = json.load(f)
            
        if not tasks: return f"La To-Do list du projet '{target_proj}' est vide."
        
        res = [f"To-Do '{target_proj}' :"]
        todo_count = 0
        
        for i, t in enumerate(tasks):
            is_done = t.get("done", False)
            status = "✅" if is_done else "⬜"
            desc = t.get('desc', 'Sans description')
            if not is_done: todo_count += 1
            res.append(f"{i+1}. {status} {desc}")
            
        res.append(f"\n(Reste à faire : {todo_count} sur {len(tasks)})")
        return "\n".join(res)
        
    except Exception as e:
        return f"Erreur lecture ToDo : {e}"

def tool_project_todo_done(arg):
    arg_str = str(arg)
    parts = arg_str.split("::")
    
    proj_name = None
    idx_str = "0"
    
    if len(parts) >= 2:
        proj_name = parts[0].strip()
        idx_str = parts[1].strip()
    else:
        proj_name = None
        idx_str = parts[0].strip()

    target_proj, error = _resolve_project_context(proj_name)
    if error: return f"Erreur : {error}"

    try:
        nums = re.findall(r'\d+', idx_str)
        if not nums: return "Le numéro de la tâche doit être un chiffre."
        idx = int(nums[0]) - 1
    except ValueError:
        return "Le numéro de la tâche est invalide."

    file_path, error = _get_project_todo_path(target_proj)
    if error: return error
    
    if not os.path.exists(file_path):
        return f"Aucune liste de tâches n'existe pour le projet '{target_proj}'."
        
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            tasks = json.load(f)
            
        if 0 <= idx < len(tasks):
            desc = tasks[idx].get('desc', 'Tâche sans nom')
            if tasks[idx].get('done', False):
                return f"La tâche {idx+1} ('{desc}') est déjà marquée comme terminée."
            
            tasks[idx]['done'] = True
            
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(tasks, f, indent=4, ensure_ascii=False)
            return f"✓ Tâche {idx+1} validée dans '{target_proj}' : {desc}"
        else:
            return f"Numéro de tâche invalide. Le projet '{target_proj}' a {len(tasks)} tâches."
    except Exception as e:
        return f"Erreur lors de la validation de la tâche : {e}"

def tool_project_display_current(arg):
    current = MEMORY.get('current_project')
    if current:
        return f"Le projet actif est actuellement : {current}"
    return "Il n'y a aucun projet actif pour le moment."

def tool_project_change_current(arg):
    proj_name = str(arg).strip()
    if not proj_name:
        return "Veuillez préciser le nom du projet à activer."

    safe_proj = re.sub(r'[^\w\-\. ]', '_', proj_name)
    path = os.path.join(PROJECTS_DIR, safe_proj)
    
    if not os.path.exists(path):
        return f"Le projet '{safe_proj}' n'existe pas. Utilisez PROJECT_NEW pour le créer."
        
    MEMORY['current_project'] = safe_proj
    save_memory(MEMORY)
    return f"Focus changé. Vous travaillez maintenant sur le projet : {safe_proj}"

# --- GESTION FENÊTRES, APPS ET FICHIERS ---

def get_window_geometry(name_query):
    """
    Retourne (x, y, w, h, title) de la première fenêtre correspondant au nom.
    Nécessite 'wmctrl'.
    """
    if not shutil.which("wmctrl"):
        return None
        
    try:
        # -lG : Liste avec Géométrie (ID, Gravity, X, Y, W, H, Host, Title)
        out = subprocess.check_output("wmctrl -lG", shell=True).decode('utf-8')
        name_query = name_query.lower()
        
        for line in out.splitlines():
            parts = line.split(None, 7)
            if len(parts) >= 8:
                # Indices: 2=X, 3=Y, 4=W, 5=H, 7=Titre
                title = parts[7].lower()
                if name_query in title:
                    return (int(parts[2]), int(parts[3]), int(parts[4]), int(parts[5]), parts[7])
    except: pass
    return None

def tool_launch(arg):
    # --- 0. INTERCEPTION COMMANDES SYSTÈME (LIENS CLIQUABLES) ---
    # Permet d'ouvrir les fichiers avec espaces/caractères spéciaux via xdg-open
    raw_arg = str(arg).strip()
    
    if raw_arg.startswith("xdg-open "):
        print(f"  [LAUNCH] Exécution directe shell : {raw_arg}", flush=True)
        try:
            # --- FIX CRITIQUE : NETTOYAGE ENVIRONNEMENT ---
            # On retire les variables Python/GTK/Qt qui font crasher les lecteurs vidéo externes
            clean_env = os.environ.copy()
            keys_to_remove = ['QT_QPA_PLATFORM_PLUGIN_PATH', 'LD_LIBRARY_PATH', 'PYTHONPATH']
            for key in keys_to_remove:
                clean_env.pop(key, None)

            # shell=True permet de gérer les quotes ('') ajoutées par shlex
            subprocess.Popen(
                raw_arg, 
                shell=True,
                env=clean_env, # <--- ON INJECTE L'ENVIRONNEMENT PROPRE
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, 
                stderr=subprocess.DEVNULL,
                start_new_session=True
            )
            return f"Ouverture du fichier demandée."
        except Exception as e:
            return f"Erreur ouverture fichier : {e}"

    # --- 1. PRÉSERVATION DE LA CASSE ---
    arg_str = raw_arg.lower()
    
    # --- 2. GESTION BATCH ---
    parts = re.split(r',|\bet\b', arg_str)
    clean_parts = [p.strip() for p in parts if len(p.strip()) > 2]
    
    if len(clean_parts) > 1:
        count = 0
        for app in clean_parts:
             # Utilisation de TASK_QUEUE depuis igor_config
             TASK_QUEUE.put({"tool": "LAUNCH", "args": app})
             count += 1
        return f"Traitement de {count} applications..."

    # --- 3. RECHERCHE DE L'APPLICATION ---
    search = clean_parts[0] if clean_parts else arg_str
    
    cmd_to_run = None
    name_display = search

    # A. Recherche EXACTE
    if search in INSTALLED_APPS:
        cmd_to_run = INSTALLED_APPS[search]

    # B. Recherche FLOUE
    if not cmd_to_run:
        for app_name, cmd in INSTALLED_APPS.items():
            if search in app_name: 
                cmd_to_run = cmd
                name_display = app_name
                break

    # C. AUTO YOUTUBE
    if not cmd_to_run and any(k in arg_str for k in ["youtube", "vidéo", "video", "clip"]):
        query = None  # ✅ Initialisation de query AVANT le if/else
        
        # ✅ AJOUT : Si c'est déjà une URL YouTube complète, on skip cette section
        if "youtube.com/watch?v=" in arg_str or "youtu.be/" in arg_str:
            # C'est déjà une URL complète, on la laisse passer à la section D (URLS/WEB)
            pass
        else:
            # C'est une recherche (ex: "Youtube Daft Punk")
            clean_query = arg_str.replace("::", " ")
            query = re.sub(r'(regarde|met|mets|lance|ouvre|la|le|une|video|vidéo|clip|sur|youtube)', '', clean_query, flags=re.IGNORECASE).strip()
        
        if query and len(query) > 1:
            print(f"  [LAUNCH] Recherche Auto Youtube : {query}", flush=True)
            try:
                search_url = f"https://www.youtube.com/results?search_query={urllib.parse.quote(query)}"
                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
                    "Accept-Language": "en-US,en;q=0.9"
                }
                cookies = {'CONSENT': 'YES+cb.20210328-17-p0.en+FX+439'}
                
                res = requests.get(search_url, headers=headers, cookies=cookies, timeout=5)
                
                vids = re.findall(r'/watch\?v=([a-zA-Z0-9_-]{11})', res.text)
                if not vids:
                    vids = re.findall(r'"videoId":"([a-zA-Z0-9_-]{11})"', res.text)

                if vids:
                    new_url = f"https://www.youtube.com/watch?v={vids[0]}"
                    raw_arg = new_url
                    search = new_url 
                    name_display = f"Youtube: {query}"
                else:
                    raw_arg = search_url
                    search = search_url
                    name_display = f"Recherche Youtube: {query}"
            except Exception as e:
                print(f"  [ERR] Youtube Search: {e}")

    # D. URLS / WEB
    if not cmd_to_run:
        if re.search(r"\.(com|fr|org|net|io|co|uk|ca|be|ch)\b", search) or "http" in search or "www." in search:
            fav_browser = MEMORY.get('fav_browser')
            if not fav_browser:
                return "Je ne connais pas votre navigateur favori. (Dites 'Utilise Chrome' ou 'Utilise Firefox')."
            
            url = raw_arg
            for prefix in ["lance ", "ouvre ", "va sur ", "launch ", "open "]:
                if url.lower().startswith(prefix):
                    url = url[len(prefix):].strip()
            
            if not url.startswith("http"): url = "https://" + url
            
            cmd_to_run = f"{fav_browser} \"{url}\""
            name_display = f"Site {url}"

    # E. Fallback Système
    if not cmd_to_run:
        # On vérifie si c'est une commande composée (ex: "xdg-open fichier.txt")
        # On prend le premier mot (le programme) pour vérifier s'il existe
        binary_candidate = search.split()[0]
        if shutil.which(search) or shutil.which(binary_candidate):
            cmd_to_run = search

    # --- 4. LANCEMENT ---
    if cmd_to_run:
        try:
            # FIX ANTI-ROOT
            sudo_user = os.environ.get('SUDO_USER')
            if sudo_user and os.geteuid() == 0:
                print(f"  [SEC] Drop privileges vers {sudo_user}", flush=True)
                cmd_to_run = f"runuser -u {sudo_user} -- {cmd_to_run}"

            argv = shlex.split(cmd_to_run)
            
            flags = GLib.SpawnFlags.SEARCH_PATH | GLib.SpawnFlags.STDOUT_TO_DEV_NULL | GLib.SpawnFlags.STDERR_TO_DEV_NULL | GLib.SpawnFlags.DO_NOT_REAP_CHILD
            
            def _safe_spawn():
                try: GLib.spawn_async(argv, flags=flags)
                except Exception as e: print(f"  [ERR] Launch: {e}")
                return False
            
            GLib.idle_add(_safe_spawn)
            return f"Lancement : {name_display}."
            
        except Exception as e:
            return f"Erreur système : {e}"

    return f"Application '{search}' introuvable."

# === Gestion des actions fenêtres ===

def tool_window_action(window_id, action):
    """
    Exécute une action sur une fenêtre spécifique.
    
    Args:
        window_id: ID hexadécimal (ex: "0x02400003")
        action: "focus" ou "close"
    
    Returns:
        Message de confirmation
    """
    if not shutil.which("wmctrl"):
        return "wmctrl requis."
    
    try:
        if action == "focus":
            # Active la fenêtre (la met au premier plan)
            subprocess.call(["wmctrl", "-ia", window_id])
            return f"✓ Focus sur fenêtre {window_id}"
        
        elif action == "close":
            # Ferme la fenêtre proprement
            subprocess.call(["wmctrl", "-ic", window_id])
            return f"✓ Fenêtre {window_id} fermée"
        
        else:
            return f"Action inconnue : {action}"
    
    except Exception as e:
        return f"Erreur action fenêtre : {e}"

def tool_window_focus(arg):
    """
    Met le focus sur une fenêtre en la cherchant par nom ou classe.
    """
    arg_str = str(arg).lower().strip()
    if not arg_str: return "Précisez le nom de la fenêtre."
    
    if not shutil.which("wmctrl"): return "wmctrl requis."

    # Nettoyage des mots parasites
    for noise in ["sur ", "la ", "le ", "l'", "fenêtre ", "application "]:
        if arg_str.startswith(noise): arg_str = arg_str[len(noise):].strip()

    try:
        # Recherche large (Titre ou Classe)
        out = subprocess.check_output("wmctrl -lx", shell=True).decode('utf-8')
        candidates = []
        
        for line in out.splitlines():
            parts = line.split(None, 3)
            if len(parts) >= 4:
                wid = parts[0]
                wm_class = parts[2].lower()
                title = parts[3].lower()
                
                # Match si la recherche est dans le titre ou la classe (ex: "arandr")
                if arg_str in wm_class or arg_str in title:
                    candidates.append((wid, parts[3])) # ID, Titre original

        if not candidates:
            return f"Aucune fenêtre trouvée pour '{arg_str}'."
        
        # On prend la dernière trouvée (souvent la plus pertinente) ou on gère l'ambiguïté
        target_wid, target_title = candidates[-1]
        
        subprocess.call(["wmctrl", "-ia", target_wid])
        return f"Focus sur : {target_title}"

    except Exception as e:
        return f"Erreur focus : {e}"

def tool_list_apps(arg):
    if not INSTALLED_APPS: return "Aucune application détectée."
    
    arg_str = str(arg).lower().strip()
    cmd_to_name = {}
    
    # Construction map unique Cmd -> Nom le plus long
    for name, cmd in INSTALLED_APPS.items():
        if cmd not in cmd_to_name or len(name) > len(cmd_to_name[cmd]):
            cmd_to_name[cmd] = name.title()

    # Détection demande "Tout"
    keywords_all = ["tout", "toute", "all", "complet", "liste", "tous"]
    is_list_all = any(k in arg_str for k in keywords_all)
    
    found_cmds = set()

    # Logique de filtrage
    if not arg_str or arg_str == "none" or (is_list_all and len(arg_str.split()) < 3):
        # On limite à 50 pour éviter le spam si "tout" est demandé sans filtre
        found_cmds = set(list(cmd_to_name.keys())[:50])
    else:
        # Filtrage par mots-clés
        ALIASES = {
            "internet": "web", "navigateur": "web", "browser": "web", "google": "web",
            "video": "player", "vidéo": "player", "film": "player",
            "musique": "music", "audio": "music", "son": "music",
            "photo": "image", "image": "image", "viewer": "image",
            "texte": "text", "note": "text", "editeur": "text",
            "calculatrice": "math", "math": "math",
            "jeu": "game", "jeux": "game", "console": "term"
        }
        
        ignore_words = ["liste", "les", "des", "le", "la", "moi", "donne", "affiche", "montre", "applications"]
        search_terms = []
        
        for word in arg_str.split():
            if word not in ignore_words:
                translated = ALIASES.get(word, word)
                if len(translated) > 1:
                    search_terms.append(translated)
        
        for term in search_terms:
            for app_key, cmd in INSTALLED_APPS.items():
                if term in app_key:
                    found_cmds.add(cmd)

    if not found_cmds:
        return f"Je n'ai trouvé aucune application correspondant à '{arg_str}'."

    # --- FORMATAGE ENRICHI ---
    # Groupement par catégorie
    categorized = {}
    
    for cmd in found_cmds:
        # Récupération de la catégorie principale via les métadonnées
        cat = "Autre"
        if cmd in APP_METADATA:
            cats = APP_METADATA[cmd].get('categories', [])
            if cats:
                # Simplification des catégories XDG pour l'affichage
                first_cat = cats[0]
                if "Game" in cats: cat = "🎮 Jeux"
                elif "Audio" in cats or "Video" in cats: cat = "🎬 Multimédia"
                elif "Development" in cats: cat = "💻 Développement"
                elif "Office" in cats: cat = "bm️ Bureautique"
                elif "Network" in cats or "WebBrowser" in cats: cat = "🌐 Internet"
                elif "System" in cats or "Settings" in cats: cat = "⚙️ Système"
                elif "Graphics" in cats: cat = "🎨 Graphisme"
                else: cat = f"📂 {first_cat}"
        
        if cat not in categorized: categorized[cat] = []
        categorized[cat].append((cmd_to_name[cmd], cmd))

    # Construction du message HTML
    lines = [f"<b>Applications trouvées ({len(found_cmds)}) :</b>"]
    
    for cat in sorted(categorized.keys()):
        lines.append(f"\n<b>{cat}</b>")
        for name, cmd in sorted(categorized[cat]):
            # CORRECTION : On échappe les caractères spéciaux (&, <, >) pour éviter le crash Pango
            safe_name = GLib.markup_escape_text(name)
            safe_cmd = GLib.markup_escape_text(cmd)
            
            # Lien formaté : launch://COMMANDE
            lines.append(f" • <a href='launch://{safe_cmd}'>{safe_name}</a>")

    if len(found_cmds) > 50:
        lines.append("\n<i>(Liste tronquée à 50 résultats)</i>")

    return "\n".join(lines)

def tool_list_windows(arg):
    """
    Liste les fenêtres avec classification et liens cliquables pour focus.
    Format enrichi avec catégories et actions.
    """
    if not shutil.which("wmctrl"):
        return "L'outil 'wmctrl' manque. Installez-le avec : sudo apt install wmctrl"

    arg_str = str(arg).lower().strip()
    
    # Détection filtre par catégorie
    category_keywords = {
        "calculatrice": ["calculator", "calc"],
        "navigateur": ["firefox", "chrome", "chromium", "brave", "edge"],
        "browser": ["firefox", "chrome", "chromium"],
        "web": ["firefox", "chrome", "chromium", "brave"],
        "terminal": ["terminal", "konsole", "gnome-terminal"],
        "fichier": ["nautilus", "dolphin", "thunar", "files"],
        "editeur": ["gedit", "kate", "mousepad", "text"],
    }
    
    filter_patterns = None
    for keyword, patterns in category_keywords.items():
        if keyword in arg_str:
            filter_patterns = patterns
            break

    try:
        # On utilise -lx pour avoir WM_CLASS
        out = subprocess.check_output("wmctrl -lx", shell=True).decode('utf-8')
        windows = []
        
        for line in out.splitlines():
            parts = line.split(None, 3)
            if len(parts) >= 4:
                window_id = parts[0]  # Ex: 0x02400003
                wm_class = parts[2].lower()  # Ex: "gnome-calculator.Gnome-calculator"
                title = parts[3]
                
                if not title.strip(): 
                    continue
                
                # Classification simple par WM_CLASS
                category = "Autre"
                app_name = wm_class.split('.')[0]  # "gnome-calculator"
                
                # Détection catégorie
                if any(w in wm_class for w in ["calculator", "calc"]):
                    category = "🔢 Calculatrice"
                elif any(w in wm_class for w in ["firefox", "chrome", "chromium", "brave", "edge"]):
                    category = "🌐 Navigateur"
                elif any(w in wm_class for w in ["terminal", "konsole", "gnome-terminal", "xterm"]):
                    category = "💻 Terminal"
                elif any(w in wm_class for w in ["nautilus", "dolphin", "thunar", "nemo", "files"]):
                    category = "📁 Fichiers"
                elif any(w in wm_class for w in ["gedit", "kate", "mousepad", "pluma", "text"]):
                    category = "📝 Éditeur"
                elif any(w in wm_class for w in ["vlc", "mpv", "totem", "player"]):
                    category = "🎬 Vidéo"
                elif any(w in wm_class for w in ["spotify", "rhythmbox", "audacious"]):
                    category = "🎵 Musique"
                
                # Filtrage si demandé
                if filter_patterns:
                    if not any(p in wm_class for p in filter_patterns):
                        continue
                
                windows.append({
                    "id": window_id,
                    "title": title,
                    "app": app_name,
                    "class": wm_class,
                    "category": category
                })

        if not windows: 
            return "Aucune fenêtre active détectée."

        # Construction du résultat enrichi
        # Groupement par catégorie
        by_category = {}
        for w in windows:
            cat = w['category']
            if cat not in by_category:
                by_category[cat] = []
            by_category[cat].append(w)
        
        # Génération HTML avec liens cliquables
        result_parts = [f"<b>Fenêtres ouvertes ({len(windows)})</b> :"]
        
        for cat in sorted(by_category.keys()):
            result_parts.append(f"\n<b>{cat}</b> :")
            
            for w in by_category[cat]:
                # Troncature du titre si trop long
                display_title = w['title']
                if len(display_title) > 60:
                    display_title = display_title[:57] + "..."
                
                # Lien cliquable avec protocole custom
                # Format: window://ACTION:WINDOW_ID
                focus_link = f"window://focus:{w['id']}"
                close_link = f"window://close:{w['id']}"
                
                # Ligne formatée
                result_parts.append(
                    f"  • <a href='{focus_link}'>[Focus]</a> "
                    f"<a href='{close_link}'>[✕]</a> "
                    f"{display_title}"
                )
        
        return "\n".join(result_parts)

    except Exception as e:
        return f"Erreur lecture fenêtres : {e}"

def tool_window_fullscreen(arg):
    if not shutil.which("xdotool") or not shutil.which("wmctrl"):
        return "Erreur : 'xdotool' et 'wmctrl' sont requis (sudo apt install xdotool wmctrl)."
    
    arg_str = str(arg).lower().strip()
    target_name = arg_str
    mode = "window"
    is_maximize = False
    
    # 1. DÉTECTION MODE (Maximiser vs Plein Écran F11)
    if any(k in arg_str for k in ["maximise", "maximize", "agrandis"]):
        is_maximize = True
        for k in ["maximise", "maximize", "agrandis"]:
            target_name = target_name.replace(k, "")

    # 2. NETTOYAGE INTELLIGENT
    # On enlève "le", "la", "fenêtre" pour garder juste "terminal"
    parasites = ["en cours", "de", "la", "le", "les", "l'", "un", "une", "fenêtre", "fenetre", "application", "active"]
    tokens = target_name.split()
    clean_tokens = [t for t in tokens if t not in parasites]
    target_name = " ".join(clean_tokens).strip()

    # Détection mode vidéo (Youtube/VLC) pour touche 'f' au lieu de F11
    video_keywords = ["vidéo", "video", "youtube", "player", "vlc", "mpv", "netflix", "film"]
    if any(kw in arg_str for kw in video_keywords):
        mode = "video"

    window_id = None

    # 3. RECHERCHE DE FENÊTRE (PAR CLASSE ET TITRE)
    if target_name:
        try:
            # On récupère la liste complète avec les classes (wmctrl -lx)
            # Format: ID Desktop Host Class Title
            out = subprocess.check_output("wmctrl -lx", shell=True).decode('utf-8').lower()
            
            # Mapping pour aider la recherche (Mot clé -> Partie de la classe système)
            CLASS_MAP = {
                "terminal": ["terminal", "konsole", "xterm"],
                "web": ["firefox", "chrome", "brave", "edge"],
                "navigateur": ["firefox", "chrome", "brave", "edge"],
                "internet": ["firefox", "chrome", "brave", "edge"],
                "calculatrice": ["calc"],
                "fichiers": ["nautilus", "dolphin", "thunar", "files"],
                "dossier": ["nautilus", "dolphin", "thunar", "files"]
            }
            
            # On enrichit les termes de recherche
            search_terms = [target_name]
            if target_name in CLASS_MAP:
                search_terms.extend(CLASS_MAP[target_name])
            
            found_windows = []
            
            for line in out.splitlines():
                parts = line.split()
                if len(parts) > 4:
                    wid = parts[0]
                    wclass = parts[2]
                    wtitle = " ".join(parts[4:])
                    
                    # On ignore la fenêtre de l'agent Igor
                    if "agent" in wtitle or "main.py" in wtitle:
                        continue
                        
                    # Vérification match
                    for term in search_terms:
                        if term in wclass or term in wtitle:
                            found_windows.append(wid)
                            break
            
            # On prend la dernière fenêtre trouvée (souvent la plus récente/active)
            if found_windows:
                window_id = found_windows[-1]
                
        except Exception as e:
            print(f"  [FULLSCREEN] Erreur recherche : {e}")

    # 4. FALLBACK : Fenêtre active (si on a rien trouvé ou rien précisé)
    if not window_id:
        try:
            active_id = subprocess.check_output("xdotool getactivewindow", shell=True).decode().strip()
            # On vérifie que ce n'est pas Igor
            active_name = subprocess.check_output(f"xdotool getwindowname {active_id}", shell=True).decode().strip().lower()
            
            if "agent" not in active_name and "main.py" not in active_name:
                window_id = active_id
            else:
                # Si Igor est actif, on essaie de trouver la fenêtre précédente dans la stack
                # (Complexité omise pour stabilité, on demande à l'utilisateur de cliquer)
                pass
        except: pass

    if not window_id:
        return f"Je ne trouve pas la fenêtre '{target_name}'."

    # 5. EXECUTION
    try:
        # On active la fenêtre (Focus)
        subprocess.call(["wmctrl", "-ia", window_id])
        
        # BRANCHE MAXIMISATION
        if is_maximize:
            # On force l'état maximisé via EWMH (Standard Linux)
            subprocess.call(["wmctrl", "-ir", window_id, "-b", "add,maximized_vert,maximized_horz"])
            return f"Fenêtre maximisée."

        # BRANCHE PLEIN ÉCRAN (F11 / f)
        import time
        time.sleep(0.2) # Petite pause pour le focus
        key = "f" if mode == "video" else "F11"
        subprocess.call(["xdotool", "key", "--clearmodifiers", key])
        
        mode_desc = "cinéma" if mode == "video" else "plein écran"
        return f"Mode {mode_desc} activé."

    except Exception as e:
        return f"Erreur système : {e}"

# Ajout dans igor_system.py

def close_browser_tab_if_needed(window_id, window_title, window_class):
    """
    Détecte si la fenêtre est un navigateur et ferme l'onglet au lieu de la fenêtre.
    Retourne True si c'est un onglet fermé, False si fenêtre complète.
    
    Args:
        window_id: ID hexadécimal de la fenêtre (ex: "0x02400003")
        window_title: Titre de la fenêtre
        window_class: WM_CLASS de la fenêtre
    """
    browser_classes = ["firefox", "chrome", "chromium", "brave", "microsoft-edge", "opera", "vivaldi"]
    
    # Vérifie si c'est un navigateur
    is_browser = any(b in window_class.lower() for b in browser_classes)
    
    if not is_browser:
        return False
    
    # Détection multi-onglets : si le titre contient "—" ou " - " typique des navigateurs
    # Ex: "YouTube - Mozilla Firefox" or "Gmail — Brave"
    has_multiple_tabs = "—" in window_title or " - " in window_title
    
    if not has_multiple_tabs:
        return False
    
    # Si c'est un navigateur avec plusieurs onglets, on ferme juste l'onglet actif
    print(f"  [SMART-CLOSE] 🎯 Détection onglet navigateur : {window_title[:50]}", flush=True)


def tool_close_window(arg):
    """
    MODIFICATION : Détection intelligente onglet vs fenêtre complète.
    """
    arg_str = str(arg).lower().strip()
    if not arg_str or arg_str == "none": 
        return "Précisez le nom de la fenêtre à fermer."

    if not shutil.which("wmctrl"):
        return "L'outil 'wmctrl' est requis."

    # 🆕 FIX : On sauvegarde l'argument original AVANT nettoyage
    original_arg = arg_str
    
    # Détection mode "TOUTES"
    close_all = False
    
    # Liste élargie de mots-clés
    all_keywords = ["toutes", "tous", "all", "tout"]
    
    # Cas 1 : Mot-clé explicite ("toutes les calculatrices")
    if any(keyword in arg_str for keyword in all_keywords):
        close_all = True
        print(f"  [CLOSE] 🔥 Mode ALL détecté via mot-clé : '{arg_str}'", flush=True)
        
        # On nettoie TOUS les mots-clés
        for keyword in all_keywords:
            arg_str = arg_str.replace(keyword, "").strip()
        
        # Nettoyage articles
        for article in ["le ", "la ", "les ", "de ", "des ", "du "]:
            arg_str = arg_str.replace(article, "").strip()
    
    # 🆕 Cas 2 : DÉTECTION PLURIEL (sur l'arg nettoyé)
    # Maintenant on vérifie le pluriel (S ou X) APRÈS avoir enlevé "toutes"
    if not close_all and (arg_str.endswith('s') or arg_str.endswith('x')) and len(arg_str) > 3:
        exceptions = ["souris", "paris", "virus", "bus", "biais", "jus", "processus", "linux", "firefox", "box", "remix"]
        
        if arg_str not in exceptions:
            print(f"  [CLOSE] 🔍 Pluriel détecté (S/X) : '{arg_str}' → Mode ALL activé", flush=True)
            close_all = True

    # 🆕 FIX CRITIQUE : On prépare les variantes de recherche (Singularisation)
    search_variants = [arg_str]
    
    # Gestion simple S/X
    if (arg_str.endswith('s') or arg_str.endswith('x')) and len(arg_str) > 3:
        exceptions = ["souris", "paris", "virus", "bus", "biais", "jus", "processus", "linux", "firefox", "box"]
        if arg_str not in exceptions:
            # Cas standard (fenêtres -> fenêtre)
            singular = arg_str[:-1]
            search_variants.append(singular)
            
            # Cas spécial "aux" -> "al" (terminaux -> terminal)
            if arg_str.endswith("aux"):
                singular_al = arg_str[:-3] + "al"
                search_variants.append(singular_al)
            
            print(f"  [CLOSE] 🔎 Recherche avec variantes : {search_variants}", flush=True)

    # Mapping catégories (on ajoute les versions plurielles)
    CATEGORY_PATTERNS = {
        "calculatrice": ["calculator", "calc", "gnome-calculator"],
        "calculatrices": ["calculator", "calc", "gnome-calculator"],  # 🆕
        "navigateur": ["firefox", "chrome", "chromium", "brave", "edge"],
        "navigateurs": ["firefox", "chrome", "chromium", "brave", "edge"],  # 🆕
        "browser": ["firefox", "chrome", "chromium", "brave"],
        "browsers": ["firefox", "chrome", "chromium", "brave"],  # 🆕
        "terminal": ["terminal", "konsole", "gnome-terminal"],
        "terminaux": ["terminal", "konsole", "gnome-terminal"],  # 🆕
        "fichiers": ["nautilus", "dolphin", "thunar", "nemo"],
    }

    # Construction des patterns de recherche
    search_patterns = []
    
    # On teste TOUTES les variantes (singulier + pluriel)
    for variant in search_variants:
        if variant in CATEGORY_PATTERNS:
            search_patterns.extend(CATEGORY_PATTERNS[variant])
            print(f"  [CLOSE] 📂 Catégorie trouvée pour '{variant}'", flush=True)
        else:
            search_patterns.append(variant)
    
    # Dédoublonnage
    search_patterns = list(set(search_patterns))
    
    print(f"  [CLOSE] 🎯 Patterns finaux : {search_patterns}", flush=True)
    print(f"  [CLOSE] 🔥 Mode ALL : {'OUI' if close_all else 'NON'}", flush=True)

    try:
        out = subprocess.check_output("wmctrl -lx", shell=True).decode('utf-8')
        candidates = []
        seen_classes = set() if not close_all else None
        
        for line in out.splitlines():
            parts = line.split(None, 3)
            if len(parts) >= 4:
                wid = parts[0]
                wm_class = parts[2].lower()
                title = parts[3]
                
                full_text = f"{wm_class} {title.lower()}"
                
                # On cherche TOUS les patterns
                match_found = False
                for pattern in search_patterns:
                    if pattern in full_text:
                        match_found = True
                        break
                
                if match_found:
                    if not close_all:
                        # Mode SINGLE : Une seule par classe
                        if wm_class not in seen_classes:
                            candidates.append((wid, title, wm_class))
                            seen_classes.add(wm_class)
                    else:
                        # Mode ALL : TOUTES
                        candidates.append((wid, title, wm_class))

        if not candidates: 
            return f"Aucune fenêtre correspondant à '{original_arg}'."
        
        print(f"  [CLOSE] 📊 {len(candidates)} fenêtre(s) trouvée(s)", flush=True)
        
        # === MODE SINGLE ===
        if not close_all:
            if len(candidates) > 1:
                debug_info = " | ".join([f"{c[1][:30]} ({c[2].split('.')[0]})" for c in candidates[:3]])
                return f"Ambiguïté : {len(candidates)} fenêtres. Dites 'Ferme TOUTES les {arg_str}' ou précisez : {debug_info}"

            target_wid, target_title, target_class = candidates[0]
            
            # 🎯 Détection onglet navigateur
            if " - " in target_title or "—" in target_title:
                is_browser = any(b in target_class.lower() for b in ["firefox", "chrome", "chromium", "brave", "edge"])
                
                if is_browser:
                    return f"""⚠️ Onglet navigateur détecté.

💡 **Pour fermer cet onglet** :
  • Ctrl+W manuellement
  • Ou 'Ferme toute la fenêtre {target_class.split('.')[0]}'"""
            
            # Fermeture classique
            print(f"  [CLOSE] ✂️ Fermeture : {target_title[:50]}", flush=True)
            subprocess.call(["wmctrl", "-ic", target_wid])
            return f"✅ Fenêtre fermée : {target_title}"
        
        # === MODE ALL ===
        else:
            print(f"  [CLOSE] 🔥 FERMETURE MASSIVE : {len(candidates)} cibles", flush=True)
            
            closed_count = 0
            tab_warnings = []
            
            for i, (wid, title, wm_class) in enumerate(candidates, 1):
                try:
                    # Détection onglet
                    is_tab = (" - " in title or "—" in title) and any(b in wm_class.lower() for b in ["firefox", "chrome", "brave"])
                    
                    if is_tab:
                        if " - " in title:
                            tab_name = title.split(" - ")[0]
                        else:
                            tab_name = title.split("—")[0]
                        tab_warnings.append(tab_name)
                    else:
                        # Fermeture standard
                        print(f"  [CLOSE]   {i}/{len(candidates)} ✂️ {title[:50]}", flush=True)
                        subprocess.call(["wmctrl", "-ic", wid], stderr=subprocess.DEVNULL)
                        closed_count += 1
                        # Pause entre fermetures
                        time.sleep(0.2)
                    
                except Exception as e:
                    print(f"  [CLOSE]   ❌ Échec {wid}: {e}", flush=True)
            
            result_parts = []
            if closed_count > 0:
                result_parts.append(f"✅ {closed_count} fenêtre(s) fermée(s)")
            
            if tab_warnings:
                result_parts.append(f"⚠️ {len(tab_warnings)} onglet(s) ignoré(s)")
            
            return " | ".join(result_parts) if result_parts else "Aucune fenêtre fermée."

    except Exception as e:
        return f"Erreur : {e}"

def tool_open_file(arg):
    """
    Ouvre un fichier avec une recherche globale et permissive ("fuzzy").
    Scanne Documents, Bureau, Downloads, Images, etc. simultanément.
    """
    arg_str = str(arg).strip()
    home = USER_HOME
    
    # === 1. NETTOYAGE AGRESSIF (VERSION REGEX - ANTI BOUCLE) ===
    # Liste de mots à supprimer
    noise_list = [
        "le", "la", "les", "l'", "un", "une", "mon", "ma", "mes", "ton", "ta", "tes", "ce", "cet", "cette",
        "fichier", "file", "document", "doc", "feuille", "image", "photo", "vidéo", "video", "dessin", 
        "projet", "truc", "machin", "dossier", "dans", "sur", "nommé", "appelé", "avec", "du", "de"
    ]
    
    # Regex compilée pour performance et sécurité
    import re
    pattern = r'\b(' + '|'.join(map(re.escape, noise_list)) + r')\b'
    
    # Nettoyage en une seule passe (Instantané)
    clean_query = re.sub(pattern, '', arg_str, flags=re.IGNORECASE)
    filename_query = re.sub(r'\s+', ' ', clean_query).strip().replace("'", "").replace('"', "")
    
    if len(filename_query) < 2:
        return "Nom de fichier trop court ou vide après nettoyage."

    print(f"  [OPEN] Recherche pour : '{filename_query}'", flush=True)

    # === 2. DÉFINITION DES ZONES DE RECHERCHE ===
    search_dirs = [
        os.path.join(home, "Documents"),
        os.path.join(home, "Downloads"),
        os.path.join(home, "Téléchargements"),
        os.path.join(home, "Desktop"),
        os.path.join(home, "Bureau"),
        os.path.join(home, "Pictures"),
        os.path.join(home, "Images"),
        os.path.join(home, "Videos"),
        os.path.join(home, "Music")
    ]
    valid_dirs = [d for d in search_dirs if os.path.exists(d)]
    
    # On protège les chemins avec des guillemets pour la commande find
    dirs_str = " ".join([f"'{d}'" for d in valid_dirs])
    
    # Extraction extension explicite si l'utilisateur en a donné une (ex: "stickman.png")
    ext_filter = ""
    name_for_search = filename_query
    if "." in filename_query:
        parts = filename_query.rsplit(".", 1)
        if len(parts[1]) <= 4: # C'est probablement une extension
            name_for_search = parts[0]
            # On ne force pas le filtre find, on laissera le scoring gérer, 
            # mais on note qu'on a une extension
    
    # === 3. RECHERCHE ITÉRATIVE PAR MOTS CLÉS (DEBUG MODE) ===
    search_words = filename_query.split()
    all_hits_counter = Counter()
    best_path = None
    
    print(f"  [DEBUG-OPEN] 🚀 Début recherche pour : '{filename_query}'", flush=True)
    print(f"  [DEBUG-OPEN] Mots à tester : {search_words}", flush=True)
    print(f"  [DEBUG-OPEN] Dossiers cibles : {dirs_str}", flush=True)

    # On itère sur chaque mot de la requête
    for i, word in enumerate(search_words):
        if len(word) < 2: 
            print(f"  [DEBUG-OPEN] Mot '{word}' ignoré (trop court)", flush=True)
            continue
        
        print(f"  [DEBUG-OPEN] --- ÉTAPE {i+1}/{len(search_words)} : Mot '{word}' ---", flush=True)

        # Commande find pour ce mot spécifique
        # NOTE : On garde le head -n 50 pour éviter le freeze si trop de résultats
        cmd = (
            f"find {dirs_str} "
            f"-maxdepth 4 "
            f"-type f "
            f"-not -path '*/.*' "
            f"-iname '*{word}*' "
            f"2>/dev/null | head -n 50"
        )
        
        print(f"  [DEBUG-CMD] {cmd}", flush=True)
        
        try:
            # On mesure le temps d'exécution
            t_start = time.time()
            output = subprocess.check_output(cmd, shell=True, timeout=5).decode("utf-8").strip()
            t_end = time.time()
            
            print(f"  [DEBUG-OPEN] Temps exécution : {t_end - t_start:.2f}s", flush=True)
            
            if not output:
                print(f"  [DEBUG-OPEN] 0 résultat pour '{word}'", flush=True)
                continue
                
            files_found = [f for f in output.split('\n') if f.strip()]
            print(f"  [DEBUG-OPEN] {len(files_found)} fichiers trouvés.", flush=True)
            
            # On stocke pour l'intersection finale
            all_hits_counter.update(files_found)
            
            # VÉRIFICATION DE LA FIABILITÉ (> 50% match)
            for f in files_found:
                fname = os.path.basename(f).lower()
                # On compare le nom du fichier trouvé avec la REQUÊTE COMPLÈTE
                ratio = SequenceMatcher(None, filename_query, fname).ratio()
                
                # Log détaillé pour comprendre le score
                if ratio > 0.3: # On affiche seulement ceux qui ressemblent un peu pour pas spammer
                    print(f"    [COMPARE] '{filename_query}' vs '{fname}' = {ratio:.2f}", flush=True)

                if ratio >= 0.5:
                    best_path = f
                    print(f"  [DEBUG-OPEN] ✅ CANDIDAT FIABLE TROUVÉ (>50%) : {fname}", flush=True)
                    break 
            
            if best_path:
                print("  [DEBUG-OPEN] Arrêt de la boucle (Candidat trouvé).", flush=True)
                break 
                
        except subprocess.TimeoutExpired:
            print(f"  [DEBUG-OPEN] ⚠️ TIMEOUT sur la commande find pour '{word}'", flush=True)
        except Exception as e:
            print(f"  [DEBUG-OPEN] ❌ ERREUR sur '{word}': {e}", flush=True)

    # === 4. INTERSECTION / FRÉQUENCE (Si pas de candidat fiable) ===
    if not best_path and all_hits_counter:
        print("  [DEBUG-OPEN] ⚠️ Pas de candidat unique fiable > 50%.", flush=True)
        print("  [DEBUG-OPEN] Analyse des fréquences (Intersection)...", flush=True)
        
        # On prend celui qui revient le plus souvent
        most_common_list = all_hits_counter.most_common(5) # On regarde les top 5 pour le log
        print(f"  [DEBUG-OPEN] Top récurrence : {most_common_list}", flush=True)
        
        if most_common_list:
            best_path = most_common_list[0][0]
            count = most_common_list[0][1]
            print(f"  [DEBUG-OPEN] Vainqueur par fréquence ({count} occurrences) : {os.path.basename(best_path)}", flush=True)

    if not best_path:
        print("  [DEBUG-OPEN] ÉCHEC TOTAL : Aucun fichier sélectionné.", flush=True)
        return f"Aucun fichier trouvé pour '{filename_query}'."

    # === 5. RÉSULTAT ET OUVERTURE ===
    print(f"  [DEBUG-OPEN] 🏁 FINALE : Sélection de '{best_path}'", flush=True)
    best_name = os.path.basename(best_path)
    
    # Ouverture du fichier avec protection d'environnement (Fix Qt/OpenCV Conflict)
    try:
        # On copie l'environnement actuel
        clean_env = os.environ.copy()
        
        # On supprime les variables toxiques injectées par OpenCV/Python qui font crasher VLC/MPV
        # C'est ce qui causait l'erreur "Could not load the Qt platform plugin xcb"
        keys_to_remove = ['QT_QPA_PLATFORM_PLUGIN_PATH', 'LD_LIBRARY_PATH', 'PYTHONPATH']
        for key in keys_to_remove:
            clean_env.pop(key, None)

        # On lance avec l'environnement nettoyé
        subprocess.Popen(
            ["xdg-open", best_path], 
            start_new_session=True, 
            env=clean_env,            # <--- C'est ici que la magie opère
            stdout=subprocess.DEVNULL, 
            stderr=subprocess.DEVNULL
        )
        
        # Petit feedback intelligent sur l'emplacement
        loc = "Dossier Inconnu"
        if "Documents" in best_path: loc = "Documents"
        elif "Desktop" in best_path or "Bureau" in best_path: loc = "Bureau"
        elif "Downloads" in best_path or "Téléchargements" in best_path: loc = "Téléchargements"
        elif "Pictures" in best_path or "Images" in best_path: loc = "Images"
        elif "Music" in best_path: loc = "Musique"
        elif "Videos" in best_path: loc = "Vidéos"
        
        return f"J'ouvre '{best_name}' (trouvé dans {loc})."
        
    except Exception as e:
        return f"Fichier trouvé ('{best_name}') mais erreur technique lors de l'ouverture : {e}"
    
def tool_find_file(arg):
    """
    Recherche intelligente de fichiers avec ciblage de dossier et scoring.
    """
    query = str(arg).strip().lower()
    
    if not query or len(query) < 2:
        return "Recherche trop courte (min 2 caractères)."
    
    # === 1. DÉFINITION DES ZONES DE RECHERCHE (CIBLAGE) ===
    dir_map = {
        "telechargement": os.path.join(USER_HOME, "Downloads"),
        "download": os.path.join(USER_HOME, "Downloads"),
        "bureau": os.path.join(USER_HOME, "Desktop"),
        "desktop": os.path.join(USER_HOME, "Desktop"),
        "document": os.path.join(USER_HOME, "Documents"),
        "image": os.path.join(USER_HOME, "Pictures"),
        "photo": os.path.join(USER_HOME, "Pictures"),
        "video": os.path.join(USER_HOME, "Videos"),
        "musique": os.path.join(USER_HOME, "Music"),
        "home": USER_HOME,
        "racine": USER_HOME,
        "projet": PROJECTS_DIR
    }

    selected_paths = []
    clean_query = query # Sera nettoyé ensuite
    
    # Détection si un dossier spécifique est mentionné (ex: "dans Downloads")
    for key, path in dir_map.items():
        if re.search(rf"\b{key}s?\b", query):  # Match 'download' ou 'downloads'
            if os.path.exists(path):
                selected_paths.append(path)
                # On retire le nom du dossier de la recherche pour ne pas fausser le nom du fichier
                clean_query = clean_query.replace(key, "").strip()

    if selected_paths:
        search_paths = list(set(selected_paths))
        print(f"  [FIND] 🎯 Ciblage spécifique ({len(search_paths)} zones) : {search_paths}", flush=True)
    else:
        # Liste par défaut (Tout scanner)
        search_paths = [
            USER_HOME,
            PROJECTS_DIR,
            os.path.join(USER_HOME, "Documents"),
            os.path.join(USER_HOME, "Downloads"),
            os.path.join(USER_HOME, "Téléchargements"),
            os.path.join(USER_HOME, "Desktop"),
            os.path.join(USER_HOME, "Bureau"),
            "/tmp"
        ]
        search_paths = list(set([p for p in search_paths if os.path.exists(p)]))
        print(f"  [FIND] Recherche globale ({len(search_paths)} zones)", flush=True)
    
    # === 2. EXTRACTION INTELLIGENTE DU NOM ===
    # clean_query est déjà partiellement nettoyé si un dossier a été trouvé
    noise_words = ["le ", "la ", "les ", "un ", "une ", "mon ", "ma ", "mes ", 
                   "fichier ", "file ", "nommé ", "appelé ", "du ", "de ", "avec "]
    for noise in noise_words:
        clean_query = clean_query.replace(noise, "").strip()
    
    # Extraction de l'extension si présente
    extension = None
    if "." in clean_query:
        parts = clean_query.rsplit(".", 1)
        if len(parts) == 2 and len(parts[1]) <= 5:
            name_part = parts[0]
            extension = parts[1]
        else:
            name_part = clean_query
    else:
        name_part = clean_query
    
    # === 3. DÉCOMPOSITION EN MOTS (FUZZY SEARCH) ===
    # On découpe la recherche en mots individuels
    # Ex: "cv alexis" → ["cv", "alexis"]
    search_words = [w for w in name_part.split() if len(w) > 1]
    
    if not search_words:
        search_words = [name_part]
    
    print(f"  [FIND] Mots-clés : {search_words}, ext={extension or 'any'}", flush=True)
    
    # === 4. RECHERCHE MULTI-PATTERNS ===
    # On essaie plusieurs stratégies de plus en plus permissives
    all_candidates = []
    
    for base_path in search_paths:
        try:
            # STRATÉGIE 1 : Recherche EXACTE (rapide)
            if extension:
                pattern_exact = f"*{name_part}*.{extension}"
            else:
                pattern_exact = f"*{name_part}*"
            
            cmd_exact = f"find '{base_path}' -maxdepth 5 -type f -iname '{pattern_exact}' 2>/dev/null"
            result = subprocess.check_output(cmd_exact, shell=True, stderr=subprocess.DEVNULL, timeout=3).decode().strip()
            
            if result:
                all_candidates.extend(result.split('\n'))
            
            # STRATÉGIE 2 : Recherche FUZZY (chaque mot séparément)
            # Ex: "cv alexis" → cherche "*cv*" ET "*alexis*"
            if len(search_words) > 1:
                # On construit un pattern qui contient TOUS les mots
                # Ex: "*cv*alexis*" ou "*alexis*cv*"
                for permutation in [search_words, search_words[::-1]]:
                    pattern_fuzzy = "*" + "*".join(permutation) + "*"
                    
                    if extension:
                        pattern_fuzzy += f".{extension}"
                    
                    cmd_fuzzy = f"find '{base_path}' -maxdepth 5 -type f -iname '{pattern_fuzzy}' 2>/dev/null"
                    result_fuzzy = subprocess.check_output(cmd_fuzzy, shell=True, stderr=subprocess.DEVNULL, timeout=2).decode().strip()
                    
                    if result_fuzzy:
                        all_candidates.extend(result_fuzzy.split('\n'))
            
            # STRATÉGIE 3 : Recherche PAR MOT (très permissif)
            # Cherche chaque mot individuellement
            for word in search_words:
                if len(word) > 2:  # Mots de 3+ lettres seulement
                    pattern_word = f"*{word}*"
                    if extension:
                        pattern_word += f".{extension}"
                    
                    cmd_word = f"find '{base_path}' -maxdepth 5 -type f -iname '{pattern_word}' 2>/dev/null"
                    result_word = subprocess.check_output(cmd_word, shell=True, stderr=subprocess.DEVNULL, timeout=2).decode().strip()
                    
                    if result_word:
                        all_candidates.extend(result_word.split('\n'))
                        
        except subprocess.TimeoutExpired:
            continue
        except Exception:
            continue
    
    # Dédoublonnage
    all_candidates = list(set([f for f in all_candidates if f and os.path.isfile(f)]))
    
    if not all_candidates:
        return f"❌ Aucun fichier trouvé pour '{query}'.\n💡 Essayez avec moins de détails (ex: juste le nom principal)."
    
    print(f"  [FIND] {len(all_candidates)} fichiers trouvés brut", flush=True)
    
    # === 5. SCORING DE PERTINENCE AMÉLIORÉ ===
    def calculate_score(filepath):
        """Calcule un score de pertinence (plus c'est haut, mieux c'est)."""
        score = 0
        filename = os.path.basename(filepath).lower()
        
        # Bonus 1 : Correspondance EXACTE du nom (JACKPOT)
        if name_part == filename.rsplit(".", 1)[0]:
            score += 1500
        
        # Bonus 2 : Tous les mots de recherche sont présents
        words_found = sum(1 for word in search_words if word in filename)
        score += words_found * 400
        
        # Bonus 3 : Le nom commence par un mot de recherche
        for word in search_words:
            if filename.startswith(word):
                score += 600
                break
        
        # Bonus 4 : Le nom contient la recherche complète
        if name_part in filename:
            score += 500
        
        # Bonus 5 : Extension correcte (si spécifiée)
        if extension and filename.endswith(f".{extension}"):
            score += 300
        
        # Bonus 6 : Ordre des mots respecté
        # Ex: "cv alexis" → "cv_alexis.pdf" a un meilleur score que "alexis_cv.pdf"
        if len(search_words) > 1:
            text_low = filename
            last_pos = -1
            in_order = True
            for word in search_words:
                pos = text_low.find(word)
                if pos > last_pos:
                    last_pos = pos
                else:
                    in_order = False
                    break
            if in_order:
                score += 250
        
        # Bonus 7 : Fichier récent (modifié < 30 jours)
        try:
            age_days = (time.time() - os.path.getmtime(filepath)) / 86400
            if age_days < 7:
                score += 100
            elif age_days < 30:
                score += 50
        except:
            pass
        
        # Bonus 8 : Dans un projet Igor
        if PROJECTS_DIR in filepath:
            score += 40
        
        # Bonus 9 : Dans Documents/Desktop (endroits importants)
        if any(folder in filepath for folder in ["/Documents/", "/Desktop/", "/Bureau/", "/Téléchargements/"]):
            score += 30
        
        # Malus 1 : Fichier caché
        if filename.startswith('.'):
            score -= 150
        
        # Malus 2 : Dans un dossier cache/système
        bad_paths = ["/.cache/", "/.local/share/", "/node_modules/", "/__pycache__/", 
                     "/.config/", "/.mozilla/", "/.thunderbird/"]
        if any(bad in filepath for bad in bad_paths):
            score -= 300
        
        # Malus 3 : Extension bizarre (backup, etc.)
        if any(ext in filename for ext in [".bak", ".tmp", ".swp", "~", ".old"]):
            score -= 100
        
        # Malus 4 : Nom très long (probablement généré automatiquement)
        if len(filename) > 60:
            score -= 50
        
        return score
    
    # === 6. TRI PAR PERTINENCE ===
    scored_files = [(calculate_score(f), f) for f in all_candidates]
    scored_files.sort(reverse=True, key=lambda x: x[0])
    
    # Filtrage : on garde seulement les scores positifs
    valid_files = [f for score, f in scored_files if score > 0]
    
    if not valid_files:
        return f"❌ {len(all_candidates)} fichiers trouvés mais aucun pertinent.\n💡 Affinez la recherche ou vérifiez l'orthographe."
    
    # === 7. AFFICHAGE DES RÉSULTATS ===
    top_results = valid_files[:10]
    
    print(f"  [FIND] Top 5 scores :", flush=True)
    for i, (score, path) in enumerate(scored_files[:5]):
        print(f"    {i+1}. [{score}pts] {os.path.basename(path)}", flush=True)
    
    # === 8. FORMATAGE DE LA RÉPONSE ===
    
    def _make_open_link(fpath):
        # 1. Protection Shell : ajoute des ' ' autour du chemin
        safe_shell_path = shlex.quote(fpath)
        # 2. Commande complète
        cmd = f"xdg-open {safe_shell_path}"
        # 3. Protection HTML
        safe_uri = GLib.markup_escape_text(cmd)
        # 4. CRITIQUE : Utiliser des guillemets doubles " pour le href
        # car safe_shell_path contient souvent des apostrophes simples '
        return f"<a href=\"launch://{safe_uri}\">[Ouvrir]</a>"

    if len(top_results) == 1:
        filepath = top_results[0]
        filename = os.path.basename(filepath)
        display_path = filepath.replace(USER_HOME, "~")
        link = _make_open_link(filepath)
        
        return f"✅ **Fichier trouvé**\n📄 {filename} {link}\n📁 {display_path}"
    
    else:
        lines = [f"✅ **{len(top_results)} fichiers trouvés** :"]
        
        for i, filepath in enumerate(top_results, 1):
            filename = os.path.basename(filepath)
            parent = os.path.dirname(filepath)
            
            if USER_HOME in parent:
                parent_display = parent.replace(USER_HOME, "~")
            else:
                parent_display = parent
            
            link = _make_open_link(filepath)
            
            # On escape le nom du fichier pour l'affichage (évite bug si '&' dans le nom)
            safe_name = GLib.markup_escape_text(filename)
            
            lines.append(f"{i}. **{safe_name}** {link}")
            lines.append(f"   📁 {parent_display}")
        
        if len(valid_files) > 10:
            lines.append(f"\n💡 +{len(valid_files)-10} autres résultats (affinez la recherche)")
        
        return "\n".join(lines)

# --- GESTION AUDIO (VOLUME) ---

AUDIO_MIXERS = ["Master", "PCM", "Speaker", "Headphone", "Digital", "HDMI", "Front"]

def get_system_volume():
    for mixer in AUDIO_MIXERS:
        try:
            cmd = f"amixer sget '{mixer}'"
            res = subprocess.check_output(cmd, shell=True, stderr=subprocess.DEVNULL).decode()
            match = re.search(r"\[(\d+)%\]", res)
            if match: return int(match.group(1))
        except: pass
    try:
        res = subprocess.check_output("pactl get-sink-volume @DEFAULT_SINK@", shell=True, stderr=subprocess.DEVNULL).decode()
        match = re.search(r"(\d+)%", res)
        if match: return int(match.group(1))
    except: pass
    return 50

def set_raw_volume(vol):
    vol = max(0, min(100, vol))
    success = False
    for mixer in AUDIO_MIXERS:
        cmd = f"amixer set '{mixer}' {vol}% unmute"
        try:
            ret = subprocess.call(cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if ret == 0: success = True
        except: pass
        if success: break
    if not success:
        try:
            subprocess.call(f"pactl set-sink-volume @DEFAULT_SINK@ {vol}%", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            success = True
        except: pass
    return success

def tool_set_volume(arg):
    nums = re.findall(r'\d+', str(arg))
    if not nums: return "Volume incompris."
    vol = int(nums[0])
    if set_raw_volume(vol): return f"Volume réglé à {vol}%"
    return "Erreur audio."

# --- ARRÊT SYSTÈME ---

def tool_exit(arg):
    """Gère l'arrêt propre du programme."""
    
    # On modifie les flags globaux dans igor_config
    igor_config.WATCH_RUNNING = False
    igor_config.ABORT_FLAG = True
    
    # On prépare l'extinction dans un thread séparé pour laisser le temps au TTS de parler
    def _shutdown_sequence():
        import time
        time.sleep(3) # Laisse 3 secondes pour dire la phrase de fin
        print("  [SYSTEM] Arrêt demandé via commande vocale.", flush=True)
        os._exit(0) # Force brute l'arrêt
        
    threading.Thread(target=_shutdown_sequence, daemon=True).start()
    
    return "Au revoir ! Je m'éteins."

# ==========================================
# INSTALLATION & TÉLÉCHARGEMENT LLM
# ==========================================

def download_with_progress(url, dest_path, progress_callback=None, done_callback=None):
    """Télécharge un fichier avec suivi de progression (Thread-safe)."""
    def _worker():
        try:
            print(f"  [DOWNLOAD] Démarrage : {url} -> {dest_path}", flush=True)
            response = requests.get(url, stream=True, timeout=10)
            response.raise_for_status()
            
            total_size = int(response.headers.get('content-length', 0))
            downloaded = 0
            
            with open(dest_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total_size > 0 and progress_callback:
                            fraction = downloaded / total_size
                            GLib.idle_add(progress_callback, fraction)
            
            print(f"  [DOWNLOAD] Terminé.", flush=True)
            # Rendre exécutable si c'est un binaire
            if "server" in dest_path or "llama" in dest_path:
                st = os.stat(dest_path)
                os.chmod(dest_path, st.st_mode | stat.S_IEXEC)
                
            if done_callback:
                GLib.idle_add(done_callback, True, f"Téléchargement réussi : {os.path.basename(dest_path)}")
                
        except Exception as e:
            print(f"  [DOWNLOAD] Erreur : {e}", flush=True)
            if done_callback:
                GLib.idle_add(done_callback, False, str(e))

    threading.Thread(target=_worker, daemon=True).start()

def install_llama_cpp_bin(dest_folder, progress_cb, done_cb):
    """Télécharge la dernière release linux de llama.cpp."""
    # URL directe vers la dernière release statique (souvent la plus compatible)
    # Note : Pour CUDA, il faudrait une version spécifique, ici on prend la version CPU/Vulkan générique
    url = "https://github.com/ggerganov/llama.cpp/releases/download/b4653/llama-b4653-bin-ubuntu-x64.zip" 
    # ASTUCE : Pour faire simple, on télécharge juste un binaire pré-compilé si dispo, 
    # mais GitHub release donne des ZIP. Pour simplifier ce script, on va supposer 
    # que l'utilisateur a 'unzip'. Sinon, voici une URL vers un build static tiers fiable ou on demande à l'utilisateur.
    
    # Pour cet exemple, je fournis une URL vers mon propre build static ou un build fiable direct sans zip
    # pour éviter la complexité de décompression en Python.
    # Remplaçons par une logique simulée ou une URL directe si vous en avez une. 
    # Sinon, on va télécharger le ZIP et extraire.
    
    # URL temporaire vers un build "llama-server" direct (exemple)
    # Mieux : On utilise curl dans un subprocess pour gérer le ZIP si besoin, 
    # mais ici utilisons la fonction download générique.
    pass # À implémenter dans l'UI via download_with_progress directement sur l'URL du modèle.