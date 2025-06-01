import os
import sys
import time
import threading
import logging
import configparser
import serial
import serial.tools.list_ports
import xml.etree.ElementTree as ET

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import pygame

import threading
import tkinter as tk
import tkinter.font as tkfont
import ctypes
from PIL import Image, ImageDraw, ImageFont

# Logging
logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

# Configuration
BASE_DIR         = os.path.dirname(os.path.abspath(__file__))
ES_EVENT_FILE    = os.path.join(BASE_DIR, 'ESEvent.arg')
PANEL_CONFIG_INI = os.path.join(BASE_DIR, 'config.ini')
SYSTEMS_DIR      = os.path.join(BASE_DIR, 'systems')
BAUDRATE         = 115200
OFF_COLOR        = 'OFF'
DEFAULT_COLOR    = 'WHITE'
TEXT_COLOR       = '#FFFFFF'
BG_COLOR         = '#2961b0'

retrobat_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))

# 3) Recalcule le chemin vers Cabin-Regular.ttf dans le thème Carbon
ES_FONT_PATH = os.path.join(
    retrobat_root,
    'emulationstation', '.emulationstation',
    'themes', 'es-theme-carbon-master',
    'art', 'fonts', 'Cabin-Regular.ttf'
)
FR_PRIVATE = 0x10

# 4) Vérifie qu’il existe
if not os.path.exists(ES_FONT_PATH):
    raise FileNotFoundError(f"Police non trouvée : {ES_FONT_PATH}")
# charge la police en privé
ctypes.windll.gdi32.AddFontResourceExW(ES_FONT_PATH, FR_PRIVATE, 0)

script_dir = os.path.dirname(os.path.realpath(__file__))
# Ton plugin est dans …/plugins/LedPanelManager/
ICON_PATH = os.path.join(script_dir, 'images', 'arcadepanel.png')
if not os.path.exists(ICON_PATH):
    raise FileNotFoundError(f"Icône introuvable : {ICON_PATH}")

def show_popup_tk(text, duration=600, font_size=24, alpha=0.9):
    """
    Affiche un petit OSD Tkinter centré, toujours topmost, frameless,
    ferme après `duration` ms, puis remet le focus sur ES.
    """
    def _run():
        # 1) Créer la fenêtre
        root = tk.Tk()
        root.overrideredirect(True)           # pas de bordure
        root.attributes("-topmost", True)     # topmost
        root.attributes("-alpha", alpha)      # transparence
        root.configure(bg=BG_COLOR)

        # 2) Le label
        #202020 969696 334b7a 2961b0
        icon = tk.PhotoImage(file=ICON_PATH, master=root)
        icon = icon.subsample(2, 2)
        #lbl = tk.Label(root, text=text, image=icon, font=("Cabin", font_size), bg="#2961b0", fg="white", padx=20, pady=20)
        #lbl.image = icon  # garde une référence pour l’empêcher d’être garbage-collected
        #lbl.pack()

        # Frame conteneur
        container = tk.Frame(root, bg=BG_COLOR)
        container.pack(padx=20, pady=10)

        # Label icône
        icon_lbl = tk.Label(container, image=icon, bg=BG_COLOR)
        icon_lbl.image = icon
        icon_lbl.pack(side="left")

        # Label texte
        text_lbl = tk.Label(
            container, text="PANEL : "+text,
            font=("Cabin", font_size), fg=TEXT_COLOR, bg=BG_COLOR
        )
        text_lbl.pack(side="left", padx=(10,0))

        # 3) Centrer
        root.update_idletasks()
        w, h = root.winfo_width(), root.winfo_height()
        ws, hs = root.winfo_screenwidth(), root.winfo_screenheight()
        x, y = (ws-w)//2, (hs-h)//2
        root.geometry(f"{w}x{h}+{x}+{y}")

        # 4) Fermeture + refocus
        def close_and_refocus():
            root.destroy()
            # remet le focus sur ES
            es = ctypes.windll.user32.FindWindowW(None, "EmulationStation")
            if es:
                ctypes.windll.user32.SetForegroundWindow(es)

        root.after(duration, close_and_refocus)
        root.after(duration, close_and_refocus)
        root.after(duration, close_and_refocus)
        root.mainloop()

    threading.Thread(target=_run, daemon=True).start()


