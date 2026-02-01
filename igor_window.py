# igor_window.py
import os
import sys
import threading
import time
import subprocess
import re
import datetime
import webbrowser
import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk, GLib, Pango
import igor_skills as skills
import igor_config
from igor_audio import stop_speaking, listen_hybrid_logic, speak_logic, play_actual_alarm_sound
from igor_brain import get_cached_or_query
from igor_ui_widgets import FaceWidget, ConfigDialog
import igor_globals

IS_PROCESSING_QUEUE = False

def task_queue_worker(window_instance):
    global IS_PROCESSING_QUEUE 
    while True:
        try:
            task = skills.TASK_QUEUE.get()
            IS_PROCESSING_QUEUE = True

            igor_config.ABORT_FLAG = False 
            
            tool_name = task.get('tool')
            args = task.get('args')
            print(f"  [QUEUE] Exécution : {tool_name} -> {args}", flush=True)
            
            GLib.idle_add(window_instance.add_chat_message, "System", f"Traitement : {tool_name}...")

            response_text = ""
            
            # Si c'est une commande BATCH, on traite chaque élément séquentiellement
            if tool_name == "BATCH" and isinstance(args, list):
                print(f"  [QUEUE] Mode BATCH détecté : {len(args)} actions", flush=True)
                
                # On envoie un message global une seule fois
                GLib.idle_add(window_instance.add_chat_message, 
                            skills.MEMORY['agent_name'], 
                            f"Traitement de {len(args)} commandes...")
                
                successful = 0
                failed = 0
                
                # On exécute chaque commande l'une après l'autre
                for i, sub_task in enumerate(args, 1):
                    # CHECK ABORT
                    if igor_config.ABORT_FLAG:
                        print(f"  [QUEUE] BATCH interrompu à la tâche #{i}", flush=True)
                        break
                        
                    if not isinstance(sub_task, dict):
                        print(f"  [QUEUE] Item BATCH #{i} invalide : {sub_task}", flush=True)
                        failed += 1
                        continue
                    
                    sub_tool = sub_task.get("tool", "CHAT")
                    sub_args = sub_task.get("args", "")
                    
                    print(f"  [QUEUE] BATCH #{i}/{len(args)} : {sub_tool} -> {sub_args}", flush=True)
                    
                    # Exécution immédiate
                    if sub_tool in skills.TOOLS:
                        try:
                            if sub_args is None: 
                                sub_args = ""
                                
                            # APPEL DE LA FONCTION
                            res = skills.TOOLS[sub_tool](sub_args)
                            
                            # Mise à jour UI si surveillance (Commande vocale/Chat)
                            if sub_tool == "WATCH":
                                GLib.idle_add(window_instance.update_cam_btn_state)
                            
                            # Message dans le chat pour chaque sous-action
                            clean_resp = str(res).replace(f"{skills.MEMORY['agent_name']}:", "").strip()
                            GLib.idle_add(window_instance.add_chat_message, 
                                        "System", 
                                        f"✓ #{i} : {clean_resp}")
                            
                            successful += 1
                            
                            # PAUSE IMPORTANTE : Temps pour que l'OS/applications réagissent
                            # Pour CLOSE_WINDOW : Temps que la fenêtre se ferme vraiment
                            # Pour LAUNCH : Temps que l'app démarre
                            if sub_tool in ["CLOSE_WINDOW", "WINDOW_ACTION"]:
                                time.sleep(1.2)  # Encore plus long pour les fermetures
                            elif sub_tool == "LAUNCH":
                                time.sleep(0.8)
                            else:
                                time.sleep(0.3)
                            
                        except Exception as e:
                            failed += 1
                            error_msg = f"✗ #{i} Erreur : {e}"
                            print(f"  [QUEUE] {error_msg}", flush=True)
                            GLib.idle_add(window_instance.add_chat_message, "System", error_msg)
                    else:
                        failed += 1
                        error_msg = f"✗ #{i} Outil inconnu : {sub_tool}"
                        GLib.idle_add(window_instance.add_chat_message, "System", error_msg)
                
                # Message final avec statistiques
                response_text = f"✓ Batch terminé : {successful} réussies, {failed} échecs."  
            
            # === CAS NORMAL (Action unique) ===
            elif tool_name in skills.TOOLS:
                try:
                    if args is None: 
                        args = ""
                    res = skills.TOOLS[tool_name](args)
                    
                    # Mise à jour UI si surveillance (Commande vocale/Chat)
                    if tool_name == "WATCH":
                        GLib.idle_add(window_instance.update_cam_btn_state)
                        
                    response_text = str(res)
                except Exception as e: 
                    response_text = f"Erreur tùche : {e}"
            else: 
                response_text = f"Outil inconnu : {tool_name}"

            # Affichage de la réponse finale
            clean_resp = response_text.replace(f"{skills.MEMORY['agent_name']}:", "").strip()
            GLib.idle_add(window_instance.add_chat_message, skills.MEMORY['agent_name'], clean_resp)

            # Historique (pour que l'IA se souvienne)
            igor_globals.CHAT_HISTORY.append(f"{skills.MEMORY['agent_name']}: {clean_resp}")

            # Animation bouche (Parole)
            GLib.idle_add(window_instance.face.set_state, "SPEAKING")
            try:
                speak_logic(clean_resp)
            except Exception as e:
                print(f"  [ERR] Crash audio évité: {e}", flush=True)
            
            # Retour au calme
            GLib.idle_add(window_instance.face.set_state, "IDLE")
            GLib.idle_add(window_instance.check_auto_hide)

            skills.TASK_QUEUE.task_done()

            # Si la queue technique est vide, on vérifie s'il reste des prompts textuels à traiter
            if skills.TASK_QUEUE.empty():
                if window_instance.pending_chain:
                    # On récupère le prochain prompt textuel
                    next_prompt = window_instance.pending_chain.pop(0)
                    print(f"  [CHAIN] ⏩ Lancement séquence suivante : '{next_prompt}'", flush=True)
                    
                    # On relance le processus via l'interface principale (Thread-safe)
                    GLib.idle_add(window_instance.process_input, next_prompt, True)
                else:
                    # Vraiment fini, on réactive l'écoute
                    igor_globals.WAIT_FOR_WAKE_WORD = True
                    print("  [SYSTEM] Cycle terminé. En attente du mot clé...", flush=True)

        except Exception as e: 
            print(f"Queue Error: {e}", flush=True)
            igor_globals.WAIT_FOR_WAKE_WORD = True
        finally: 
            IS_PROCESSING_QUEUE = False
            if skills.TASK_QUEUE.empty():
                igor_globals.WAIT_FOR_WAKE_WORD = True

