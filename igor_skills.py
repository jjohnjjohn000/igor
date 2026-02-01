# igor_skills.py
import re
import os

# --- 1. IMPORT DE LA CONFIGURATION ET DE L'ETAT GLOBAL ---
from igor_config import (
    MEMORY, 
    save_memory, 
    TASK_QUEUE, 
    ABORT_FLAG, 
    PLAY_ALARM_CALLBACK, 
    SET_MUTE_CALLBACK, 
    ON_FRAME_CALLBACK,
    WATCH_RUNNING, 
    ALARM_LOCK, 
    ALARM_STYLES,
    KNOWLEDGE_DIR,
    PROJECTS_DIR,
    AUTO_LEARN_MODE,
    LAST_WIKI_OPTIONS,
    smart_summarize,
    abort_tasks # Fonction vitale pour le bouton STOP
)

# --- 2. IMPORT DU SYSTEME (Fichiers, Fenêtres, Apps, Son) ---
from igor_system import (
    tool_launch, 
    tool_system_stats, 
    tool_list_apps, 
    tool_list_windows,
    tool_window_action,
    tool_window_focus,
    tool_window_fullscreen, 
    tool_close_window, 
    tool_open_file,
    tool_find_file,
    tool_project_new, 
    tool_project_list, 
    tool_project_save_file,
    tool_project_read_files, 
    tool_project_delete, 
    tool_project_delete_file,
    tool_project_todo_add, 
    tool_project_todo_list, 
    tool_project_todo_done,
    tool_project_set_active, 
    tool_project_display_current, 
    tool_project_change_current,
    tool_set_volume, 
    tool_exit, 
    bash_exec,
    get_system_volume, 
    set_raw_volume, 
    INSTALLED_APPS
)

# --- 3. IMPORT DE LA VISION ---
from igor_vision import (
    tool_vision_look, 
    tool_surveillance, 
    WATCH_THREAD, 
    VISION_LIBS_AVAILABLE,
    YOLO_AVAILABLE,
    GESTURES_AVAILABLE
)

# --- 4. IMPORT DU SAVOIR (Web, Wiki, Notes, Alarmes, Audio) ---
from igor_knowledge import (
    tool_search_web, 
    tool_weather, 
    tool_time, 
    tool_learn, 
    tool_consult,
    tool_calculate, 
    tool_note_write, 
    tool_note_read, 
    tool_note_clear, 
    tool_delete_note,
    tool_set_alarm, 
    tool_list_alarms, 
    tool_delete_alarm, 
    tool_set_alarm_sound,
    tool_media_control, 
    tool_music_checkup, 
    tool_listen_system,
    tool_shortcut_add, 
    tool_shortcut_list, 
    tool_shortcut_open, 
    tool_shortcut_delete,
    tool_remember,
    tool_read_memory
)

# --- 5. OUTILS DE CONFIGURATION RAPIDE (Définis ici pour simplicité) ---

def tool_check_context(arg):
    """
    Affiche le statut complet de l'agent avec formatage élégant.
    """
    # === 1. MÉMOIRE & DONNÉES ===
    nb_facts = len(MEMORY.get('facts', []))
    nb_notes = len(MEMORY.get('notebook', []))
    nb_alarms = len(MEMORY.get('alarms', []))
    
    memory_lines = []
    memory_lines.append(f"💭 **Mémoire** : {nb_facts} fait(s)")
    memory_lines.append(f"📝 **Notes** : {nb_notes} entrée(s)")
    memory_lines.append(f"⏰ **Alarmes** : {nb_alarms} programmée(s)")
    
    # === 2. CONFIGURATION VOIX & AUDIO ===
    speed = MEMORY.get('voice_speed', 1.1)
    alarm_snd = MEMORY.get('alarm_sound', 'Classique').capitalize()
    mute_state = "🔇 Muet" if MEMORY.get('muted') else "🔊 Actif"
    
    audio_lines = []
    audio_lines.append(f"🎙️ **Voix** : {speed}x")
    audio_lines.append(f"🔔 **Sonnerie** : {alarm_snd}")
    audio_lines.append(f"🔊 **Mode** : {mute_state}")
    
    # === 3. APPLICATIONS FAVORITES ===
    fav_music = MEMORY.get('fav_music_app', 'Non défini')
    raw_browser = MEMORY.get('fav_browser', 'Non défini')
    fav_email = MEMORY.get('fav_email', 'Non défini')
    fav_voip = MEMORY.get('fav_voip', 'Non défini')
    fav_terminal = MEMORY.get('fav_terminal', 'Non défini')
    fav_filemanager = MEMORY.get('fav_filemanager', 'Non défini')
    
    # Nettoyage du nom du navigateur
    if raw_browser and raw_browser != 'Non défini':
        # Extraction du nom propre (ex: "google-chrome" → "Chrome")
        browser_map = {
            'google-chrome': 'Chrome',
            'firefox': 'Firefox',
            'brave-browser': 'Brave',
            'microsoft-edge': 'Edge',
            'chromium': 'Chromium'
        }
        fav_web = browser_map.get(raw_browser.lower(), raw_browser.replace('-', ' ').title())
    else:
        fav_web = 'Non défini'
    
    app_lines = []
    app_lines.append(f"🎵 **Musique** : {fav_music}")
    app_lines.append(f"🌐 **Navigateur** : {fav_web}")
    app_lines.append(f"📧 **Email** : {fav_email}")
    app_lines.append(f"📞 **VoIP** : {fav_voip}")
    app_lines.append(f"💻 **Terminal** : {fav_terminal}")
    app_lines.append(f"📁 **Fichiers** : {fav_filemanager}")
    
    # === 4. PARAMÈTRES SYSTÈME ===
    auto_learn = "✅ Activé" if MEMORY.get('auto_learn') else "❌ Désactivé"
    
    system_lines = []
    system_lines.append(f"🧠 **Auto-apprentissage** : {auto_learn}")
    
    # === 5. PROJET ACTIF (si pertinent) ===
    current_project = MEMORY.get('current_project')
    if current_project:
        system_lines.append(f"📂 **Projet actif** : {current_project}")
    
    # === ASSEMBLAGE FINAL ===
    sections = [
        "📊 **Statut Agent**",
        "",
        "**💾 Données**",
        *memory_lines,
        "",
        "**🎛️ Audio**",
        *audio_lines,
        "",
        "**⚙️ Préférences**",
        *app_lines,
        "",
        "**🔧 Système**",
        *system_lines
    ]
    
    return "\n".join(sections)

