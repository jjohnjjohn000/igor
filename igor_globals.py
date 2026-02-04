# igor_globals.py
import threading
import igor_skills as skills

# --- CONFIGURATION API & MODÈLES ---
API_URL = "http://localhost:8080/completion"
MODEL_PATH = "model"

# --- GESTION PROCESSUS LLM LOCAL ---
LLM_SERVER_PROCESS = None

# --- GLOBALS PARTAGÉS ---
# Ces variables sont modifiées par d'autres modules, donc on les centralise ici
WAKE_WORD_LIST = '["igor", "assistant", "ordinateur"]'
WAIT_FOR_WAKE_WORD = True
IS_MUTED = skills.MEMORY.get('muted', False)
CURRENT_PLAYER_PROCESS = None
SPEAK_LOCK = threading.Lock()

# --- IA & MÉMOIRE ---
CHAT_HISTORY = []
QUERY_CACHE = {}
CACHE_MAX_SIZE = 100
MAX_HISTORY = 6

# --- STATISTIQUES ---
STATS = {
    "heuristic_hits": 0,
    "cache_hits": 0,
    "ai_calls": 0,
    "intents": {}
}

# --- DICTIONNAIRES DE LOGIQUE ---
# --- MICRO-PROMPTS PAR CATÉGORIE D'INTENTION ---
MICRO_PROMPTS = {
    "IDENTITY": """
**LOGIQUE D'IDENTITÉ STRICTE :**

CAS 1 : MODIFICATION / AFFIRMATION (L'utilisateur définit la réalité)
Si l'utilisateur dit "Je m'appelle X", "Mon nom est X", "Appelle-moi X" ou "Tu es Y", "Ton nom est Y" :
→ TU DOIS utiliser l'outil pour SAUVEGARDER ce changement.
→ Extrais UNIQUEMENT le nom propre (ex: "Marc", pas "je suis Marc").

Exemples :
"Je suis Sophie" → {{"tool": "USERNAME", "args": "Sophie"}}
"Appelle-moi Maître" → {{"tool": "USERNAME", "args": "Maître"}}
"Change ton nom en Jarvis" → {{"tool": "AGENTNAME", "args": "Jarvis"}}
"Tu es Igor" → {{"tool": "AGENTNAME", "args": "Igor"}}

CAS 2 : QUESTION (L'utilisateur demande une info)
Si l'utilisateur demande "Qui suis-je ?", "Comment je m'appelle ?", "Qui es-tu ?" :
→ TU DOIS utiliser l'outil CHAT pour répondre en utilisant les infos du CONTEXTE (en haut).
→ N'utilise PAS USERNAME ou AGENTNAME pour une question.

Exemples :
"Qui suis-je ?" → {{"tool": "CHAT", "args": "Vous êtes {user_name}."}}
"Comment tu t'appelles ?" → {{"tool": "CHAT", "args": "Je suis {agent_name}."}}

**EXTINCTION / ARRÊT** (CRITIQUE):
"Éteins-toi" → {{"tool": "EXIT", "args": ""}}
"Arrête-toi" → {{"tool": "EXIT", "args": ""}}
"Ferme-toi" → {{"tool": "EXIT", "args": ""}}
"Quitte" → {{"tool": "EXIT", "args": ""}}
""",
    
    "MEDIA": """
Exemples média:
"C'est quoi ce son ?" → {{"tool": "LISTEN_SYSTEM", "args": "15"}}
"Pause" → {{"tool": "MEDIA", "args": "pause"}}
"Tais-toi" → {{"tool": "SET_MUTE", "args": "on"}}
Règle: LISTEN_SYSTEM pour identifier, MEDIA pour contrôler, SET_MUTE pour agent
""",
    
    "SHORTCUT": """
Exemples raccourcis:
"Sauvegarde cette vidéo" → {{"tool": "SHORTCUT_ADD", "args": "Vidéo"}}
"Crée le raccourci mail vers gmail.com" → {{"tool": "SHORTCUT_ADD", "args": "mail vers gmail.com"}}
"Garde ce site comme Recettes" → {{"tool": "SHORTCUT_ADD", "args": "Recettes"}}
"Ouvre mes mails" → {{"tool": "SHORTCUT_OPEN", "args": "mails"}}
"Quels sont mes raccourcis ?" → {{"tool": "SHORTCUT_LIST", "args": ""}}
"Supprime le raccourci Rome" → {{"tool": "SHORTCUT_DELETE", "args": "Rome"}}
Règle: Si l'utilisateur donne une URL explicite, laisse-la dans args. Sinon, juste le nom.
""",
    
    "VISION": """
Exemples vision:
"Prends une photo" → {{"tool": "VISION", "args": "webcam"}}
"Regarde mon écran" → {{"tool": "VISION", "args": "screen"}}
"Analyse Firefox" → {{"tool": "VISION", "args": "fenêtre Firefox"}}

CAS RAPIDE:
"Vite, regarde l'écran" → {{"tool": "VISION", "args": "vite screen"}}
"Analyse ça rapidement" → {{"tool": "VISION", "args": "vite screen"}}
"Prends une photo vite" → {{"tool": "VISION", "args": "vite webcam"}}

Règle: webcam=selfie, screen=écran, "fenêtre X"=app
Règle: Si "vite" ou "rapide" est dit, INCLUS le mot "vite" dans args.
""",
    
    "KNOWLEDGE": """
Exemples savoir:
"Que sais-tu des serpents ?" → {{"tool": "LEARN", "args": "serpents"}}
"Parle moi de Napoléon" → {{"tool": "LEARN", "args": "Napoléon"}}
"C'est quoi Python ?" → {{"tool": "LOCALKNOWLEDGE", "args": "Python"}} (si existe)
"C'est quoi Rust ?" → {{"tool": "LEARN", "args": "Rust"}} (si n'existe pas)
"Cherche Python sur le web" → {{"tool": "SEARCH", "args": "Python"}}
"Calcule 2+2" → {{"tool": "MATH", "args": "2+2"}}

ATTENTION CRITIQUE:
- "Lance la calculatrice" → {{"tool": "LAUNCH", "args": "calculatrice"}}  (PAS MATH !)
- "Ouvre la calculatrice" → {{"tool": "LAUNCH", "args": "calculatrice"}}
- MATH est SEULEMENT pour les calculs directs avec opérateurs (+, -, *, /)

Règle: LOCALKNOWLEDGE si existe, LEARN sinon, SEARCH si "web" explicite, MATH si opération mathématique
Savoirs locaux: {local_files}
""",
    
    "LAUNCH": """
Exemples lancement:
"Ouvre Firefox" → {{"tool": "LAUNCH", "args": "Firefox"}}
"Lance la calculatrice" → {{"tool": "LAUNCH", "args": "calculatrice"}}

**COMMANDES TERMINAL / SHELL** :
Si l'utilisateur dit "commande" ou une instruction shell précise :
"Lance la commande ls -la" → {{"tool": "SHELL", "args": "ls -la"}}
"Exécute echo test" → {{"tool": "SHELL", "args": "echo test"}}

VIDÉOS YOUTUBE (TRÈS IMPORTANT):
"Mets Daft Punk sur Youtube" → {{"tool": "LAUNCH", "args": "Youtube Daft Punk"}}
"Lance vidéo de chat" → {{"tool": "LAUNCH", "args": "Youtube chat"}}
"Regarde Asimov sur Youtube" → {{"tool": "LAUNCH", "args": "Youtube Asimov"}}
"Joue la musique de Stranger Things" → {{"tool": "LAUNCH", "args": "Youtube Stranger Things musique"}}

Règle CRITIQUE: 
- Pour TOUTE vidéo/musique, utilise TOUJOURS le format "Youtube [recherche]"
- Le système trouvera automatiquement la meilleure vidéo
- Si l'utilisateur dit "sur Youtube", garde juste le titre dans args
- Exemples de transformation:
  * "Mets X sur Youtube" → "Youtube X"
  * "Vidéo de X" → "Youtube X"
  * "Regarde X" → "Youtube X"
- Pour sites web: URL directe ou nom de domaine
""",
    
    "ALARM": """
Exemples alarme:
"Réveille-moi à 7h" → {{"tool": "ALARM", "args": "à 7h"}}
"Alarme dans 30min" → {{"tool": "ALARM", "args": "dans 30min"}}
"Supprime alarme 8h" → {{"tool": "DEL_ALARM", "args": "8h"}}
Règle: Copie EXACTEMENT la phrase temporelle
""",
    
    "PROJECT": """
Exemples projet:

"Crée projet Web" → {{"tool": "PROJECT_NEW", "args": "Web"}}
"Sauve index.html" → {{"tool": "PROJECT_SAVE", "args": "index.html :: "}}
"Liste fichiers" → {{"tool": "PROJECT_SHOW", "args": ""}}

**GESTION TÂCHES (TODO)** :
"Ajoute tâche faire les tests" → {{"tool": "PROJECT_TODO_ADD", "args": "faire les tests"}}
"Coche la tâche 1" → {{"tool": "PROJECT_TODO_DONE", "args": "1"}}
"Met le point 2 à fait" → {{"tool": "PROJECT_TODO_DONE", "args": "2"}}
"Valide la tache numéro 3" → {{"tool": "PROJECT_TODO_DONE", "args": "3"}}

**RECHERCHE DE FICHIERS** (CRITIQUE):
"Trouve index.html" → {{"tool": "FIND", "args": "index.html"}}
"Cherche cv.pdf" → {{"tool": "FIND", "args": "cv.pdf"}}
"Où est mon diplome" → {{"tool": "FIND", "args": "diplome"}}
"Localise config.py" → {{"tool": "FIND", "args": "config.py"}}

RÈGLE ABSOLUE:
- Si "trouve", "cherche", "où est", "localise" + NOM DE FICHIER → Utilise FIND
- FIND cherche dans TOUT le système (HOME + projets)
- PROJECT_SHOW liste les fichiers d'un projet (sans recherche)

Projet actif: {current_proj}
Règle: Si projet pas précisé, utilise actif
""",
    
    "CONTROL": """
**RÈGLE #1 ABSOLUE - DÉTECTION MULTI-ACTIONS** :
SI la phrase contient "et", "puis", "ensuite", "après" :
→ OBLIGATOIRE : Renvoyer un TABLEAU JSON [...] avec PLUSIEURS objets

**EXEMPLES MULTI-ACTIONS** :
"Ferme la calculatrice et ouvre Firefox" → [{"tool": "CLOSE_WINDOW", "args": "calculatrice"}, {"tool": "LAUNCH", "args": "Firefox"}]
"Ferme Chrome puis lance le terminal" → [{"tool": "CLOSE_WINDOW", "args": "Chrome"}, {"tool": "LAUNCH", "args": "terminal"}]

**EXEMPLE FACTORISATION (Verbe unique)** :
"Ferme Firefox et la calculatrice" → [{"tool": "CLOSE_WINDOW", "args": "Firefox"}, {"tool": "CLOSE_WINDOW", "args": "calculatrice"}]
"Lance Spotify et VSC" → [{"tool": "LAUNCH", "args": "Spotify"}, {"tool": "LAUNCH", "args": "VSC"}]

**EXEMPLES ACTION UNIQUE** :
"Ferme Firefox" → {"tool": "CLOSE_WINDOW", "args": "Firefox"}
"Ferme le terminal" → {"tool": "CLOSE_WINDOW", "args": "terminal"}
"Maximise le terminal" → {"tool": "FULLSCREEN", "args": "terminal"}
"Mets Firefox en plein écran" → {"tool": "FULLSCREEN", "args": "Firefox"}

**FERMETURE MASSIVE (IMPORTANT)** :
Si l'utilisateur dit "tous", "toutes" ou utilise un PLURIEL ("les terminaux") :
→ TU DOIS inclure le mot "tous/toutes" ou garder le pluriel dans 'args'.
"Ferme tous les terminaux" → {"tool": "CLOSE_WINDOW", "args": "tous les terminaux"}
"Ferme les fenêtres de code" → {"tool": "CLOSE_WINDOW", "args": "toutes les fenêtres code"}

**IMPORTANT - LISTE VS ACTIONS** :
- "Montre les fichiers" → {"tool": "PROJECT_SHOW", "args": ""}  (PAS une action de contrôle)
- "Liste les fenêtres" → {"tool": "LIST_WINDOWS", "args": ""}
- Si "liste", "montre", "affiche" sans action de fermeture/lancement → Ce n'est PAS du CONTROL
""",
    
    "MEMORY": """
Exemples mémoire:
"Note RDV demain 14h" -> {{"tool": "NOTE", "args": "RDV demain 14h"}}
"Lis mes notes" -> {{"tool": "READ_NOTE", "args": ""}}
"Retiens que j'aime le jazz" -> {{"tool": "MEM", "args": "aime le jazz"}}
"Que sais-tu sur moi ?" -> {{"tool": "READ_MEM", "args": ""}}
"Qu'est-ce que tu sais me concernant ?" -> {{"tool": "READ_MEM", "args": ""}}
"Mémoire utilisateur" -> {{"tool": "READ_MEM", "args": ""}}

RÈGLE : READ_MEM concerne UNIQUEMENT l'utilisateur (moi, je). Si le sujet est externe (ex: serpents, histoire), c'est KNOWLEDGE.
Mémoire actuelle: {facts}
""",
    
    "SEARCH": """
Exemples recherche:
"Cherche recette crêpes" → {{"tool": "SEARCH", "args": "recette crêpes"}}
"Quelle heure est-il ?" → {{"tool": "TIME", "args": ""}}
"Météo telle ville" → {{"tool": "WEATHER", "args": "Telle ville"}}
"Quel temps fait-il ?" → {{"tool": "WEATHER", "args": ""}}
"Donne la météo" → {{"tool": "WEATHER", "args": ""}}

RÈGLE CRITIQUE : Si l'utilisateur ne précise PAS de ville, l'argument DOIT être vide ("").
""",
    
    "CHAT": """
Règle: Si aucune action concrète, utilise CHAT pour répondre.
"""
}

