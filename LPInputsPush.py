"""
RetroBat Arcade Layout Injector
Dynamically rewrites MAME .cfg inputs on game‑selection.
Sources:
 • es_input.cfg       → EmulationStation button‑to‑ID mapping
 • <game>_inputs.cfg  → MAME port definitions (type, masks)
 • <game>.xml         → Arcade layout metadata (positions, colors, functions)
 • config.ini         → Physical panel button count & players count & manual mapping
Backups original <game>.cfg to <game>_backup.cfg.
"""
import os
import re
import time
import shutil
import logging
import configparser
import xml.etree.ElementTree as ET
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# ——— Paths ———
RETROBAT_ROOT    = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
ES_INPUT_CFG     = os.path.join(RETROBAT_ROOT, 'emulationstation', '.emulationstation', 'es_input.cfg')
ES_EVENT_FILE    = os.path.join(RETROBAT_ROOT, 'plugins', 'LedPanelManager', 'ESEvent.arg')
PANEL_CONFIG_INI = os.path.join(RETROBAT_ROOT, 'plugins', 'LedPanelManager', 'config.ini')
MAME_CFG_DIR     = os.path.join(RETROBAT_ROOT, 'bios', 'mame', 'cfg')
ARCADE_XML_DIR   = os.path.join(RETROBAT_ROOT, 'plugins', 'LedPanelManager', 'arcade')

# ——— Logging ———
logging.basicConfig(level=logging.DEBUG, format='[%(asctime)s] %(levelname)s: %(message)s')
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

# ——— Helpers ———

def parse_es_event(path):
    """
    Lit ESEvent.arg et renvoie (emulator, rom_basename).
    Ex:
      event=game-selected&param1="mame"&param2=".../mslug.zip"&param3="..."
      → ("mame", "mslug")
    """
    data = open(path, encoding='utf-8').read().strip()
    parts = [p for p in data.split('&') if '=' in p]
    params = dict(p.split('=', 1) for p in parts)
    emulator = params.get('param1', '').strip('"').lower()
    rompath  = params.get('param2', '').strip('"')
    game     = os.path.splitext(os.path.basename(rompath))[0]
    return emulator, game

def load_es_input(path):
    """
    Retourne un dict mapping contrôleur → (id:int, type:str)
    type ∈ {'button','key','axis'}
    """
    tree = ET.parse(path)
    es_map = {}
    for inp in tree.findall('.//input'):
        name = inp.get('name').upper()
        idx  = int(inp.get('id'))
        kind = inp.get('type')
        es_map[name] = (idx, kind)
    return es_map

def load_mame_ports(path):
    tree = ET.parse(path)
    return tree.findall('.//port')

def detect_max_player(ports):
    nums = [int(m.group(1)) for p in ports
            if (m := re.match(r'^P(\d+)_', p.get('type','')))]
    return max(nums) if nums else 1

def load_layout(game, btn_count):
    xml = os.path.join(ARCADE_XML_DIR, f"{game}.xml")
    for layout in ET.parse(xml).findall('.//layout'):
        if layout.get('panelButtons') == str(btn_count):
            return layout
    raise ValueError(f"No layout for {btn_count} buttons in {game}")

def backup_cfg(game):
    src = os.path.join(MAME_CFG_DIR, f"{game}.cfg")
    dst = os.path.join(MAME_CFG_DIR, f"{game}_backup.cfg")
    if os.path.exists(src):
        shutil.copy2(src, dst)
        logger.info(f"Backup {src} → {dst}")

def build_button_map(max_player):
    bm = {}
    letters = {i: chr(64 + i) for i in range(1, 9)}
    for n in range(1, 9):
        letter = letters[n]
        patterns = []
        for p in range(1, max_player + 1):
            patterns.append(fr'^P{p}[_\-]?BUTTON[_\-]?{n}$')
            patterns.append(fr'^P{p}[_\-]?{letter}$')
        patterns += [fr'^BUTTON[_\-]?{n}$', fr'^{letter}$']
        bm[str(n)] = patterns
        bm[letter]    = patterns
    rng = f"1-{max_player}"
    bm['START'] = [
        fr'^(?:P[{rng}]_)?PLAYER[_\-]?START$',
        fr'^[{rng}]_PLAYER[_\-]?START$',
    ]
    bm['COIN'] = [
        fr'^(?:P[{rng}]_)?COIN[_\-]?1$',
        fr'^(?:P[{rng}]_)?COIN[_\-]?2$',
    ]
    return bm

def find_port(ports, patterns):
    types = [p.get('type','') for p in ports]
    logger.debug("Ports disponibles: %s", types)
    for pat in patterns:
        logger.debug("  → test pattern %r", pat)
        rg = re.compile(pat, re.IGNORECASE)
        for p in ports:
            if rg.match(p.get('type','')):
                logger.debug("    ✅ matched %r", p.get('type'))
                return p
    logger.error("    ❌ aucun port trouvé pour patterns: %s", patterns)
    return None