def get_system_emulator(system_name: str) -> (str, str):
    """
    Lit <RetroBatRoot>/emulationstation/.emulationstation/es_settings.cfg
    pour extraire l'émulateur et le core par défaut du système <system_name>.
    Renvoie un tuple (emulator, core). Si le fichier est introuvable ou qu'aucune entrée
    n'existe, renvoie ("", "").
    """
    # BASE_DIR est, par exemple, ...\RetroBatV7\plugins\LedPanelManager
    # On remonte jusqu'à <RetroBatRoot> (= parent de "plugins").
    retrobat_root = os.path.dirname(os.path.dirname(BASE_DIR))
    es_home = os.path.join(retrobat_root, "emulationstation", ".emulationstation")
    cfg_path = os.path.join(es_home, "es_settings.cfg")

    emulator, core = "", ""
    try:
        tree = ET.parse(cfg_path)
        root = tree.getroot()
        # ex. <string name="mame.emulator" value="libretro" />
        #     <string name="mame.core"     value="mame2014" />
        for s in root.findall('string'):
            name_attr = s.get('name', '')
            val_attr  = s.get('value', '')
            if name_attr == f"{system_name}.emulator":
                emulator = val_attr
            elif name_attr == f"{system_name}.core":
                core = val_attr
        return emulator, core
    except FileNotFoundError:
        logger.debug(f"es_settings.cfg non trouvé à '{cfg_path}'")
        return "", ""
    except Exception as e:
        logger.debug(f"Impossible de parser '{cfg_path}': {e}")
        return "", ""

def get_game_emulator(system_name: str, game_name: str) -> (str, str):
    """
    Lit <RetroBatRoot>/roms/<system_name>/gamelist.xml et recherche
    dans chaque <game> l'élément dont <name> ou <path> correspond à <game_name>,
    puis renvoie (emulator, core) s'il existe une surcharge. Sinon, retourne ("", "").
    """
    retrobat_root = os.path.dirname(os.path.dirname(BASE_DIR))
    roms_dir = os.path.join(retrobat_root, "roms", system_name)
    gamelist_path = os.path.join(roms_dir, "gamelist.xml")

    emulator, core = "", ""
    try:
        tree = ET.parse(gamelist_path)
        root = tree.getroot()
        for game_elem in root.findall('game'):
            name_tag = game_elem.find('name')
            path_tag = game_elem.find('path')
            name_val = name_tag.text.strip() if name_tag is not None else ""
            path_val = path_tag.text.strip() if path_tag is not None else ""
            base = os.path.splitext(os.path.basename(path_val))[0]
            if game_name == name_val or game_name == base:
                em_tag = game_elem.find('emulator')
                co_tag = game_elem.find('core')
                if em_tag is not None and em_tag.text:
                    emulator = em_tag.text.strip()
                if co_tag is not None and co_tag.text:
                    core = co_tag.text.strip()
                break
        return emulator, core
    except FileNotFoundError:
        logger.debug(f"gamelist.xml non trouvé à '{gamelist_path}'")
        return "", ""
    except Exception as e:
        logger.debug(f"Impossible de parser '{gamelist_path}': {e}")
        return "", ""


def _read_panel_cfg() -> configparser.ConfigParser:
    cfg = configparser.ConfigParser()
    try:
        with open(PANEL_CONFIG_INI, encoding='utf-8', errors='ignore') as fh:
            cfg.read_file(fh)
    except Exception:
        cfg.read(PANEL_CONFIG_INI)
    return cfg

def load_layout_buttons(system, btn_count, phys_to_label):
    xml_path = os.path.join(SYSTEMS_DIR, f"{system}.xml")
    if not os.path.exists(xml_path):
        logger.warning(f"No XML for system '{system}' at {xml_path}")
        return []
    try:
        tree = ET.parse(xml_path)
        for layout in tree.findall('.//layout'):
            if layout.get('panelButtons') == str(btn_count):
                out = []
                joy = layout.find('joystick')
                if joy is not None:
                    c = joy.get('color', DEFAULT_COLOR).upper()
                    out.append(('JOY', OFF_COLOR if c == 'BLACK' else c))
                for btn in layout.findall('button'):
                    phys   = btn.get('physical')
                    idname = btn.get('id','').upper()
                    label  = idname if idname in ('START','COIN','JOY') else phys_to_label.get(phys, f"B{phys}")
                    c      = btn.get('color', DEFAULT_COLOR).upper()
                    out.append((label, OFF_COLOR if c == 'BLACK' else c))
                return out
    except Exception as e:
        logger.error(f"Error parsing XML for {system}: {e}")
    return []

def parse_es_event(path):
    with open(path, encoding='cp1252') as f:
        raw = f.read().replace('\r','').replace('\n','')
    data = raw.strip()
    params = dict(p.split('=', 1) for p in data.split('&') if '=' in p)
    ev     = params.get('event','').strip('"').lower()
    system = params.get('param1','').strip('"').strip().lower()
    raw2   = params.get('param2','').strip('"').strip()
    # on renvoie raw2 pour qu’on puisse déterminer fichier vs dossier
    return ev, system, raw2