# ÉGALEMENT : Modifier INTENT_TOOL_GROUPS pour que LAUNCH soit prioritaire
INTENT_TOOL_GROUPS = {
    "IDENTITY": ["BASE", "MEMORY", "CONFIG"],  # Ajout CONFIG (pour AGENTNAME, USERNAME)
    "MEDIA": ["AUDIO", "CONFIG"],             # Ajout CONFIG (pour SET_MUTE, SET_DEFAULT_MUSIC)
    "SHORTCUT": ["WEB", "CONFIG"],            # Ajout CONFIG (pour SET_DEFAULT_BROWSER)
    "VISION": ["SYSTEM"],
    "PROJECT": ["DEV", "SYSTEM", "CONFIG"],   # Ajout CONFIG (pour SET_DEFAULT_TERMINAL/IDE)
    "ALARM": ["MEMORY", "CONFIG"],            # Ajout CONFIG (pour SET_ALARM_SOUND)
    "KNOWLEDGE": ["KNOWLEDGE", "WEB"],
    "LAUNCH": ["SYSTEM", "WEB", "CONFIG"],    # Ajout CONFIG (pour définir les apps par défaut si besoin)
    "CONTROL": ["SYSTEM", "AUDIO", "CONFIG"], # Ajout CONFIG (pour Mute/Volume/Speed)
    "MEMORY": ["MEMORY", "CONFIG"],
    "SEARCH": ["WEB", "CONFIG"],              # Ajout CONFIG (pour Time/Weather/Browser)
    "CHAT": ["BASE", "CONFIG"]                # Au cas où on demande de changer un réglage en discutant
}

