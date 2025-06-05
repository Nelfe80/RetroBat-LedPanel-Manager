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

TYPE_MAP = {
    4: 4,
    3: 3,
    5: 5,
    7: 7,
    1: 1,
    2: 2,
    6: 6,
    8: 8,
}

def add_button(parent, port, btn, es_map, player_num, type_index, phys):
    """
    Injecte un <port> basé sur 'physical' du layout,
    avec type="P{player_num}_BUTTON{type_index}",
    et newseq="JOYCODE_{player_num}_BUTTON{phys}".
    """
    tag  = port.get('tag')
    mask = port.get('mask')
    pfx  = f"P{player_num}"

    # Créer <port type="P{player}_BUTTON{type_index}"> (pas d’underscore avant le numéro)
    port_el = ET.SubElement(
        parent, 'port',
        tag=tag,
        type=f"{pfx}_BUTTON{type_index}",
        mask=mask,
        defvalue=mask
    )

    # À l’intérieur, injecter <newseq>JOYCODE_{player}_BUTTON{phys}</newseq>
    seq = ET.SubElement(port_el, 'newseq', type='standard')
    ctrl = btn.get('controller', '').upper()
    idx, kind = es_map.get(ctrl, (None, None))
    if kind == 'button':
        seq.text = f"JOYCODE_{player_num}_BUTTON{phys}"
    elif kind == 'key':
        seq.text = f"KEYCODE_{ctrl}"
    else:
        seq.text = ''


import re

