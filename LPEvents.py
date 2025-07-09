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
from watchdog.events import PatternMatchingEventHandler
import pygame

import threading
import tkinter as tk
import tkinter.font as tkfont
import ctypes
from PIL import Image, ImageDraw, ImageFont
from typing import Dict, Tuple, Optional, List

# Logging
logging.basicConfig(level=logging.WARNING, format='[%(asctime)s] %(levelname)s: %(message)s')
logger = logging.getLogger(__name__)
logging.getLogger('watchdog').setLevel(logging.DEBUG)

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
CONFIG_CACHE     = None

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

# —————————————————————————————————————————————————————————
# Parsers XML globaux pour es_settings.cfg et es_systems.cfg
# —————————————————————————————————————————————————————————
retrobat_root = os.path.dirname(os.path.dirname(BASE_DIR))
es_home      = os.path.join(retrobat_root, "emulationstation", ".emulationstation")

_SETTINGS_CFG = os.path.join(es_home, "es_settings.cfg")
_SYSTEMS_CFG  = os.path.join(es_home, "es_systems.cfg")

# Cache des arbres XML
try:
    _settings_tree = ET.parse(_SETTINGS_CFG)
    _settings_root = _settings_tree.getroot()
    logger.info(f"Loaded settings XML from {_SETTINGS_CFG}")
except Exception:
    _settings_root = None
    logger.warning(f"Could not parse {_SETTINGS_CFG}, will fallback to systems only")

try:
    _systems_tree = ET.parse(_SYSTEMS_CFG)
    _systems_root = _systems_tree.getroot()
    logger.info(f"Loaded systems XML from {_SYSTEMS_CFG}")
except Exception:
    _systems_root = None
    logger.error(f"Could not parse {_SYSTEMS_CFG}, emulator lookup disabled")