TOOLS_GROUPS = {
    "BASE": [
        "CHAT(msg: str) // Discuter ou répondre",
        "EXIT() // Éteindre l'assistant",
        "STATUS() // Diagnostic technique (Volume, Vitesse, Config, Uptime)",
        "SYSTEM_STATS() // RAM/CPU/Uptime"
    ],
    "CONFIG": [
        "AGENTNAME(new_agent_name: str) // Changer le nom de l'assistant",
        "USERNAME(new_user_name: str) // Changer le nom de l'utilisateur",
        "SET_SPEED(val: float) // Vitesse voix (ex: 1.2)",
        "SET_MUTE(state: 'on'|'off'|'toggle') // Rendre l'assistant muet",
        "SET_DEFAULT_MUSIC(app_or_url: str) // Définir favori musical",
        "SET_DEFAULT_BROWSER(name: str) // Changer navigateur par défaut",
        "SET_DEFAULT_EMAIL(app_name: str) // Définir application email",
        "SET_DEFAULT_VOIP(app_name: str) // Définir application VoIP",
        "SET_DEFAULT_TERMINAL(app_name: str) // Définir terminal",
        "SET_DEFAULT_FILEMANAGER(app_name: str) // Définir gestionnaire de fichiers",
        "SET_ALARM_SOUND(style: 'douceur'|'alerte'|'classique') // Changer sonnerie"
    ],
    "SYSTEM": [
        "LAUNCH(app_name: str) // Ouvrir logiciel, site web ou vidéo ('Youtube [Titre]')",
        "OPEN_FILE(args: 'Fichier.ext :: Dossier') // Ouvre fichier SYSTÈME (pas projet). Ex: 'facture.pdf :: Documents', 'photo.jpg :: Images'. Si dossier omis, cherche dans HOME. Extensions supportées: .pdf .jpg .doc .mp4 .txt etc.",
        "LIST_APPS(filter?: str) // Lister logiciels installés",
        "LIST_WINDOWS(filter?: str) // Lister fenêtres ouvertes",
        "CLOSE_WINDOW(name: str) // Fermer une fenêtre",
        "FULLSCREEN(args: str) // Args: 'Firefox' (F11), 'Maximise Terminal' (Agrandir) ou 'Youtube :: video' (touche 'f').",
        "SHELL(cmd: str) // Commande terminal simple",
        "VISION(arg: str) // args: 'webcam' (pour photo/selfie) OU 'screen' (pour écran) OU 'fenêtre X'", 
        "WATCH(state: 'on'|'off') // Surveillance continue",
        "FIND(filename: str) // Recherche fichier local"
    ],
    "WEB": [
        "SEARCH(query: str) // Recherche Google/DuckDuckGo",
        "TIME(location?: str) // Heure",
        "WEATHER(location?: str) // Météo",
        "SHORTCUT_ADD(args: 'Nom' OR 'Nom vers URL') // Si l'utilisateur dicte l'URL, incluez-la. Sinon donnez juste le nom.",
        "SHORTCUT_LIST()",
        "SHORTCUT_DELETE(name: str)",
        "SHORTCUT_OPEN(name: str)"
    ],
    "AUDIO": [
        "VOLUME(val: int) // 0-100",
        "MEDIA(action: 'play'|'pause'|'next'|'prev') // Mettre en pause/lecture Youtube/Spotify/VLC",
        "MUSIC_CHECK() // Vérifier l'ambiance. Lance la musique si tout est calme. (NE PAS utiliser pour 'Quelle est cette musique ?')",
        "LISTEN_SYSTEM(duration: int) // Écouter audio PC (sec)"
    ],
    "MEMORY": [
        "NOTE(text: str) // Ajouter au carnet",
        "READ_NOTE() // Lire carnet",
        "DEL_NOTE(keyword: str) // Supprimer note",
        "CLEAR_NOTE() // Vider carnet",
        "MEM(fact: str) // Retenir fait durable sur user",
        "READ_MEM() // Réciter ce que je sais sur l'utilisateur UNIQUEMENT (Pas de savoir général)",
        "ALARM(phrase: str) // 'tous les jours à 8h'",
        "DEL_ALARM(time_approx: str) // '8h'",
        "SHOW_ALARMS()"
    ],
    "KNOWLEDGE": [
        "LEARN(topic: str) // Télécharger page Wikipedia",
        "LOCALKNOWLEDGE(topic: str) // Lire savoir local",
        "MATH(expr: str) // Calcul (2+2) ou équation (2x=4)"
    ],
    "DEV": [
        "PROJECT_NEW(name: str) // Crée et active un projet",
        "PROJECT_DISPLAY_CURRENT() // Quel est le projet actif ?",
        "PROJECT_CHANGE_CURRENT(name: str) // Changer de projet actif",
        "PROJECT_LIST()",
        "PROJECT_SHOW(name?: str) // Fichiers du projet (actif si arg vide)",
        "PROJECT_SAVE(args: 'Fichier :: Contenu' OR 'Projet :: Fichier :: Contenu')",
        "PROJECT_DELETE(name: str) // AVEC CONFIRMATION CHAT AVANT",
        "PROJECT_DELETE_FILE(args: 'Projet :: Fichier' OR 'Fichier') // AVEC CONFIRMATION",
        "PROJECT_TODO_ADD(args: 'Tâche' OR 'Projet :: Tâche')",
        "PROJECT_TODO_LIST(project: str) // Voir la liste",
        "PROJECT_TODO_DONE(args: 'Projet :: Numéro' OR 'Numéro') // Valider une tâche (ex: 'Web :: 1')"
    ]
}