def load_game_layout_buttons(system, game_name, btn_count, phys_to_label):
    path = os.path.join(SYSTEMS_DIR, system, f"{game_name}.xml")
    if not os.path.exists(path):
        logger.warning(f"No game XML for '{system}/{game_name}'")
        return []
    try:
        tree = ET.parse(path)
        # on cherche la section <game name="game_name">
        game_elem = tree.find(f".//game[@name='{game_name}']")
        if game_elem is None:
            logger.warning(f"<game name='{game_name}'> not found in {path}")
            return []
        # trouver layout à panelButtons=btn_count
        layout = game_elem.find(f".//layout[@panelButtons='{btn_count}']")
        if layout is None:
            logger.warning(f"No layout[@panelButtons={btn_count}] in {path}")
            return []
        out = []
        # joystick
        joy = layout.find('joystick')
        if joy is not None:
            c = joy.get('color', DEFAULT_COLOR).upper()
            out.append(('JOY', OFF_COLOR if c=='BLACK' else c))
        # chaque bouton
        for btn in layout.findall('button'):
            phys   = btn.get('physical')
            label  = btn.get('id','').upper() if btn.get('id','').upper() in ('START','COIN') \
                     else phys_to_label.get(phys, f"B{phys}")
            c      = btn.get('color', DEFAULT_COLOR).upper()
            out.append((label, OFF_COLOR if c=='BLACK' else c))
        return out

    except Exception as e:
        logger.error(f"Error parsing game XML {path}: {e}")
        return []

def find_pico():
    logger.info("🔍 Scanning serial ports for Pico...")
    for port in serial.tools.list_ports.comports():
        try:
            s = serial.Serial(port.device, BAUDRATE, timeout=1)
            s.reset_input_buffer(); time.sleep(0.2)
            s.write(b"ping\n"); time.sleep(0.2)
            data = s.read_all().lower()
            s.close()
            if b"pong" in data:
                logger.info(f"✅ Pico found on {port.device}")
                return port.device
        except Exception:
            continue
    logger.error("❌ No Pico detected.")
    return None