def indent(elem, level=0):
    i = "\n" + "    " * level
    if len(elem):
        if not elem.text or not elem.text.strip():
            elem.text = i + "    "
        for e in elem:
            indent(e, level + 1)
        if not elem.tail or not elem.tail.strip():
            elem.tail = i
    else:
        if level and (not elem.tail or not elem.tail.strip()):
            elem.tail = i

def insert_ports(tree, ports, es_map, layout, button_map):
    root   = tree.getroot()
    system = root.find('.//system')
    # remove existing <input>
    for old in system.findall('input'):
        system.remove(old)
    bgfx = system.find('bgfx')
    inp  = ET.Element('input')
    if bgfx is not None:
        idx = list(system).index(bgfx)
        system.insert(idx, inp)
    else:
        system.append(inp)

    # joystick directions for all players
    cfg = configparser.ConfigParser()
    cfg.read(PANEL_CONFIG_INI)
    players_count = cfg.getint('Panel', 'players_count', fallback=1)
    for pnum in range(1, players_count + 1):
        prefix = f"P{pnum}_"
        for port in ports:
            t = port.get('type','').upper()
            parts = t.split('_', 1)
            if t.startswith(prefix) and parts[1] in ('UP','DOWN','LEFT','RIGHT'):
                add_port(inp, port, es_map)

    # custom buttons from layout
    for btn in layout.findall('button'):
        ctrl = btn.get('controller','').upper()
        gb   = btn.get('gameButton')
        if ctrl not in es_map:
            logger.info(f"Ignored {gb} (controller {ctrl} absent du pad)")
            continue
        logger.debug("Traitement gameButton=%r avec controller=%r", gb, ctrl)
        patterns = button_map.get(gb, [])
        p = find_port(ports, patterns)
        if p is not None:
            add_port(inp, p, es_map, ctrl)
        else:
            logger.error("Port manquant pour gameButton=%r", gb)

def add_port(parent, port, es_map, ctrl=None):
    tag, typ, mask = port.get('tag'), port.get('type'), port.get('mask')
    el  = ET.SubElement(parent, 'port', tag=tag, type=typ, mask=mask, defvalue=mask)
    seq = ET.SubElement(el, 'newseq', type='standard')
    if not ctrl or ctrl not in es_map:
        return
    idx, kind = es_map[ctrl]
    if kind == 'button':
        seq.text = f"JOYCODE_BUTTON{idx+1}"
    elif kind == 'key':
        seq.text = f"KEYCODE_{ctrl}"
    else:
        seq.text = ''

def write_cfg(game, es_map, ports, layout, button_map):
    cfg  = os.path.join(MAME_CFG_DIR, f"{game}.cfg")
    tree = ET.parse(cfg)
    insert_ports(tree, ports, es_map, layout, button_map)
    indent(tree.getroot())
    tree.write(cfg, encoding='utf-8', xml_declaration=True)
    logger.info(f"Wrote {cfg}")

class GameHandler(FileSystemEventHandler):
    def __init__(self):
        self.last_game = None

    def on_modified(self, event):
        if event.is_directory or os.path.basename(event.src_path) != os.path.basename(ES_EVENT_FILE):
            return
        emulator, game = parse_es_event(ES_EVENT_FILE)
        if emulator != 'mame':
            logger.debug("Skipped event for emulator '%s'", emulator)
            return
        if not game or game == self.last_game:
            return

        # load config.ini
        cfg_ini       = configparser.ConfigParser()
        cfg_ini.read(PANEL_CONFIG_INI)
        btn_count     = cfg_ini.getint('Panel', 'buttons_count', fallback=6)
        players_count = cfg_ini.getint('Panel', 'players_count', fallback=1)

        # load es_input and apply manual mapping overrides
        es_map = load_es_input(ES_INPUT_CFG)
        if cfg_ini.has_section('Mapping'):
            for ctrl, val in cfg_ini.items('Mapping'):
                if val.strip():
                    key = ctrl.upper()
                    forced = int(val.strip())
                    es_map[key] = (forced, 'button')

        logger.info("Selected MAME game: %s", game)
        try:
            ports        = load_mame_ports(os.path.join(MAME_CFG_DIR, f"{game}_inputs.cfg"))
            detected_max = detect_max_player(ports)
            max_p        = min(detected_max, players_count)
            button_map   = build_button_map(max_p)
            layout       = load_layout(game, btn_count)
            backup_cfg(game)
            write_cfg(game, es_map, ports, layout, button_map)
            self.last_game = game
        except Exception:
            logger.exception("Erreur lors de l'injection de layout")

def main():
    if not os.path.exists(ES_EVENT_FILE):
        return
    observer = Observer()
    observer.schedule(GameHandler(), os.path.dirname(ES_EVENT_FILE), recursive=False)
    observer.start()
    logger.info("Injector running")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()

if __name__ == '__main__':
    main()