# —————————————————————————————————————————————————————————
# Cache des fichiers .info de RetroArch
# —————————————————————————————————————————————————————————
info_dir = os.path.join(retrobat_root, "emulators", "retroarch", "info")
_INFO_CACHE = {}
if os.path.isdir(info_dir):
    for fname in os.listdir(info_dir):
        if not fname.lower().endswith(".info"):
            continue
        # Déterminer le nom de core (exclut '_libretro.info' si présent)
        if fname.lower().endswith("_libretro.info"):
            core_key = fname[:-len("_libretro.info")]
        else:
            core_key = fname[:-len(".info")]
        path = os.path.join(info_dir, fname)
        try:
            with open(path, encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line = line.strip()
                    if line.lower().startswith("corename"):
                        parts = line.split("=", 1)[1].strip()
                        # dépouille les guillemets
                        if parts.startswith('"') and parts.endswith('"'):
                            parts = parts[1:-1]
                        _INFO_CACHE[core_key.lower()] = parts
                        break
        except Exception as e:
            logger.warning(f"Impossible de parser {fname}: {e}")
else:
    logger.warning(f"Dossier info introuvable: {info_dir}")

# —————————————————————————————————————————————————————————
# Cache des gamelists.xml
# —————————————————————————————————————————————————————————
_GAMELIST_CACHE = {}
roms_root = os.path.join(retrobat_root, "roms")
if os.path.isdir(roms_root):
    for system in os.listdir(roms_root):
        gamelist_path = os.path.join(roms_root, system, "gamelist.xml")
        if os.path.isfile(gamelist_path):
            try:
                tree = ET.parse(gamelist_path)
                root = tree.getroot()
                _GAMELIST_CACHE[system.lower()] = root
                logger.info(f"Loaded gamelist for system '{system}'")
            except Exception as e:
                logger.warning(f"Failed to parse gamelist.xml for '{system}': {e}")
else:
    logger.warning(f"Roms directory not found: {roms_root}")
_GAME_INDEX = {}  # clé = (system.lower(), game_name) → (emu, core)
for sys, root in _GAMELIST_CACHE.items():
    for game in root.findall('game'):
        name = game.findtext('name','').strip()
        path = game.findtext('path','').strip()
        base = os.path.splitext(os.path.basename(path))[0]
        emu = game.findtext('emulator','').strip()
        cor = game.findtext('core','').strip()
        for key in (name, base):
            _GAME_INDEX[(sys, key)] = (emu, cor)

# —————————————————————————————————————————————————————————
# 1. Préchargement de tous les XML *système*
# —————————————————————————————————————————————————————————
_SYSTEM_CFG_CACHE: Dict[str, ET.Element] = {}
systems_dir = os.path.join(retrobat_root, "emulationstation", "systems")
if os.path.isdir(systems_dir):
    for fname in os.listdir(systems_dir):
        if not fname.lower().endswith(".xml"):
            continue
        key = os.path.splitext(fname)[0].lower()
        path = os.path.join(systems_dir, fname)
        try:
            _SYSTEM_CFG_CACHE[key] = ET.parse(path).getroot()
            logger.info(f"Cached system XML for '{key}'")
        except Exception as e:
            logger.warning(f"Cannot parse system XML '{fname}': {e}")
else:
    logger.warning(f"systems_dir introuvable: {systems_dir}")

# —————————————————————————————————————————————————————————
# 2. Préchargement de tous les XML *jeu*
# —————————————————————————————————————————————————————————
_GAME_CFG_CACHE: Dict[Tuple[str,str], ET.Element] = {}
for system, root in _SYSTEM_CFG_CACHE.items():
    system_dir = os.path.join(systems_dir, system)
    if not os.path.isdir(system_dir):
        continue
    for fname in os.listdir(system_dir):
        if not fname.lower().endswith(".xml"):
            continue
        game = os.path.splitext(fname)[0]
        path = os.path.join(system_dir, fname)
        try:
            _GAME_CFG_CACHE[(system, game)] = ET.parse(path).getroot()
            logger.info(f"Cached game XML for '{system}/{game}'")
        except Exception as e:
            logger.warning(f"Cannot parse game XML '{system}/{fname}': {e}")

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


def escape_arg_value(s: str) -> str:
    """
    Remplace dans la chaîne s toutes les séquences d’échappement
    par leur caractère d’origine pour le batch .arg,
    en incluant désormais la séquence '|' → '!'.
    """
    # d’abord les séquences à deux caractères :
    repl2 = {
        '""': '"',   # guillemet    → double-guillemet
        '|A': '&',   # esperluette  → |A
        '|v': ',',   # virgule      → |v
        '|p': '+',   # plus         → |p
        '|%': '!',   # point d’excl.→ |%
        '||': '|',   # pipe         → ||
        '%%': '%',   # pourcent     → %%
    }
    # puis la séquence à un caractère :
    repl1 = {
        '|': '!'
    }

    result = []
    i = 0
    while i < len(s):
        # 1) on essaie la séquence de longueur 2
        if i + 1 < len(s) and s[i:i+2] in repl2:
            result.append(repl2[s[i:i+2]])
            i += 2
        # 2) sinon on regarde si le caractère seul est à remplacer
        elif s[i] in repl1:
            result.append(repl1[s[i]])
            i += 1
        # 3) sinon on le reprend tel quel
        else:
            result.append(s[i])
            i += 1

    return ''.join(result)

def get_system_emulator(system_name: str) -> (str, str):
    """
    Renvoie (emulator, core) en lisant uniquement les arbres déjà parsés.
    """
    emulator = core = ""
    # 1) Tentative dans es_settings.cfg
    if _settings_root is not None:
        for s in _settings_root.findall('string'):
            n = s.get('name',''); v = s.get('value','')
            if n == f"{system_name}.emulator":
                emulator = v
            elif n == f"{system_name}.core":
                core = v
    # 2) Si l'un manque, on complète depuis es_systems.cfg
    if (_systems_root is not None) and (not emulator or not core):
        def find_and_parse(sys_elem):
            ems = sys_elem.find('emulators')
            if ems is None: return "", ""
            # priorité au core default
            for em in ems.findall('emulator'):
                cores = em.find('cores')
                if cores is not None:
                    for co in cores.findall('core'):
                        if co.get('default','').lower() == 'true':
                            return em.get('name',''), co.text.strip()
            # fallback premier
            em = ems.find('emulator')
            if em is None: return "", ""
            co = em.find('cores/core')
            return em.get('name',''), (co.text.strip() if co is not None else "")
        # chercher le <system>
        for sys_elem in _systems_root.findall('system'):
            nm = sys_elem.find('name')
            if nm is not None and nm.text.strip().lower() == system_name.lower():
                emu2, core2 = find_and_parse(sys_elem)
                # si `<theme>` pointe vers un autre système
                if not emu2 or not core2:
                    theme = sys_elem.find('theme')
                    if theme is not None:
                        real = theme.text.strip().lower()
                        for s2 in _systems_root.findall('system'):
                            nm2 = s2.find('name')
                            if nm2 is not None and nm2.text.strip().lower() == real:
                                emu2, core2 = find_and_parse(s2)
                                break
                emulator = emulator or emu2
                core     = core     or core2
                break
    return emulator, core

def get_system_platform(system_name: str) -> str:
    """
    Lit l’arbre systèmes déjà chargé et renvoie la balise <platform>.
    """
    if _systems_root is None:
        return ""
    for sys_elem in _systems_root.findall('system'):
        nm = sys_elem.find('name')
        if nm is not None and nm.text.strip().lower() == system_name.lower():
            plat = sys_elem.find('platform')
            return plat.text.strip().lower() if plat is not None else ""
    return ""

def get_core_folder_name(core_name: str) -> str:
    """
    Renvoie la valeur 'name' extraite depuis <core_name>_libretro.info,
    à partir du cache pré-chargé.
    """
    return _INFO_CACHE.get(core_name.lower(), "")

def get_game_emulator(system_name, game_name):
    return _GAME_INDEX.get((system_name.lower(), game_name), ("",""))

def _read_panel_cfg(force_reload=False) -> configparser.ConfigParser:
    global CONFIG_CACHE
    if CONFIG_CACHE is None or force_reload:
        cfg = configparser.ConfigParser()
        try:
            with open(PANEL_CONFIG_INI, encoding='utf-8', errors='ignore') as fh:
                cfg.read_file(fh)
        except Exception:
            cfg.read(PANEL_CONFIG_INI)
        CONFIG_CACHE = cfg
    return CONFIG_CACHE

def load_layout_buttons(system: str) -> List[Dict]:
    root = _SYSTEM_CFG_CACHE.get(system.lower())
    if root is None:
        logger.warning(f"No cached XML for system '{system}'")
        return []
    try:
        root = _SYSTEM_CFG_CACHE.get(system.lower())
        if not root: return []
        for layout in root.findall('.//layout'):
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

def safe_serial_write(ser, cmd, label=""):
    """
    Vide les buffers d’entrée et de sortie, puis envoie cmd immédiatement.
    """
    try:
        # Supprime toute donnée en attente côté Pico (input)…
        #ser.reset_input_buffer()
        # …et côté OS (output), si nécessaire
        #ser.reset_output_buffer()
        logger.error(f"afe_serial_write")
    except Exception:
        # Certains drivers n’ont pas ces méthodes, on ignore
        pass

    try:
        ser.write(cmd.encode('utf-8'))
        #ser.flush()
    except Exception as e:
        logger.error(f"❌ Erreur série lors de l’envoi de '{label}': {e}")

def monitor_serial_buffer(ser):
    while True:
        time.sleep(1)
        try:
            in_buf = ser.in_waiting
            out_buf = ser.out_waiting
            logger.info(f"[Serial Buffer] IN={in_buf} | OUT={out_buf}")
            dump = ser.read(ser.in_waiting)
            logger.info(f"[SERIAL DUMP] <<< {dump}")
        except Exception as e:
            logger.warning(f"[Serial Monitor] Erreur lecture buffer : {e}")
            break

def read_serial_feedback(ser):
    while True:
        try:
            if ser.in_waiting:
                lines = ser.read(ser.in_waiting).decode(errors="ignore").splitlines()
                for line in lines:
                    logger.debug(f"[PICO REPLY] {line}")
        except Exception as e:
            logger.warning(f"[Feedback] Erreur lecture Pico : {e}")
        time.sleep(0.1)

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


class LedEventHandler(PatternMatchingEventHandler):
    def __init__(self, ser, panel_id):
        super().__init__(patterns=[ES_EVENT_FILE], ignore_directories=True)
        self.ser           = ser
        self.panel_id      = panel_id
        self.cfg = _read_panel_cfg()
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
        cfg = self.cfg
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
        #global CONFIG_CACHE
        cfg = self.cfg
        if not cfg.has_section('PanelDefaults'):
            cfg.add_section('PanelDefaults')
            logger.debug("Created PanelDefaults section.")
        name = ''
        #if 0 <= idx < len(self.system_layouts):
        #    name = self.system_layouts[idx].get('name', '')
        if 0 <= idx < len(self.system_layouts):
            # si un name existe, on le prend, sinon on retombe sur le type (ex. "6-Button")
            name = (
                self.system_layouts[idx].get('name')
                or self.system_layouts[idx].get('type', '')
            )
        logger.debug(f"Saving idx {idx} (name '{name}') for system '{system}'")
        cfg.set('PanelDefaults', system, name)
        tmp = PANEL_CONFIG_INI + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as fh:
            cfg.write(fh)
        os.replace(tmp, PANEL_CONFIG_INI)
        #CONFIG_CACHE = None
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
            for layout in root.findall(".//layout"):
                # lire la valeur panelButtons (0 si absent)
                try:
                    pcount = int(layout.get('panelButtons', '0'))
                except ValueError:
                    continue
                # n’inclure que si panelButtons <= btn_cnt
                if pcount > btn_cnt:
                    continue

                name = layout.get('name') or layout.get('type')
                mapping = []

                # Joystick
                joy = layout.find('joystick')
                if joy is not None:
                    c = joy.get('color', DEFAULT_COLOR).upper()
                    mapping.append(("JOY", "OFF" if c == "BLACK" else c))

                # Boutons
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


    def _apply_saved_layout(self, key: str, layouts, idx_attr: str, save: bool = True):
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

        prev = self.system_layouts
        self.system_layouts = layouts

        cfg = self.cfg
        # ── FALLBACK SYSTEME : pas d'entrée PanelDefaults → on choisit selon le btn_count du panel ──
        if not cfg.has_section('PanelDefaults') or not cfg.has_option('PanelDefaults', key):
            panel_id = self.panel_id
            btn_cnt = cfg.getint(
                'Panel',
                f'player{panel_id}_buttons_count',
                fallback=cfg.getint('Panel', 'buttons_count', fallback=0)
            )
            # trouve l'index du layout "N-Button"
            saved_idx = next(
                (i for i, entry in enumerate(layouts)
                 if entry.get('name') == f"{btn_cnt}-Button"),
                0
            )
        else:
            # sinon, lecture normale de l'idx sauvegardé
            saved_idx = self._get_saved_layout_idx(key)

        # ➌ sécurité bornes
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
            safe_serial_write(self.ser, cmd, label=f"{entry['name']} layout")
            logger.info(f"    ➡ Sent ({key} layout) [{saved_idx}] '{entry['name']}'")
        except Exception as e:
            logger.error(f"    Erreur envoi layout pour '{key}': {e}")

        # 4b) Sauvegarde dans config.ini (PanelDefaults) uniquement si demandé
        if save:
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
        cfg = self.cfg
        panels = '|'.join(
            str(i)
            for i in range(1, cfg.getint('Panel', 'players_count', fallback=1) + 1)
        )
        mapping = ';'.join(f"{lbl}:{clr}" for lbl, clr in entry['buttons'])
        cmd = f"SetPanelColors={panels},{mapping},default=yes\n"

        try:
            safe_serial_write(self.ser, cmd, label=f"{entry['name']} layout")
            logger.info(f"➡ Switched to layout [{self.current_layout_idx}] '{entry['name']}'")
        except Exception as e:
            logger.error(f"Error sending layout: {e}")

    def on_modified(self, event):
        now = time.time()
        logger.warning(f"START [OBSERVER] on_modified reçu à {now:.3f}")
        try:
            mtime = os.path.getmtime(ES_EVENT_FILE)   # en secondes float
        except Exception:
            mtime = None

        # 2) Capturer l'instant où on entre dans on_modified
        now = time.time()

        if mtime is not None:
            delay = (now - mtime) * 1000
            logger.warning(f"[WATCHDOG] file-modified → on_modified delay = {delay:.1f} ms")
        else:
            logger.warning(f"[WATCHDOG] on_modified without valid mtime")
        # Parse EmulationStation event
        ev, system, raw2 = parse_es_event(ES_EVENT_FILE)
        system = escape_arg_value(system)
        logger.debug(f"on_modified: ev='{ev}', system='{system}' (in_game={self.in_game})")
        # récupérer le nom exact du dossier remaps pour le core système
        emu_sys, core_sys = get_system_emulator(system)
        remap_folder_sys = get_core_folder_name(core_sys)
        if remap_folder_sys == "Caprice32":
            remap_folder_sys = "cap32"
        if remap_folder_sys == "Dolphin":
            remap_folder_sys = "dolphin-emu"

        # —————— 1) system-selected: switch system, exit game mode
        if ev == 'system-selected' and (system != self.last_system or self.in_game):
            logger.info(f"Branch: system-selected for '{system}' (last was '{self.last_system}')")

            # On quitte le mode “jeu”
            self.current_game = None
            self.in_game      = False



            if emu_sys or core_sys:
                logger.info(
                    f"  Système '{system}' → emulator={emu_sys}, "
                    f"core={core_sys}, remaps_folder='{remap_folder_sys}'"
                )
            else:
                logger.info(f"  Aucun émulateur par défaut défini pour '{system}'")

            # Recharge et applique le layout système
            plat = get_system_platform(system) or system
            self.last_system = plat

            # ➋ Recharge et applique le layout système
            self._load_system_layouts(plat)
            self._apply_saved_layout(plat, self.system_layouts, 'current_layout_idx', save=False)

            self.lip_events  = []
            now = time.time()
            logger.warning(f"SYSTEM SELECTED [OBSERVER] on_modified reçu à {now:.3f}")
            return

        # —————— 2) game-selected ——————
        if ev == 'game-selected' or self.in_game :
            logger.info(f"game-selected for system : '{system}'")
            raw2 = escape_arg_value(raw2)
            logger.info(f"  raw2 '{raw2}'")
            plat = get_system_platform(system) or system
            self.last_system = plat
            logger.info(f"game-selected for system : '{system}' - plateform '{plat}'")
            if 'arcade' in plat:
                logger.info(f"  Plateforme '{plat}' marque arcade → pas de remap généré")

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

                # 3) Log émulateur système + remaps folder
                if emu_sys or core_sys:
                    logger.info(f"    Système '{system}' → emulator={emu_sys}, core={core_sys}, remaps_folder='{remap_folder_sys}'")
                else:
                    logger.info(f"    Aucun émulateur système défini pour '{system}'")

                # 4) Override éventuel pour le jeu ; sinon fallback sur système
                emu_game, core_game = get_game_emulator(system, game)
                if not emu_game:
                    emu_game = emu_sys
                if not core_game:
                    core_game = core_sys

                remap_folder_game = get_core_folder_name(core_game)
                if remap_folder_game == "Caprice32":
                    remap_folder_game = "cap32"
                if remap_folder_game == "Dolphin":
                    remap_folder_game = "dolphin-emu"
                logger.info(
                    f"    Jeu '{game}' → emulator={emu_game}, "
                    f"core={core_game}, remaps_folder='{remap_folder_game}'"
                )

                # 4) Charger les layouts “jeu” via XML
                game_xml_path = os.path.join(SYSTEMS_DIR, system, f"{game}.xml")
                self.game_layouts = self._load_layouts_from_xml(game_xml_path)
                # —————————————————————————————————————————————————————
                # 7) GÉNÉRATION DU .rmp APRÈS AVOIR APPLIQUÉ LE LAYOUT COURANT
                # —————————————————————————————————————————————————————
                if remap_folder_game:
                    # chemin vers remaps/<core_folder>
                    remaps_root = os.path.join(retrobat_root,
                                               "emulators","retroarch","config","remaps")
                    target_dir = os.path.join(remaps_root, remap_folder_game)
                    os.makedirs(target_dir, exist_ok=True)

                    # nom du fichier de sortie
                    target_rmp = os.path.join(target_dir, f"{game}.rmp")

                    # if we have neither game-specific nor system layouts, skip remap
                    if not self.game_layouts and not self.system_layouts:
                        logger.warning(
                            f"No layouts for '{system}/{game}' → skipping remap generation"
                        )
                        now = time.time()
                        logger.warning(f"GAME SELECTED [OBSERVER] on_modified reçu à {now:.3f}")
                        return
                    # Le layout courant (défini par _apply_saved_layout ou fallback)
                    layout_name = (
                        self.game_layouts[self.current_game_idx]['name']
                        if self.game_layouts
                        else self.system_layouts[self.current_layout_idx]['name']
                    )

                    # choisir le template .rmp
                    plugin_dir  = SYSTEMS_DIR
                    plugin_rmp1 = os.path.join(plugin_dir, f"{system}-{layout_name}.rmp")
                    plugin_rmp0 = os.path.join(plugin_dir, f"{system}.rmp")
                    src_rmp = plugin_rmp1 if os.path.isfile(plugin_rmp1) else \
                              (plugin_rmp0 if os.path.isfile(plugin_rmp0) else None)

                    # ——————————————————————————————————————————————————————————
                    # Génération du .rmp :
                    #   1) on cherche un template (system-layout ou system-game-layout)
                    #   2) si trouvé, on copie comme avant
                    #   3) sinon, on génère un .rmp minimal à partir du XML (retropad_id)
                    # ——————————————————————————————————————————————————————————

                    if src_rmp:
                        # 2) Template trouvé : copie + remplacement de <p>
                        logger.info(f"  Génération remap depuis '{os.path.basename(src_rmp)}' → '{target_rmp}'")
                        cfg     = _read_panel_cfg()
                        players = cfg.getint('Panel', 'players_count', fallback=1)
                        with open(src_rmp, 'r', encoding='utf-8') as src, \
                             open(target_rmp, 'w', encoding='utf-8') as dst:
                            for line in src:
                                if '<p>' in line:
                                    for p in range(1, players + 1):
                                        dst.write(line.replace('<p>', str(p)))
                                else:
                                    dst.write(line)

                    else:
                        # 3) Pas de template → fallback : génération dynamique depuis XML
                        cfg = _read_panel_cfg()

                        # a) Reconstruire phys_to_label (B1, B2, START, COIN, JOY)
                        phys_to_label = {}
                        total = cfg.getint('Panel', 'buttons_count', fallback=0)
                        for i in range(1, total + 1):
                            opt = f'panel_button_{i}'
                            if cfg.has_option('Panel', opt):
                                phys = cfg.get('Panel', opt).rstrip(';')
                                phys_to_label[phys] = f"B{i}"
                        for opt, lab in [
                            ('panel_button_select','COIN'),
                            ('panel_button_start','START'),
                            ('panel_button_joy','JOY')
                        ]:
                            if cfg.has_option('Panel', opt):
                                phys = cfg.get('Panel', opt).rstrip(';')
                                phys_to_label[phys] = lab

                        panel_id = self.panel_id

                        # b) Choix du XML : jeu d’abord, sinon système
                        xml_game     = os.path.join(SYSTEMS_DIR, system, f"{game}.xml")
                        xml_fallback = os.path.join(SYSTEMS_DIR, f"{system}.xml")
                        xml_to_parse = xml_game if os.path.isfile(xml_game) else xml_fallback
                        logger.info(f"\n xml_game = {xml_game}\n xml_fallback = {xml_fallback}\n xml_to_parse = {xml_to_parse}\n")

                        if not os.path.isfile(xml_game) and not os.path.isfile(xml_fallback):
                            logger.warning(f"  Pas de XML jeu ni système trouvé pour '{system}/{game}', skip remap")
                            now = time.time()
                            logger.warning(f"GAME SELECTED [OBSERVER] on_modified reçu à {now:.3f}")
                            return

                        try:
                            #tree = ET.parse(xml_to_parse)
                            #root = tree.getroot()

                            tree_sys = ET.parse(xml_to_parse)
                            root     = tree_sys.getroot()
                            if os.path.isfile(xml_to_parse):
                                tree_game = ET.parse(xml_to_parse)
                                root_game = tree_game.getroot()
                                for layout in root_game.findall('.//layout'):
                                    root.append(layout)

                            # Nombre de joueurs définis dans config.ini
                            players = cfg.getint('Panel', 'players_count', fallback=1)
                            remap_lines = []
                            # On ne veut qu’un seul keyboard_mode=1
                            keyboard_mode_used = False
                            # Boucle pour chaque joueur
                            for panel_id in range(1, players + 1):
                                # 0) vérification d’un fallback layout “system|game” dans config.ini
                                logger.info(f"#### test")
                                cfg = _read_panel_cfg()
                                # 1) Nombre de boutons max pour ce joueur
                                btn_cfg = cfg.getint(
                                    'Panel', f'player{panel_id}_buttons_count',
                                    fallback=cfg.getint('Panel', 'buttons_count', fallback=0)
                                )
                                layout_name = f"{btn_cfg}-Button"
                                game_key = f"{system}|{game}"
                                if cfg.has_section('PanelDefaults') and cfg.has_option('PanelDefaults', game_key):
                                    saved_game_layout = cfg.get('PanelDefaults', game_key)
                                    if saved_game_layout:
                                        logger.info(f"  Utilisation du layout sauvegardé pour '{game_key}' → '{saved_game_layout}'")
                                        layout_name = saved_game_layout
                                    else:
                                        logger.debug(f"  Clé '{game_key}' vide – on garde '{layout_name}'")
                                else:
                                    logger.debug(f"  Pas de layout jeu-spécifique pour '{game_key}'")

                                logger.info(f"#### btn_cfg {btn_cfg}")
                                # 2) Choix du <layout> pour ce player
                                #    a) tentative par layout_name
                                layout_elem = root.find(f".//layout[@name='{layout_name}']") or \
                                              root.find(f".//layout[@type='{layout_name}']")
                                #    b) si trouvé mais inadapté (panelButtons > btn_cfg), ignorer et passe en fallback
                                if layout_elem is not None:
                                    try:
                                        pb = int(layout_elem.get('panelButtons', '0'))
                                        logger.info(f"#### pb {pb}")
                                    except ValueError:
                                        pb = 0
                                    if pb > btn_cfg:
                                        layout_elem = None

                                #    c) fallback : parmi les layouts <= btn_cfg, prendre celui avec panelButtons max
                                if layout_elem is None:
                                    candidates = []
                                    for le in root.findall('.//layout'):
                                        try:
                                            pb = int(le.get('panelButtons', '0'))
                                            logger.info(f"####>> pb {pb}")
                                        except ValueError:
                                            continue
                                        if pb <= btn_cfg:
                                            candidates.append((pb, le))
                                    if candidates:
                                        layout_elem = max(candidates, key=lambda x: x[0])[1]

                                if layout_elem is None:
                                    raise ValueError(f"Aucun <layout> matching '{layout_name}' pour player{panel_id}")
                                logger.info(f"#### layout_name {layout_name} panel_id {panel_id}")

                                # Nombre de boutons défini dans ce layout (panelButtons)
                                try:
                                    xml_max = int(layout_elem.get('panelButtons', '0'))
                                except ValueError:
                                    xml_max = 0

                                logger.info(f"#### xml_max {xml_max}")
                                # 3) Génération des lignes de config
                                device    = layout_elem.get('retropad_device', '1')
                                dpad_mode = layout_elem.get('retropad_analog_dpad_mode', '0')
                                raw_keyboard_mode = layout_elem.get('retropad_keyboard_mode', '0')
                                remap_lines.append(f'input_libretro_device_p{panel_id} = "{device}"\n')
                                remap_lines.append(f'input_player{panel_id}_analog_dpad_mode = "{dpad_mode}"\n')

                                if raw_keyboard_mode == "1" and not keyboard_mode_used:
                                    btn_type = "key"
                                    keyboard_mode_used = True
                                else:
                                    btn_type = "btn"

                                # Boucle des boutons: on utilise l'attribut 'id' pour inclure START/COIN
                                for btn in layout_elem.findall('button'):
                                    btn_id = btn.get('id', '').upper()
                                    phys_str = btn.get('physical', '')
                                    # calcul phys for numeric ids
                                    try:
                                        phys = int(phys_str)
                                    except (ValueError, TypeError):
                                        phys = 0
                                    # inclure START et COIN toujours
                                    if btn_id not in ('START', 'COIN'):
                                        if (phys > btn_cfg) or (xml_max and phys > xml_max):
                                            continue
                                    rid_str = btn.get('retropad_id') or ''
                                    if not rid_str:
                                        continue

                                    # déterminer le label selon 'id'
                                    if btn_id == 'START':
                                        label = 'start'
                                    elif btn_id == 'COIN':
                                        label = 'select'
                                    else:
                                        controller = btn.get('controller', '').lower()
                                        if controller == 'pageup':
                                            label = 'l'
                                        elif controller == 'pagedown':
                                            label = 'r'
                                        elif controller == 'select':
                                            label = 'select'
                                        elif controller == 'start':
                                            label = 'start'
                                        elif controller:
                                            label = controller
                                        else:
                                            label = phys_to_label.get(phys_str, f"B{phys_str}")

                                    logger.info(f"input_player{panel_id}_{btn_type}_{label} = '{rid_str}'")
                                    remap_lines.append(f'input_player{panel_id}_{btn_type}_{label} = "{rid_str}"\n')

                            # 4) Écriture du fichier généré
                            os.makedirs(os.path.dirname(target_rmp), exist_ok=True)
                            with open(target_rmp, 'w', encoding='utf-8') as dst:
                                dst.writelines(remap_lines)

                            logger.info(
                                f"Remap généré dynamiquement depuis XML '{xml_to_parse}' → '{target_rmp}'"
                            )
                        except Exception as e:
                            logger.error(f"  Échec génération fallback remap depuis XML: {e}")

                game_key = f"{system}|{game}"
                # si aucun layout dédié, on récupère les layouts système
                layouts = self.game_layouts or self.system_layouts
                # Utiliser la clé système par défaut si pas de layouts jeu
                cfg = _read_panel_cfg()

                game_key = f"{system}|{game}"
                layouts  = self.game_layouts or self.system_layouts

                # On ne veut tomber sur game_key que si c'est explicitement dans PanelDefaults
                has_game_override = (
                    cfg.has_section('PanelDefaults')
                    and cfg.get('PanelDefaults', game_key, fallback='').strip() != ''
                )
                key_to_use = game_key if has_game_override else system
                logger.info(f"key_to_use :{key_to_use} game_key:{game_key} self.game_layouts:{self.game_layouts}")
                self._apply_saved_layout(key_to_use, layouts, 'current_game_idx', save=False)

                self.lip_events = []
                now = time.time()
                logger.warning(f"GAME SELECTED [OBSERVER] on_modified reçu à {now:.3f}")
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
            now = time.time()
            logger.warning(f"GAME START [OBSERVER] on_modified reçu à {now:.3f}")
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
            safe_serial_write(self.ser, cmd, label=f"{key} layout")
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
            lip_tree = ET.parse(lip_path)
            lip_root = lip_tree.getroot()

            # on choisit le layout actif : jeu si on est en game-mode, sinon système
            if self.current_game is not None and self.game_layouts:
                current_layout = self.game_layouts[self.current_game_idx]['name']
            else:
                current_layout = self.system_layouts[self.current_layout_idx]['name']

            # parcours de tous les <events> pour trouver celui dont name == current_layout
            evroot = None
            logger.info(f"Current layout '{current_layout}'")
            for ev in lip_root.findall('events'):
                name = ev.get('name')
                etype = ev.get('type', '')
                logger.info(f"Checking <events> name='{name}' type='{etype}'")
                if name == current_layout or etype == current_layout:
                    logger.info(f".lip events matching layout {current_layout} = {ev.get('name')}")
                    evroot = ev
                    break

            if evroot is None:
                logger.info(f"No .lip events matching layout '{current_layout}'")
                return

            # on a trouvé le bon bloc <events>
            lip_name = evroot.get('name')           # ex. "Arcade-Shark 6B"
            lip_type = evroot.get('type', '')       # ex. "6-Button"
            lip_btn_cnt = int(lip_type.split('-', 1)[0])
            logger.info(f"layout {lip_name} {lip_type} {lip_btn_cnt} ")
        except Exception as e:
            logger.error(f"Error parsing .lip header: {e}")
            return

        # 5) lire le panelButtons configuré dans config.ini
        cfg = self.cfg
        panel_btn_cnt = cfg.getint(
            'Panel',
            'Player1_buttons_count',
            fallback=cfg.getint('Panel','buttons_count',fallback=0)
        )

        # 6) si ça ne correspond pas, on skippe
        if lip_btn_cnt > panel_btn_cnt:
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
            b   = ev.get('button','').upper()               # ex. "B5"
            trg = ev.get('trigger','press').lower()         # "press" ou "release"
            phys_src = label_to_phys.get(b)                 # ex. 5
            if phys_src is None:
                logger.info(f"Skipping .lip event for unknown label '{b}'")
                continue

            # chaque <macro> dans cet <event>
            for macro in ev.findall('macro'):
                mtype = macro.get('type','').lower()
                if mtype == 'set_panel_colors':
                    arg = macro.find('.//colors').text.strip()
                    entry = {
                        'id':      phys_src-1,
                        'trigger': trg,
                        'macro':   'set_panel_colors',
                        'arg':     arg
                    }
                if mtype == 'restore_panel':
                    arg = macro.find('.//panel').text.strip()
                    entry = {
                        'id':      phys_src-1,
                        'trigger': trg,
                        'macro':   'restore_panel',
                        'arg':     arg
                    }
                if mtype == 'set_button':
                    # couleur par bouton
                    # le texte est du type "CURRENT,B6:BLACK;" ou plusieurs mappings séparés par ';'
                    raw = macro.find('color').text.strip().rstrip(';')
                    # on retire le préfixe "CURRENT,"
                    _, mapping = raw.split(',',1)      # mapping == "B6:BLACK"
                    target, color = mapping.split(':',1)
                    entry = {
                        'id':      phys_src-1,
                        'trigger': trg,
                        'macro':   'set_button',
                        'target':  target,   # ex. "B6"
                        'color':   color     # ex. "BLACK"
                    }
                if mtype == 'blink_button':
                    # couleur par bouton
                    # le texte est du type "CURRENT,B6:BLACK;" ou plusieurs mappings séparés par ';'
                    raw = macro.find('color').text.strip().rstrip(';')
                    # on retire le préfixe "CURRENT,"
                    _, mapping = raw.split(',',1)      # mapping == "B6:BLACK"
                    target, color1, color2, timecolor1, timecolor2 = mapping.split(',',5)
                    entry = {
                        'id':      phys_src-1,
                        'trigger': trg,
                        'macro':   'blink_button',
                        'target':  target,   # ex. "B6"
                        'color1':   color1,     # ex. "BLACK"
                        'color2':   color2,     # ex. "BLACK"
                        'timecolor1':   timecolor1,     # ex. "200"
                        'timecolor2':   timecolor2     # ex. "200"
                    }
                #else:
                    # type de macro inconnu → on skippe
                    #continue

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

                            # --- nouvelle prise en charge set_button ---
                            if le['macro'] == 'set_button':
                                cmd = (
                                    f"SetButton={handler.panel_id},"
                                    f"{le['target']},{le['color']}"
                                )
                                logger.info(f"    ➡ Executing macro: {cmd}")
                                safe_serial_write(handler.ser, cmd + '\n', label="joystick")
                                # on ne traite pas les autres macros pour ce même event
                                continue
                            if le['macro'] == 'blink_button':
                                #BlinkButton=1,B3,PINK,BLACK,300,300
                                cmd = (
                                    f"BlinkButton={handler.panel_id},"
                                    f"{le['target']},{le['color1']},{le['color2']},{le['timecolor1']},{le['timecolor2']}"
                                )
                                logger.info(f"    ➡ Executing macro: {cmd}")
                                safe_serial_write(handler.ser, cmd + '\n', label="joystick")
                                # on ne traite pas les autres macros pour ce même event
                                continue
                            # --- macro couleur globale ---
                            if le['macro'] == 'set_panel_colors':
                                mapping = le['arg'].replace('CURRENT', str(handler.panel_id))
                                cmd     = f"SetPanelColors={mapping}"
                            else:
                                panel_arg = le['arg'].replace('CURRENT', str(handler.panel_id))
                                cmd       = f"RestorePanel={panel_arg}"

                            logger.info(f"    ➡ Executing macro: {cmd}")
                            safe_serial_write(handler.ser, cmd + '\n', label="joystick")
                            break

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
                        # Si on est dans un menu “jeu” (game-selected) :
                        if handler.current_game is not None:
                            # 1) Sélection des layouts et détermination si on doit enregistrer
                            if handler.game_layouts:
                                layouts     = handler.game_layouts
                                idx_attr    = 'current_game_idx'
                                save_key    = f"{handler.last_system}|{handler.current_game}"
                                should_save = True
                                logger.info("Game-specific layouts sélectionnés")
                            elif handler.system_layouts:
                                layouts     = handler.system_layouts
                                idx_attr    = 'current_layout_idx'
                                save_key    = f"{handler.last_system}|{handler.current_game}"
                                should_save = True
                                logger.info(f"Pas de layout jeu → fallback system-layout {handler.last_system} {handler.current_game}")
                            else:
                                logger.warning("Aucun layout disponible → abort")
                                return

                            # 2) Initialisation de l’index selon PanelDefaults
                            cfg         = _read_panel_cfg()
                            panel_defs  = cfg['PanelDefaults']
                            system_key  = handler.last_system or ''
                            game_key    = f"{system_key}|{handler.current_game}"


                            if game_key in panel_defs:
                                default_type = panel_defs.get(game_key)
                            elif system_key in panel_defs:
                                default_type = panel_defs.get(system_key)
                            else:
                                default_type = None


                            if default_type:
                                idx = next(
                                    (i for i, entry in enumerate(layouts)
                                     if entry.get('name') == default_type or entry.get('type') == default_type),
                                    0
                                )
                            else:
                                # choisir le layout en fonction du nombre de boutons du panel concerné
                                cfg      = _read_panel_cfg()
                                panel_id = handler.panel_id
                                # récupère playerN_buttons_count ou, à défaut, buttons_count
                                btn_cnt  = cfg.getint(
                                    'Panel',
                                    f'player{panel_id}_buttons_count',
                                    fallback=cfg.getint('Panel','buttons_count',fallback=0)
                                )
                                # trouver l'idx du layout "N-Button"
                                idx = next(
                                    (i for i, entry in enumerate(layouts)
                                     if entry.get('type') == f"{btn_cnt}-Button"),
                                    0
                                )
                            #else:
                            #    idx = getattr(handler, idx_attr, 0)



                            setattr(handler, idx_attr, idx)

                            # 3) Navigation circulaire
                            if direction == 'Left':
                                idx = (idx - 1) % len(layouts)
                                logger.info("    ↶ previous layout")
                            else:
                                idx = (idx + 1) % len(layouts)
                                logger.info("    ↷ next layout")
                            setattr(handler, idx_attr, idx)

                            # 4) Envoi du SetPanelColors
                            cfg         = _read_panel_cfg()
                            panels      = '|'.join(str(i) for i in range(
                                1,
                                cfg.getint('Panel','players_count',fallback=1) + 1
                            ))
                            entry       = layouts[idx]
                            mapping     = ';'.join(f"{lbl}:{clr}" for lbl, clr in entry['buttons'])
                            cmd         = f"SetPanelColors={panels},{mapping},default=yes\n"
                            safe_serial_write(handler.ser, cmd + '\n', label="joystick")
                            name_or_type = entry.get('name') or entry.get('type')
                            logger.info(f"    ➡ Sent (layout '{name_or_type}')")

                            # 5) Sauvegarde conditionnelle du choix (uniquement pour les layouts jeu)
                            if should_save:
                                #prev = handler.system_layouts
                                #handler.system_layouts = layouts
                                #handler._save_layout_idx(save_key, idx)
                                #handler.system_layouts = prev
                                threading.Thread(
                                    target=handler._save_layout_idx,
                                    args=(save_key, idx),
                                    daemon=True
                                ).start()


                            # 6) Popup puis retour au début de la boucle
                            show_popup_tk(name_or_type)
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

                        #time.sleep(0.01)
                        continue
import time
orig_on_modified = LedEventHandler.on_modified
def profiled_on_modified(self, event):
    t0 = time.perf_counter()
    orig_on_modified(self, event)
    dt = (time.perf_counter() - t0)*1000
    logger.warning(f"[PROFILE] on_modified took {dt:.1f} ms")
LedEventHandler.on_modified = profiled_on_modified

def main():
    cfg = _read_panel_cfg()

    pico = find_pico()
    if not pico:
        sys.exit(1)
    ser = serial.Serial(pico, BAUDRATE, timeout=1, write_timeout=0)

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
        #ser.flush()
        logger.info(f"➡ Sent INIT: {init_cmd.strip()}")
    except Exception as e:
        logger.error(f"Failed to send INIT: {e}")

    logger.info(f"Config: players={cfg.getint('Panel','players_count',fallback=1)}, Player1_buttons_count={btn_cnt}")
    led_handler = LedEventHandler(ser, panel_id)
    observer = Observer(timeout=0.1)  # passe de 1 s à 100 ms
    observer.schedule(led_handler, os.path.dirname(ES_EVENT_FILE), recursive=False)
    observer.start()
    logger.info(f"Observer class   : {type(observer).__name__}")
    logger.info(f"Emitter class    : {observer._emitter_class.__name__}")

    t = threading.Thread(target=joystick_listener, args=(led_handler,), daemon=True)
    t.start()
    threading.Thread(target=monitor_serial_buffer, args=(ser,), daemon=True).start()
    threading.Thread(target=read_serial_feedback, args=(ser,), daemon=True).start()

    logger.info("Led Panel Color Manager running…")
    try:
        while True:
            time.sleep(0.1)
    except KeyboardInterrupt:
        observer.stop()
        observer.stop()
        observer.stop()
    observer.join()
    ser.close()

if __name__ == '__main__':
    main()