def tool_set_agent_name(arg):
    """Définit le nom de l'IA (Agent)."""
    # On fait confiance au Cerveau (main.py) pour extraire juste le nom
    name = str(arg).strip().title()
    
    # Sécurité basique
    if not name or len(name) < 2: 
        return "Nom trop court ou invalide."
        
    MEMORY['agent_name'] = name
    save_memory(MEMORY)
    return f"Identité mise à jour. Je suis {name}."

def tool_set_user_name(arg):
    """Définit le nom de l'humain (User)."""
    name = str(arg).strip().title()
    
    if not name or len(name) < 2: 
        return "Nom trop court ou invalide."
        
    MEMORY['user_name'] = name
    save_memory(MEMORY)
    return f"Identité mise à jour. Vous êtes {name}."

def tool_set_speed(arg):
    arg = str(arg).lower()
    current = MEMORY.get('voice_speed', 1.1)
    if any(x in arg for x in ["normal", "reset"]):
        MEMORY['voice_speed'] = 1.0; save_memory(MEMORY); return "Vitesse normale. 1.0"
    
    if any(x in arg for x in ["moins", "lent", "doucement"]): new_speed = max(current - 0.25, 0.5)
    elif any(x in arg for x in ["plus", "rapide", "vite"]): new_speed = min(current + 0.25, 3.0)
    else: 
        nums = re.findall(r"\d+[\.,]\d+|\d+", arg)
        new_speed = float(nums[0].replace(',', '.')) if nums else current

    MEMORY['voice_speed'] = round(new_speed, 2)
    save_memory(MEMORY)
    return f"Vitesse réglée sur {MEMORY['voice_speed']}"

def tool_set_mute(arg):
    """
    Active ou désactive le mode muet via le callback de l'interface (igor_window).
    """
    arg = str(arg).lower().strip()
    
    # True = Muet, False = Parler
    target_state = True 
    
    if any(x in arg for x in ["off", "non", "stop", "unmute", "parle", "parler", "active", "remets"]):
        target_state = False
    elif any(x in arg for x in ["toggle", "inverse", "change"]):
        target_state = "toggle"
        
    if SET_MUTE_CALLBACK:
        return SET_MUTE_CALLBACK(target_state)
    return "Fonction mute non connectée à l'interface."

def tool_set_fav_music(arg):
    app_name = str(arg).strip()
    MEMORY['fav_music_app'] = app_name
    save_memory(MEMORY)
    return f"C'est noté. Votre application musicale par défaut est maintenant {app_name}."

def tool_set_fav_browser(arg):
    app_name = str(arg).strip()
    
    if " " in app_name:
        cmd = app_name
    else:
        cmd = None
        if app_name in INSTALLED_APPS:
            cmd = INSTALLED_APPS[app_name]
        
        if not cmd:
            common_map = {
                "chrome": "google-chrome", "firefox": "firefox", 
                "brave": "brave-browser", "edge": "microsoft-edge",
                "chromium": "chromium-browser", "opera": "opera"
            }
            cmd = common_map.get(app_name.lower(), app_name)

    MEMORY['fav_browser'] = cmd
    save_memory(MEMORY)
    return f"C'est noté. Navigateur : {cmd}"

def tool_set_fav_email(arg):
    """Configure l'application email favorite (Gmail, Thunderbird, etc.)"""
    app_name = str(arg).strip()
    MEMORY['fav_email'] = app_name
    save_memory(MEMORY)
    return f"C'est noté. Application email par défaut : {app_name}."

