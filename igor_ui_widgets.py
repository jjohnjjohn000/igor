# igor_ui_widgets.py
import os
import threading
import subprocess
import numpy as np
import re
import math
import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk, GLib, GdkPixbuf, Pango, PangoCairo
import igor_skills as skills
import igor_config
import igor_globals
import igor_system
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

    def _draw_llm_status(self, cr):
        """Affiche un petit indicateur du modèle actif (Ollama/Llama) en haut à gauche."""
        backend = skills.MEMORY.get('llm_backend', 'llamacpp')
        model = skills.MEMORY.get('llm_model_name', '')
        
        # Configuration visuelle
        if 'ollama' in backend.lower():
            color = (0.2, 0.8, 1.0) # Cyan pour Ollama
            letter = "O"
        else:
            color = (1.0, 0.6, 0.2) # Orange pour Llama.cpp
            letter = "L"
            
        # Nom court du modèle (ex: mistral-nemo -> Mistral)
        short_model = "Local"
        if model:
            short_model = model.split(':')[0].split('-')[0].title()[:8]

        cr.save()
        # Position statique (Top Left)
        cr.translate(10, 10)
        
        # 1. Fond sombre du badge
        cr.set_source_rgba(0.1, 0.1, 0.1, 0.6)
        cr.arc(12, 12, 14, 0, 2*math.pi)
        cr.fill()
        
        # 2. Lettre du Backend (O ou L)
        layout = self.create_pango_layout(letter)
        layout.set_font_description(Pango.FontDescription("Sans Bold 12"))
        
        # Ombre portée texte
        cr.set_source_rgba(0, 0, 0, 0.5)
        cr.move_to(8, 4)
        PangoCairo.show_layout(cr, layout)
        
        # Texte coloré
        cr.set_source_rgba(*color, 1.0)
        cr.move_to(7, 3)
        PangoCairo.show_layout(cr, layout)
        
        # 3. Nom du modèle (Petit, dessous)
        layout_sub = self.create_pango_layout(short_model)
        layout_sub.set_font_description(Pango.FontDescription("Sans 7"))
        
        cr.set_source_rgba(0.9, 0.9, 0.9, 0.8)
        cr.move_to(0, 28)
        PangoCairo.show_layout(cr, layout_sub)
        
        cr.restore()

    def on_draw(self, w, cr):
        w = self.get_allocated_width(); h = self.get_allocated_height()

        # --- 1. DESSIN DU FOND (VIDEO OU COULEUR) ---
        if self.video_pixbuf and igor_config.WATCH_RUNNING:
            Gdk.cairo_set_source_pixbuf(cr, self.video_pixbuf, 0, 0)
            cr.paint()
            cr.set_source_rgba(0, 0, 0, 0.4)
            cr.paint()

        # --- NOUVEAU : INDICATEUR LLM ---
        self._draw_llm_status(cr)
        # -----------------------------

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

        # === ONGLET 4 : LLMS (NOUVEAU) ===
        self.llm_tab = self._create_llm_tab()
        self.notebook.append_page(self.llm_tab, Gtk.Label(label="🧠 LLMs"))
        
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
    # ONGLET 4 : CONFIGURATION LLMS (MULTI-INSTANCES)
    # ============================================================
    
    def _create_llm_tab(self):
        import time
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=15)
        main_box.set_margin_top(20); main_box.set_margin_bottom(20)
        main_box.set_margin_start(20); main_box.set_margin_end(20)
        scroll.add(main_box)
        
        # Titre Global
        title = Gtk.Label()
        title.set_markup("<span size='x-large' weight='bold' foreground='#ffffff'>Moteurs IA &amp; Priorité</span>")
        main_box.pack_start(title, False, False, 0)
        
        info = Gtk.Label()
        info.set_markup("<span foreground='#c0c0c0'>Configurez ici vos modèles <b>Mistral</b>. L'agent utilisera le premier de la liste qui est <b>activé (ON)</b>.<br/>Activez 'Nemo' pour la vitesse ou 'Small' pour l'intelligence.</span>")
        info.set_line_wrap(True)
        main_box.pack_start(info, False, False, 0)

        # Barre d'outils (Ajouter)
        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        btn_add_llama = Gtk.Button(label="➕ Ajouter un profil Llama.cpp")
        btn_add_llama.connect("clicked", self._on_add_instance, "llamacpp")
        toolbar.pack_start(btn_add_llama, False, False, 0)
        
        # On garde le bouton Ollama au cas où, mais il n'est plus le défaut
        btn_add_ollama = Gtk.Button(label="➕ Ajouter Ollama (Service)")
        btn_add_ollama.connect("clicked", self._on_add_instance, "ollama")
        toolbar.pack_start(btn_add_ollama, False, False, 0)
        
        main_box.pack_start(toolbar, False, False, 0)

        # Conteneur de la liste des LLMs
        self.llm_list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=15)
        main_box.pack_start(self.llm_list_box, False, False, 0)

        # --- INITIALISATION INTELLIGENTE ---
        self.llm_instances = skills.MEMORY.get('llm_instances', [])
        
        # Si aucune configuration n'existe (premier lancement ou après reset)
        # On crée les trois boites : Llama.cpp (Custom) et les Mistral (Ollama)
        if not self.llm_instances:
            base_bin = skills.MEMORY.get('llm_binary_path', '') # Récupère ancien chemin si existe
            
            # 1. Instance Llama.cpp (Serveur Local / GGUF Custom)
            llama_inst = {
                'id': f"llamacpp_custom_{int(time.time())}",
                'type': 'llamacpp',
                'name': 'Llama.cpp (Local)',
                'enabled': False, 
                'binary_path': base_bin,
                'gguf_path': os.path.join(igor_config.USER_HOME, "igor_llm/model.gguf"),
                'url': 'http://localhost:8080/completion'
            }

            # 2. Instance Mistral Nemo (Via Ollama)
            nemo_inst = {
                'id': f"mistral_nemo_{int(time.time())+1}",
                'type': 'ollama', # Bascule sur Ollama
                'name': 'Ollama (Nemo)',
                'enabled': True,
                'model_name': 'mistral-nemo',
                'url': 'http://localhost:11434/api/generate'
            }
            
            # 3. Instance Mistral Small (Via Ollama)
            small_inst = {
                'id': f"mistral_small_{int(time.time())+2}",
                'type': 'ollama', # Bascule sur Ollama
                'name': 'Ollama (Small)',
                'enabled': False,
                'model_name': 'mistral-small',
                'url': 'http://localhost:11434/api/generate'
            }
            
            self.llm_instances = [llama_inst, nemo_inst, small_inst]

        # Stockage des références UI
        self.llm_ui_refs = {} 

        # --- RENDU DE LA LISTE ---
        for instance in self.llm_instances:
            self._render_instance_row(instance)

        # Barre de progression
        self.progress_bar = Gtk.ProgressBar()
        self.progress_bar.set_text("Prêt")
        self.progress_bar.set_show_text(True)
        main_box.pack_end(self.progress_bar, False, False, 0)

        # Timer status
        GLib.timeout_add(2000, self._refresh_instances_status)

        return scroll

    def _on_add_instance(self, btn, type_key):
        import time
        unique_id = f"{type_key}_{int(time.time()*1000)}"
        new_instance = {
            'id': unique_id,
            'type': type_key,
            'enabled': True,
            'name': 'Nouveau Moteur'
        }
        # Valeurs par défaut
        if type_key == 'llamacpp':
            new_instance['url'] = 'http://localhost:8080/completion'
        else:
            new_instance['url'] = 'http://localhost:11434/api/generate'
            new_instance['model_name'] = 'mistral'
            
        self._render_instance_row(new_instance)
        # On scroll en bas (optionnel, simple hack)
        # adj = self.llm_list_box.get_parent().get_vadjustment()
        # GLib.idle_add(adj.set_value, adj.get_upper())

    def _render_instance_row(self, instance):
        if instance['type'] == 'llamacpp':
            self._create_llamacpp_row(instance)
        elif instance['type'] == 'ollama':
            self._create_ollama_row(instance)
        
        self.llm_list_box.show_all()

    def _create_llm_row_base(self, instance, status_label):
        """Crée le cadre d'une ligne (Header + Expander)"""
        unique_id = instance['id']
        
        row_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        row_box.get_style_context().add_class("llm-row") # Pour CSS
        # Stockage de l'ID dans le widget pour la sauvegarde
        row_box.instance_id = unique_id 
        row_box.instance_type = instance['type']
        
        # 1. HEADER
        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        
        # Entry pour le nom (Editable)
        entry_name = Gtk.Entry()
        entry_name.set_text(instance.get('name', instance['type']))
        entry_name.set_width_chars(20)
        header_box.pack_start(entry_name, False, False, 0)
        
        # Status Label
        header_box.pack_start(status_label, False, False, 0)
        
        # Spacer
        header_box.pack_start(Gtk.Label(), True, True, 0)
        
        # Boutons Priorité
        btn_up = Gtk.Button(label="⬆")
        btn_up.set_tooltip_text("Monter la priorité")
        btn_up.connect("clicked", self._on_move_llm, row_box, -1)
        header_box.pack_start(btn_up, False, False, 0)
        
        btn_down = Gtk.Button(label="⬇")
        btn_down.connect("clicked", self._on_move_llm, row_box, 1)
        header_box.pack_start(btn_down, False, False, 0)
        
        # Switch Enable (SÉLECTION LOGIQUE)
        # C'est celui-ci qui dit à l'agent : "Utilise cette configuration pour réfléchir"
        sw_enable = Gtk.Switch()
        sw_enable.set_active(instance.get('enabled', True))
        sw_enable.set_tooltip_text("ACTIVER ce profil pour l'Agent.\n(Ne démarre pas forcément le serveur, sélectionne juste la config).")
        header_box.pack_start(sw_enable, False, False, 5)
        
        # Bouton Supprimer
        btn_del = Gtk.Button(label="✕")
        btn_del.get_style_context().add_class("destructive-action") # Rouge si thème le supporte
        btn_del.set_tooltip_text("Supprimer cette configuration")
        btn_del.connect("clicked", self._on_delete_llm, row_box)
        header_box.pack_start(btn_del, False, False, 0)
        
        row_box.pack_start(header_box, False, False, 5)
        
        # 2. CONFIG AREA
        expander = Gtk.Expander(label="Détails de configuration")
        config_area = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        config_area.set_margin_start(20); config_area.set_margin_top(5)
        config_area.set_margin_bottom(10)
        
        # Bordure visuelle
        frame = Gtk.Frame()
        frame.add(config_area)
        expander.add(frame)
        row_box.pack_start(expander, False, False, 5)
        
        self.llm_list_box.pack_start(row_box, False, False, 0)
        
        # Initialisation refs
        self.llm_ui_refs[unique_id] = {
            'name': entry_name,
            'switch': sw_enable,
            'status_lbl': status_label
        }
        
        return config_area

    def _create_llamacpp_row(self, instance):
        unique_id = instance['id']
        lbl_stat = Gtk.Label()
        lbl_stat.set_markup("<span background='#aa0000' foreground='white' size='small'> OFFLINE </span>")
        
        config_area = self._create_llm_row_base(instance, lbl_stat)
        
        # Switch Serveur Process (Marche/Arrêt)
        hbox_srv = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        sw_process = Gtk.Switch()
        sw_process.set_tooltip_text("Démarrer ou arrêter l'instance serveur Llama.cpp pour ce modèle.")
        # On ne stocke pas l'état du process dans le JSON persistant (toujours off au démarrage UI)
        sw_process.connect("state-set", self._on_llama_process_switch, unique_id)
        
        lbl_srv = Gtk.Label(label="🔌 Serveur Llama.cpp (Marche / Arrêt) :")
        lbl_srv.set_markup("<b>🔌 Serveur Llama.cpp (Marche / Arrêt) :</b>")
        
        hbox_srv.pack_start(lbl_srv, False, False, 0)
        hbox_srv.pack_start(sw_process, False, False, 0)
        config_area.pack_start(hbox_srv, False, False, 0)
        
        # Grid Config
        grid = Gtk.Grid()
        grid.set_column_spacing(10); grid.set_row_spacing(10)
        
        # Binaire
        entry_bin = Gtk.Entry(placeholder_text="/chemin/vers/llama-server")
        entry_bin.set_text(instance.get('binary_path', ''))
        entry_bin.set_hexpand(True)
        
        # Boite pour Browse + Install
        box_bin_actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        
        btn_browse_bin = Gtk.Button(label="📁")
        btn_browse_bin.set_tooltip_text("Parcourir...")
        btn_browse_bin.connect("clicked", self._on_browse_file, entry_bin, "Sélectionner llama-server")
        
        btn_install = Gtk.Button(label="⬇️ Installer")
        btn_install.connect("clicked", self._on_install_llama_bin, entry_bin)
        
        box_bin_actions.pack_start(btn_browse_bin, False, False, 0)
        box_bin_actions.pack_start(btn_install, False, False, 0)
        
        grid.attach(Gtk.Label(label="Exécutable :"), 0, 0, 1, 1)
        grid.attach(entry_bin, 1, 0, 1, 1)
        grid.attach(box_bin_actions, 2, 0, 1, 1)

        # Modèle GGUF
        entry_gguf = Gtk.Entry(placeholder_text="/chemin/vers/model.gguf")
        entry_gguf.set_text(instance.get('gguf_path', ''))
        entry_gguf.set_hexpand(True)
        
        btn_browse_gguf = Gtk.Button(label="📁")
        btn_browse_gguf.set_tooltip_text("Choisir un modèle .gguf")
        btn_browse_gguf.connect("clicked", self._on_browse_file, entry_gguf, "Sélectionner Modèle", "*.gguf")

        grid.attach(Gtk.Label(label="Modèle GGUF :"), 0, 1, 1, 1)
        grid.attach(entry_gguf, 1, 1, 1, 1)
        grid.attach(btn_browse_gguf, 2, 1, 1, 1)
        
        # URL
        entry_url = Gtk.Entry()
        entry_url.set_text(instance.get('url', 'http://localhost:8080/completion'))
        entry_url.set_hexpand(True)
        
        grid.attach(Gtk.Label(label="URL API :"), 0, 2, 1, 1)
        grid.attach(entry_url, 1, 2, 1, 1)
        
        config_area.pack_start(grid, False, False, 0)
        
        # Refs spécifiques
        self.llm_ui_refs[unique_id]['bin'] = entry_bin
        self.llm_ui_refs[unique_id]['gguf'] = entry_gguf
        self.llm_ui_refs[unique_id]['url'] = entry_url
        self.llm_ui_refs[unique_id]['sw_process'] = sw_process # Pour update visuel status

    def _create_ollama_row(self, instance):
        unique_id = instance['id']
        lbl_stat = Gtk.Label()
        lbl_stat.set_markup("<span background='#aa0000' foreground='white' size='small'> OFFLINE </span>")
        
        config_area = self._create_llm_row_base(instance, lbl_stat)
        
        # Switch Service Ollama (Global)
        hbox_srv = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        sw_service = Gtk.Switch()
        
        # Tooltip d'avertissement demandé
        sw_service.set_tooltip_text("⚠️ Attention : Ollama est un service global.\nCe bouton contrôle l'instance Ollama pour TOUS les modèles.")
        
        sw_service.connect("state-set", self._on_ollama_service_switch, unique_id)
        
        lbl_srv = Gtk.Label()
        lbl_srv.set_markup("<b>🔌 Service Ollama (Global) :</b>")
        
        hbox_srv.pack_start(lbl_srv, False, False, 0)
        hbox_srv.pack_start(sw_service, False, False, 0)
        config_area.pack_start(hbox_srv, False, False, 0)
        
        grid = Gtk.Grid()
        grid.set_column_spacing(10); grid.set_row_spacing(10)

        entry_model = Gtk.Entry(placeholder_text="mistral-small")
        entry_model.set_text(instance.get('model_name', 'mistral-small'))
        entry_model.set_hexpand(True)
        
        entry_url = Gtk.Entry()
        entry_url.set_text(instance.get('url', 'http://localhost:11434/api/generate'))
        entry_url.set_hexpand(True)

        grid.attach(Gtk.Label(label="Modèle Ollama :"), 0, 0, 1, 1)
        grid.attach(entry_model, 1, 0, 1, 1)
        
        grid.attach(Gtk.Label(label="URL API :"), 0, 1, 1, 1)
        grid.attach(entry_url, 1, 1, 1, 1)

        config_area.pack_start(grid, False, False, 0)
        
        self.llm_ui_refs[unique_id]['model'] = entry_model
        self.llm_ui_refs[unique_id]['url'] = entry_url
        self.llm_ui_refs[unique_id]['sw_service'] = sw_service # Ref pour update visuel status

    # --- ACTIONS UI ---

    def _on_browse_file(self, btn, entry_target, title, filter_pattern=None):
        """Ouvre un sélecteur de fichier."""
        dialog = Gtk.FileChooserDialog(
            title=title,
            parent=self,
            action=Gtk.FileChooserAction.OPEN
        )
        dialog.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_OPEN, Gtk.ResponseType.OK
        )
        
        if filter_pattern:
            filter_file = Gtk.FileFilter()
            filter_file.set_name(f"Fichiers {filter_pattern}")
            filter_file.add_pattern(filter_pattern)
            dialog.add_filter(filter_file)
            
        filter_all = Gtk.FileFilter()
        filter_all.set_name("Tous les fichiers")
        filter_all.add_pattern("*")
        dialog.add_filter(filter_all)
        
        # Définit le dossier actuel si le chemin existe
        current_path = entry_target.get_text().strip()
        if current_path:
            current_path = os.path.expanduser(current_path)
            if os.path.exists(current_path):
                if os.path.isdir(current_path):
                    dialog.set_current_folder(current_path)
                else:
                    dialog.set_filename(current_path)
            elif os.path.exists(os.path.dirname(current_path)):
                dialog.set_current_folder(os.path.dirname(current_path))

        response = dialog.run()
        if response == Gtk.ResponseType.OK:
            filename = dialog.get_filename()
            entry_target.set_text(filename)
            
        dialog.destroy()

    def _on_move_llm(self, btn, row_widget, direction):
        parent = self.llm_list_box
        children = parent.get_children()
        try:
            curr_idx = children.index(row_widget)
            new_idx = curr_idx + direction
            if 0 <= new_idx < len(children):
                parent.reorder_child(row_widget, new_idx)
        except: pass

    def _on_delete_llm(self, btn, row_widget):
        # Confirmation basique : on supprime direct (c'est un dialog de config après tout)
        self.llm_list_box.remove(row_widget)
        # On nettoie les refs
        if row_widget.instance_id in self.llm_ui_refs:
            del self.llm_ui_refs[row_widget.instance_id]

    def _refresh_instances_status(self):
        if not self.get_visible(): return True # Continue timer
        
        from igor_brain import check_llama_status, check_ollama_status
        
        # On check Llama local globalement (port 8080)
        llama_ok = check_llama_status()
        # On check Ollama globalement (port 11434)
        ollama_ok = check_ollama_status()
        
        for uid, refs in self.llm_ui_refs.items():
            # Si c'est un Llama local
            if 'bin' in refs: 
                # On vérifie si l'URL pointe bien vers localhost:8080
                # (Simple check visuel, pour le switch process)
                url = refs['url'].get_text()
                if "8080" in url:
                    if llama_ok:
                        refs['status_lbl'].set_markup("<span background='#00aa00' foreground='white' weight='bold'> ONLINE </span>")
                        # if 'sw_process' in refs: refs['sw_process'].set_state(True) # Désactivé pour éviter boucle
                    else:
                        refs['status_lbl'].set_markup("<span background='#aa0000' foreground='white' size='small'> OFFLINE </span>")
                        # if 'sw_process' in refs: refs['sw_process'].set_state(False) # Désactivé pour éviter boucle
            
            # Si c'est Ollama
            elif 'model' in refs:
                if ollama_ok:
                    refs['status_lbl'].set_markup("<span background='#00aa00' foreground='white' weight='bold'> ONLINE </span>")
                    # Synchronise le switch si le service est détecté actif
                    # if 'sw_service' in refs: 
                    #    refs['sw_service'].set_state(True) # Désactivé pour éviter boucle
                else:
                    refs['status_lbl'].set_markup("<span background='#aa0000' foreground='white' size='small'> OFFLINE </span>")
                    # if 'sw_service' in refs: 
                    #    refs['sw_service'].set_state(False) # Désactivé pour éviter boucle
        
        return True

    def _on_llama_process_switch(self, switch, state, unique_id):
        from igor_brain import manage_local_server
        
        # On récupère les chemins de CETTE instance spécifique
        refs = self.llm_ui_refs.get(unique_id)
        if not refs: return True
        
        # FIX: Expanduser pour gérer le '~' et vérification basique
        bin_path = os.path.expanduser(refs['bin'].get_text().strip())
        gguf_path = os.path.expanduser(refs['gguf'].get_text().strip())
        
        if state and (not bin_path or not gguf_path):
            print("  [UI] Erreur: Chemins vides.", flush=True)
            # On remet le switch à off visuellement si les chemins sont vides
            switch.set_state(False)
            return True

        # On met à jour la mémoire temporairement pour que manage_local_server sache quoi lancer
        skills.MEMORY['llm_binary_path'] = bin_path
        skills.MEMORY['llm_gguf_path'] = gguf_path
        
        action = "start" if state else "stop"
        
        # On tente l'action
        success = manage_local_server(action)
        
        # Si le démarrage échoue, on remet le switch à OFF
        if action == "start" and not success:
             switch.set_state(False)
             
        return True

    def _on_ollama_service_switch(self, switch, state, unique_id):
        """Lance ou arrête le service Ollama via systemd (SUDO SANS MDP)."""
        import shutil
        
        print(f"  [DEBUG-OLLAMA] Switch toggled. State: {state}, ID: {unique_id}", flush=True)

        if not shutil.which("systemctl"):
            print("  [OLLAMA-CTRL] ❌ Erreur : 'systemctl' introuvable.", flush=True)
            switch.set_state(False)
            return True

        # Utilisation de 'sudo -n' (non-interactive).
        # Nécessite la config visudo : user ALL=(ALL) NOPASSWD: /usr/bin/systemctl start ollama
        action = "start" if state else "stop"
        cmd = ["sudo", "-n", "systemctl", action, "ollama"]
        
        def _run_bg():
            try:
                print(f"  [OLLAMA-CTRL] ⚙️ Exécution : {' '.join(cmd)}", flush=True)
                
                # On utilise run() pour capturer le code de retour immédiatement
                res = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True
                )
                
                print(f"  [DEBUG-OLLAMA] Return Code: {res.returncode}", flush=True)
                print(f"  [DEBUG-OLLAMA] STDOUT: {res.stdout.strip()}", flush=True)
                print(f"  [DEBUG-OLLAMA] STDERR: {res.stderr.strip()}", flush=True)

                if res.returncode == 0:
                    icon = "🚀" if state else "🛑"
                    print(f"  [OLLAMA-CTRL] {icon} Succès : Service {action}ed.", flush=True)
                else:
                    print(f"  [OLLAMA-CTRL] ❌ ÉCHEC (Code {res.returncode})", flush=True)
                    print(f"  [OLLAMA-LOG] {res.stderr.strip()}", flush=True)
                    if "password" in res.stderr.lower() or res.returncode == 1:
                        print("  [OLLAMA-HELP] 💡 ASTUCE : Ajoutez ceci dans 'sudo visudo' :")
                        try: user = os.getlogin()
                        except: user = os.environ.get('USER', 'utilisateur')
                        print(f"                 {user} ALL=(ALL) NOPASSWD: /usr/bin/systemctl start ollama, /usr/bin/systemctl stop ollama")
                    
                    # On remet le switch à l'état précédent visuellement
                    GLib.idle_add(switch.set_state, not state)

            except Exception as e:
                print(f"  [OLLAMA-CTRL] ❌ Exception : {e}", flush=True)
                GLib.idle_add(switch.set_state, not state)

        # Lancement dans un thread pour ne pas freezer l'interface
        threading.Thread(target=_run_bg, daemon=True).start()
            
        return False

    # --- TÉLÉCHARGEMENTS (Adaptés pour recevoir le widget cible) ---

    def _update_progress(self, fraction):
        self.progress_bar.set_fraction(fraction)
        self.progress_bar.set_text(f"Téléchargement... {int(fraction*100)}%")

    def _on_download_done(self, success, msg, target_entry=None):
        self.progress_bar.set_fraction(1.0 if success else 0.0)
        self.progress_bar.set_text(msg)
        if success and target_entry:
            # On met le chemin complet dans l'entry spécifique passée en argument
            # On doit reconstruire le chemin complet car msg ne contient que le nom parfois
            if "llama-server" in msg:
                target_entry.set_text(os.path.join(igor_config.USER_HOME, "igor_llm/llama-server"))
            elif "nemo" in msg.lower():
                target_entry.set_text(os.path.join(igor_config.USER_HOME, "igor_llm/mistral-nemo-12b.gguf"))
            elif "mistral" in msg.lower():
                target_entry.set_text(os.path.join(igor_config.USER_HOME, "igor_llm/mistral-small.gguf"))

    def _on_install_llama_bin(self, btn, target_entry):
        install_dir = os.path.join(igor_config.USER_HOME, "igor_llm")
        if not os.path.exists(install_dir): os.makedirs(install_dir)
        url = "https://github.com/ggerganov/llama.cpp/releases/download/b4665/llama-b4665-bin-ubuntu-x64.zip"
        zip_dest = os.path.join(install_dir, "llama.zip")
        
        def _install_logic():
            import requests, zipfile
            try:
                GLib.idle_add(self.progress_bar.set_text, "Téléchargement ZIP...")
                response = requests.get(url, stream=True)
                total = int(response.headers.get('content-length', 0))
                dl = 0
                with open(zip_dest, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        f.write(chunk)
                        dl += len(chunk)
                        if total: GLib.idle_add(self.progress_bar.set_fraction, dl/total)
                
                GLib.idle_add(self.progress_bar.set_text, "Extraction...")
                with zipfile.ZipFile(zip_dest, 'r') as zip_ref:
                    for file in zip_ref.namelist():
                        if "llama-server" in file:
                            zip_ref.extract(file, install_dir)
                            extracted_path = os.path.join(install_dir, file)
                            final_path = os.path.join(install_dir, "llama-server")
                            os.rename(extracted_path, final_path)
                            os.chmod(final_path, 0o755)
                            # Update UI
                            GLib.idle_add(lambda: target_entry.set_text(final_path))
                            break
                os.remove(zip_dest)
                GLib.idle_add(self._on_download_done, True, "Llama-server installé !", None)
            except Exception as e:
                GLib.idle_add(self._on_download_done, False, f"Erreur: {e}", None)

        threading.Thread(target=_install_logic, daemon=True).start()

    def _on_download_mistral(self, btn, target_entry):
        self._generic_download_model(
            "https://huggingface.co/MaziyarPanahi/Mistral-Small-24B-Instruct-2501-GGUF/resolve/main/Mistral-Small-24B-Instruct-2501.Q4_K_M.gguf",
            "mistral-small.gguf",
            target_entry
        )

    def _on_download_nemo(self, btn, target_entry):
        self._generic_download_model(
            "https://huggingface.co/bartowski/Mistral-Nemo-12B-Instruct-v1-GGUF/resolve/main/Mistral-Nemo-12B-Instruct-v1-Q4_K_M.gguf",
            "mistral-nemo-12b.gguf",
            target_entry
        )

    def _generic_download_model(self, url, filename, target_entry):
        install_dir = os.path.join(igor_config.USER_HOME, "igor_llm")
        if not os.path.exists(install_dir): os.makedirs(install_dir)
        dest = os.path.join(install_dir, filename)
        
        # On utilise une lambda pour passer target_entry au callback de fin
        done_cb = lambda success, msg: self._on_download_done(success, msg, target_entry)
        igor_system.download_with_progress(url, dest, self._update_progress, done_cb)

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
    # SAUVEGARDE GLOBALE (MODIFIÉE POUR MULTI-INSTANCES)
    # ============================================================
    
    def get_settings(self):
        """Récupère tous les paramètres et reconstruit la liste des LLMs"""
        settings = {}
        
        # ... (Partie Audio/Apps/Général inchangée) ...
        settings['audio_config'] = {
            'mic_enabled': self.sw_mic.get_active(),
            'mic_index': self._get_selected_index(self.combo_mic),
            'sys_enabled': self.sw_sys.get_active(),
            'sys_index': self._get_selected_index(self.combo_sys),
            'sys_delay': int(self.scale_delay.get_value()),
            'debug_audio': self.sw_debug.get_active()
        }
        settings['fav_browser'] = self.entry_fav_browser.get_text().strip()
        settings['fav_email'] = self.entry_fav_email.get_text().strip()
        settings['fav_music_app'] = self.entry_fav_music_app.get_text().strip()
        settings['fav_voip'] = self.entry_fav_voip.get_text().strip()
        settings['fav_terminal'] = self.entry_fav_terminal.get_text().strip()
        settings['fav_filemanager'] = self.entry_fav_filemanager.get_text().strip()
        settings['voice_speed'] = round(self.scale_speed.get_value(), 1)
        settings['alarm_sound'] = self.combo_alarm.get_active_text().lower()
        settings['auto_learn'] = self.sw_autolearn.get_active()
        settings['agent_name'] = self.entry_agent_name.get_text().strip()
        settings['user_name'] = self.entry_user_name.get_text().strip()
        
        # === SAUVEGARDE LLMS ===
        new_instances = []
        children = self.llm_list_box.get_children()
        
        first_active_found = False
        
        # On parcourt l'ordre VISUEL
        for row in children:
            if hasattr(row, 'instance_id'):
                uid = row.instance_id
                refs = self.llm_ui_refs.get(uid)
                if not refs: continue
                
                instance_data = {
                    'id': uid,
                    'type': row.instance_type,
                    'name': refs['name'].get_text().strip(),
                    'enabled': refs['switch'].get_active(),
                    'url': refs['url'].get_text().strip()
                }
                
                if row.instance_type == 'llamacpp':
                    instance_data['binary_path'] = refs['bin'].get_text().strip()
                    instance_data['gguf_path'] = refs['gguf'].get_text().strip()
                elif row.instance_type == 'ollama':
                    instance_data['model_name'] = refs['model'].get_text().strip()
                
                new_instances.append(instance_data)
                
                # Le premier moteur activé devient le moteur par défaut GLOBAL
                if instance_data['enabled'] and not first_active_found:
                    first_active_found = True
                    settings['llm_backend'] = instance_data['type']
                    settings['llm_api_url'] = instance_data['url']
                    
                    # On met à jour les clés legacy pour compatibilité avec igor_brain
                    if row.instance_type == 'llamacpp':
                        settings['llm_binary_path'] = instance_data['binary_path']
                        settings['llm_gguf_path'] = instance_data['gguf_path']
                    elif row.instance_type == 'ollama':
                        settings['llm_model_name'] = instance_data['model_name']

        settings['llm_instances'] = new_instances
        
        # Fallback si aucun actif
        if not first_active_found:
            settings['llm_backend'] = 'llamacpp'
            settings['llm_api_url'] = 'http://localhost:8080/completion'

        return settings