EXIT_ACTIONS = {"eteins", "eteint", "arrete", "ferme", "eteinstoi", "eteinttoi", "arretetoi", "fermetoi", "quit", "quitte", "bye", "revoir", "ciao", "shutdown", "exit", "stop"}
EXIT_OBJECTS = {"toi", "ordinateur", "programme", "pc", "laptop", "systeme", "system"}
BASE_ACTIONS = {"va", "utilisation"} #utilisation n'est pas une action, temporaire
BASE_OBJECTS = {"ordinateur", "pc", "laptop", "systeme", "system", "ram", "memoire"}
BASE_INQUIRIES = {"comment"}
MUTE_ACTIONS = {"eteins", "eteint", "arrete", "ferme", "stop", "coupe", "parle", "tais", "taistoi", "met", "mets", "mettoi", "metstoi", "mode"}
MUTE_OBJECTS = {"son", "volume", "bruit", "gueule", "clapet", "bouche", "mode", "muet", "silencieux", "silence"}
VOLUME_ACTIONS = {"baisse", "descend", "met", "mets"}
VOLUME_OBJECTS = {"son", "volume", "bruit"}
OPEN_ACTIONS = {"ouvre", "affiche", "montre", "lis", "open", "lance"}
OPEN_OBJECTS = {
    "fichier", "documents", "document", "picture", "pictures",
    "images", "photos", "videos", "musique",
    ".pdf", ".jpg", ".doc", ".txt", ".mp4", ".zip"}