def tool_set_fav_voip(arg):
    """Configure l'application VoIP favorite (Zoom, Teams, Discord, etc.)"""
    app_name = str(arg).strip()
    MEMORY['fav_voip'] = app_name
    save_memory(MEMORY)
    return f"C'est noté. Application VoIP par défaut : {app_name}."

def tool_set_fav_terminal(arg):
    """Configure le terminal favori (gnome-terminal, konsole, etc.)"""
    app_name = str(arg).strip()
    MEMORY['fav_terminal'] = app_name
    save_memory(MEMORY)
    return f"C'est noté. Terminal par défaut : {app_name}."

def tool_set_fav_filemanager(arg):
    """Configure le gestionnaire de fichiers favori (nautilus, dolphin, etc.)"""
    app_name = str(arg).strip()
    MEMORY['fav_filemanager'] = app_name
    save_memory(MEMORY)
    return f"C'est noté. Gestionnaire de fichiers par défaut : {app_name}."

# --- 6. REGISTRE DES OUTILS (TOOLS MAP) ---
# C'est ce dictionnaire que main.py utilise pour mapper les commandes JSON aux fonctions Python

TOOLS = {
    # DIAGNOSTIC
    "STATUS": tool_check_context,
    "SYSTEM_STATS": tool_system_stats,
    
    # CONFIG
    "AGENTNAME": tool_set_agent_name,
    "USERNAME": tool_set_user_name,
    "SET_SPEED": tool_set_speed,
    "SET_MUTE": tool_set_mute,
    "SET_DEFAULT_MUSIC": tool_set_fav_music,
    "SET_DEFAULT_BROWSER": tool_set_fav_browser,
    "SET_DEFAULT_EMAIL": tool_set_fav_email,
    "SET_DEFAULT_VOIP": tool_set_fav_voip,
    "SET_DEFAULT_TERMINAL": tool_set_fav_terminal,
    "SET_DEFAULT_FILEMANAGER": tool_set_fav_filemanager,
    "SET_ALARM_SOUND": tool_set_alarm_sound,
    
    # SYSTEME
    "SHELL": bash_exec,
    "LAUNCH": tool_launch,
    "OPEN_FILE": tool_open_file,
    "LIST_APPS": tool_list_apps,
    "LIST_WINDOWS": tool_list_windows,
    "CLOSE_WINDOW": tool_close_window,
    "WINDOW_ACTION": tool_window_action,
    "FOCUS_WINDOW": tool_window_focus,
    "FULLSCREEN": tool_window_fullscreen,
    "FIND": tool_find_file,
    "EXIT": tool_exit,
    
    # WEB / TEMPS
    "SEARCH": tool_search_web,
    "TIME": tool_time,
    "WEATHER": tool_weather,
    
    # RACCOURCIS
    "SHORTCUT_ADD": tool_shortcut_add,
    "SHORTCUT_LIST": tool_shortcut_list,
    "SHORTCUT_DELETE": tool_shortcut_delete,
    "SHORTCUT_OPEN": tool_shortcut_open,
    
    # MEMOIRE & SAVOIR
    "MEM": tool_remember,
    "READ_MEM": tool_read_memory,
    "LEARN": tool_learn,
    "LOCALKNOWLEDGE": tool_consult,
    "CHAT": lambda x: f"{MEMORY['agent_name']}: {x}",
    "MATH": tool_calculate,
    "CALC": tool_calculate, # Alias pour l'IA qui préfère parfois CALC
    
    # CARNET DE NOTES
    "NOTE": tool_note_write,
    "READ_NOTE": tool_note_read,
    "CLEAR_NOTE": tool_note_clear,
    "DEL_NOTE": tool_delete_note,
    
    # ALARMES
    "ALARM": tool_set_alarm,
    "SET_ALARM": tool_set_alarm, # Alias pour corriger l'hallucination IA
    "SHOW_ALARMS": tool_list_alarms,
    "DEL_ALARM": tool_delete_alarm,
    
    # PROJETS (DEV)
    "PROJECT_NEW": tool_project_new,
    "PROJECT_DISPLAY_CURRENT": tool_project_display_current,
    "PROJECT_CHANGE_CURRENT": tool_project_change_current,
    "PROJECT_LIST": tool_project_list,
    "PROJECT_SAVE": tool_project_save_file,
    "PROJECT_SHOW": tool_project_read_files,
    "PROJECT_LIST_FILES": tool_project_read_files,
    "PROJECT_DELETE": tool_project_delete,
    "PROJECT_DELETE_FILE": tool_project_delete_file,
    "PROJECT_TODO_ADD": tool_project_todo_add,
    "PROJECT_TODO_LIST": tool_project_todo_list,
    "PROJECT_TODO_DONE": tool_project_todo_done,
    "PROJECT_SET_ACTIVE": tool_project_set_active,

    # VISION & AUDIO
    "VISION": tool_vision_look,
    "WATCH": tool_surveillance,
    "LISTEN_SYSTEM": tool_listen_system,
    "VOLUME": tool_set_volume,
    "MEDIA": tool_media_control,
    "MUSIC_CHECK": tool_music_checkup
}