class LedEventHandler(FileSystemEventHandler):
    def __init__(self, ser, panel_id):
        self.ser           = ser
        self.panel_id      = panel_id
        self.last_system   = None
        self.listening     = False
        self.last_es_event = (None, None, None)
        self.lip_events    = []
        # ——————————————————————————————————————————————————
        #   LAYOUTS “SYSTÈME”
        # ——————————————————————————————————————————————————
        self.system_layouts     = []    # liste des layouts dispo pour le système courant
        self.current_layout_idx = 0     # index du layout actif (système)
        self.in_game            = False

        # ——————————————————————————————————————————————————
        #   LAYOUTS “JEU”
        # ——————————————————————————————————————————————————
        self.current_game       = None  # nom du jeu actuellement en menu “game-selected”
        self.game_layouts       = []    # liste des layouts dispo pour ce jeu
        self.current_game_idx   = 0     # index du layout actif (jeu)

    def _get_saved_layout_idx(self, system: str) -> int:
        cfg = _read_panel_cfg()
        has_section = cfg.has_section('PanelDefaults')
        logger.debug(f"Fetching saved layout for system '{system}'. Section exists? {has_section}")
        if has_section:
            try:
                saved_name = cfg.get('PanelDefaults', system)
                logger.debug(f"Saved name from config: '{saved_name}'")
                names = [l.get('name') for l in self.system_layouts]
                logger.debug(f"Current layout options: {names}")
                for i, l in enumerate(self.system_layouts):
                    if l.get('name') == saved_name:
                        logger.debug(f"Matched saved layout '{saved_name}' at index {i}")
                        return i
            except Exception as e:
                logger.debug(f"Error fetching saved layout: {e}")
        logger.debug(f"No saved layout match for system '{system}', default to 0")
        return 0

    def _save_layout_idx(self, system: str, idx: int) -> None:
        cfg = _read_panel_cfg()
        if not cfg.has_section('PanelDefaults'):
            cfg.add_section('PanelDefaults')
            logger.debug("Created PanelDefaults section.")
        name = ''
        if 0 <= idx < len(self.system_layouts):
            name = self.system_layouts[idx].get('name', '')
        logger.debug(f"Saving idx {idx} (name '{name}') for system '{system}'")
        cfg.set('PanelDefaults', system, name)
        tmp = PANEL_CONFIG_INI + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as fh:
            cfg.write(fh)
        os.replace(tmp, PANEL_CONFIG_INI)
        logger.debug(f"PanelDefaults section now: {dict(cfg.items('PanelDefaults'))}")

    def _load_system_layouts(self, system: str) -> None:
        """
        Charge les layouts “système” depuis <SYSTEMS_DIR>/<system>.xml>
        et positionne self.current_layout_idx sur l’index déjà sauvé (ou 0).
        """
        system_name = os.path.basename(system) if os.path.sep in system else system
        xml_path = os.path.join(SYSTEMS_DIR, f"{system_name}.xml")

        # 1) Charger les layouts depuis le XML
        self.system_layouts = self._load_layouts_from_xml(xml_path)
        if not self.system_layouts:
            logger.warning(f"Aucun layout système trouvé pour '{system_name}' (fichier: {xml_path})")
            self.current_layout_idx = 0
            return

        # 2) Récupérer l’index déjà sauvegardé dans config.ini
        idx = self._get_saved_layout_idx(system_name)
        if idx < 0 or idx >= len(self.system_layouts):
            logger.debug(f"Saved idx {idx} hors bornes pour '{system_name}', on retombe à 0")
            idx = 0
        self.current_layout_idx = idx

        names = ", ".join(l['name'] for l in self.system_layouts)
        logger.info(f"Loaded {len(self.system_layouts)} layouts for '{system_name}': {names}")

    def _load_layouts_from_xml(self, xml_path: str):
        """
        Lit le fichier XML de layouts (que ce soit pour un système ou un jeu)
        et renvoie une liste de dicts de la forme :
            [ { 'name': <nom_layout>, 'buttons': [(label, couleur), …] }, … ]
        Ne renvoie [] que si le fichier n'existe pas ou qu’aucun <layout> matching n’est trouvé.
        """
        cfg = _read_panel_cfg()

        # 1) Construction de phys_to_label à partir de la section [Panel] de config.ini
        phys_to_label = {}
        total = cfg.getint('Panel', 'buttons_count', fallback=0)
        for i in range(1, total + 1):
            opt = f'panel_button_{i}'
            if cfg.has_option('Panel', opt):
                phys_to_label[cfg.get('Panel', opt).rstrip(';')] = f"B{i}"
        for opt, lab in [
                ('panel_button_select', 'COIN'),
                ('panel_button_start',  'START'),
                ('panel_button_joy',    'JOY')
            ]:
            if cfg.has_option('Panel', opt):
                phys_to_label[cfg.get('Panel', opt).rstrip(';')] = lab

        # 2) Lecture du nombre de boutons Player1 (btn_cnt)
        btn_cnt = cfg.getint(
            'Panel', 'Player1_buttons_count',
            fallback=cfg.getint('Panel', 'buttons_count', fallback=0)
        )

        layouts = []
        if not os.path.exists(xml_path):
            return []  # pas de fichier → pas de layouts

        try:
            tree = ET.parse(xml_path)
            root = tree.getroot()
            for layout in root.findall(f".//layout[@panelButtons='{btn_cnt}']"):
                name = layout.get('name') or layout.get('type')
                mapping = []

                # – Joystick
                joy = layout.find('joystick')
                if joy is not None:
                    c = joy.get('color', DEFAULT_COLOR).upper()
                    mapping.append(("JOY", "OFF" if c == "BLACK" else c))

                # – Chaque bouton
                for btn in layout.findall('button'):
                    phys = btn.get('physical')
                    idn  = btn.get('id', '').upper()
                    if idn in ('START', 'COIN', 'JOY'):
                        label = idn
                    else:
                        label = phys_to_label.get(phys, f"B{phys}")
                    c = btn.get('color', DEFAULT_COLOR).upper()
                    mapping.append((label, "OFF" if c == "BLACK" else c))

                layouts.append({'name': name, 'buttons': mapping})

        except Exception as e:
            logger.error(f"Error loading layouts from '{xml_path}': {e}")
            return []

        return layouts


    def _apply_saved_layout(self, key: str, layouts, idx_attr: str):
        """
        *key*            : chaîne “system” ou “system|game” (pour la clef PanelDefaults)
        *layouts*        : liste <{'name':…, 'buttons': [(label,couleur),…] }>
        *idx_attr*       : nom de l’attribut self à mettre à jour
                           (par exemple 'current_layout_idx' ou 'current_game_idx').

        1) Vérifie d’abord que `layouts` n'est pas vide, sinon on sort.
        2) Pour forcer _get_saved_layout_idx​ à regarder *cet* array, on fait temporairement :
           self.system_layouts = layouts
        3) saved_idx = self._get_saved_layout_idx(key). Si en dehors de bornes, on retombe à 0
           et on fait setattr(self, idx_attr, saved_idx).
        4) Reconstruit le SetPanelColors pour layouts[saved_idx], l’écrit en série,
           puis appelle self._save_layout_idx(key, saved_idx).
        5) Restaure l’ancienne self.system_layouts avant de quitter.
        """
        if not layouts:
            return

        # 2) “usurper” self.system_layouts pour que _get_saved_layout_idx​ regarde ici
        prev = self.system_layouts
        self.system_layouts = layouts

        saved_idx = self._get_saved_layout_idx(key)
        if saved_idx < 0 or saved_idx >= len(layouts):
            saved_idx = 0
        setattr(self, idx_attr, saved_idx)

        # 4) Envoi de la commande SetPanelColors pour le layout choisi
        entry = layouts[saved_idx]
        cfg = _read_panel_cfg()
        panels = '|'.join(str(i) for i in range(
            1,
            cfg.getint('Panel', 'players_count', fallback=1) + 1
        ))
        mapping = ';'.join(f"{lbl}:{clr}" for lbl, clr in entry['buttons'])
        cmd = f"SetPanelColors={panels},{mapping},default=yes\n"
        try:
            self.ser.write(cmd.encode('utf-8'))
            logger.info(f"    ➡ Sent ({key} layout) [{saved_idx}] '{entry['name']}'")
        except Exception as e:
            logger.error(f"    Erreur envoi layout pour '{key}': {e}")

        # 4b) Sauvegarde dans config.ini (PanelDefaults)
        self._save_layout_idx(key, saved_idx)

        # 5) Restauration
        self.system_layouts = prev

    def _send_current_layout(self):
        """
        Envoie en série le SetPanelColors correspondant à self.system_layouts[self.current_layout_idx].
        (Depuis la refactorisation, chaque entry est un dict { 'name': str, 'buttons': [(label, couleur), …] }.)
        """
        if not self.system_layouts:
            logger.warning("No layouts available to send")
            return

        entry = self.system_layouts[self.current_layout_idx]
        cfg = _read_panel_cfg()
        panels = '|'.join(
            str(i)
            for i in range(1, cfg.getint('Panel', 'players_count', fallback=1) + 1)
        )
        mapping = ';'.join(f"{lbl}:{clr}" for lbl, clr in entry['buttons'])
        cmd = f"SetPanelColors={panels},{mapping},default=yes\n"

        try:
            self.ser.write(cmd.encode('utf-8'))
            logger.info(f"➡ Switched to layout [{self.current_layout_idx}] '{entry['name']}'")
        except Exception as e:
            logger.error(f"Error sending layout: {e}")

    def on_modified(self, event):
        # Parse EmulationStation event
        ev, system, raw2 = parse_es_event(ES_EVENT_FILE)
        logger.debug(f"on_modified: ev='{ev}', system='{system}' (in_game={self.in_game})")

        # —————— 1) system-selected: switch system, exit game mode
        if ev == 'system-selected' and (system != self.last_system or self.in_game):
            logger.info(f"Branch: system-selected for '{system}' (last was '{self.last_system}')")

            # On quitte le mode “jeu”
            self.current_game = None
            self.in_game      = False

            emu_sys, core_sys = get_system_emulator(system)
            if emu_sys or core_sys:
                logger.info(f"  Système '{system}' → emulator='{emu_sys}', core='{core_sys}'")
            else:
                logger.info(f"  Aucun émulateur par défaut défini pour '{system}'")

            # Recharge et applique le layout système
            self._load_system_layouts(system)
            self._apply_saved_layout(system, self.system_layouts, 'current_layout_idx')

            self.last_system = system
            self.lip_events  = []
            return

        # —————— 2) game-selected ——————
        if ev == 'game-selected' or self.in_game :

            # On n’est pas encore en jeu physique (juste menu “jeu”)
            self.in_game      = False
            # 1) Résolution du nom du jeu
            from urllib.parse import unquote
            formatted = os.path.normpath(unquote(raw2))
            if os.path.isfile(formatted):
                game = os.path.splitext(os.path.basename(formatted))[0]
            elif os.path.isdir(formatted):
                game = os.path.basename(formatted)
            else:
                game = os.path.splitext(os.path.basename(raw2))[0]

            if game != self.current_game :
                logger.info(f"Branch: game-selected for '{system}'")
                logger.info(f"  Resolved game name: '{game}'")

                # 2) On est sur un NOUVEAU jeu : on réinitialise
                self.current_game     = game
                self.current_game_idx = 0
                self.game_layouts     = []

                # 3) Log émulateur système + override éventuel
                emu_sys, core_sys = get_system_emulator(system)
                if emu_sys or core_sys:
                    logger.info(f"    Système '{system}' → emulator='{emu_sys}', core='{core_sys}'")
                else:
                    logger.info(f"    Aucun émulateur système défini pour '{system}'")

                emu_game, core_game = get_game_emulator(system, game)
                if emu_game or core_game:
                    logger.info(f"    Jeu '{game}' override → emulator='{emu_game}', core='{core_game}'")
                else:
                    logger.info(f"    Pas d'override dans gamelist.xml pour '{game}' (fallback système)")

                # 4) Charger les layouts “jeu” via XML
                game_xml_path = os.path.join(SYSTEMS_DIR, system, f"{game}.xml")
                self.game_layouts = self._load_layouts_from_xml(game_xml_path)

                if self.game_layouts:
                    # 5) Si on a bien des layouts “jeu”, appliquer et sauvegarder
                    game_key = f"{system}|{game}"
                    self._apply_saved_layout(game_key, self.game_layouts, 'current_game_idx')
                else:
                    # 6) Sinon : retomber sur le layout système courant
                    logger.info("    No game-specific layout: reapplying system default")
                    self._send_current_layout()

                self.lip_events = []
                return

        # —————— 3) game-start → enable listening and load .lip macros
        if ev == 'game-start':
            logger.info("→ Branch: game-start")
            self.in_game = True
            # always reset previous lip events on new start
            self.lip_events = []
            logger.debug("Cleared lip_events before loading new .lip")
            # resolve game name as above
            from urllib.parse import unquote
            formatted = os.path.normpath(unquote(raw2))
            if os.path.isfile(formatted):
                game = os.path.splitext(os.path.basename(formatted))[0]
            elif os.path.isdir(formatted):
                game = os.path.basename(formatted)
            else:
                game = os.path.splitext(os.path.basename(raw2))[0]
            logger.info(f"Resolved game name for .lip: '{game}'")

            if not self.listening:
                self.listening = True
                logger.info(f"▶ Game started → now listening to panel inputs for '{game}'")
            else:
                logger.debug("Already listening, refreshing .lip")

            self._load_lip(system, game)
            logger.debug(f"lip_events after load: {self.lip_events}")
            return

        # —————— 4) implicit game-end on new selection
        if self.listening and ev == 'game-selected':
            self.listening = False
            self.in_game = False
            logger.info("■ Implicit game-end — stopped listening")
            return

        # —————— 5) explicit game-end or exit
        if ev in ('game-end', 'exit') and self.listening:
            self.listening = False
            self.in_game = False
            logger.info("■ Game ended — stopped listening")
            return



    def _send_init_colors(self, system):
        cfg = _read_panel_cfg()

        phys_to_label = {}
        total = cfg.getint('Panel','buttons_count',fallback=0)
        for i in range(1, total+1):
            key = f'panel_button_{i}'
            if cfg.has_option('Panel', key):
                phys_to_label[cfg.get('Panel', key).rstrip(';')] = f"B{i}"
        for opt,label in [('panel_button_select','COIN'),
                          ('panel_button_start','START'),
                          ('panel_button_joy','JOY')]:
            if cfg.has_option('Panel', opt):
                phys_to_label[cfg.get('Panel', opt).rstrip(';')] = label

        players  = cfg.getint('Panel','players_count',fallback=1)
        panels   = '|'.join(str(i) for i in range(1, players+1))
        btn_cnt  = cfg.getint('Panel','Player1_buttons_count',
                              fallback=cfg.getint('Panel','buttons_count',fallback=0))

        btns = load_layout_buttons(system, btn_cnt, phys_to_label)
        if not btns:
            logger.warning(f"No layout for {system} with {btn_cnt} buttons")
            return

        mapping = ';'.join(f"{lbl}:{clr}" for lbl,clr in btns)
        cmd = f"SetPanelColors={panels},{mapping},default=yes\n"
        try:
            self.ser.write(cmd.encode('utf-8'))
            logger.info(f"➡ Sent: {cmd.strip()}")
        except Exception as e:
            logger.error(f"Error sending command: {e}")

    def _load_lip(self, system, game):
        # → 1) toujours repartir d’une liste vide
        self.lip_events = []

        # 2) normaliser le nom « propre » du système
        if os.path.sep in system or (':' in system and system.count(os.path.sep) > 0):
            system_name = os.path.basename(os.path.dirname(system))
        else:
            system_name = system

        # 3) chercher en premier un .lip jeu‐spécifique
        lip_path = os.path.join(SYSTEMS_DIR, system_name, f"{game}.lip")
        logger.info(f"Looking for .lip at: {lip_path}")
        if not os.path.exists(lip_path):
            # 3a) fallback sur <system>.lip
            fallback = os.path.join(SYSTEMS_DIR, f"{system_name}.lip")
            if os.path.exists(fallback):
                logger.info(f"Game .lip not found, loading system default: {fallback}")
                lip_path = fallback
            else:
                logger.info(f"No .lip file for game or system (tried {lip_path} and {fallback})")
                return

        # 4) parser le .lip et vérifier le type (N-Button)
        try:
            lip_tree    = ET.parse(lip_path)
            lip_root    = lip_tree.getroot()
            evroot      = lip_root.find('events')
            if evroot is None:
                logger.warning("No <events> in .lip")
                return

            # ** Nouveau : filtrage sur le name du layout **
            lip_name = evroot.get('name')  # ex. "Arcade Shark"
            current_layout = self.system_layouts[self.current_layout_idx]['name']
            if lip_name and lip_name != current_layout:
                logger.info(
                    f"Skipping .lip events: .lip is for layout '{lip_name}' "
                    f"but current layout is '{current_layout}'"
                )
                return

            lip_type    = evroot.get('type','')           # ex: "8-Button"
            lip_btn_cnt = int(lip_type.split('-',1)[0])
        except Exception as e:
            logger.error(f"Error parsing .lip header: {e}")
            return

        # 5) lire le panelButtons configuré dans config.ini
        cfg = _read_panel_cfg()
        panel_btn_cnt = cfg.getint(
            'Panel',
            'Player1_buttons_count',
            fallback=cfg.getint('Panel','buttons_count',fallback=0)
        )

        # 6) si ça ne correspond pas, on skippe
        if lip_btn_cnt != panel_btn_cnt:
            logger.info(
                f"Skipping .lip events: .lip is for {lip_btn_cnt}-Button "
                f"but current panel has {panel_btn_cnt} buttons"
            )
            return

        # 7) charger le layout correspondant dans systems/<system_name>.xml
        xml_path = os.path.join(SYSTEMS_DIR, f"{system_name}.xml")
        try:
            layout = ET.parse(xml_path).find(f".//layout[@panelButtons='{lip_btn_cnt}']")
            if layout is None:
                logger.warning(f"No layout[@panelButtons={lip_btn_cnt}] in {system_name}.xml")
                return
        except Exception as e:
            logger.error(f"Error loading system XML: {e}")
            return

        # 8) construire label→physical
        label_to_phys = {'JOY': None}
        for btn in layout.findall('button'):
            phys  = int(btn.get('physical'))
            idn   = btn.get('id','').upper()
            label = idn if idn in ('START','COIN','JOY') else f"B{phys}"
            label_to_phys[label] = phys

        # 9) extraire les <event> et peupler self.lip_events
        count = 0
        for ev in evroot.findall('event'):
            b   = ev.get('button','').upper()
            trg = ev.get('trigger','press').lower()
            mac = ev.find('macro').get('type').lower()
            arg = None
            if mac == 'set_panel_colors':
                arg = ev.find('.//colors').text.strip()
            elif mac == 'restore_panel':
                arg = ev.find('.//panel').text.strip()

            phys = label_to_phys.get(b)
            if phys is None:
                logger.info(f"Skipping .lip event for unknown label '{b}'")
                continue

            entry = {
                'id':      phys - 1,
                'trigger': trg,
                'macro':   mac,
                'arg':     arg
            }
            self.lip_events.append(entry)
            logger.info(f"Loaded .lip event: {entry}")
            count += 1

        logger.info(f"Total .lip events loaded: {count}")


