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

# Configuration
BASE_DIR         = os.path.dirname(os.path.abspath(__file__))
ES_EVENT_FILE    = os.path.join(BASE_DIR, 'ESEvent.arg')
PANEL_CONFIG_INI = os.path.join(BASE_DIR, 'config.ini')
SYSTEMS_DIR      = os.path.join(BASE_DIR, 'systems')
BAUDRATE         = 115200
OFF_COLOR        = 'OFF'
DEFAULT_COLOR    = 'WHITE'

# Dynamically locate es_input.cfg
_current = os.getcwd()
_root    = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(_current))))
ES_INPUT_FILE = os.path.join(_root, 'emulationstation', '.emulationstation', 'es_input.cfg')

# Logging
logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

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
        data = f.read().strip()
    params = dict(p.split('=', 1) for p in data.split('&') if '=' in p)
    ev     = params.get('event','').strip('"').lower()
    system = params.get('param1','').strip('"').lower()
    raw2   = params.get('param2','').strip('"')
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
        self.system_layouts      = []    # Liste des layouts dispo pour le système courant
        self.current_layout_idx  = 0     # Index du layout actif

    def _load_system_layouts(self, system):
        """Charge tous les <layout> du XML système et prépare self.system_layouts."""
        # reconvertir 'c:\…\n64\…' en 'n64'
        system_name = os.path.basename(system) if os.path.sep in system else system
        xml_path = os.path.join(SYSTEMS_DIR, f"{system_name}.xml")
        self.system_layouts = []
        try:
            tree = ET.parse(xml_path)
            # on reconstruit phys_to_label comme dans _send_init_colors
            cfg = configparser.ConfigParser()
            with open(PANEL_CONFIG_INI, encoding='utf-8', errors='ignore') as f:
                cfg.read_file(f)
            phys_to_label = {}
            total = cfg.getint('Panel','buttons_count',fallback=0)
            for i in range(1, total+1):
                opt = f'panel_button_{i}'
                if cfg.has_option('Panel', opt):
                    phys_to_label[cfg.get('Panel', opt).rstrip(';')] = f"B{i}"
            for opt,lab in [('panel_button_select','COIN'),
                            ('panel_button_start','START'),
                            ('panel_button_joy','JOY')]:
                if cfg.has_option('Panel', opt):
                    phys_to_label[cfg.get('Panel', opt).rstrip(';')] = lab

            btn_cnt = cfg.getint('Panel','Player1_buttons_count',
                                 fallback=cfg.getint('Panel','buttons_count',fallback=0))
            players = cfg.getint('Panel','players_count',fallback=1)
            panels  = '|'.join(str(i) for i in range(1, players+1))

            for layout in tree.findall(f".//layout[@panelButtons='{btn_cnt}']"):
                name = layout.get('name') or layout.get('type')
                # recréer mapping bouton→couleur
                mapping = []
                if layout.find('joystick') is not None:
                    c = layout.find('joystick').get('color', DEFAULT_COLOR).upper()
                    mapping.append(f"JOY:{'OFF' if c=='BLACK' else c}")
                for btn in layout.findall('button'):
                    phys = btn.get('physical')
                    idn  = btn.get('id','').upper()
                    label = idn if idn in ('START','COIN','JOY') else phys_to_label.get(phys, f"B{phys}")
                    c = btn.get('color', DEFAULT_COLOR).upper()
                    mapping.append(f"{label}:{'OFF' if c=='BLACK' else c}")
                cmd = f"SetPanelColors={panels},{';'.join(mapping)},default=yes\n"
                self.system_layouts.append({'name': name, 'cmd': cmd})

            logger.info(f"Loaded {len(self.system_layouts)} layouts for system '{system_name}': "+
                        ', '.join(l['name'] for l in self.system_layouts))
        except Exception as e:
            logger.error(f"Error loading system layouts from {xml_path}: {e}")
            self.system_layouts = []

    def _send_current_layout(self):
        """Envoie le layout LED courant (self.current_layout_idx)."""
        if not self.system_layouts:
            logger.warning("No system_layouts to send")
            return
        entry = self.system_layouts[self.current_layout_idx]
        self.ser.write(entry['cmd'].encode('utf-8'))
        logger.info(f"➡ Switched to layout [{self.current_layout_idx}] '{entry['name']}'")

    def on_modified(self, event):
        # --- DEBUG ENTRY ---
        logger.info(f"on_modified triggered by: {event.src_path}")
        # Only handle our ES event file
        if event.is_directory or os.path.basename(event.src_path) != os.path.basename(ES_EVENT_FILE):
            logger.debug("Ignored: not the ESEvent.arg file")
            return

        # Parse the ES event
        ev, system, raw2 = parse_es_event(ES_EVENT_FILE)
        logger.info(f"Parsed ES_EVENT → ev='{ev}', system='{system}', raw2='{raw2}'")

        current = (ev, system, raw2)
        if current == self.last_es_event:
            logger.debug("Duplicate ES_EVENT, skipping")
            return
        self.last_es_event = current

        logger.info(f"Handling ES_EVENT: event={ev}, system={system}, raw2={raw2}")
        logger.debug(f"Listening={self.listening}, last_system={self.last_system}, lip_events_count={len(self.lip_events)}")

        if ev == 'system-selected' and self.listening:
            self.listening = False
            logger.info("■ Exit de jeu détecté (system-selected) — stopped listening to .lip events.")

        # 1) system-selected or initial blank → init system lighting
        if ev in ('system-selected', '') and system != self.last_system:
            logger.info("→ Branch: system-selected / initial")
            self._load_system_layouts(system)
            self.current_layout_idx = 0
            self._send_current_layout()
            self.last_system = system
            self.lip_events = []
            return

        # 2) game-selected → set up colors + reload layout (game-specific or fallback)
        if ev == 'game-selected':
            logger.info("→ Branch: game-selected")
            from urllib.parse import unquote
            formatted = os.path.normpath(unquote(raw2))
            if os.path.isfile(formatted):
                game = os.path.splitext(os.path.basename(formatted))[0]
            elif os.path.isdir(formatted):
                game = os.path.basename(formatted)
            else:
                game = os.path.splitext(os.path.basename(raw2))[0]
            logger.info(f"Resolved game name: '{game}'")

            # build phys_to_label mapping
            cfg = configparser.ConfigParser()
            try:
                with open(PANEL_CONFIG_INI, encoding='utf-8', errors='ignore') as f:
                    cfg.read_file(f)
            except Exception:
                cfg.read(PANEL_CONFIG_INI)
            phys_to_label = {}
            total = cfg.getint('Panel', 'buttons_count', fallback=0)
            for i in range(1, total + 1):
                opt = f'panel_button_{i}'
                if cfg.has_option('Panel', opt):
                    phys_to_label[cfg.get('Panel', opt).rstrip(';')] = f"B{i}"
            for opt, lab in [('panel_button_select','COIN'),
                             ('panel_button_start','START'),
                             ('panel_button_joy','JOY')]:
                if cfg.has_option('Panel', opt):
                    phys_to_label[cfg.get('Panel', opt).rstrip(';')] = lab

            btn_cnt = cfg.getint('Panel','Player1_buttons_count',
                        fallback=cfg.getint('Panel','buttons_count',fallback=0))
            # attempt game-specific layout
            btns = load_game_layout_buttons(system, game, btn_cnt, phys_to_label)
            if btns:
                logger.debug(f"Loaded game-specific layout: {btns}")
            else:
                logger.debug("No game-specific layout, falling back to system layout")
                btns = load_layout_buttons(system, btn_cnt, phys_to_label)
                logger.debug(f"System layout: {btns}")

            if btns:
                panels = '|'.join(str(i) for i in range(1, cfg.getint('Panel','players_count',fallback=1)+1))
                mapping = ';'.join(f"{lbl}:{clr}" for lbl, clr in btns)
                cmd = f"SetPanelColors={panels},{mapping},default=yes\n"
                try:
                    self.ser.write(cmd.encode('utf-8'))
                    logger.info(f"➡ Sent (game-selected): {cmd.strip()}")
                except Exception as e:
                    logger.error(f"Error sending game-selected colors: {e}")
            else:
                logger.warning(f"No layout found for system='{system}', game='{game}'")
                # re-init system colors
                self._send_init_colors(system)
                logger.debug("Re-applied system colors after game-selected")
            # clear any lip events when switching games
            self.lip_events = []
            logger.debug("Cleared lip_events on game-selected")
            return

        # 3) game-start → enable listening and load .lip macros
        if ev == 'game-start':
            logger.info("→ Branch: game-start")
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

        # 4) implicit game-end on new selection
        if self.listening and ev == 'game-selected':
            self.listening = False
            logger.info("■ Implicit game-end — stopped listening")
            return

        # 5) explicit game-end or exit
        if ev in ('game-end', 'exit') and self.listening:
            self.listening = False
            logger.info("■ Game ended — stopped listening")
            return



    def _send_init_colors(self, system):
        cfg = configparser.ConfigParser()
        try:
            with open(PANEL_CONFIG_INI, encoding='utf-8', errors='ignore') as f:
                cfg.read_file(f)
        except:
            cfg.read(PANEL_CONFIG_INI)

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
            evroot      = lip_tree.getroot().find('events')
            if evroot is None:
                logger.warning("No <events> in .lip")
                return
            lip_type    = evroot.get('type','')           # ex: "8-Button"
            lip_btn_cnt = int(lip_type.split('-',1)[0])
        except Exception as e:
            logger.error(f"Error parsing .lip header: {e}")
            return

        # 5) lire le panelButtons configuré dans config.ini
        cfg = configparser.ConfigParser()
        try:
            with open(PANEL_CONFIG_INI, encoding='utf-8', errors='ignore') as f:
                cfg.read_file(f)
        except Exception:
            cfg.read(PANEL_CONFIG_INI)
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

    HOTKEY_ID = 9      # id du bouton "hotkey" dans es_input.cfg
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
                logger.info(f"  • Panel {handler.panel_id} Hat moved → {ev.value}")
                # Hotkey + left/right hors game-start
                if not handler.listening and states[iid].get(HOTKEY_ID, False):
                    if x == -1:
                        handler.current_layout_idx = (handler.current_layout_idx - 1) % len(handler.system_layouts)
                        logger.info("    ↶ Hotkey+Hat-Left → previous layout")
                        handler._send_current_layout()
                    elif x == 1:
                        handler.current_layout_idx = (handler.current_layout_idx + 1) % len(handler.system_layouts)
                        logger.info("    ↷ Hotkey+Hat-Right → next layout")
                        handler._send_current_layout()

            # — Axis (D-pad en axis 0/1) —
            elif ev.type == JOYAXISMOTION:
                iid  = ev.instance_id
                axis = ev.axis
                val  = ev.value
                # On ne gère que l'axe 0 (gauche/droite)
                if axis == 0 and abs(val) > AXIS_THRESHOLD:
                    direction = 'Left' if val < 0 else 'Right'
                    logger.info(f"  • Panel {handler.panel_id} Axis0 moved → {direction} ({val:.2f})")
                    # Hotkey + left/right hors game-start
                    if not handler.listening and states[iid].get(HOTKEY_ID, False):
                        if direction == 'Left':
                            handler.current_layout_idx = (handler.current_layout_idx - 1) % len(handler.system_layouts)
                            logger.info("    ↶ Hotkey+Axis-Left → previous layout")
                        else:
                            handler.current_layout_idx = (handler.current_layout_idx + 1) % len(handler.system_layouts)
                            logger.info("    ↷ Hotkey+Axis-Right → next layout")
                        handler._send_current_layout()

        time.sleep(0.01)




def main():
    cfg = configparser.ConfigParser()
    try:
        with open(PANEL_CONFIG_INI, encoding='utf-8', errors='ignore') as f:
            cfg.read_file(f)
    except:
        cfg.read(PANEL_CONFIG_INI)

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
