import os
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
    data = open(path, encoding='utf-8').read().strip()
    parts = [p for p in data.split('&') if '=' in p]
    params = dict(p.split('=', 1) for p in parts)
    emulator = params.get('param1', '').strip('"').lower()
    rompath  = params.get('param2', '').strip('"')
    game     = os.path.splitext(os.path.basename(rompath))[0]
    return emulator, game


def load_es_input(path):
    tree = ET.parse(path)
    es_maps = []
    # priorité joystick
    for inputcfg in tree.findall('.//inputConfig'):
        if inputcfg.get('type') != 'joystick':
            continue
        mapping = {}
        for inp in inputcfg.findall('input'):
            name = inp.get('name').upper()
            idx = int(inp.get('id')) if inp.get('id').isdigit() else 0
            kind = inp.get('type')
            mapping[name] = (idx, kind)
        es_maps.append(mapping)
    # fallback clavier
    if not es_maps:
        for inputcfg in tree.findall('.//inputConfig'):
            if inputcfg.get('type') == 'keyboard':
                mapping = {}
                for inp in inputcfg.findall('input'):
                    name = inp.get('name').upper()
                    idx = int(inp.get('id')) if inp.get('id').isdigit() else 0
                    kind = inp.get('type')
                    mapping[name] = (idx, kind)
                es_maps.append(mapping)
                break
    return es_maps


def load_mame_ports(path):
    tree = ET.parse(path)
    return tree.findall('.//port')


def detect_max_player(ports):
    nums = []
    for p in ports:
        t = p.get('type', '')
        parts = t.split('_', 1)
        if parts[0].startswith('P') and parts[0][1:].isdigit():
            nums.append(int(parts[0][1:]))
        elif parts[0].isdigit():
            nums.append(int(parts[0]))
    return max(nums) if nums else 1


def load_layout(game, btn_count):
    xml = os.path.join(ARCADE_XML_DIR, f"{game}.xml")
    tree = ET.parse(xml)
    for layout in tree.findall('.//layout'):
        if layout.get('panelButtons') == str(btn_count):
            return layout
    raise ValueError(f"No layout for {btn_count} buttons in {game}")


def backup_cfg(game):
    src = os.path.join(MAME_CFG_DIR, f"{game}.cfg")
    dst = os.path.join(MAME_CFG_DIR, f"{game}_backup.cfg")
    if os.path.exists(src):
        shutil.copy2(src, dst)
        logger.info(f"Backup {src} → {dst}")


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


def add_direction(parent, port, es_map, player_num, players_count):
    orig_type = port.get('type', '').upper()
    mask = port.get('mask')
    tag = port.get('tag')

    port_el = ET.SubElement(parent, 'port', tag=tag, type=orig_type, mask=mask, defvalue=mask)
    seq = ET.SubElement(port_el, 'newseq', type='standard')

    parts = orig_type.split('_', 1)
    if len(parts) != 2 or parts[1] not in ('UP', 'DOWN', 'LEFT', 'RIGHT'):
        seq.text = ''
        return
    direction = parts[1]

    # priorité au hat si dispo
    kind = None
    if direction in es_map and es_map[direction][1] == 'hat':
        kind = 'hat'
    else:
        axis_key = f"JOYSTICK{player_num}{direction}"
        if axis_key in es_map and es_map[axis_key][1] == 'axis':
            kind = 'axis'

    if kind == 'hat':
        seq.text = f"JOYCODE_HAT{player_num}{direction}"
    elif kind == 'axis':
        axis_name = 'XAXIS' if direction in ('LEFT', 'RIGHT') else 'YAXIS'
        if players_count > 1:
            seq.text = f"JOYCODE_{player_num}_{axis_name}_{direction}_SWITCH"
        else:
            seq.text = f"JOYCODE_{axis_name}_{direction}_SWITCH"
    else:
        axis_name = 'YAXIS' if direction in ('UP', 'DOWN') else 'XAXIS'
        if players_count > 1:
            seq.text = f"JOYCODE_{player_num}_{axis_name}_{direction}_SWITCH"
        else:
            seq.text = f"JOYCODE_{axis_name}_{direction}_SWITCH"


