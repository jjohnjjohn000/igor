# igor_app_selector.py
"""Module de sélection manuelle d'applications avec catégorisation."""

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, GLib
from igor_config import MEMORY, save_memory
from igor_system import APP_METADATA

# Mapping des catégories XDG vers des groupes logiques
CATEGORY_GROUPS = {
    "Navigateurs Web": ["WebBrowser"],
    "Email": ["Email"],
    "Messagerie": ["InstantMessaging", "Chat"],
    "VoIP / Visio": ["VideoConference", "Telephony"],
    "Musique": ["Audio", "AudioVideo", "Player"],
    "Vidéo": ["Video", "Player"],
    "Bureautique": ["Office", "WordProcessor", "Spreadsheet", "Presentation"],
    "Éditeurs de texte": ["TextEditor"],
    "Développement": ["Development", "IDE"],
    "Terminal": ["TerminalEmulator"],
    "Gestionnaire de fichiers": ["FileManager"],
    "Graphisme": ["Graphics", "RasterGraphics", "VectorGraphics"],
    "Utilitaires": ["Utility", "Archiving", "Compression"],
    "Jeux": ["Game"],
    "Système": ["System", "Settings"]
}

# Mapping des préférences vers les catégories
PREFERENCE_TO_CATEGORY = {
    "fav_browser": "Navigateurs Web",
    "fav_email": "Email",
    "fav_voip": "VoIP / Visio",
    "fav_music_app": "Musique",
    "fav_terminal": "Terminal",
    "fav_filemanager": "Gestionnaire de fichiers"
}

def categorize_apps():
    """Organise toutes les applications découvertes par catégories."""
    categorized = {cat: [] for cat in CATEGORY_GROUPS.keys()}
    categorized["Autres"] = []
    
    seen_cmds = set()
    
    for cmd, meta in APP_METADATA.items():
        if cmd in seen_cmds:
            continue
        seen_cmds.add(cmd)
        
        app_name = meta['names'][0] if meta['names'] else cmd.split('/')[-1]
        app_name = app_name.title()
        
        categories = meta['categories']
        matched = False
        
        for group_name, xdg_cats in CATEGORY_GROUPS.items():
            if any(cat in categories for cat in xdg_cats):
                categorized[group_name].append((app_name, cmd))
                matched = True
                break
        
        if not matched:
            categorized["Autres"].append((app_name, cmd))
    
    # Tri alphabétique
    for category in categorized:
        categorized[category].sort(key=lambda x: x[0].lower())
    
    # Supprimer les catégories vides
    categorized = {k: v for k, v in categorized.items() if v}
    
    return categorized