def joystick_listener(handler):
    import time
    import pygame
    from pygame.locals import JOYBUTTONDOWN, JOYBUTTONUP, JOYHATMOTION, JOYAXISMOTION

    # Initialisation
    pygame.init()
    pygame.joystick.init()
    logger.info("▶ joystick_listener thread started")

    # Détection des joysticks
    joysticks = []
    count = pygame.joystick.get_count()
    logger.info(f"Detected {count} joystick(s)")
    for i in range(count):
        js = pygame.joystick.Joystick(i)
        js.init()
        joysticks.append(js)
        logger.info(f"  • Initialized Joystick #{i}: {js.get_name()} (instance_id={js.get_instance_id()})")

    # États des boutons
    states = {
        js.get_instance_id(): {btn: False for btn in range(js.get_numbuttons())}
        for js in joysticks
    }
    # États des hats
    hat_states = {js.get_instance_id(): (0, 0) for js in joysticks}

    HOTKEY_ID = 8      # id du bouton "Start" dans es_input.cfg
    AXIS_THRESHOLD = 0.5

    while True:
        for ev in pygame.event.get():
            # Debug complet
            logger.debug(f"← pygame event: {ev}")

            # — Boutons press/release —
            if ev.type in (JOYBUTTONDOWN, JOYBUTTONUP):
                iid     = ev.instance_id
                pressed = (ev.type == JOYBUTTONDOWN)
                prev    = states[iid].get(ev.button, None)
                states[iid][ev.button] = pressed

                logger.info(f"  • Panel {handler.panel_id} Button {ev.button+1} "
                            f"{'pressed' if pressed else 'released'}")

                # Si en game-start, traiter .lip
                if handler.listening and prev is not None and prev != pressed:
                    for le in handler.lip_events:
                        logger.debug(f"    → Checking .lip event {le}")
                        if le['id'] == ev.button and le['trigger'] == ('press' if pressed else 'release'):
                            if le['macro'] == 'set_panel_colors':
                                mapping = le['arg'].replace('CURRENT', str(handler.panel_id))
                                cmd     = f"SetPanelColors={mapping}"
                            else:
                                panel_arg = le['arg'].replace('CURRENT', str(handler.panel_id))
                                cmd       = f"RestorePanel={panel_arg}"
                            logger.info(f"    ➡ Executing macro: {cmd}")
                            handler.ser.write((cmd + '\n').encode('utf-8'))

            # — Hat (D-pad en hat) —
            elif ev.type == JOYHATMOTION:
                iid = ev.instance_id
                x, y = ev.value
                logger.info(f"UU  • Panel {handler.panel_id} Hat moved → {ev.value}")
                # Hotkey + left/right hors game-start
                if not handler.in_game and states[iid].get(HOTKEY_ID, False):
                    if not handler.system_layouts:
                        logging.warning("Aucun layout défini : switch ignoré")
                        return  # ou return, ou break selon la structure de ta boucle

                    if x == -1:
                        handler.current_layout_idx = (handler.current_layout_idx - 1) % len(handler.system_layouts)
                        logger.info("    ↶ Hotkey+Hat-Left → previous layout")
                        handler._send_current_layout()
                        handler._save_layout_idx(handler.last_system or '', handler.current_layout_idx)

                    elif x == 1:
                        handler.current_layout_idx = (handler.current_layout_idx + 1) % len(handler.system_layouts)
                        logger.info("    ↷ Hotkey+Hat-Right → next layout")
                        handler._send_current_layout()
                        handler._save_layout_idx(handler.last_system or '', handler.current_layout_idx)


            # — Axis (D-pad en axis 0/1) —
            elif ev.type == JOYAXISMOTION:
                iid  = ev.instance_id
                axis = ev.axis
                val  = ev.value
                # On ne gère que l'axe 0 (gauche/droite)
                if axis == 0 and abs(val) > AXIS_THRESHOLD:
                    direction = 'Left' if val < 0 else 'Right'
                    logger.info(f"OO  • Panel {handler.panel_id} Axis0 moved → {direction} ({val:.2f})")
                    logger.info(f"Joystick listening={handler.listening}, hotkey_pressed={states[iid].get(HOTKEY_ID, False)}")

                    # ── On veut uniquement gérer Hotkey+Left/Right hors game-start
                    if not handler.in_game and states[iid].get(HOTKEY_ID, False):

                        # — Si on est dans un menu “jeu” (game-selected) avec des layouts “jeu” chargés :
                        if handler.current_game is not None and handler.game_layouts:
                            # 1) Calculer le nouvel index “jeu”
                            if direction == 'Left':
                                handler.current_game_idx = (handler.current_game_idx - 1) % len(handler.game_layouts)
                                logger.info("    ↶ Hotkey+Axis-Left → previous game-layout")
                            else:
                                handler.current_game_idx = (handler.current_game_idx + 1) % len(handler.game_layouts)
                                logger.info("    ↷ Hotkey+Axis-Right → next game-layout")

                            # 2) Envoyer le SetPanelColors pour ce layout “jeu”
                            cfg = _read_panel_cfg()
                            panels = '|'.join(str(i) for i in range(
                                1,
                                cfg.getint('Panel', 'players_count', fallback=1) + 1
                            ))
                            entry = handler.game_layouts[handler.current_game_idx]
                            mapping = ';'.join(f"{lbl}:{clr}" for lbl, clr in entry['buttons'])
                            cmd = f"SetPanelColors={panels},{mapping},default=yes\n"
                            try:
                                handler.ser.write(cmd.encode('utf-8'))
                                logger.info(f"    ➡ Sent (game layout '{entry['name']}')")
                            except Exception as e:
                                logger.error(f"    Error sending game layout: {e}")

                            # 3) Sauvegarder le nouvel index “jeu” dans config.ini
                            game_key = f"{handler.last_system}|{handler.current_game}"
                            prev_sys = handler.system_layouts
                            handler.system_layouts = handler.game_layouts
                            handler._save_layout_idx(game_key, handler.current_game_idx)
                            handler.system_layouts = prev_sys

                            # 4) Afficher le popup et sortir
                            show_popup_tk(entry['name'])
                            time.sleep(0.01)
                            continue

                        # — Sinon, on retombe sur la logique “système” (pas de jeu actif) —
                        if not handler.system_layouts:
                            logging.warning("Aucun layout défini : switch ignoré")
                            continue

                        if direction == 'Left':
                            handler.current_layout_idx = (
                                handler.current_layout_idx - 1
                            ) % len(handler.system_layouts)
                            handler._save_layout_idx(
                                handler.last_system or '',
                                handler.current_layout_idx
                            )
                            logger.info("    ↶ Hotkey+Axis-Left → previous system-layout")
                        else:
                            handler.current_layout_idx = (
                                handler.current_layout_idx + 1
                            ) % len(handler.system_layouts)
                            handler._save_layout_idx(
                                handler.last_system or '',
                                handler.current_layout_idx
                            )
                            logger.info("    ↷ Hotkey+Axis-Right → next system-layout")

                        handler._send_current_layout()
                        name = handler.system_layouts[handler.current_layout_idx]['name']
                        show_popup_tk(name)

                        time.sleep(0.01)
                        continue