def check_alarms_gtk():
    try:
        now = datetime.datetime.now()
        cur_hm = now.strftime("%H:%M"); cur_wd = now.weekday()
        
        # PROTECTION CRITIQUE : On utilise le verrou de skills
        with skills.ALARM_LOCK:
            alarms = list(skills.MEMORY.get('alarms', []))
            trig_idxs = []
            should_ring = False
            
            for i, a in enumerate(alarms):
                if a['type'] == 'oneshot':
                    if now.timestamp() >= float(a['timestamp']):
                        should_ring = True; trig_idxs.append(i)
                elif a['type'] == 'recurring':
                    if a['time'] == cur_hm and cur_wd in a['days'] and now.second < 5:
                        should_ring = True
            
            # Si on doit supprimer des alarmes (oneshot passées)
            if trig_idxs:
                for idx in sorted(trig_idxs, reverse=True): 
                    del skills.MEMORY['alarms'][idx]
                skills.save_memory(skills.MEMORY)
        
        # On joue le son APRÈS avoir relâché le verrou pour ne pas bloquer les autres threads
        if should_ring:
            threading.Thread(target=play_actual_alarm_sound).start()

    except Exception as e: print(f"Alarm Check Err: {e}")
    return True

class AgentWindow(Gtk.Window):
    def __init__(self):
        super().__init__(title="Agent")
        
        # Contrôle du thread de détection
        self.wake_thread_running = False
        self.wake_thread = None
        self.wake_detector_process = None

        # 1. Configuration de base de la fenêtre
        self.set_decorated(False)
        self.set_app_paintable(True)
        self.set_visual(self.get_screen().get_rgba_visual())
        
        # Change set_default_size par set_size_request pour forcer une contrainte minimale/fixe
        self.set_size_request(400, 500) 
        self.set_resizable(False) # Optionnel : empêche totalement le redimensionnement manuel/auto
        
        # 2. État épinglé (Au-dessus des autres)
        self.is_pinned = skills.MEMORY.get('pinned', True)
        self.set_keep_above(self.is_pinned)
        
        # Variables de gestion de fenêtre
        self.is_wayland = "WAYLAND" in os.environ.get("XDG_SESSION_TYPE", "").upper()
        self.drag_start_x = 0
        self.drag_start_y = 0

        self.was_hidden_before_listen = False 

        # 3. Construction de l'Interface (UI)
        # Fond (EventBox pour capturer les clics et le fond semi-transparent)
        self.event_box = Gtk.EventBox()
        self.event_box.set_visible_window(True)
        self.event_box.set_name("main_background")
        self.add(self.event_box)

        # Conteneur Vertical Principal
        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        self.box.set_margin_top(5)
        self.box.set_margin_bottom(10)
        self.box.set_margin_start(10)
        self.box.set_margin_end(10)
        self.event_box.add(self.box)

        # Barre du haut (Boutons)
        self.top_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.box.pack_start(self.top_bar, False, False, 0)
        
        # Espace vide flexible pour pousser les boutons à droite
        self.top_bar.pack_start(Gtk.Label(), True, True, 0)
        
        # Bouton Apprentissage (Cerveau/Livre)
        self.wiki_btn = Gtk.Button(label="🧠" if skills.AUTO_LEARN_MODE else "📖")
        self.wiki_btn.set_relief(Gtk.ReliefStyle.NONE)
        self.wiki_btn.set_tooltip_text("Mode Auto-apprentissage (Wikipedia)")
        self.wiki_btn.connect("clicked", self.on_toggle_wiki)
        self.top_bar.pack_start(self.wiki_btn, False, False, 5)

        # Bouton Vision (Oeil)
        self.eye_btn = Gtk.Button(label="👁️")
        self.eye_btn.set_relief(Gtk.ReliefStyle.NONE)
        self.eye_btn.set_tooltip_text("Analyser l'écran (Vision)")
        self.eye_btn.connect("clicked", self.on_vision_clicked)
        self.top_bar.pack_start(self.eye_btn, False, False, 5)

        # 🆕 Bouton Langue FR/EN
        self.lang_mode = skills.MEMORY.get('wake_lang', 'FR')
        self.lang_btn = Gtk.Button(label="🇫🇷" if self.lang_mode == "FR" else "🇬🇧")
        self.lang_btn.set_relief(Gtk.ReliefStyle.NONE)
        self.lang_btn.set_tooltip_text("Français/English")
        self.lang_btn.connect("clicked", self.on_toggle_lang)
        self.top_bar.pack_start(self.lang_btn, False, False, 5)

        # Bouton Écoute Système (Oreille)
        self.ear_btn = Gtk.Button(label="👂")
        self.ear_btn.set_relief(Gtk.ReliefStyle.NONE)
        self.ear_btn.set_tooltip_text("Écouter l'audio système")
        self.ear_btn.connect("clicked", self.on_listen_system_clicked)
        self.top_bar.pack_start(self.ear_btn, False, False, 5)

        # --- BOUTON CONFIGURATION ---
        self.conf_btn = Gtk.Button(label="⚙️")
        self.conf_btn.set_relief(Gtk.ReliefStyle.NONE)
        self.conf_btn.set_tooltip_text("Configuration Audio (Micro/Système)")
        self.conf_btn.connect("clicked", self.on_config_clicked)
        self.top_bar.pack_start(self.conf_btn, False, False, 5)

        # BOUTON STOP
        self.stop_btn = Gtk.Button(label="⏹️")
        self.stop_btn.set_relief(Gtk.ReliefStyle.NONE)
        self.stop_btn.set_tooltip_text("Arrêter toutes les tâches")
        self.stop_btn.connect("clicked", self.on_stop_clicked)
        self.top_bar.pack_start(self.stop_btn, False, False, 5)

        # --- BOUTON CAMÉRA ---
        self.cam_btn = Gtk.Button(label="🛑" if igor_config.WATCH_RUNNING else "📹")
        self.cam_btn.set_relief(Gtk.ReliefStyle.NONE)
        self.cam_btn.set_tooltip_text("Surveillance vidéo en continu")
        self.cam_btn.connect("clicked", self.on_toggle_cam)
        self.top_bar.pack_start(self.cam_btn, False, False, 5)

        # Bouton Épingle (Pin)
        self.pin_btn = Gtk.Button(label="📌" if self.is_pinned else "🍃")
        self.pin_btn.set_relief(Gtk.ReliefStyle.NONE)
        self.pin_btn.set_tooltip_text("Épingler la fenêtre (toujours visible)")
        self.pin_btn.connect("clicked", self.on_toggle_pin)
        self.top_bar.pack_start(self.pin_btn, False, False, 0)

        # Widget Visage
        self.face = FaceWidget()
        self.box.pack_start(self.face, False, False, 5)

        # Zone de Chat (Défilable)
        self.scrolled = Gtk.ScrolledWindow()
        self.scrolled.set_min_content_height(200)
        self.scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.chat_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        self.scrolled.add(self.chat_box)
        self.box.pack_start(self.scrolled, True, True, 0)

        # Zone de saisie (Bas)
        input_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.box.pack_end(input_box, False, False, 0)
        
        # Bouton Micro
        self.mic_btn = Gtk.Button(label="🎤")
        self.mic_btn.set_relief(Gtk.ReliefStyle.NONE)
        self.mic_btn.set_tooltip_text("Activer la commande vocale")
        self.mic_btn.connect("clicked", self.on_mic_clicked)
        input_box.pack_start(self.mic_btn, False, False, 0)
        
        # Champ Texte
        self.entry = Gtk.Entry()
        self.entry.set_placeholder_text("Tapez entrée...")
        self.entry.connect("activate", self.on_entry_activate)
        input_box.pack_start(self.entry, True, True, 0)

        # 4. Styles et Événements Souris/Focus
        self.is_hovered = False
        self.is_focused = False
        self.css_provider = Gtk.CssProvider()
        self.apply_css()
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), self.css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        self.connect("focus-in-event", self.on_focus_change, True)
        self.connect("focus-out-event", self.on_focus_change, False)
        
        # Événements pour le déplacement de la fenêtre (Drag & Drop)
        self.event_box.connect("enter-notify-event", self.on_mouse_enter)
        self.event_box.connect("leave-notify-event", self.on_mouse_leave)
        self.event_box.connect("button-press-event", self.on_button_press)
        self.event_box.connect("motion-notify-event", self.on_motion_notify)
        
        # 5. Gestion de la fermeture (Sauvegarde position)
        # "delete-event" se déclenche QUAND on demande la fermeture (La fenêtre est encore là)
        # C'est le bon moment pour sauvegarder.
        self.connect("delete-event", self.on_save_state_before_close)
        
        # "destroy" se déclenche une fois la fermeture validée
        # On quitte juste la boucle principale.
        self.connect("destroy", Gtk.main_quit)
        
        # Affichage
        self.show_all()
        
        self.setup_window_shortcuts()
        
        # 6. RESTAURATION POSITION (Différée pour le multi-écran)
        self.connect("map-event", self.on_window_map)
        
        # 7. Démarrage des Threads et Services
        self.pending_chain = [] # File d'attente pour les prompts séquentiels
        self.add_chat_message("System", f"Prêt.")

        # --- CONNECTER LE CALLBACK VIDEO ---
        # Attacher le callback sur igor_config directement
        igor_config.ON_FRAME_CALLBACK = self.on_frame_received

        # --- CONNECTER LE CALLBACK GESTE ---
        igor_config.ON_GESTURE_CALLBACK = self.on_gesture_detected

        # Gestion du focus automatique lors du wake word
        self.previous_focused_window = None
        self.should_restore_focus = False

        self.start_wake_detector_subprocess()

        GLib.timeout_add(2000, check_alarms_gtk)
        
        q_thread = threading.Thread(target=task_queue_worker, args=(self,))
        q_thread.daemon = True
        q_thread.start()

    def on_gesture_detected(self, gesture_name):
        """
        Appelé par igor_vision quand un geste est vu.
        Change l'état du visage pour une courte durée.
        """
        if gesture_name == "OPEN_HAND":
            # On change l'état uniquement si on n'est pas déjà occupé (thinking/listening)
            if self.face.state in ["IDLE", "SURPRISED"]:
                
                # 1. Met le visage en mode Surprise
                GLib.idle_add(self.face.set_state, "SURPRISED")
                
                # 2. Programme le retour à la normale dans 1.5 secondes
                # On utilise une fonction lambda pour remettre IDLE
                def _reset_face():
                    if self.face.state == "SURPRISED":
                        self.face.set_state("IDLE")
                    return False # Arrête le timeout
                
                GLib.timeout_add(1500, _reset_face)

    def start_wake_detector_subprocess(self):
        """Lance le détecteur dans un processus séparé (ANTI-SEGFAULT)."""
        
        # 1. Tuer l'ancien processus si existe
        if self.wake_detector_process and self.wake_detector_process.poll() is None:
            print("  [SYSTEM] 🛑 Arrêt ancien détecteur...", flush=True)
            self.wake_detector_process.terminate()
            try:
                self.wake_detector_process.wait(timeout=2)
            except:
                self.wake_detector_process.kill()
        
        # 2. Vérifier que le script existe
        script_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wake_detector.py")
        if not os.path.exists(script_path):
            print(f"  [ERR] ❌ Script manquant : {script_path}", flush=True)
            self.add_chat_message("System", "⚠️ wake_detector.py manquant. Créez-le depuis l'artifact.")
            return
        
        # 3. Lancement subprocess
        try:
            self.wake_detector_process = subprocess.Popen(
                [sys.executable, script_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=1,
                universal_newlines=True,
                cwd=os.path.dirname(os.path.abspath(__file__))  # Important pour memory.json
            )
            
            print(f"  [SYSTEM] ✅ Détecteur lancé (PID: {self.wake_detector_process.pid})", flush=True)
            
            # 4. Thread de monitoring des logs
            def monitor_output():
                try:
                    for line in self.wake_detector_process.stdout:
                        line = line.strip()
                        if line:
                            print(f"  [WAKE-SUB] {line}", flush=True)
                            
                            # Affichage dans le chat si détection
                            if "DÉTECTION" in line or "TRIGGER" in line:
                                GLib.idle_add(
                                    self.add_chat_message, 
                                    "System", 
                                    f"🔔 {line.split(']')[-1].strip()}"
                                )
                except:
                    pass
                
                # Si le processus meurt, notifier
                if self.wake_detector_process.poll() is not None:
                    print("  [SYSTEM] ⚠️ Subprocess terminé inattendu", flush=True)
                    GLib.idle_add(
                        self.add_chat_message,
                        "System",
                        "⚠️ Détecteur arrêté. Relancez via config."
                    )
            
            threading.Thread(target=monitor_output, daemon=True).start()
            
            self.add_chat_message("System", f"✅ Détecteur démarré ({self.lang_mode})")
            
        except Exception as e:
            print(f"  [ERR] Échec lancement subprocess : {e}", flush=True)
            self.add_chat_message("System", f"❌ Détecteur : {e}")

    def _save_focused_window(self):
        """Sauvegarde la fenêtre actuellement focusée (si ce n'est pas Igor)."""
        try:
            # Obtenir la fenêtre actuellement focusée
            screen = Gdk.Screen.get_default()
            active_window = screen.get_active_window()
            
            # Vérifier si c'est notre propre fenêtre
            our_window = self.get_window()
            
            if active_window and our_window:
                active_xid = active_window.get_xid()
                our_xid = our_window.get_xid()
                
                if active_xid != our_xid:
                    # Une autre fenêtre est active, on la sauvegarde
                    self.previous_focused_window = active_window
                    self.should_restore_focus = True
                    print(f"  [FOCUS] 📌 Fenêtre précédente sauvegardée (XID={active_xid})", flush=True)
                else:
                    # Notre fenêtre était déjà active
                    self.should_restore_focus = False
                    print("  [FOCUS] ✅ Notre fenêtre déjà active, pas de restauration", flush=True)
            else:
                self.should_restore_focus = False
                
        except Exception as e:
            print(f"  [FOCUS] ⚠️ Erreur sauvegarde: {e}", flush=True)
            self.should_restore_focus = False

    def _restore_focused_window(self):
        """Restaure le focus à la fenêtre précédente."""
        if not self.should_restore_focus:
            return False  # Important pour GLib.timeout_add
            
        if not self.previous_focused_window:
            return False
        
        try:
            # Restaurer le focus
            timestamp = Gdk.CURRENT_TIME
            self.previous_focused_window.focus(timestamp)
            
            print("  [FOCUS] 🔙 Focus restauré", flush=True)
            
            # Nettoyage
            self.previous_focused_window = None
            self.should_restore_focus = False
            
        except Exception as e:
            print(f"  [FOCUS] ⚠️ Erreur restauration: {e}", flush=True)
            self.should_restore_focus = False
        
        return False  # Pour GLib.timeout_add (ne pas répéter)

    def setup_window_shortcuts(self):
        """Configure les raccourcis clavier."""
        self.connect("key-press-event", self.on_key_press)

    def on_key_press(self, widget, event):
        """Gère les raccourcis clavier."""
        # F5 = Rafraîchir la liste des fenêtres
        if event.keyval == 65474:  # F5
            result = skills.tool_list_windows("")
            self.add_chat_message(skills.MEMORY['agent_name'], result)
            return True

        return False

    # --- Méthode pour recacher la fenêtre automatiquement ---
    def check_auto_hide(self):
        """Cache la fenêtre si elle l'était avant l'écoute."""
        if self.was_hidden_before_listen:
            self.hide()
            self.was_hidden_before_listen = False # Reset

    # --- Méthode wrapper thread-safe ---
    def on_frame_received(self, rgb_frame):
        # Important : Les mises à jour GUI doivent se faire via idle_add
        GLib.idle_add(self.face.update_video_frame, rgb_frame)

    def on_window_map(self, widget, event):
        """Appelé quand la fenêtre s'affiche physiquement à l'écran."""
        self.apply_saved_position()
        return False

    def apply_saved_position(self):
        """Logique robuste de positionnement."""
        try:
            last_x = skills.MEMORY.get('window_x')
            last_y = skills.MEMORY.get('window_y')
            
            print(f"  [DEBUG WINDOW] Mémoire chargée : X={last_x}, Y={last_y}", flush=True)

            if last_x is None or last_y is None:
                print("  [DEBUG WINDOW] Pas de coords sauvegardées -> Center.", flush=True)
                self.set_position(Gtk.WindowPosition.CENTER)
                return

            # Vérification Ecrans
            display = Gdk.Display.get_default()
            n_monitors = display.get_n_monitors()
            position_valid = False
            
            print(f"  [DEBUG WINDOW] Nombre d'écrans détectés : {n_monitors}", flush=True)

            for i in range(n_monitors):
                monitor = display.get_monitor(i)
                geo = monitor.get_geometry()
                print(f"    - Ecran {i}: x={geo.x}, y={geo.y}, w={geo.width}, h={geo.height}", flush=True)
                
                # Vérifie si le point (last_x, last_y) est DANS ce moniteur
                # On ajoute une marge de tolérance (ex: la barre du haut prend de la place)
                if (geo.x <= last_x < geo.x + geo.width) and \
                   (geo.y <= last_y < geo.y + geo.height):
                    position_valid = True
                    break
            
            if position_valid:
                print(f"  [DEBUG WINDOW] Position valide. DÉPLACEMENT FORCÉ vers {last_x}, {last_y}", flush=True)
                # Astuce : On move, puis on s'assure que GTK ne l'oublie pas
                self.move(last_x, last_y)
                # Parfois nécessaire pour les configurations multi-écrans complexes
                self.get_window().move(last_x, last_y)
            else:
                print("  [DEBUG WINDOW] Position hors limites -> Recentrage de sécurité.", flush=True)
                self.set_position(Gtk.WindowPosition.CENTER)

        except Exception as e:
            print(f"  [DEBUG WINDOW] Erreur critique position: {e}", flush=True)

    def on_save_state_before_close(self, widget, event):
        """Sauvegarde position + Nettoyage subprocess."""
        try:
            # Sauvegarde position (code existant)
            win = self.get_window()
            if win:
                x, y = win.get_root_origin()
                print(f"  [DEBUG WINDOW] Sauvegarde : X={x}, Y={y}", flush=True)
                skills.MEMORY['window_x'] = x
                skills.MEMORY['window_y'] = y
                skills.save_memory(skills.MEMORY)
            
            # ✅ NOUVEAU : Tuer le subprocess proprement
            if self.wake_detector_process and self.wake_detector_process.poll() is None:
                print("  [SYSTEM] 🛑 Arrêt détecteur (fermeture app)...", flush=True)
                self.wake_detector_process.terminate()
                try:
                    self.wake_detector_process.wait(timeout=1)
                except:
                    self.wake_detector_process.kill()
        
        except Exception as e:
            print(f"  [DEBUG WINDOW] Erreur sauvegarde: {e}", flush=True)
        
        return False

    def on_toggle_wiki(self, w):
        skills.AUTO_LEARN_MODE = not skills.AUTO_LEARN_MODE
        skills.MEMORY['auto_learn'] = skills.AUTO_LEARN_MODE; skills.save_memory(skills.MEMORY)
        self.wiki_btn.set_label("🧠" if skills.AUTO_LEARN_MODE else "📖")

    def on_toggle_pin(self, w):
        self.is_pinned = not self.is_pinned
        skills.MEMORY['pinned'] = self.is_pinned; skills.save_memory(skills.MEMORY)
        self.pin_btn.set_label("📌" if self.is_pinned else "🍃")
        self.set_keep_above(self.is_pinned)

    def apply_css(self):
        bg = "0.85" if self.is_hovered else ("0.6" if self.is_focused else "0.2")
        css = f"#main_background {{ background-color: rgba(30,30,30,{bg}); border-radius: 15px; }} window {{ background: transparent; }} entry {{ background: rgba(0,0,0,0.5); color: white; border: none; }} label {{ color: white; }} button {{ background: transparent; color: #aaa; border: none; font-size: 16px; }}"
        try: self.css_provider.load_from_data(css.encode())
        except: pass

    def on_focus_change(self, w, e, f): self.is_focused = f; self.apply_css()
    def on_mouse_enter(self, w, e): self.is_hovered = True; self.apply_css()
    def on_mouse_leave(self, w, e): self.is_hovered = False; self.apply_css()
    def on_button_press(self, w, e):
        if e.button == 1:
            self.drag_start_x = e.x; self.drag_start_y = e.y
            if self.is_wayland: self.begin_move_drag(e.button, int(e.x_root), int(e.y_root), e.time)
            return not self.is_wayland
        return False
    def on_motion_notify(self, w, e):
        if e.get_state() & Gdk.ModifierType.BUTTON1_MASK and not self.is_wayland:
            wx, wy = self.get_position()
            self.move(wx + int(e.x - self.drag_start_x), wy + int(e.y - self.drag_start_y))

    def add_chat_message(self, sender, text):
        if threading.current_thread() is not threading.main_thread():
            GLib.idle_add(self.add_chat_message, sender, text)
            return

        align = Gtk.Align.END if sender == "User" else Gtk.Align.START
        color = "#4da6ff" if sender == "User" else "#cccccc"
        
        try:
            if text is None: text = ""
            text = str(text)
            
            # Si le texte contient déjà des balises <a href...>, on suppose qu'il est déjà formaté
            # Sinon, on échappe les caractères pour éviter les crashs Pango
            if "<a href=" not in text:
                safe_text = GLib.markup_escape_text(text)
            else:
                # Si c'est du markup généré par nous (avec liens), on le garde tel quel
                # Attention : le texte source doit avoir été échappé AVANT d'ajouter les balises <a>
                safe_text = text

            safe_sender = GLib.markup_escape_text(str(sender))
            
            lbl = Gtk.Label()
            lbl.set_line_wrap(True)
            # Force le retour à la ligne même pour les longs liens/chemins sans espaces
            lbl.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
            lbl.set_max_width_chars(50)
            lbl.set_selectable(True)
            lbl.set_xalign(0)
            
            # Application du Markup avec couleur + Support des liens
            span_str = f"<span foreground='{color}'><b>{safe_sender}:</b> {safe_text}</span>"
            lbl.set_markup(span_str)

            # --- SIGNAL CLIC HYPERLIEN ---
            # C'est ici qu'on rend le lien actif
            lbl.connect("activate-link", self.on_chat_link_click)
            
            bg = Gtk.Box()
            bg.pack_start(lbl, False, False, 2)
            bg.set_halign(align)
            self.chat_box.pack_start(bg, False, False, 2)
            
            lbl.show(); bg.show()
            
            def _smooth_scroll():
                adj = self.scrolled.get_vadjustment()
                # On force le scroll tout en bas
                # Le délai permet d'avoir la bonne valeur de get_upper() après le wrap du texte
                adj.set_value(adj.get_upper() - adj.get_page_size())
                return False
            
            # REMPLACER GLib.idle_add PAR CECI :
            # 50ms est imperceptible pour l'œil mais suffit à GTK pour le recalcul géométrique
            GLib.timeout_add(50, _smooth_scroll)
            
        except Exception as e:
            print(f"  [UI ERR] Erreur affichage: {e}", flush=True)

    def on_chat_link_click(self, label, uri):
        """
        Gère le clic sur un lien (web, wiki, ou fenêtre).
        """
        print(f"  [UI] Lien cliqué : {uri}", flush=True)

        # === 0. LIENS APPLICATIONS (NOUVEAU) ===
        if uri.startswith("launch://"):
            cmd = uri.replace("launch://", "")
            self.add_chat_message("System", f"Lancement de : {cmd}...")
            # On utilise l'outil existant pour lancer proprement
            skills.tool_launch(cmd)
            return True
        
        # === 1. LIENS FENÊTRES (NOUVEAU) ===
        if uri.startswith("window://"):
            try:
                # Format: window://ACTION:WINDOW_ID
                # Ex: window://focus:0x02400003
                parts = uri.replace("window://", "").split(":", 1)
                if len(parts) == 2:
                    action, window_id = parts
                    
                    # Appel direct de la fonction système
                    from igor_system import tool_window_action
                    result = tool_window_action(window_id, action)
                    
                    # Feedback dans le chat
                    self.add_chat_message("System", result)
                    
                    # Si c'était une fermeture, on rafraîchit la liste
                    if action == "close":
                        # Petit délai pour laisser la fenêtre se fermer
                        import time
                        time.sleep(0.3)
                        # On recharge la liste
                        refresh_result = skills.tool_list_windows("")
                        self.add_chat_message(skills.MEMORY['agent_name'], refresh_result)
                    
                    return True
            except Exception as e:
                print(f"  [ERR] Erreur action fenêtre: {e}", flush=True)
                self.add_chat_message("System", f"Erreur : {e}")
                return False
        
        # === 2. LIENS WEB (EXISTANT) ===
        if uri.startswith("http://") or uri.startswith("https://"):
            fav_browser = skills.MEMORY.get('fav_browser')
            
            if fav_browser:
                try:
                    self.add_chat_message("System", f"Ouverture via {fav_browser}...")
                    subprocess.Popen(f"{fav_browser} {uri}", shell=True)
                    return True
                except Exception as e:
                    print(f"Erreur lancement navigateur favori: {e}")
            
            if not fav_browser:
                self.add_chat_message("System", "Navigateur favori non défini. J'utilise le défaut système.")
            
            try:
                webbrowser.open(uri)
                return True
            except Exception as e:
                print(f"Erreur ouverture navigateur système: {e}")
                return False
        
        # === 3. PROTOCOLE RACCOURCIS (NOUVEAU) ===
        if uri.startswith("shortcut://"):
            # Extraction de la clé du raccourci
            key = uri.replace("shortcut://", "")
            self.add_chat_message("System", f"Ouverture du raccourci : {key}...")
            # Appel direct de l'outil d'ouverture
            skills.tool_shortcut_open(key)
            return True

        # === 4. AUTRES PROTOCOLES ===
        return False

    def on_entry_activate(self, w):
        text = w.get_text().strip(); w.set_text("")
        if not text: self.start_listening()
        else: self.add_chat_message("User", text); self.process_input(text)

    def start_listening(self):

        # NOTE: _save_focused_window is now called BEFORE this method
        # (either in sig_h for wake word, or in on_mic_clicked for manual trigger)
        
        # 1. Vérification de l'état actuel
        is_visible = self.get_visible()
        
        # 2. Si elle est cachée, on note qu'il faudra la recacher, et on l'affiche
        if not is_visible:
            self.was_hidden_before_listen = True
            self.show_all()
        
        # 3. On force le focus (Même si elle était déjà visible)
        self.deiconify()
        self.present()
        
        igor_globals.WAIT_FOR_WAKE_WORD = False
        stop_speaking()
        self.face.set_state("LISTENING")
        self.entry.set_sensitive(False); self.mic_btn.set_sensitive(False)
        self.entry.set_placeholder_text("J'écoute...")
        t = threading.Thread(target=self._listening_worker); t.daemon = True; t.start()

    def _listening_worker(self):
        GLib.idle_add(self._on_listen_done, listen_hybrid_logic())

    def _on_listen_done(self, text):
        
        self.entry.set_sensitive(True); self.mic_btn.set_sensitive(True)
        self.entry.set_placeholder_text("Tapez entrée...")
        self.entry.grab_focus()
        
        if text: 
            self.add_chat_message("User", text)
            self.process_input(text)
        else: 
            # Si aucun texte n'a été entendu (timeout), on retourne immédiatement en veille
            self.face.set_state("IDLE")
            igor_globals.WAIT_FOR_WAKE_WORD = True 
            print("  [SYSTEM] Rien entendu. Retour veille.", flush=True)
            # NOUVEAU: Restaurer le focus même en cas d'erreur
            GLib.timeout_add(300, self._restore_focused_window)
            self.check_auto_hide()

    def process_input(self, text, is_chained=False):
        # Découpage sur les mots de liaison (et, puis, ensuite...)
        # On ne redécoupe pas si on est déjà dans une chaîne (pour éviter les boucles infinies)
        if not is_chained and any(sep in text for sep in [" et ", " puis ", " ensuite ", " après "]):
            parts = re.split(r'\s+(?:et|puis|ensuite|après)\s+', text.strip(), flags=re.IGNORECASE)
            parts = [p.strip() for p in parts if p.strip()]
            
            if parts:
                # On prend le premier prompt pour tout de suite
                current_prompt = parts.pop(0)
                # On ajoute le reste à la file d'attente
                self.pending_chain.extend(parts)
                print(f"  [CHAIN] Prompt actif: '{current_prompt}' | En attente: {self.pending_chain}", flush=True)
                
                # On remplace le texte à traiter par le premier segment
                text = current_prompt

        igor_globals.CHAT_HISTORY.append(f"User: {text}")
        self.face.set_state("THINKING")
        t = threading.Thread(target=self._brain_worker, args=(text,)); t.daemon = True; t.start()

    def _brain_worker(self, text):

        # Si on a des options en attente dans skills.py, on vérifie si l'utilisateur répond à ça.
        # Cela empêche l'IA de confondre "1" avec "Lancer l'application 1" ou "Lister les apps".
        if skills.LAST_WIKI_OPTIONS:
            lower_text = text.lower()
            is_selection_response = False
            
            # Mots clés de sélection
            selection_keywords = [
                "premier", "1er", "deuxième", "second", "2ème", "troisième", "3ème", 
                "quatrième", "4ème", "cinquième", "5ème", "choix", "option", "celui"
            ]
            
            # Si le texte contient un chiffre ou un mot clé de sélection
            if any(k in lower_text for k in selection_keywords) or re.search(r'\d+', text):
                print(f"  [SYSTEM] Interception Contexte : Réponse d'ambiguïté détectée -> LEARN", flush=True)
                skills.TASK_QUEUE.put({"tool": "LEARN", "args": text})
                
                # On quitte la fonction ici, on ne demande PAS à l'IA (brain_query)
                return 

        tool, args = get_cached_or_query(text)
        
        has_task = False

        if tool == "BATCH" and isinstance(args, list):
            self.add_chat_message("System", f"Batch {len(args)}.")
            for i in args: 
                # AJOUT DE SÉCURITÉ : On vérifie que 'i' est bien un dictionnaire
                if isinstance(i, dict):
                    skills.TASK_QUEUE.put({"tool": i.get("tool","CHAT"), "args": i.get("args","")})
                else:
                    print(f"  [ERR] Batch item invalid (not dict): {i}", flush=True)
            has_task = True
        else:
            # Si l'IA renvoie CHAT vide, on ne met rien en file d'attente
            if tool == "CHAT" and not args:
                 pass
            else:
                 skills.TASK_QUEUE.put({"tool": tool, "args": args})
                 has_task = True
        
        # CORRECTIF : Si aucune tâche n'a été ajoutée (ex: bug IA ou réponse vide),
        # le task_queue_worker ne se lancera pas. Il faut donc réactiver l'écoute ICI.
        if not has_task:
             GLib.idle_add(self.face.set_state, "IDLE")
             igor_globals.WAIT_FOR_WAKE_WORD = True
             print("  [SYSTEM] Aucune tâche générée. Réactivation mot clé immédiate.", flush=True)
        
        # NOUVEAU: Restaurer le focus après le traitement (500ms de délai)
        GLib.timeout_add(500, self._restore_focused_window)

    def on_mic_clicked(self, w):
        # Save focus before manually triggered listening
        self._save_focused_window()
        self.start_listening()

    def on_vision_clicked(self, w):
        """
        Gère le clic sur l'œil :
        1. Cache la fenêtre
        2. Attend un peu
        3. Lance la tâche VISION
        4. Réaffiche la fenêtre
        """
        def _vision_thread():
            # On cache la fenêtre pour voir ce qu'il y a derrière
            GLib.idle_add(self.hide)
            time.sleep(0.5) # Temps que l'animation de fermeture se fasse
            
            # On lance l'outil directement via la queue
            skills.TASK_QUEUE.put({"tool": "VISION", "args": "Décris ce que tu vois sur mon écran."})
            
            # On réaffiche la fenêtre après la capture (la capture dans skills prend ~1sec)
            time.sleep(1.0) 
            GLib.idle_add(self.show_all)

        threading.Thread(target=_vision_thread, daemon=True).start()

    def on_listen_system_clicked(self, w):
        """
        Déclenche manuellement l'écoute système.
        """
        self.add_chat_message("System", "J'écoute l'audio système (10s)...")
        # Envoie directement la tâche à la file d'attente
        skills.TASK_QUEUE.put({"tool": "LISTEN_SYSTEM", "args": "10"})

    def on_toggle_lang(self, widget):
        """Changement de langue = sauvegarde + redémarrage subprocess."""
        if self.lang_mode == "FR":
            self.lang_mode = "EN"
            self.lang_btn.set_label("🇬🇧")
            msg = "Mode Anglais (OpenWakeWord)"
        else:
            self.lang_mode = "FR"
            self.lang_btn.set_label("🇫🇷")
            msg = "Mode Français (Vosk + Filtrage)"
        
        # Sauvegarde
        skills.MEMORY['wake_lang'] = self.lang_mode
        skills.save_memory(skills.MEMORY)
        
        self.add_chat_message("System", f"✅ {msg} - Redémarrage...")
        
        print(f"  [TOGGLE] Switch vers {self.lang_mode}", flush=True)
        
        # Redémarrage subprocess (le script lira memory.json)
        def _restart():
            time.sleep(0.5)  # Temps que le vieux processus meure
            GLib.idle_add(self.start_wake_detector_subprocess)
        
        threading.Thread(target=_restart, daemon=True).start()

    def update_cam_btn_state(self):
        """Met à jour l'état visuel du bouton caméra."""
        self.cam_btn.set_label("🛑" if igor_config.WATCH_RUNNING else "📹")

    def on_toggle_cam(self, w):
        """Active ou désactive la surveillance vidéo (Webcam + YOLO)."""
        # On détermine l'action inverse de l'état actuel
        action = "off" if igor_config.WATCH_RUNNING else "on"
        
        # On appelle l'outil directement pour avoir le retour immédiat
        # (tool_surveillance lance déjà ses propres threads, donc ça ne bloque pas l'UI)
        response = skills.tool_surveillance(action)
        
        # Mise à jour de l'icône via la méthode centralisée
        self.update_cam_btn_state()
        
        # Feedback dans le chat
        self.add_chat_message("System", response)
        
        # Si on active, on force un redessin immédiat
        if igor_config.WATCH_RUNNING:
            self.face.queue_draw()
        
    def on_stop_clicked(self, w):
        """Action du bouton Stop : Arrêt immédiat de tout."""
        print("  [UI] STOP Cliqué.", flush=True)
        self.add_chat_message("System", "🛑 Arrêt forcé.")
        
        # Coupe la parole
        stop_speaking()
        
        # Coupe la vision (via le ABORT_FLAG que stop_speaking déclenche)
        skills.abort_tasks()
        
        # Reset visuel
        self.face.set_state("IDLE")
        
        # Important : On s'assure que le wake word est réactivé
        igor_globals.WAIT_FOR_WAKE_WORD = True

    def on_config_clicked(self, w):
        """Ouvre le menu de configuration et redémarre le subprocess."""
        
        # 1. Arrêt subprocess
        print("  [CONFIG] 🛑 Pause détecteur...", flush=True)
        if self.wake_detector_process and self.wake_detector_process.poll() is None:
            self.wake_detector_process.terminate()
            try:
                self.wake_detector_process.wait(timeout=2)
            except:
                self.wake_detector_process.kill()

        # 2. Dialogue NOUVEAU
        dialog = ConfigDialog(self)  # <-- CHANGED FROM SettingsDialog
        response = dialog.run()

        if response == Gtk.ResponseType.OK:
            new_settings = dialog.get_settings()
            
            # Sauvegarde de toutes les configurations
            for key, value in new_settings.items():
                skills.MEMORY[key] = value
            
            skills.save_memory(skills.MEMORY)
            
            # Sync mémoire
            igor_config.MEMORY = igor_config.load_memory()
            skills.MEMORY = igor_config.MEMORY 
            
            print(f"  [CONFIG] ✅ Configuration sauvegardée", flush=True)
            self.add_chat_message("System", "✅ Configuration appliquée.")
            
        dialog.destroy()
        
        # 3. Redémarrage subprocess
        print("  [CONFIG] ▶️ Redémarrage détecteur...", flush=True)
        threading.Thread(
            target=lambda: (time.sleep(0.5), GLib.idle_add(self.start_wake_detector_subprocess)), 
            daemon=True
        ).start()