def write_cfg(game, es_maps, ports):
    """
    Reconstruit la section <input> du fichier <game>.cfg en prenant
    pour source de vérité le layout XML, et en :

      1) Ignorant les boutons dont color="Black" dans le XML.
      2) Construisant une liste triée par mask pour les ports joueurs :
         - player_ports = tous les ports dont type commence par "P{player_num}_BUTTON"
      3) Parcourant tous les <button> du XML (dans l’ordre d’apparition) :
         - si c’est un bouton de combat (A, B, X, Y, L1, R1, L2, R2) :
             • si jeu en punchkick ou tributton → type_index = remap_type.get(phys, phys)
               et port_key = f"P{player_num}_BUTTON_{type_index}"
             • sinon (logique standard) → on prend player_ports.pop(0)
               et on extrait type_index via split('_')[2] pour gérer "(CHEAT)"
         - si c’est START → port_key = next((f"{player_num}_PLAYER_START", f"{player_num}_PLAYERS_START"))
             puis type_index = phys
         - si c’est COIN  → port_key = f"COIN_{player_num}", type_index = phys
      4) Pour les jeux “tri-button” (si configurés), on applique en fin la table tb_remap
         sur chaque <newseq> généré.
      5) Autrement, on retombe sur cette même logique, adaptée au XML et aux ports réellement disponibles.
    """
    cfg_path = os.path.join(MAME_CFG_DIR, f"{game}.cfg")

    # ———————————————————————————————————————————————————————————————
    # Si <game>.cfg n'existe pas, on le crée avec le gabarit minimal
    # ———————————————————————————————————————————————————————————————
    if not os.path.exists(cfg_path):
        logger.warning(f"Fichier cfg introuvable pour '{game}', création d’un nouveau fichier.")
        template = f"""<?xml version="1.0"?>
                        <!-- This file is autogenerated; comments and unknown tags will be stripped -->
                        <mameconfig version="10">
                            <system name="{game}">

                            </system>
                        </mameconfig>
                        """
        with open(cfg_path, "w", encoding="utf-8") as f:
            f.write(template)


    tree = ET.parse(cfg_path)
    root = tree.getroot()
    system = root.find('.//system')

    # Supprimer l’ancienne <input> si elle existe
    old_input = system.find('input')
    if old_input is not None:
        system.remove(old_input)

    # Créer la nouvelle balise <input> (avant <bgfx> si présent)
    inp = ET.Element('input')
    bgfx = system.find('bgfx') or system.find('bgfg')
    if bgfx is not None:
        system.insert(list(system).index(bgfx), inp)
    else:
        system.append(inp)

    # Indexer les ports MAME existants par leur type
    ports_by_type = {p.get('type'): p for p in ports}

    # Lire PANEL_CONFIG_INI pour punch/kick et tributton
    cfg_ini = configparser.ConfigParser()
    cfg_ini.read(PANEL_CONFIG_INI)

    pk_games = []
    pk_remap = {}
    tb_games = []
    tb_remap = {}
    if cfg_ini.has_section('Mapping'):
        # punch/kick
        pk_list = cfg_ini.get('Mapping', 'punchkickgames', fallback='').split(',')
        pk_games = [n.strip().lower() for n in pk_list if n.strip()]
        pk_str = cfg_ini.get('Mapping', 'punchkickgames_remap', fallback='').strip()
        if pk_str:
            for pair in pk_str.split(','):
                a, b = pair.split(':')
                pk_remap[int(a)] = int(b)

        # tri-button
        tb_list = cfg_ini.get('Mapping', 'tributtons', fallback='').split(',')
        tb_games = [n.strip().lower() for n in tb_list if n.strip()]
        tb_str = cfg_ini.get('Mapping', 'tributtons_remap', fallback='').strip()
        if tb_str:
            for pair in tb_str.split(','):
                a, b = pair.split(':')
                tb_remap[int(a)] = int(b)

    players_count = min(
        cfg_ini.getint('Panel', 'players_count', fallback=1),
        len(es_maps)
    )

    for player_num in range(1, players_count + 1):
        es_map = es_maps[player_num - 1]

        # Charger le layout XML selon btn_count
        btn_count = cfg_ini.getint(
            'Panel',
            f'Player{player_num}_buttons_count',
            fallback=cfg_ini.getint('Panel', 'buttons_count', fallback=6)
        )
        try:
            layout = load_layout(game, btn_count)
        except (FileNotFoundError, ValueError):
            logger.warning(f"No XML layout pour '{game}' ({btn_count} boutons)")
            continue

        # Choix du remap_type (punch/kick vs tributton vs TYPE_MAP)
        name_lower = game.lower()
        if name_lower in pk_games and pk_remap:
            remap_type = pk_remap
            is_tributton = False
        elif name_lower in tb_games and tb_remap:
            remap_type = TYPE_MAP  # on ajustera ensuite via tb_remap
            is_tributton = True
        else:
            remap_type = TYPE_MAP
            is_tributton = False

        # Injecter d’abord les directions (UP, DOWN, LEFT, RIGHT)
        for direction in ('UP', 'DOWN', 'LEFT', 'RIGHT'):
            key = f"P{player_num}_{direction}"
            if key in ports_by_type:
                add_direction(
                    inp,
                    ports_by_type[key],
                    es_map,
                    player_num,
                    players_count
                )
            else:
                logger.debug(f"No port pour direction {key}")

        # Construire la liste des ports joueurs triés par mask
        player_ports = [
            p_elem
            for p_elem in ports
            if p_elem.get('type').startswith(f"P{player_num}_BUTTON")
        ]
        player_ports.sort(key=lambda p: int(p.get('mask')))  # ordre croissant de mask

        # Parcourir tous les <button> dans le XML, dans l’ordre d’apparition
        for btn in layout.findall('button'):
            # 1) Si color="Black", on ignore ce bouton
            if btn.get('color', '').lower() == 'black':
                continue

            gb = btn.get('gameButton', '').upper()
            phys = int(btn.get('physical'))

            # 2) Boutons de combat (A, B, X, Y, L1, R1, L2, R2)
            if gb in ('A', 'B', 'X', 'Y', 'L1', 'R1', 'L2', 'R2'):
                # a) Si jeu en punchkick ou tributton : remap via remap_type
                if (name_lower in pk_games and pk_remap) or (name_lower in tb_games and tb_remap):
                    type_index = remap_type.get(phys, phys)
                    port_key = f"P{player_num}_BUTTON_{type_index}"
                    if port_key not in ports_by_type:
                        logger.error(f"Missing port pour key {port_key} (phys={phys}, gb={gb})")
                        continue
                    port = ports_by_type[port_key]
                else:
                    # b) Sinon : prendre le premier port libre dans player_ports
                    if not player_ports:
                        logger.error(f"Pas assez de ports joueurs pour phys={phys}, gameButton={gb}")
                        continue
                    port = player_ports.pop(0)
                    # EXTRACTION CORRIGÉE : utiliser split('_')[2] pour récupérer l'index numérique
                    # même si le type contient "(CHEAT)".
                    parts = port.get('type').split('_')
                    type_index = int(parts[2])

                add_button(inp, port, btn, es_map, player_num, type_index, phys)

            # 3) Bouton START
            elif gb == 'START':
                port_key = next(
                    (k for k in (f"{player_num}_PLAYER_START", f"{player_num}_PLAYERS_START")
                     if k in ports_by_type),
                    None
                )
                if not port_key or port_key not in ports_by_type:
                    logger.error(f"Missing port pour key {port_key} (phys={phys}, gb=START)")
                    continue

                port = ports_by_type[port_key]
                # On prend phys comme index pour START
                type_index = phys
                add_button(inp, port, btn, es_map, player_num, type_index, phys)

            # 4) Bouton COIN
            elif gb == 'COIN':
                port_key = f"COIN_{player_num}"
                if port_key not in ports_by_type:
                    logger.error(f"Missing port pour key {port_key} (phys={phys}, gb=COIN)")
                    continue

                port = ports_by_type[port_key]
                # On prend phys comme index pour COIN
                type_index = phys
                add_button(inp, port, btn, es_map, player_num, type_index, phys)

            else:
                # Tout autre gameButton inattendu → on ignore
                logger.debug(f"Bouton XML non reconnu: gameButton={gb}, phys={phys}")
                continue

        # Si jeu “tri-button” : réajuster chaque newseq selon tb_remap
        if is_tributton:
            for port_el in inp.findall('port'):
                seq = port_el.find('newseq')
                if seq is None or not seq.text:
                    continue
                parts = seq.text.split('BUTTON')
                if len(parts) != 2:
                    continue
                try:
                    old_phys = int(parts[1])
                except ValueError:
                    continue
                if old_phys in tb_remap:
                    new_phys = tb_remap[old_phys]
                    seq.text = f"JOYCODE_{player_num}_BUTTON{new_phys}"

    # Réindenter puis écrire le fichier
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
            try:
                ports = load_mame_ports(os.path.join(MAME_CFG_DIR, f"{game}_inputs.cfg"))
            except FileNotFoundError:
                logger.error(f"Fichier inputs introuvable pour '{game}', on quitte write_cfg.")
                return
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