CLOSE_ACTIONS = {"ferme", "close", "quitte", "termine"}
CLOSE_OBJECTS = {"fenetre", "app", "application", "programme", "program", "terminal", "terminaux", "terminals"}
CONTROL_ACTIONS = {"ferme", "close", "minimise", "minimize", "reduit", "rapetisse"}
CONTROL_OBJECTS = {"fenetre", "app", "application", "programme", "program", "terminal", "terminaux", "terminals"}
FOCUS_ACTIONS = {"focus", "met"}
FOCUS_OBJECTS = {"fenetre", "avantplan", "application", "programme", "program", "avant-plan", "avant plan"}
FULLSCREEN_ACTIONS = {"aggrandi", "met", "mets", "maximise", "maximize"}
FULLSCREEN_OBJECTS = {"fenetre", "avantplan", "application", "programme", "program", "avant-plan", "avant plan"}
FULLSCREEN_STATES = {"plein ecran", "plein", "ecran", "full screen", "fullscreen", "pleinecran","pleineecran", "pleine ecran"}
SYSTEM_FOLDERS = {
    "documents", "document", "downloads", "bureau", "desktop", "picture", "pictures",
    "images", "photos", "videos", "musique", "telechargements", "download", "telechargement"}
VISION_ACTIONS = {"prend", "regarde", "vois", "capture", "analyse", "decris"}
VISION_OBJECTS = {"photo", "selfie", "webcam", "image", "ecran", "screen", "bureau"}
LISTEN_ACTIONS = {"questce", "joue", "joue", "cest", "ecoute", "passe"}
LISTEN_OBJECTS = {"musique", "pc", "son"}
LISTEN_INQUIRIES = {"quoi", "qu", "qui"}
SHORTCUT_ACTIONS = {"sauvegarde", "garde", "enregistre", "bookmark", "ajoute", "cree", "ouvre"}
SHORTCUT_OBJECTS = {"favori", "bookmark", "raccourci", "onglet", "tab", "site"}
SHORTCUTLIST_ACTIONS = {"listemoi", "montremoi", "liste", "montre", "affiche"}
SHORTCUTLIST_OBJECTS = {"raccourci", "bookmark", "raccourcis"}
MEDIA_ACTIONS = {"ecoute", "pause", "play", "augmente", "monte", "baisse", "reduit", "lecture", "met", "mets"}
MEDIA_OBJECTS = {"musique", "son", "audio", "volume", "video", "film", "ambiance"}
FILE_EXTENSIONS = {".pdf", ".jpg", ".doc", ".txt", ".mp4", ".zip"}
MEMORY_ACTIONS = {"retiens", "carnet", "memorise", "souviens", "rappelle", "ajoute", "note", "noter"}
MEMORY_OBJECTS = {"carnet", "note", "notes", "alarme", "alarmes", "fait", "faits", "information", "informations"}
MEMORY_INQUIRIES = {"que"}
READMEM_ACTIONS = {"souviens", "rappelle"}
READMEM_OBJECTS = {"souvenirs", "memoire"}
NOTES_ACTIONS = {"efface", "vide", "vider","lis", "supprime", "enleve"}
NOTES_OBJECTS = {"note", "notes"}
SEARCH_ACTIONS = {"cherche", "google", "donne", "trouve", "fais"}
SEARCH_OBJECTS = {"meteo", "weather", "heure", "time", "recette"}
TIME_ACTIONS = {"estil", "est", "donne"}
TIME_OBJECTS = {"heure"}
TIME_INQUIRIES = {"quel", "quelle"}
METEO_ACTIONS = {"faitil", "fait", "donne", "trouve", "estcequ", "est", "cest"}
METEO_OBJECTS = {"meteo", "temps", "temperature", "chaud", "froid"}
METEO_INQUIRIES = {"quel", "quelle"}
LEARN_ACTIONS = {"cest", "est", "apprends", "apprendsmoi", "explique", "sais", "saistu"}
LEARN_OBJECTS = {"quoi", "qui", "qu", "sujet", "personne", "chose", "choses"}
SETFAVORITE_ACTIONS = {"est", "configure", "set", "utilise"}
SETFAVORITE_OBJECTS = {"email", "music", "musique", "mail", "navigateur", "browser", "appels" , "voip", "appel", "terminal"}
FIND_ACTIONS = {"trouve", "cherche", "est"}
FIND_OBJECTS = {"fichiers", "fichier"}
SHELL_ACTIONS = {"lance", "execute", "run"}
SHELL_OBJECTS = {"commande", "bash"}
WINDOWSTATS_ACTIONS = {"sont"}
WINDOWSTATS_OBJECTS = {"fenetre", "fenetres", "window", "windows", "application", "applications", "programme", "programmes"}
WINDOWSTATS_INQUIRIES = {"quelles", "quelle", "quel", "quels"}
WINDOWSTATS_STATES = {"ouverte", "ouvertes"}
WEB_ACTIONS = {"ouvre", "met", "montre", "mets", "open", "lance"}
WEB_OBJECTS = {"youtube"}
STOP_WORDS = {
    # --- Articles & Déterminants ---
    "le", "la", "les", 
    "un", "une", "des",
    "du", "de", 
    "ce", "cet", "cette", "ces", "ca", 
    "mon", "ton", "son", "ma", "ta", "sa", "mes", "tes", "ses",
    "notre", "votre", "leur", "nos", "vos", "leurs",

    # --- Pronoms (Sujets / Objets / Relatifs) ---
    # Note: J'ai exclu "toi" car il est dans votre liste EXIT_OBJECTS
    "je", "tu", "il", "elle", "on", "nous", "vous", "ils", "elles",
    "me", "te", "se", "en", "lui",
    "moi", "toi", "eux", "celui", "celle", "ceux",
    "qui", "que", "quoi", "dont", "ou", "qu",

    # --- Prépositions & Conjonctions ---
    "au", "aux", 
    "avec", "sans", "pour", "par", 
    "dans", "sur", "sous", "vers", "chez",
    "et", "ou", "ni", "mais", "donc", "or", "car", 
    "si", "comme", "quand",

    # --- Politesse & Formules (Bruit fréquent en vocal) ---
    "sil", "plait", "stp", "svp", "merci", # "s'il" devient souvent "sil" ou "s il" après nettoyage
    "bonjour", "salut", "hello", "please", 
    "monsieur", "madame", "assistant", "bot", "robot",

    # --- Adverbes / Temps / Quantité (Fillers) ---
    "maintenant", "immediatement", "vite", "rapidement",
    "tout", "toute", "tous", "toutes",
    "ici", "la", "juste", "bien", "alors", "bon", "ok", 
    "fois", "encore", "aussi", "meme", "deja"

    # --- Alphabet Complet
    "a", "b", "c", "d", "e", "f", "g", "h", "i", "j", "k", "l", "m", "n", "o", "p", "q", "r", "s", "t", "u", "v", "w", "x", "y", "z"
}
MULTI_ACTIONS = {
    # --- Démarrage / Activation / Ouverture ---
    "allume", "allumer", "allumez",
    "ouvre", "ouvrir", "ouvrez",
    "active", "activer", "activez",
    "lance", "lancer", "lancez",
    "demarre", "demarrer", "demarrez",
    "execute", "executer", "run", "start",
    "charge", "charger", "load",
    "debuter", "commencer", "commence",
    "reveille", "reveiller", "wake",
    "deverrouille", "unlock",

    # --- Arrêt / Fermeture / Désactivation ---
    "eteins", "eteindre", "eteignez",
    "ferme", "fermer", "fermez",
    "coupe", "couper", "coupez",
    "arrete", "arreter", "arretez", "stop", "stope",
    "desactive", "desactiver", "disable",
    "quitte", "quitter", "exit",
    "tue", "tuer", "kill",
    "termine", "terminer", "finis", "finir",
    "suspendre", "suspend", "pause",
    "verrouille", "lock",

    # --- Redémarrage / Système ---
    "redemarre", "redemarrer", "reboot", "restart",
    "relance", "relancer", "reset", "reinitialise",
    "actualise", "actualiser", "rafraichis", "refresh",

    # --- Configuration / Modification ---
    "mets", "met", "mettre", "mettez", # Très fréquent ("Mets de la musique", "Mets le volume")
    "set", "setup",
    "change", "changer", "changez",
    "modifie", "modifier",
    "regle", "regler", "ajuste", "ajuster",
    "configure", "configurer",
    "augmente", "monte", "plus",
    "diminue", "baisse", "reduis", "moins",

    # --- Navigation / Affichage ---
    "affiche", "afficher", "montre", "montrer", "show",
    "cache", "cacher", "hide",
    "masque", "masquer",
    "agrandis", "maximise", "zoom",
    "reduis", "minimise",
    "scrolle", "defile", "descends", 
    "reviens", "retour", "precedent", "suivant",

    # --- Interaction / Recherche / Communication ---
    "cherche", "chercher", "recherche", "search", "trouve", "trouver",
    "google", "bing", # Parfois utilisé comme verbe
    "dis", "dire", "raconte", "lire", "lis",
    "calcule", "calculer",
    "traduis", "traduire",
    "envoie", "envoyer", "ecris", "ecrire",
    "appelle", "appeler", "contacte",
    "repete", "repetes",

    # --- Multimédia (si pertinent pour ton usage) ---
    "joue", "jouer", "play",
    "passe", "skip",
    "silence", "chut", "mute",
}