def add_button(parent, port, btn, es_map, player_num):
    """
    Injecte un bouton en se basant sur l'attribut 'physical' du layout.
    """
    phys = int(btn.get('physical'))
    ctrl = btn.get('controller').upper()

    tag  = port.get('tag')
    mask = port.get('mask')
    pfx  = f"P{player_num}"
    port_el = ET.SubElement(
        parent, 'port',
        tag=tag,
        type=f"{pfx}_BUTTON{phys}",
        mask=mask,
        defvalue=mask
    )
    seq = ET.SubElement(port_el, 'newseq', type='standard')

    idx, kind = es_map.get(ctrl, (None, None))
    if kind == 'button':
        seq.text = f"JOYCODE_{pfx}_BUTTON{phys}"
    elif kind == 'key':
        seq.text = f"KEYCODE_{ctrl}"
    else:
        seq.text = ''


def write_cfg(game, es_maps, ports, layout):
    cfg_path = os.path.join(MAME_CFG_DIR, f"{game}.cfg")
    tree = ET.parse(cfg_path)
    root = tree.getroot()
    system = root.find('.//system')
    old = system.find('input')
    if old is not None:
        system.remove(old)
    inp = ET.Element('input')
    bgfx = system.find('bgfx') or system.find('bgfg')
    if bgfx is not None:
        system.insert(list(system).index(bgfx), inp)
    else:
        system.append(inp)

    ports_by_type = {p.get('type'): p for p in ports}

    cfg_ini = configparser.ConfigParser()
    cfg_ini.read(PANEL_CONFIG_INI)
    players_count = min(
        cfg_ini.getint('Panel', 'players_count', fallback=1),
        len(es_maps)
    )

    # Directions inchangées
    for player_num in range(1, players_count + 1):
        es_map = es_maps[player_num - 1]
        for dir in ('UP', 'DOWN', 'LEFT', 'RIGHT'):
            key = f"P{player_num}_{dir}"
            if key in ports_by_type:
                add_direction(
                    inp,
                    ports_by_type[key],
                    es_map,
                    player_num,
                    players_count
                )
            else:
                logger.debug(f"No port for direction {key}")

    # Buttons par joueur
    for player_num in range(1, players_count + 1):
        es_map = es_maps[player_num - 1]
        btn_count = cfg_ini.getint(
            'Panel',
            f'Player{player_num}_buttons_count',
            fallback=cfg_ini.getint('Panel', 'buttons_count', fallback=6)
        )
        layout = load_layout(game, btn_count)

        for btn in layout.findall('button'):
            gb = btn.get('gameButton').upper()
            if gb == 'NONE':
                continue
            if gb in ('A','B','C','D','E','F','G','H'):
                port_key = f"P{player_num}_{gb}"
            elif gb == 'START':
                port_key = next(
                    (k for k in (f"{player_num}_PLAYER_START", f"{player_num}_PLAYERS_START")
                     if k in ports_by_type),
                    None
                )
            elif gb == 'COIN':
                port_key = f"COIN_{player_num}"
            else:
                port_key = None

            if not port_key or port_key not in ports_by_type:
                logger.error(f"Missing port for key {port_key}")
                continue

            port = ports_by_type[port_key]
            add_button(inp, port, btn, es_map, player_num)

    indent(root)
    tree.write(cfg_path, encoding='utf-8', xml_declaration=True)
    logger.info(f"Wrote {cfg_path}")


class GameHandler(FileSystemEventHandler):
    def __init__(self):
        self.last_game = None

    def on_modified(self, event):
        if event.is_directory or os.path.basename(event.src_path) != os.path.basename(ES_EVENT_FILE):
            return
        emulator, game = parse_es_event(ES_EVENT_FILE)
        if emulator != 'mame' or game == self.last_game:
            return

        cfg_ini = configparser.ConfigParser()
        cfg_ini.read(PANEL_CONFIG_INI)
        btn_count = cfg_ini.getint('Panel', 'buttons_count', fallback=6)

        logger.info(f"Selected MAME game: {game}")
        try:
            ports = load_mame_ports(os.path.join(MAME_CFG_DIR, f"{game}_inputs.cfg"))
            es_maps = load_es_input(ES_INPUT_CFG)
            # layout loaded inside write_cfg per joueur
            backup_cfg(game)
            write_cfg(game, es_maps, ports, layout=None)
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
