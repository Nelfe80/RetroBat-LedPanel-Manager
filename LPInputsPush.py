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
# Pour chaque BUTTON<n>, on accepte une ou plusieurs variantes de "function"
BUTTON_TO_ACTION = {
    "BUTTON1": ["HIGH_PUNCH",    "JAB_PUNCH"],
    "BUTTON2": ["BLOCK",         "STRONG_PUNCH"],
    "BUTTON3": ["HIGH_KICK",     "FIERCE_PUNCH"],
    "BUTTON4": ["LOW_PUNCH",     "SHORT_KICK"],
    "BUTTON5": ["LOW_KICK",      "FORWARD_KICK"],
    "BUTTON6": ["BLOCK_2",       "RUN",
                "ROUNDHOUSE_KICK"],
}
# Inversion automatique (on ne l’utilise que si on veut passer ACTION → BUTTON<n> ; sinon, on cherche directement)
ACTION_TO_BUTTON = {
    action: btn
    for btn, actions in BUTTON_TO_ACTION.items()
    for action in actions
}


def write_cfg(game, es_maps, ports):
    """
    Reconstruit la section <input> du fichier <game>.cfg (MAME) en prenant
    pour source de vérité le layout XML, et en :
      - Pour MK : toujours créer <port type="P{player}_BUTTON<n>"> dans le
        même ordre (BUTTON1…BUTTON6) ; seul le JOYCODE changera si mkgames_remap évolue.
      - Pour les autres jeux : logique inchangée (punch-kick, tributton, etc.).
      - Les attributs mask/defvalue/tag sont toujours hérités du port d’origine
        (ex: P1_HIGH_PUNCH, P1_BLOCK, …) lu dans ports_by_type.
    """
    cfg_path = os.path.join(MAME_CFG_DIR, f"{game}.cfg")

    # — Si le fichier n'existe pas, on le crée minimalement
    if not os.path.exists(cfg_path):
        logger.warning(f"Fichier cfg introuvable pour '{game}', création d’un nouveau fichier.")
        template = f"""<?xml version="1.0"?>
<!-- This file est autogenerated; comments et unknown tags seront supprimés -->
<mameconfig version="10">
    <system name="{game}">

    </system>
</mameconfig>
"""
        with open(cfg_path, "w", encoding="utf-8") as f:
            f.write(template)

    # On parse/charge l’arbre XML existant
    tree = ET.parse(cfg_path)
    root = tree.getroot()
    system = root.find('.//system')

    # Supprimer l’ancienne balise <input> si elle existe
    old_input = system.find('input')
    if old_input is not None:
        system.remove(old_input)

    # Créer une nouvelle balise <input> (juste avant <bgfx> si présent, sinon à la fin)
    inp = ET.Element('input')
    bgfx = system.find('bgfx')
    if bgfx is not None:
        system.insert(list(system).index(bgfx), inp)
    else:
        system.append(inp)

    # — Lecture des mappings depuis PANEL_CONFIG_INI —
    cfg_ini = configparser.ConfigParser()
    cfg_ini.read(PANEL_CONFIG_INI)

    pk_games = []
    pk_remap = {}
    tb_games = []
    tb_remap = {}
    mk_games = []
    mk_remap = {}

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

        # Mortal Kombat
        mk_list = cfg_ini.get('Mapping', 'mkgames', fallback='').split(',')
        mk_games = [n.strip().lower() for n in mk_list if n.strip()]
        mk_str = cfg_ini.get('Mapping', 'mkgames_remap', fallback='').strip()
        if mk_str:
            for pair in mk_str.split(','):
                a, b = pair.split(':')
                mk_remap[int(a)] = int(b)

    # Nombre de joueurs à traiter (on prend le min entre cfg et es_maps)
    players_count = min(
        cfg_ini.getint('Panel', 'players_count', fallback=1),
        len(es_maps)
    )

    # Dictionnaire pour retrouver les ports d’origine par type (ex: "P1_HIGH_PUNCH")
    ports_by_type = {p.get('type').upper(): p for p in ports}

    for player_num in range(1, players_count + 1):
        es_map = es_maps[player_num - 1]

        # Charger le layout XML du panel (selon le nombre de boutons)
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

        # (1) Déterminer si jeu MK, PK ou TB
        name_lower = game.lower()
        is_mk = False
        is_pk = False
        is_tb = False

        if name_lower in mk_games and mk_remap:
            remap_type = mk_remap
            is_mk = True
        elif name_lower in pk_games and pk_remap:
            remap_type = pk_remap
            is_pk = True
        elif name_lower in tb_games and tb_remap:
            remap_type = TYPE_MAP  # on appliquera tb_remap en fin
            is_tb = True
        else:
            remap_type = TYPE_MAP

        # (2) On injecte toujours les directions UP/DOWN/LEFT/RIGHT
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

        # (3) Traitement spécial MK
        if is_mk:
            # 3.1) D’abord, INSÉRER START et COIN, tels quels
            for btn in layout.findall('button'):
                action = btn.get('function', '').upper().replace(' ', '_')
                phys = int(btn.get('physical'))

                if action == 'START':
                    port_key = f"{player_num}_PLAYER_START"
                    if port_key in ports_by_type:
                        port = ports_by_type[port_key]
                        # On génère <port type="P{player}_BUTTON{phys}">
                        # → BUT : le JOYCODE final sera “BUTTON{remap_type[phys]}”
                        add_button(inp, port, btn, es_map, player_num, phys, phys)
                    else:
                        logger.error(f"Missing port pour {port_key} (MK START)")
                    continue

                if action == 'COIN':
                    port_key = f"COIN_{player_num}"
                    if port_key in ports_by_type:
                        port = ports_by_type[port_key]
                        add_button(inp, port, btn, es_map, player_num, phys, phys)
                    else:
                        logger.error(f"Missing port pour {port_key} (MK COIN)")
                    continue

            # 3.2) Maintenant, on génère systématiquement BUTTON1…BUTTON6 dans cet ordre fixe :
            for button_name, possible_actions in BUTTON_TO_ACTION.items():
                btn_elem = None
                for action in possible_actions:
                    for b in layout.findall('button'):
                        func = b.get('function', '').upper().replace(' ', '_')
                        if func == action:
                            if b.get('color', '').lower() == 'black':
                                btn_elem = None
                            else:
                                btn_elem = b
                            break
                    if btn_elem is not None:
                        break

                if btn_elem is None:
                    continue

                phys = int(btn_elem.get('physical'))
                action_found = btn_elem.get('function', '').upper().replace(' ', '_')
                port_key = f"P{player_num}_{action_found}"
                if port_key not in ports_by_type:
                    logger.error(f"Missing port pour key {port_key} (phys={phys}, action={action_found})")
                    continue

                orig_port = ports_by_type[port_key]
                mask_val = orig_port.get('mask')
                def_val  = orig_port.get('defvalue')
                tag_val  = orig_port.get('tag')

                new_port_type = f"P{player_num}_{button_name}"
                new_port = ET.SubElement(
                    inp,
                    'port',
                    tag=tag_val,
                    type=new_port_type,
                    mask=mask_val,
                    defvalue=def_val
                )

                mapped_index = remap_type.get(phys, phys)
                seq = ET.SubElement(new_port, 'newseq', type='standard')
                seq.text = f"JOYCODE_{player_num}_BUTTON{mapped_index}"

            # Fin du traitement MK pour ce joueur
            continue

        # (4) Traitement “normal” (punch-kick, tributton, etc.)
        # ── Filtrage des ports du joueur (BUTTON_n + actions texte) ──
        player_ports = []
        for p_elem in ports:
            t = p_elem.get('type', '').upper()
            if t.startswith(f"P{player_num}_BUTTON"):
                player_ports.append(p_elem)
            else:
                for actions in BUTTON_TO_ACTION.values():
                    for action in actions:
                        if t == f"P{player_num}_{action}":
                            player_ports.append(p_elem)
                            break
                    else:
                        continue
                    break
        player_ports.sort(key=lambda p: int(p.get('mask')))
        # ──────────────────────────────────────────────────────────────

        for btn in layout.findall('button'):
            if btn.get('color', '').lower() == 'black':
                continue

            gb = btn.get('gameButton', '').upper()
            phys = int(btn.get('physical'))

            # (4.2) Boutons de combat A/B/X/Y/L1/R1/L2/R2
            if gb in ('A', 'B', 'X', 'Y', 'L1', 'R1', 'L2', 'R2'):
                port = None
                type_index = None

                if (name_lower in pk_games and pk_remap) or (name_lower in tb_games and tb_remap):
                    type_index = remap_type.get(phys, phys)

                    # ── On cherche BUTTON_n, puis l'action texte si besoin ──
                    port_key = f"P{player_num}_BUTTON_{type_index}"
                    port     = ports_by_type.get(port_key)

                    if port is None:
                        btn_key = f"BUTTON{type_index}"
                        actions = BUTTON_TO_ACTION.get(btn_key, [])
                        for action in actions:
                            port_key2 = f"P{player_num}_{action}"
                            if port_key2 in ports_by_type:
                                port = ports_by_type[port_key2]
                                break
                    # ───────────────────────────────────────────────────────────

                    if port is None:
                        logger.error(
                            f"Impossible de trouver un port pour phys={phys}, "
                            f"type_index={type_index} (jeu={game}, joueur={player_num})"
                        )
                        continue  # on skippe ce bouton car port manquant

                else:
                    if not player_ports:
                        logger.error(f"Pas assez de ports joueurs pour phys={phys}, gameButton={gb}")
                        continue
                    port = player_ports.pop(0)
                    parts = port.get('type').split('_')
                    try:
                        type_index = int(parts[2])
                    except (IndexError, ValueError):
                        logger.error(f"Format inattendu pour port {port.get('type')}")
                        continue

                # Au point où l'on en est, port et type_index sont définis
                add_button(inp, port, btn, es_map, player_num, type_index, phys)

            # (4.3) Bouton START (hors MK)
            elif gb == 'START':
                # Essayer le nom correct, puis l'ancien
                port_key = f"{player_num}_PLAYER_START"
                if port_key not in ports_by_type:
                    port_key = f"START_{player_num}"
                if port_key not in ports_by_type:
                    logger.error(f"Missing port pour START (clés testées: {player_num}_PLAYER_START et START_{player_num})")
                    continue
                port = ports_by_type[port_key]
                type_index = phys
                add_button(inp, port, btn, es_map, player_num, type_index, phys)

            # (4.4) Bouton COIN (hors MK)
            elif gb == 'COIN':
                port_key = f"COIN_{player_num}"
                if port_key not in ports_by_type:
                    logger.error(f"Missing port pour COIN_{player_num}")
                    continue
                port = ports_by_type[port_key]
                type_index = phys
                add_button(inp, port, btn, es_map, player_num, type_index, phys)

            else:
                logger.debug(f"Bouton XML non reconnu: gameButton={gb}, phys={phys}")
                continue

        # (5) Si tributton : réajuster newseq selon tb_remap
        if is_tb:
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

    # Ré-indentation du XML avant écriture
    indent(root)
    tree.write(cfg_path, encoding="utf-8", xml_declaration=True)
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