class AppSelectorDialog(Gtk.Dialog):
    """Dialogue de sélection d'application avec organisation par catégories."""
    
    def __init__(self, parent, preference_key, current_value=None):
        super().__init__(
            title=f"Choisir une application - {self._get_pref_title(preference_key)}",
            parent=parent,
            modal=True
        )
        
        self.preference_key = preference_key
        self.current_value = current_value
        self.selected_cmd = None
        
        self.set_default_size(700, 500)
        self.set_border_width(10)
        
        self.add_button("Annuler", Gtk.ResponseType.CANCEL)
        self.add_button("Confirmer", Gtk.ResponseType.OK)
        
        self._build_ui()
    
    def _get_pref_title(self, key):
        titles = {
            "fav_browser": "Navigateur Web",
            "fav_email": "Client Email",
            "fav_voip": "Application VoIP",
            "fav_music_app": "Lecteur Musical",
            "fav_terminal": "Terminal",
            "fav_filemanager": "Gestionnaire de Fichiers"
        }
        return titles.get(key, key)
    
    def _build_ui(self):
        content = self.get_content_area()
        content.set_spacing(10)
        
        # Barre de recherche
        search_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        search_label = Gtk.Label(label="🔍 Recherche:")
        self.search_entry = Gtk.Entry()
        self.search_entry.set_placeholder_text("Filtrer les applications...")
        self.search_entry.connect("changed", self._on_search_changed)
        
        search_box.pack_start(search_label, False, False, 0)
        search_box.pack_start(self.search_entry, True, True, 0)
        content.pack_start(search_box, False, False, 0)
        
        # Affichage actuel
        if self.current_value:
            current_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
            current_label = Gtk.Label()
            current_label.set_markup(f"<b>Actuel:</b> {self._format_app_name(self.current_value)}")
            current_box.pack_start(current_label, False, False, 0)
            content.pack_start(current_box, False, False, 0)
        
        # Notebook par catégories
        self.notebook = Gtk.Notebook()
        self.notebook.set_scrollable(True)
        
        categorized = categorize_apps()
        target_category = PREFERENCE_TO_CATEGORY.get(self.preference_key)
        
        if target_category and target_category in categorized:
            self._add_category_tab(target_category, categorized[target_category], first=True)
            for cat_name, apps in categorized.items():
                if cat_name != target_category:
                    self._add_category_tab(cat_name, apps)
        else:
            for cat_name, apps in categorized.items():
                self._add_category_tab(cat_name, apps)
        
        content.pack_start(self.notebook, True, True, 0)
        
        info_label = Gtk.Label()
        info_label.set_markup("<i>💡 Double-cliquez sur une application pour la sélectionner</i>")
        content.pack_start(info_label, False, False, 0)
        
        self.show_all()
    
    def _add_category_tab(self, category_name, apps, first=False):
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        
        store = Gtk.ListStore(str, str)
        for app_name, cmd in apps:
            store.append([app_name, cmd])
        
        treeview = Gtk.TreeView(model=store)
        treeview.set_headers_visible(True)
        treeview.set_search_column(0)
        
        renderer_name = Gtk.CellRendererText()
        column_name = Gtk.TreeViewColumn("Application", renderer_name, text=0)
        column_name.set_sort_column_id(0)
        treeview.append_column(column_name)
        
        renderer_cmd = Gtk.CellRendererText()
        renderer_cmd.set_property("foreground", "#666666")
        renderer_cmd.set_property("size-points", 9)
        column_cmd = Gtk.TreeViewColumn("Commande", renderer_cmd, text=1)
        treeview.append_column(column_cmd)
        
        selection = treeview.get_selection()
        selection.set_mode(Gtk.SelectionMode.SINGLE)
        
        treeview.connect("row-activated", self._on_row_activated)
        selection.connect("changed", self._on_selection_changed, store)
        
        scrolled.add(treeview)
        
        label = Gtk.Label(label=f"{category_name} ({len(apps)})")
        self.notebook.append_page(scrolled, label)
        
        if first:
            self.notebook.set_current_page(self.notebook.get_n_pages() - 1)
    
    def _on_selection_changed(self, selection, store):
        model, treeiter = selection.get_selected()
        if treeiter:
            self.selected_cmd = model[treeiter][1]
    
    def _on_row_activated(self, treeview, path, column):
        model = treeview.get_model()
        treeiter = model.get_iter(path)
        self.selected_cmd = model[treeiter][1]
        self.response(Gtk.ResponseType.OK)
    
    def _on_search_changed(self, entry):
        search_text = entry.get_text().lower()
        
        for page_num in range(self.notebook.get_n_pages()):
            page = self.notebook.get_nth_page(page_num)
            scrolled = page
            treeview = scrolled.get_child()
            model = treeview.get_model()
            
            if hasattr(model, 'get_model'):
                original_model = model.get_model()
            else:
                original_model = model
            
            if search_text:
                filtered = original_model.filter_new()
                filtered.set_visible_func(self._filter_func, search_text)
                treeview.set_model(filtered)
            else:
                treeview.set_model(original_model)
    
    def _filter_func(self, model, iter, search_text):
        app_name = model[iter][0].lower()
        cmd = model[iter][1].lower()
        return search_text in app_name or search_text in cmd
    
    def _format_app_name(self, cmd):
        if cmd in APP_METADATA:
            names = APP_METADATA[cmd]['names']
            if names:
                return names[0].title()
        return cmd.split('/')[-1].title()
    
    def get_selected_app(self):
        return self.selected_cmd

def open_app_selector(parent_window, preference_key):
    """Ouvre le dialogue de sélection d'application."""
    current_value = MEMORY.get(preference_key)
    
    dialog = AppSelectorDialog(parent_window, preference_key, current_value)
    response = dialog.run()
    
    selected = None
    if response == Gtk.ResponseType.OK:
        selected = dialog.get_selected_app()
        if selected:
            MEMORY[preference_key] = selected
            save_memory(MEMORY)
    
    dialog.destroy()
    return selected