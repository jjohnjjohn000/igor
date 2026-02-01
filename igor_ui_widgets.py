# igor_ui_widgets.py
import threading
import subprocess
import numpy as np
import re
import math
import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk, GLib, GdkPixbuf
import igor_skills as skills
import igor_config
import igor_globals
from igor_audio import stop_speaking

# --- WIDGET VISAGE ---
class FaceWidget(Gtk.DrawingArea):
    def __init__(self):
        super().__init__()
        self.set_size_request(200, 120)
        self.state = "IDLE"
        self.mouth_open = 0.0
        self.mouth_target = 0.0
        self.eye_scale = 1.0
        self.blink_timer = 100
        self.is_blinking = False
        self.inertia_x = 0.0
        self.inertia_y = 0.0
        self.last_win_x = None
        self.last_win_y = None
        
        # Variable pour le défilement du texte (Mode THINKING)
        self.text_scroll_offset = 0.0
        
        # Variables pour l'effet Dizzy (Tournis)
        self.dizzy_timer = 0
        self.spiral_angle = 0.0
        
        # NOUVEAU : Timer pour l'animation "Shake it off" (Récupération)
        self.recover_timer = 0

        # Buffer pour l'image vidéo
        self.video_pixbuf = None 
        
        self.add_events(Gdk.EventMask.ENTER_NOTIFY_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK | Gdk.EventMask.BUTTON_PRESS_MASK)
        self.connect("enter-notify-event", self.on_enter)
        self.connect("leave-notify-event", self.on_leave)
        self.connect("button-press-event", self.on_click)
        self.connect("draw", self.on_draw)
        GLib.timeout_add(50, self.animate)

    # Méthode pour recevoir la frame depuis le thread
    def update_video_frame(self, rgb_array):
        try:
            height, width, channels = rgb_array.shape
            # Création du Pixbuf depuis les données brutes
            # (colorspace, has_alpha, bits_per_sample, width, height, rowstride, callback_destroy)
            self.video_pixbuf = GdkPixbuf.Pixbuf.new_from_data(
                rgb_array.tobytes(),
                GdkPixbuf.Colorspace.RGB,
                False, 
                8,
                width,
                height,
                width * channels,
                None, 
                None
            )
            self.queue_draw()
        except Exception as e:
            print(f"Frame error: {e}")


    def update_cursor(self):
        win = self.get_window()
        if win: win.set_cursor(Gdk.Cursor.new(Gdk.CursorType.HAND1 if igor_globals.IS_MUTED else Gdk.CursorType.HAND2))
    def on_enter(self, w, e): self.update_cursor()
    def on_leave(self, w, e): 
        if self.get_window(): self.get_window().set_cursor(None)
    def on_click(self, w, e):
        igor_globals.IS_MUTED
        if e.button == 1:
            igor_globals.IS_MUTED = not igor_globals.IS_MUTED
            
            # Sauvegarde immédiate
            skills.MEMORY['muted'] = igor_globals.IS_MUTED
            skills.save_memory(skills.MEMORY)

            self.update_cursor()
            if igor_globals.IS_MUTED: stop_speaking()
            return True
        return False
    
    def set_state(self, state):
        # On empêche de changer d'état si on est en plein vertige ou récupération
        if (self.state == "DIZZY" or self.state == "RECOVERING") and state != "IDLE":
             pass
        else:
            self.state = state
        self.queue_draw()

    def animate(self):
        top = self.get_toplevel()
        if top.is_toplevel():
            cx, cy = top.get_position()
            if self.last_win_x is not None:
                self.inertia_x -= (cx - self.last_win_x) * 0.6
                self.inertia_y -= (cy - self.last_win_y) * 0.6
            self.last_win_x = cx; self.last_win_y = cy
        
        self.inertia_x *= 0.9; self.inertia_y *= 0.9
        stress = math.sqrt(self.inertia_x**2 + self.inertia_y**2)

        # --- Détection du "Shake" (Secousse) ---
        if stress > 150.0:
            if self.state != "DIZZY":
                self.state = "DIZZY"
            self.dizzy_timer = 40 
        
        # --- Gestion de l'animation Dizzy ---
        if self.state == "DIZZY":
            self.spiral_angle += 0.3 
            
            if stress < 10.0: 
                self.dizzy_timer -= 1
                if self.dizzy_timer <= 0:
                    # MODIFICATION : Au lieu d'aller en IDLE, on passe en RECOVERING
                    self.state = "RECOVERING"
                    self.recover_timer = 15 # Durée de la secousse de récupération
        
        # --- NOUVEAU : Gestion de l'animation "Shake it off" ---
        if self.state == "RECOVERING":
            self.recover_timer -= 1
            
            # On simule une secousse rapide (gauche/droite)
            # On utilise math.sin pour faire osciller l'inertie artificiellement
            shake_intensity = 15.0
            self.inertia_x = math.sin(self.recover_timer * 2.0) * shake_intensity
            
            # On force les yeux fermés pendant qu'il secoue la tête
            self.is_blinking = True
            
            if self.recover_timer <= 0:
                self.state = "IDLE"
                self.is_blinking = False # Réouvre les yeux
        # -------------------------------------------------------
        
        target_eye = 1.0 + min(stress/40.0, 0.5) if stress > 2.0 else 1.0
        if stress > 2.0 and self.state == "IDLE": self.mouth_open = min(stress/50.0, 0.5)
        elif self.state == "IDLE": self.mouth_open *= 0.5
        
        self.eye_scale += (target_eye - self.eye_scale) * 0.2
        
        # --- Gestion de l'état SURPRISED (Main détectée) ---
        if self.state == "SURPRISED":
            # Ouvre grand les yeux et la bouche
            target_eye = 1.5  # Yeux très grands (150%)
            self.mouth_target = 0.3 # Bouche en 'O' moyen
        else:
            # Comportement normal (Code existant légèrement modifié)
            target_eye = 1.0 + min(stress/40.0, 0.5) if stress > 2.0 else 1.0
            if stress > 2.0 and self.state == "IDLE": self.mouth_open = min(stress/50.0, 0.5)
            elif self.state == "IDLE": self.mouth_open *= 0.5
        
        self.eye_scale += (target_eye - self.eye_scale) * 0.2

        # Gestion du clignement (Modifié pour ne PAS cligner si SURPRISED)
        if self.state in ["IDLE", "LISTENING"] and self.state != "RECOVERING" and self.state != "SURPRISED":
            self.blink_timer -= 1
            if self.blink_timer <= 0:
                self.is_blinking = True
                if self.blink_timer < -4:
                    self.is_blinking = False; import random; self.blink_timer = random.randint(50, 150)
        elif self.state != "RECOVERING": 
            # Si on n'est pas en RECOVERING (qui force le blink), on reset
            self.is_blinking = False
        
        if igor_globals.IS_MUTED: self.mouth_open = 0.0
        # Important : Applique la cible définie plus haut (soit normale, soit surprised)
        self.mouth_open += (self.mouth_target - self.mouth_open) * 0.2
        
        if self.state == "THINKING":
            self.text_scroll_offset -= 3.0 
        
        self.queue_draw()
        return True

    def on_draw(self, w, cr):
        w = self.get_allocated_width(); h = self.get_allocated_height()

        # --- 1. DESSIN DU FOND (VIDEO OU COULEUR) ---
        if self.video_pixbuf and igor_config.WATCH_RUNNING:
            Gdk.cairo_set_source_pixbuf(cr, self.video_pixbuf, 0, 0)
            cr.paint()
            cr.set_source_rgba(0, 0, 0, 0.4)
            cr.paint()

        # --- 2. CONFIGURATION DU TRAIT ---
        # Couleur (Cyan si vidéo, Blanc sinon)
        if self.video_pixbuf and igor_config.WATCH_RUNNING:
            cr.set_source_rgba(0.0, 1.0, 1.0, 0.9)
        else:
            cr.set_source_rgba(0.9, 0.9, 1.0, 1)
            
        cr.set_line_width(3)
        
        # --- 3. TRANSFORMATION (Inertie) ---
        # FIX: On ne sauvegarde le contexte qu'UNE SEULE FOIS ici
        cr.save() 
        cr.translate(self.inertia_x, self.inertia_y)
        
        ey = h * 0.4
        
        # --- 4. DESSIN OREILLES (Mode LISTENING) ---
        if self.state == "LISTENING":
            cr.save()
            cr.set_source_rgba(1.0, 0.5, 0.0, 0.9) 
            cr.set_line_width(4)
            
            # Oreille Gauche
            cr.move_to(w/2 - 85, ey - 20)
            cr.curve_to(w/2 - 100, ey - 10, w/2 - 100, ey + 10, w/2 - 85, ey + 20)
            cr.stroke()
            
            # Oreille Droite
            cr.move_to(w/2 + 85, ey - 20)
            cr.curve_to(w/2 + 100, ey - 10, w/2 + 100, ey + 10, w/2 + 85, ey + 20)
            cr.stroke()
            
            cr.restore()

        # --- 5. LOGIQUE DESSIN YEUX ---
        if self.state == "THINKING":
            cr.set_line_width(4)
            cr.set_dash([15, 10], self.text_scroll_offset)
            for offset_y in [-15, 0, 15]:
                cr.move_to(w/2 - 50, ey + offset_y)
                cr.line_to(w/2 + 50, ey + offset_y)
                cr.stroke()
            cr.set_dash([], 0)
            
        elif self.state == "DIZZY":
            eo = 40
            for x_pos in [w/2 - eo, w/2 + eo]:
                cr.save()
                cr.translate(x_pos, ey)
                cr.rotate(self.spiral_angle)
                cr.move_to(0, 0)
                for i in range(0, 1080, 10): 
                    rad = math.radians(i)
                    radius = i * 0.025
                    cr.line_to(math.cos(rad) * radius, math.sin(rad) * radius)
                cr.stroke()
                cr.restore()

        elif self.state == "SURPRISED":
            # --- AJOUT : Yeux écarquillés ---
            eo = 40
            er = 10 * self.eye_scale # eye_scale sera grand (~1.5)
            
            # Dessin des yeux (sans paupières/blink)
            cr.arc(w/2-eo, ey, er, 0, 2*math.pi); cr.fill()
            cr.arc(w/2+eo, ey, er, 0, 2*math.pi); cr.fill()
            
            # Optionnel : Ajout d'un petit reflet blanc pour l'effet "Vivant"
            cr.set_source_rgba(1, 1, 1, 0.8)
            cr.arc(w/2-eo - 3, ey - 3, 3, 0, 2*math.pi); cr.fill()
            cr.arc(w/2+eo - 3, ey - 3, 3, 0, 2*math.pi); cr.fill()
            
            # On remet la couleur du trait
            if self.video_pixbuf and igor_config.WATCH_RUNNING:
                cr.set_source_rgba(0.0, 1.0, 1.0, 0.9)
            else:
                cr.set_source_rgba(0.9, 0.9, 1.0, 1)

        else:
            # Yeux normaux (IDLE, LISTENING...)
            eo = 40; er = 10 * self.eye_scale
            if self.is_blinking:
                cr.move_to(w/2-eo-10, ey); cr.line_to(w/2-eo+10, ey)
                cr.move_to(w/2+eo-10, ey); cr.line_to(w/2+eo+10, ey)
                cr.stroke()
            else:
                cr.arc(w/2-eo, ey, er, 0, 2*math.pi); cr.fill()
                cr.arc(w/2+eo, ey, er, 0, 2*math.pi); cr.fill()
            
        # --- 6. LOGIQUE DESSIN BOUCHE ---
        my = h * 0.7
        if igor_globals.IS_MUTED:
            cs = 8
            cr.move_to(w/2-cs, my-cs); cr.line_to(w/2+cs, my+cs)
            cr.move_to(w/2+cs, my-cs); cr.line_to(w/2-cs, my+cs)
            cr.stroke()
        
        if self.state == "SURPRISED":
            # Bouche en cercle parfait (étonnement)
            cr.save(); cr.translate(w/2, my)
            # On force un cercle un peu plus petit mais bien rond
            cr.scale(15, 15) 
            cr.arc(0, 0, 1, 0, 2*math.pi)
            cr.restore()
            cr.stroke() # Juste le contour pour l'étonnement

        elif self.state == "DIZZY":
            cr.move_to(w/2 - 20, my)
            cr.curve_to(w/2 - 10, my - 10, w/2 + 10, my + 10, w/2 + 20, my)
            cr.stroke()
            pass
        elif not igor_globals.IS_MUTED:
            # Normal
            cr.save(); cr.translate(w/2, my); cr.scale(40, max(1, 20*self.mouth_open))
            cr.arc(0, 0, 1, 0, 2*math.pi); cr.restore()
            if self.mouth_open > 0.1: cr.fill()
            else: cr.stroke()

        else:
            cr.save(); cr.translate(w/2, my); cr.scale(40, max(1, 20*self.mouth_open))
            cr.arc(0, 0, 1, 0, 2*math.pi); cr.restore()
            if self.mouth_open > 0.1: cr.fill()
            else: cr.stroke()
            
        # Restauration finale (Correspond au cr.save() de l'étape 3)
        cr.restore()

