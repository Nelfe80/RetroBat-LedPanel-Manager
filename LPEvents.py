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

def parse_es_event(path):
    with open(path, encoding='cp1252') as f:
        data = f.read().strip()
    params = dict(p.split('=', 1) for p in data.split('&') if '=' in p)
    ev     = params.get('event', '').strip('"').lower()
    p1     = params.get('param1', '').strip('"')
    rom    = p1 if ev == 'game-start' else params.get('param2', '').strip('"')
    game   = os.path.splitext(os.path.basename(rom))[0]
    if ev == 'game-start':
        system = os.path.basename(os.path.dirname(p1)).lower()
    else:
        system = p1.lower()
    return ev, system, game

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

    def on_modified(self, event):
        if event.is_directory or os.path.basename(event.src_path) != os.path.basename(ES_EVENT_FILE):
            return
        ev, system, game = parse_es_event(ES_EVENT_FILE)
        current = (ev, system, game)
        if current == self.last_es_event:
            return
        self.last_es_event = current

        logger.info(f"ES_EVENT: event={ev}, system={system}, game={game}")

        if self.listening and ev == 'game-selected':
            self.listening = False
            logger.info("■ Implicit game-end on new selection — stopped listening.")
        if ev == 'game-start' and not self.listening:
            self.listening = True
            logger.info("▶ Game started — now listening to panel inputs…")
            self._load_lip(system, game)
            return
        if ev in ('game-end', 'exit') and self.listening:
            self.listening = False
            logger.info("■ Game ended — stopped listening.")
            return
        if ev in ('system-selected', 'game-selected', '') and system != self.last_system:
            self._send_init_colors(system)
            self.last_system = system

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
        # Try game-specific .lip
        lip_path = os.path.join(SYSTEMS_DIR, system, f"{game}.lip")
        logger.info(f"Looking for .lip at: {lip_path}")
        if not os.path.exists(lip_path):
            # fallback to system default .lip
            fallback = os.path.join(SYSTEMS_DIR, f"{system}.lip")
            if os.path.exists(fallback):
                logger.info(f"Game .lip not found, loading system default: {fallback}")
                lip_path = fallback
            else:
                logger.info(f"No .lip file for game or system (tried {lip_path} and {fallback})")
                return

        self.lip_events = []

        # Parse .lip for <events type="N-Button">
        try:
            lip_tree = ET.parse(lip_path)
            lip_root = lip_tree.getroot()
            evroot   = lip_root.find('events')
            if evroot is None:
                logger.warning("No <events> in .lip")
                return
            btn_type  = evroot.get('type','')
            btn_count = btn_type.split('-',1)[0]
        except Exception as e:
            logger.error(f"Error parsing .lip header: {e}")
            return

        # Load corresponding layout from system XML
        xml_path = os.path.join(SYSTEMS_DIR, f"{system}.xml")
        try:
            sys_tree = ET.parse(xml_path)
            layout   = sys_tree.find(f".//layout[@panelButtons='{btn_count}']")
            if layout is None:
                logger.warning(f"No layout[@panelButtons={btn_count}] in {system}.xml")
                return
        except Exception as e:
            logger.error(f"Error loading system XML: {e}")
            return

        # Build label→physical map
        label_to_phys = {'JOY': None}
        for btn in layout.findall('button'):
            phys   = int(btn.get('physical'))
            idname = btn.get('id','').upper()
            label  = idname if idname in ('START','COIN','JOY') else f"B{phys}"
            label_to_phys[label] = phys

        # Collect events
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
    pygame.init()
    pygame.joystick.init()
    joysticks = [pygame.joystick.Joystick(i) for i in range(pygame.joystick.get_count())]
    for js in joysticks:
        js.init()
        logger.info(f"Joystick initialized: {js.get_name()} (Instance {js.get_instance_id()})")

    states = {js.get_instance_id(): {i: False for i in range(js.get_numbuttons())}
              for js in joysticks}

    while True:
        for ev in pygame.event.get():
            if ev.type in (pygame.JOYBUTTONDOWN, pygame.JOYBUTTONUP):
                iid     = ev.instance_id
                pressed = (ev.type == pygame.JOYBUTTONDOWN)
                prev    = states[iid][ev.button]
                states[iid][ev.button] = pressed

                if handler.listening and prev != pressed:
                    panel    = handler.panel_id
                    physical = ev.button + 1
                    action   = 'pressed' if pressed else 'released'
                    logger.info(f"  • Panel {panel} Button {physical} {action}")

                    if not handler.lip_events:
                        logger.info("No .lip events to process")
                    for le in handler.lip_events:
                        logger.info(f"Checking .lip event {le}")
                        if le['id'] == ev.button and le['trigger'] == ('press' if pressed else 'release'):
                            if le['macro'] == 'set_panel_colors':
                                mapping = le['arg'].replace('CURRENT', str(panel))
                                cmd     = f"SetPanelColors={mapping}"
                            else:
                                panel_arg = le['arg'].replace('CURRENT', str(panel))
                                cmd       = f"RestorePanel={panel_arg}"
                            logger.info(f"➡ Executing macro command: {cmd}")
                            handler.ser.write((cmd + '\n').encode('utf-8'))
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