def main():
    cfg = _read_panel_cfg()

    pico = find_pico()
    if not pico:
        sys.exit(1)
    ser = serial.Serial(pico, BAUDRATE, timeout=1)
    time.sleep(1)
    logger.info(f"Connected to Pico on {pico} @ {BAUDRATE}")

    panel_id = 1
    btn_cnt  = cfg.getint('Panel','Player1_buttons_count',
                         fallback=cfg.getint('Panel','buttons_count',fallback=0))
    coin_ch  = cfg.get('Panel','panel_button_select').rstrip(';')
    start_ch = cfg.get('Panel','panel_button_start').rstrip(';')
    joy_ch   = cfg.get('Panel','panel_button_joy').rstrip(';')

    init_cmd = f"INIT=panel={panel_id},count={btn_cnt},select={coin_ch},start={start_ch},joy={joy_ch}\n"
    try:
        ser.write(init_cmd.encode('utf-8'))
        logger.info(f"➡ Sent INIT: {init_cmd.strip()}")
    except Exception as e:
        logger.error(f"Failed to send INIT: {e}")

    logger.info(f"Config: players={cfg.getint('Panel','players_count',fallback=1)}, Player1_buttons_count={btn_cnt}")

    observer    = Observer()
    led_handler = LedEventHandler(ser, panel_id)
    observer.schedule(led_handler, os.path.dirname(ES_EVENT_FILE), recursive=False)
    observer.start()

    t = threading.Thread(target=joystick_listener, args=(led_handler,), daemon=True)
    t.start()

    logger.info("Led Panel Color Manager running…")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()
    ser.close()

if __name__ == '__main__':
    main()