# --- DIALOGUE DE CONFIGURATION ---
class ConfigDialog(Gtk.Dialog):
    """
    Dialogue de configuration multi-onglets avec :
    - Configuration Audio (ancien SettingsDialog)
    - Applications Favorites (NOUVEAU)
    - Paramètres Généraux (vitesse voix, etc.)
    """
    
    def __init__(self, parent):
        super().__init__(title="⚙️ Configuration Igor", transient_for=parent, flags=0)
        self.set_default_size(700, 700)
        self.set_modal(True)
        
        # Variables pour le monitoring audio
        self.audio_monitor_active = True
        self.audio_thread = None
        self.mic_level = 0.0
        self.sys_level = 0.0
        
        # Processus parec (pour éviter segfault)
        self.parec_mic_process = None
        self.parec_sys_process = None

        # CSS unifié - Application globale avec priorité maximale
        css_provider = Gtk.CssProvider()
        css_style = """
        * { background-color: #1e1e1e; color: #e0e0e0; }
        window, dialog { background-color: #1e1e1e; color: #e0e0e0; }
        scrolledwindow, scrolledwindow viewport { background-color: #1e1e1e; }
        box, grid { background-color: #1e1e1e; }
        label { color: #e0e0e0; font-size: 11pt; }
        button { background-image: none; background-color: #3a3a3a; color: #ffffff; border: 1px solid #555555; border-radius: 4px; padding: 8px 12px; font-weight: bold; }
        button:hover { background-color: #4a4a4a; }
        entry { color: #ffffff; background-color: #2a2a2a; border: 1px solid #4a4a4a; padding: 6px; border-radius: 3px; }
        entry:focus { border-color: #5a9fd4; }
        combobox, combobox * { color: #ffffff; background-color: #2a2a2a; }
        combobox button { background-color: #3a3a3a; color: #ffffff; }
        separator { background-color: #3a3a3a; min-height: 1px; }
        progressbar { background-color: #2a2a2a; border: 1px solid #3a3a3a; }
        progressbar progress { background-color: #5a9fd4; }
        notebook { background-color: #1e1e1e; }
        notebook tab { background-color: #2a2a2a; color: #b0b0b0; padding: 10px 16px; border: 1px solid #3a3a3a; margin: 2px; }
        notebook tab:checked { background-color: #3a3a3a; color: #ffffff; border-bottom: 2px solid #5a9fd4; }
        switch { background-color: #3a3a3a; }
        switch:checked { background-color: #5a9fd4; }
        switch slider { background-color: #ffffff; }
        scale trough { background-color: #2a2a2a; border: 1px solid #3a3a3a; }
        scale highlight { background-color: #5a9fd4; }
        """
        css_provider.load_from_data(css_style.encode())
        
        # Application à tout l'écran avec priorité APPLICATION (plus haute que USER)
        screen = Gdk.Screen.get_default()
        Gtk.StyleContext.add_provider_for_screen(
            screen, 
            css_provider, 
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        # Conteneur principal
        box = self.get_content_area()
        box.set_spacing(10)
        box.set_margin_top(10); box.set_margin_bottom(10)
        box.set_margin_start(10); box.set_margin_end(10)
        
        # Création du Notebook (onglets)
        self.notebook = Gtk.Notebook()
        box.pack_start(self.notebook, True, True, 0)
        
        # === ONGLET 1 : AUDIO ===
        self.audio_tab = self._create_audio_tab()
        self.notebook.append_page(self.audio_tab, Gtk.Label(label="🎤 Audio"))
        
        # === ONGLET 2 : APPLICATIONS ===
        self.apps_tab = self._create_apps_tab()
        self.notebook.append_page(self.apps_tab, Gtk.Label(label="⚙️ Applications"))
        
        # === ONGLET 3 : GÉNÉRAL ===
        self.general_tab = self._create_general_tab()
        self.notebook.append_page(self.general_tab, Gtk.Label(label="🔧 Général"))
        
        # Boutons de dialogue
        self.add_button("Annuler", Gtk.ResponseType.CANCEL)
        self.add_button("Enregistrer", Gtk.ResponseType.OK)
        
        # Démarrage monitoring audio
        self._start_parec_monitor()
        
        self.connect("destroy", self._on_dialog_destroy)
        
        self.show_all()
    
    # ============================================================
    # ONGLET 1 : CONFIGURATION AUDIO
    # ============================================================
    
    def _create_audio_tab(self):
        """Crée l'onglet de configuration audio (ancien SettingsDialog)"""
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=15)
        box.set_margin_top(20); box.set_margin_bottom(20)
        box.set_margin_start(20); box.set_margin_end(20)
        scroll.add(box)
        
        # Chargement config audio
        raw_config = skills.MEMORY.get('audio_config', {})
        self.audio_config = {
            'mic_enabled': raw_config.get('mic_enabled', True),
            'mic_index': raw_config.get('mic_index'),
            'sys_enabled': raw_config.get('sys_enabled', False),
            'sys_index': raw_config.get('sys_index'),
            'sys_delay': raw_config.get('sys_delay', 0),
            'debug_audio': raw_config.get('debug_audio', False)
        }
        
        # Scan devices
        self.devices = self._scan_devices()
        
        # --- SECTION MICROPHONE ---
        lbl_mic = Gtk.Label()
        lbl_mic.set_markup("<span size='large' weight='bold' foreground='#ffffff'>🎤 Microphone (Voix)</span>")
        lbl_mic.set_halign(Gtk.Align.START)
        box.pack_start(lbl_mic, False, False, 0)
        
        hbox_mic = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.sw_mic = Gtk.Switch()
        self.sw_mic.set_active(self.audio_config['mic_enabled'])
        hbox_mic.pack_start(self.sw_mic, False, False, 0)
        hbox_mic.pack_start(Gtk.Label(label="Activer l'écoute vocale"), False, False, 0)
        box.pack_start(hbox_mic, False, False, 0)
        
        self.combo_mic = Gtk.ComboBoxText()
        self.combo_mic.append_text("Par défaut (Système)")
        active_mic_iter = 0
        
        saved_mic_idx = self.audio_config['mic_index']
        
        for i, (idx, name) in enumerate(self.devices):
            self.combo_mic.append_text(f"[{idx}] {name}")
            if saved_mic_idx is not None and idx is not None and int(saved_mic_idx) == int(idx):
                active_mic_iter = i + 1

        self.combo_mic.set_active(active_mic_iter)
        box.pack_start(self.combo_mic, False, False, 0)
        
        # Visualiseur micro
        mic_meter_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        mic_meter_box.pack_start(Gtk.Label(label="Activité :"), False, False, 0)
        
        self.mic_meter = Gtk.ProgressBar()
        self.mic_meter.set_show_text(True)
        self.mic_meter.set_fraction(0.0)
        mic_meter_box.pack_start(self.mic_meter, True, True, 0)
        
        box.pack_start(mic_meter_box, False, False, 5)

        box.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 10)

        # --- SECTION SYSTÈME ---
        lbl_sys = Gtk.Label()
        lbl_sys.set_markup("<span size='large' weight='bold' foreground='#ffffff'>🔊 Audio Système (Monitor)</span>")
        lbl_sys.set_halign(Gtk.Align.START)
        box.pack_start(lbl_sys, False, False, 0)
        
        info_lbl = Gtk.Label()
        info_lbl.set_markup("<span foreground='#c0c0c0'>Sélectionnez le périphérique 'Monitor' pour que l'IA entende la musique.</span>")
        info_lbl.set_line_wrap(True)
        info_lbl.set_xalign(0)
        box.pack_start(info_lbl, False, False, 0)
        
        hbox_sys = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.sw_sys = Gtk.Switch()
        self.sw_sys.set_active(self.audio_config['sys_enabled'])
        hbox_sys.pack_start(self.sw_sys, False, False, 0)
        hbox_sys.pack_start(Gtk.Label(label="Activer l'écoute système"), False, False, 0)
        box.pack_start(hbox_sys, False, False, 0)
        
        self.combo_sys = Gtk.ComboBoxText()
        self.combo_sys.append_text("Désactivé / Aucun")
        active_sys_iter = 0
        
        saved_sys_idx = self.audio_config['sys_index']

        for i, (idx, name) in enumerate(self.devices):
            self.combo_sys.append_text(f"[{idx}] {name}")
            if saved_sys_idx is not None and idx is not None and int(saved_sys_idx) == int(idx):
                active_sys_iter = i + 1

        self.combo_sys.set_active(active_sys_iter)
        box.pack_start(self.combo_sys, False, False, 0)

        # Curseur latence
        lbl_delay = Gtk.Label(label="Délai de synchronisation (ms) :")
        lbl_delay.set_halign(Gtk.Align.START)
        box.pack_start(lbl_delay, False, False, 0)

        hbox_delay = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        
        self.scale_delay = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 1000, 10)
        self.scale_delay.set_value(self.audio_config['sys_delay'])
        self.scale_delay.set_digits(0)
        self.scale_delay.set_value_pos(Gtk.PositionType.RIGHT)
        self.scale_delay.set_hexpand(True)
        
        hbox_delay.pack_start(self.scale_delay, True, True, 0)
        box.pack_start(hbox_delay, False, False, 0)
        
        # Visualiseur système
        sys_meter_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        sys_meter_box.pack_start(Gtk.Label(label="Activité :"), False, False, 0)
        
        self.sys_meter = Gtk.ProgressBar()
        self.sys_meter.set_show_text(True)
        self.sys_meter.set_fraction(0.0)
        sys_meter_box.pack_start(self.sys_meter, True, True, 0)
        
        box.pack_start(sys_meter_box, False, False, 5)

        # --- SECTION DEBUG ---
        box.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 10)
        hbox_dbg = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.sw_debug = Gtk.Switch()
        self.sw_debug.set_active(self.audio_config['debug_audio'])
        hbox_dbg.pack_start(self.sw_debug, False, False, 0)
        hbox_dbg.pack_start(Gtk.Label(label="Mode Debug : Enregistrer les fichiers WAV (/tmp/)"), False, False, 0)
        box.pack_start(hbox_dbg, False, False, 0)
        
        return scroll
    
    # ============================================================
    # ONGLET 2 : APPLICATIONS FAVORITES
    # ============================================================
    
    def _create_apps_tab(self):
        """Crée l'onglet de configuration des applications favorites"""
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=15)
        box.set_margin_top(20); box.set_margin_bottom(20)
        box.set_margin_start(20); box.set_margin_end(20)
        scroll.add(box)
        
        # Titre
        title = Gtk.Label()
        title.set_markup("<span size='x-large' weight='bold' foreground='#ffffff'>Applications Favorites</span>")
        title.set_halign(Gtk.Align.START)
        box.pack_start(title, False, False, 0)
        
        info = Gtk.Label()
        info.set_markup("<span foreground='#c0c0c0'>Configurez vos applications par défaut.\nVous pouvez utiliser des URLs (Gmail, YouTube Music) ou des commandes système.</span>")
        info.set_line_wrap(True)
        info.set_xalign(0)
        box.pack_start(info, False, False, 5)
        
        box.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 10)
        
        # --- NAVIGATEUR WEB ---
        self._add_app_entry(box, "🌐 Navigateur Web", "fav_browser", 
                           "Exemples: google-chrome, firefox, https://www.google.com")
        
        # --- EMAIL ---
        self._add_app_entry(box, "📧 Email", "fav_email",
                           "Exemples: thunderbird, https://mail.google.com")
        
        # --- MUSIQUE ---
        self._add_app_entry(box, "🎵 Musique", "fav_music_app",
                           "Exemples: spotify, rhythmbox, https://music.youtube.com")
        
        # --- VOIP / VISIO ---
        self._add_app_entry(box, "📞 VoIP / Visioconférence", "fav_voip",
                           "Exemples: zoom, teams, discord, https://meet.google.com")
        
        # --- TERMINAL ---
        self._add_app_entry(box, "💻 Terminal", "fav_terminal",
                           "Exemples: gnome-terminal, konsole, xterm")
        
        # --- GESTIONNAIRE DE FICHIERS ---
        self._add_app_entry(box, "📁 Gestionnaire de Fichiers", "fav_filemanager",
                           "Exemples: nautilus, dolphin, thunar")
        
        return scroll
    
    def _add_app_entry(self, parent_box, label_text, config_key, placeholder):
        """Ajoute une entrée de configuration d'application avec bouton de sélection"""
        # Label
        label = Gtk.Label()
        label.set_markup(f"<span weight='bold' foreground='#ffffff'>{label_text}</span>")
        label.set_halign(Gtk.Align.START)
        parent_box.pack_start(label, False, False, 0)
        
        # Box horizontale pour Entry + Bouton
        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        
        # Entry
        entry = Gtk.Entry()
        entry.set_placeholder_text(placeholder)
        current_value = skills.MEMORY.get(config_key, "")
        if current_value:
            entry.set_text(current_value)
        
        # Stockage de l'entry pour récupération ultérieure
        setattr(self, f"entry_{config_key}", entry)
        
        hbox.pack_start(entry, True, True, 0)
        
        # Bouton "Sélectionner"
        select_btn = Gtk.Button(label="📋 Sélectionner")
        select_btn.connect("clicked", self._on_select_app, config_key, entry)
        hbox.pack_start(select_btn, False, False, 0)
        
        parent_box.pack_start(hbox, False, False, 0)
        parent_box.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 5)
    
    def _on_select_app(self, button, config_key, entry):
        """Ouvre le dialogue de sélection d'application"""
        from igor_app_selector import open_app_selector
        
        selected_cmd = open_app_selector(self, config_key)
        if selected_cmd:
            entry.set_text(selected_cmd)
    
    # ============================================================
    # ONGLET 3 : PARAMÈTRES GÉNÉRAUX
    # ============================================================
    
    def _create_general_tab(self):
        """Crée l'onglet des paramètres généraux"""
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=15)
        box.set_margin_top(20); box.set_margin_bottom(20)
        box.set_margin_start(20); box.set_margin_end(20)
        scroll.add(box)
        
        # Titre
        title = Gtk.Label()
        title.set_markup("<span size='x-large' weight='bold' foreground='#ffffff'>Paramètres Généraux</span>")
        title.set_halign(Gtk.Align.START)
        box.pack_start(title, False, False, 0)
        
        box.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 10)
        
        # --- VITESSE DE PAROLE ---
        lbl_speed = Gtk.Label()
        lbl_speed.set_markup("<span weight='bold' foreground='#ffffff'>🎙️ Vitesse de parole</span>")
        lbl_speed.set_halign(Gtk.Align.START)
        box.pack_start(lbl_speed, False, False, 0)
        
        hbox_speed = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        
        self.scale_speed = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0.5, 3.0, 0.1)
        self.scale_speed.set_value(skills.MEMORY.get('voice_speed', 1.1))
        self.scale_speed.set_digits(1)
        self.scale_speed.set_value_pos(Gtk.PositionType.RIGHT)
        self.scale_speed.set_hexpand(True)
        
        # Marques pour valeurs communes
        self.scale_speed.add_mark(0.75, Gtk.PositionType.BOTTOM, "Lent")
        self.scale_speed.add_mark(1.0, Gtk.PositionType.BOTTOM, "Normal")
        self.scale_speed.add_mark(1.5, Gtk.PositionType.BOTTOM, "Rapide")
        
        hbox_speed.pack_start(self.scale_speed, True, True, 0)
        box.pack_start(hbox_speed, False, False, 0)
        
        box.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 10)
        
        # --- STYLE D'ALARME ---
        lbl_alarm = Gtk.Label()
        lbl_alarm.set_markup("<span weight='bold' foreground='#ffffff'>🔔 Style d'alarme</span>")
        lbl_alarm.set_halign(Gtk.Align.START)
        box.pack_start(lbl_alarm, False, False, 0)
        
        self.combo_alarm = Gtk.ComboBoxText()
        alarm_styles = ["classique", "douceur", "alerte", "gong"]
        current_alarm = skills.MEMORY.get('alarm_sound', 'classique').lower()
        
        for i, style in enumerate(alarm_styles):
            self.combo_alarm.append_text(style.capitalize())
            if style == current_alarm:
                self.combo_alarm.set_active(i)
        
        if self.combo_alarm.get_active() == -1:
            self.combo_alarm.set_active(0)
        
        box.pack_start(self.combo_alarm, False, False, 0)
        
        box.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 10)
        
        # --- AUTO-APPRENTISSAGE ---
        lbl_learn = Gtk.Label()
        lbl_learn.set_markup("<span weight='bold' foreground='#ffffff'>🧠 Auto-apprentissage</span>")
        lbl_learn.set_halign(Gtk.Align.START)
        box.pack_start(lbl_learn, False, False, 0)
        
        info_learn = Gtk.Label()
        info_learn.set_markup("<span foreground='#c0c0c0'>Apprendre automatiquement de Wikipédia lors des recherches web</span>")
        info_learn.set_line_wrap(True)
        info_learn.set_xalign(0)
        box.pack_start(info_learn, False, False, 0)
        
        hbox_learn = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.sw_autolearn = Gtk.Switch()
        self.sw_autolearn.set_active(skills.MEMORY.get('auto_learn', False))
        hbox_learn.pack_start(self.sw_autolearn, False, False, 0)
        hbox_learn.pack_start(Gtk.Label(label="Activer l'auto-apprentissage"), False, False, 0)
        box.pack_start(hbox_learn, False, False, 0)
        
        box.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 10)
        
        # --- NOM DE L'AGENT ---
        lbl_agent = Gtk.Label()
        lbl_agent.set_markup("<span weight='bold' foreground='#ffffff'>🤖 Nom de l'agent</span>")
        lbl_agent.set_halign(Gtk.Align.START)
        box.pack_start(lbl_agent, False, False, 0)
        
        self.entry_agent_name = Gtk.Entry()
        self.entry_agent_name.set_text(skills.MEMORY.get('agent_name', 'Igor'))
        box.pack_start(self.entry_agent_name, False, False, 0)
        
        box.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 10)
        
        # --- NOM DE L'UTILISATEUR ---
        lbl_user = Gtk.Label()
        lbl_user.set_markup("<span weight='bold' foreground='#ffffff'>👤 Nom de l'utilisateur</span>")
        lbl_user.set_halign(Gtk.Align.START)
        box.pack_start(lbl_user, False, False, 0)
        
        self.entry_user_name = Gtk.Entry()
        self.entry_user_name.set_text(skills.MEMORY.get('user_name', 'Utilisateur'))
        box.pack_start(self.entry_user_name, False, False, 0)
        
        return scroll
    
    # ============================================================
    # MÉTHODES AUDIO (monitoring, scan devices, etc.)
    # ============================================================
    
    def _start_parec_monitor(self):
        """Lance le monitoring via parec (lecteur PulseAudio non-bloquant)"""
        self.audio_monitor_active = True
        self.audio_thread = threading.Thread(target=self._parec_monitor_worker, daemon=True)
        self.audio_thread.start()
    
    def _get_source_name_from_index(self, device_idx):
        """Convertit un index PyAudio en nom de source PulseAudio"""
        if device_idx is None:
            return None
        
        try:
            if device_idx >= 100:
                pa_id = device_idx - 100
                
                result = subprocess.run(
                    ['pactl', 'list', 'sources', 'short'],
                    capture_output=True,
                    text=True,
                    timeout=2
                )
                
                for line in result.stdout.split('\n'):
                    if not line.strip():
                        continue
                    
                    parts = line.split('\t')
                    if len(parts) >= 2:
                        source_id = int(parts[0].strip())
                        source_name = parts[1].strip()
                        
                        if source_id == pa_id:
                            return source_name
            
            return None
            
        except Exception as e:
            print(f"  [MONITOR] Erreur résolution source : {e}", flush=True)
            return None
    
    def _parec_monitor_worker(self):
        """Thread qui lit l'audio brut via parec"""
        print("  [MONITOR] Démarrage monitoring parec...", flush=True)
        
        # Démarrage flux micro
        mic_source = None
        if self.sw_mic.get_active():
            mic_idx = self._get_selected_index(self.combo_mic)
            mic_source = self._get_source_name_from_index(mic_idx)
        
        # Démarrage flux système
        sys_source = None
        if self.sw_sys.get_active():
            sys_idx = self._get_selected_index(self.combo_sys)
            sys_source = self._get_source_name_from_index(sys_idx)
        
        # Lancement processus parec
        if mic_source or (self.sw_mic.get_active() and not mic_source):
            try:
                cmd = ['parec', '--format=s16le', '--rate=16000', '--channels=1']
                if mic_source:
                    cmd.extend(['--device', mic_source])
                
                self.parec_mic_process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL
                )
                print("  [MONITOR] ✅ parec micro démarré", flush=True)
            except Exception as e:
                print(f"  [MONITOR] Erreur parec micro : {e}", flush=True)
        
        if sys_source:
            try:
                self.parec_sys_process = subprocess.Popen(
                    ['parec', '--format=s16le', '--rate=16000', '--channels=1', '--device', sys_source],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL
                )
                print("  [MONITOR] ✅ parec système démarré", flush=True)
            except Exception as e:
                print(f"  [MONITOR] Erreur parec système : {e}", flush=True)
        
        # Boucle de lecture
        CHUNK_SIZE = 3200
        
        while self.audio_monitor_active:
            try:
                # Lecture micro
                if self.parec_mic_process:
                    try:
                        data = self.parec_mic_process.stdout.read(CHUNK_SIZE)
                        if data:
                            audio_np = np.frombuffer(data, dtype=np.int16)
                            rms = np.sqrt(np.mean(audio_np.astype(np.float32) ** 2))
                            self.mic_level = min(rms / 8000.0, 1.0)
                            GLib.idle_add(self._update_mic_meter)
                    except:
                        pass
                
                # Lecture système
                if self.parec_sys_process:
                    try:
                        data = self.parec_sys_process.stdout.read(CHUNK_SIZE)
                        if data:
                            audio_np = np.frombuffer(data, dtype=np.int16)
                            rms = np.sqrt(np.mean(audio_np.astype(np.float32) ** 2))
                            self.sys_level = min(rms / 15000.0, 1.0)
                            GLib.idle_add(self._update_sys_meter)
                    except:
                        pass
                
            except Exception as e:
                print(f"  [MONITOR] Erreur lecture : {e}", flush=True)
                break
        
        # Nettoyage
        if self.parec_mic_process:
            self.parec_mic_process.terminate()
            self.parec_mic_process.wait()
        
        if self.parec_sys_process:
            self.parec_sys_process.terminate()
            self.parec_sys_process.wait()
        
        print("  [MONITOR] Thread parec arrêté", flush=True)
    
    def _update_mic_meter(self):
        """Met à jour la barre du micro"""
        self.mic_meter.set_fraction(self.mic_level)
        
        if self.mic_level > 0.7:
            self.mic_meter.set_text(f"🔴 {int(self.mic_level * 100)}%")
        elif self.mic_level > 0.3:
            self.mic_meter.set_text(f"🟡 {int(self.mic_level * 100)}%")
        else:
            self.mic_meter.set_text(f"🟢 {int(self.mic_level * 100)}%")
        
        return False
    
    def _update_sys_meter(self):
        """Met à jour la barre système"""
        self.sys_meter.set_fraction(self.sys_level)
        
        if self.sys_level > 0.7:
            self.sys_meter.set_text(f"🔴 {int(self.sys_level * 100)}%")
        elif self.sys_level > 0.3:
            self.sys_meter.set_text(f"🟡 {int(self.sys_level * 100)}%")
        else:
            self.sys_meter.set_text(f"🟢 {int(self.sys_level * 100)}%")
        
        return False
    
    def _on_dialog_destroy(self, widget):
        """Arrête le monitoring à la fermeture"""
        self.audio_monitor_active = False
        
        if self.parec_mic_process:
            self.parec_mic_process.terminate()
        if self.parec_sys_process:
            self.parec_sys_process.terminate()
        
        if self.audio_thread:
            self.audio_thread.join(timeout=1)
    
    def _scan_devices(self):
        """Scan ultra-rapide : matériel (arecord) + virtuel (pactl)"""
        devices = []
        
        # Devices matériels
        try:
            result = subprocess.run(
                ['arecord', '-l'],
                capture_output=True,
                text=True,
                timeout=2
            )
            
            for line in result.stdout.split('\n'):
                match = re.search(r'card (\d+):.*?\[([^\]]+)\].*?device (\d+):.*?\[([^\]]+)\]', line)
                if match:
                    card_num = int(match.group(1))
                    card_name = match.group(2).strip()
                    dev_num = int(match.group(3))
                    dev_name = match.group(4).strip()
                    
                    pyaudio_idx = card_num * 10 + dev_num
                    display_name = f"{card_name} - {dev_name}"
                    devices.append((pyaudio_idx, display_name))
            
        except:
            pass
        
        # Devices virtuels PulseAudio
        try:
            result = subprocess.run(
                ['pactl', 'list', 'sources', 'short'],
                capture_output=True,
                text=True,
                timeout=2
            )
            
            for line in result.stdout.split('\n'):
                if not line.strip():
                    continue
                
                parts = line.split('\t')
                if len(parts) >= 2:
                    source_id = parts[0].strip()
                    source_name = parts[1].strip()
                    
                    if 'monitor' in source_name.lower():
                        pa_idx = 100 + int(source_id)
                        
                        if 'analog' in source_name:
                            display_name = "📊 Monitor Analog (Audio Système)"
                        elif 'hdmi' in source_name.lower():
                            display_name = "📊 Monitor HDMI"
                        else:
                            display_name = f"📊 Monitor {source_id}"
                        
                        devices.append((pa_idx, display_name))
                    
                    elif 'input' in source_name:
                        pa_idx = 100 + int(source_id)
                        
                        if 'analog' in source_name:
                            display_name = "🎤 PulseAudio Analog"
                        else:
                            display_name = f"🎤 PulseAudio {source_id}"
                        
                        devices.append((pa_idx, display_name))
        
        except:
            pass
        
        print(f"  [CONFIG] ✅ {len(devices)} devices trouvés", flush=True)
        
        return devices

    def _get_selected_index(self, combo):
        txt = combo.get_active_text()
        if txt and "[" in txt:
            try: return int(txt.split("[")[1].split("]")[0])
            except: pass
        return None

    # ============================================================
    # SAUVEGARDE DES PARAMÈTRES
    # ============================================================
    
    def get_settings(self):
        """Récupère tous les paramètres configurés"""
        settings = {}
        
        # === AUDIO ===
        settings['audio_config'] = {
            'mic_enabled': self.sw_mic.get_active(),
            'mic_index': self._get_selected_index(self.combo_mic),
            'sys_enabled': self.sw_sys.get_active(),
            'sys_index': self._get_selected_index(self.combo_sys),
            'sys_delay': int(self.scale_delay.get_value()),
            'debug_audio': self.sw_debug.get_active()
        }
        
        # === APPLICATIONS ===
        settings['fav_browser'] = self.entry_fav_browser.get_text().strip()
        settings['fav_email'] = self.entry_fav_email.get_text().strip()
        settings['fav_music_app'] = self.entry_fav_music_app.get_text().strip()
        settings['fav_voip'] = self.entry_fav_voip.get_text().strip()
        settings['fav_terminal'] = self.entry_fav_terminal.get_text().strip()
        settings['fav_filemanager'] = self.entry_fav_filemanager.get_text().strip()
        
        # === GÉNÉRAL ===
        settings['voice_speed'] = round(self.scale_speed.get_value(), 1)
        settings['alarm_sound'] = self.combo_alarm.get_active_text().lower()
        settings['auto_learn'] = self.sw_autolearn.get_active()
        settings['agent_name'] = self.entry_agent_name.get_text().strip()
        settings['user_name'] = self.entry_user_name.get_text().strip()
        
        return settings