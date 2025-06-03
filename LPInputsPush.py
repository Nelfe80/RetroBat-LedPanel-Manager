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
ARCADE_XML_DIR   = os.path.join(RETROBAT_ROOT, 'plugins', 'LedPanelManager', 'systems', 'mame')

# ——— Logging ———
logging.basicConfig(level=logging.DEBUG, format='[%(asctime)s] %(levelname)s: %(message)s')
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

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

def load_layout(game, btn_count):
    """
    Renvoie l'élément <layout> dont panelButtons == btn_count pour le jeu donné.
    """
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

def add_button(parent, port, btn, es_map, player_num, remap):
    """
    Injecte un bouton en se basant sur 'physical' du layout,
    et utilise le dictionnaire `remap` (physical → index_MAME) pour le newseq.
    """
    phys = int(btn.get('physical'))
    ctrl = btn.get('controller').upper()

    tag  = port.get('tag')
    mask = port.get('mask')
    pfx  = f"P{player_num}"

    # Le 'type' du <port> reprend phys pour correspondre au *_inputs.cfg
    port_el = ET.SubElement(
        parent, 'port',
        tag=tag,
        type=f"{pfx}_BUTTON{phys}",
        mask=mask,
        defvalue=mask
    )

    mapped = remap.get(phys, phys)
    #<port tag=":IN0" type="P1_BUTTON1" mask="16" defvalue="16">
    #<newseq type="standard">JOYCODE_1_BUTTON3</newseq>
    # </port>
    seq = ET.SubElement(port_el, 'newseq', type='standard')
    idx, kind = es_map.get(ctrl, (None, None))
    if kind == 'button':
        seq.text = f"JOYCODE_{player_num}_BUTTON{mapped}"
    elif kind == 'key':
        seq.text = f"KEYCODE_{ctrl}"
    else:
        seq.text = ''

def write_cfg(game, es_maps, ports):
    """
    Reconstruit la section <input> dans <game>.cfg à partir du mapping ES
    et du layout XML, en calculant automatiquement le remap par (y,x),
    sauf si on détecte un jeu 'punch/kick' via le champ 'function', auquel cas
    on applique le remap fixe.
    """
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

    for player_num in range(1, players_count + 1):
        es_map = es_maps[player_num - 1]

        # 1) Charger le layout approprié
        btn_count = cfg_ini.getint(
            'Panel',
            f'Player{player_num}_buttons_count',
            fallback=cfg_ini.getint('Panel', 'buttons_count', fallback=6)
        )
        try:
            layout = load_layout(game, btn_count)
        except (FileNotFoundError, ValueError):
            logger.warning(f"No XML layout for game '{game}' with {btn_count} buttons")
            continue

        # 2) Détecter s'il s'agit d'un jeu "kick/punch" en inspectant le champ 'function'
        is_kick_punch = False
        for btn in layout.findall('button'):
            func = btn.get('function', '')
            if func and ('punch' in func.lower() or 'kick' in func.lower()):
                is_kick_punch = True
                break

        # 3) Définir le remap
        if is_kick_punch:
            # Remap fixe pour jeux kick-punch
            remap = {
                1: 3,
                2: 4,
                3: 5,
                4: 1,
                5: 2,
                6: 6,
                7: 7,
                8: 8
            }
        else:
            # Remap automatique par (y,x)
            boutons = []
            for btn in layout.findall('button'):
                gb = btn.get('gameButton', '').upper()
                if gb in ('A','B','X','Y','L1','R1','L2','R2'):
                    phys = int(btn.get('physical'))
                    x = int(btn.get('x'))
                    y = int(btn.get('y'))
                    boutons.append((phys, x, y))
            if boutons:
                ys = sorted({y for (_, _, y) in boutons})
                ordre = []
                for row_y in ys:
                    ligne = [b for b in boutons if b[2] == row_y]
                    ordre.extend(sorted(ligne, key=lambda item: item[1]))
                remap = {phys: idx + 1 for idx, (phys, _, _) in enumerate(ordre)}
            else:
                remap = {}

        # 4) Injecter d'abord les directions
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

        # 5) Injecter ensuite les boutons, en passant `remap`
        for btn in layout.findall('button'):
            gb = btn.get('gameButton', '').upper()
            phys = int(btn.get('physical'))

            # Essayer d'abord "P{n}_BUTTON_{phys}", sinon "P{n}_BUTTON{phys}"
            if gb in ('A','B','X','Y','L1','R1','L2','R2'):
                port_key = f"P{player_num}_BUTTON_{phys}"
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
            add_button(inp, port, btn, es_map, player_num, remap)

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

        logger.info(f"Selected MAME game: {game}")
        try:
            ports = load_mame_ports(os.path.join(MAME_CFG_DIR, f"{game}_inputs.cfg"))
            es_maps = load_es_input(ES_INPUT_CFG)
            backup_cfg(game)
            write_cfg(game, es_maps, ports)
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
