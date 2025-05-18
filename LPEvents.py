"""
Led Panel Color Manager

Surveille les sélections de système via ESEvent.arg et envoie des commandes de couleur
au Pico pour allumer/éteindre les LEDs du panel selon les couleurs définies dans le XML du système,
via une unique commande RPL SetPanelColors.
"""
import os
import time
import logging
import configparser
import serial
import serial.tools.list_ports
import xml.etree.ElementTree as ET
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# ——— Configuration ———
BASE_DIR         = os.path.dirname(os.path.abspath(__file__))
ES_EVENT_FILE    = os.path.join(BASE_DIR, 'ESEvent.arg')
PANEL_CONFIG_INI = os.path.join(BASE_DIR, 'config.ini')
SYSTEMS_DIR      = os.path.join(BASE_DIR, 'systems')
BAUDRATE         = 115200
OFF_COLOR        = 'OFF'
DEFAULT_COLOR    = 'WHITE'

# ——— Logging ———
logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

# ——— Fonctions ———

def parse_es_event(path):
    """Lit ESEvent.arg et retourne le système sélectionné et le nom du jeu"""
    with open(path, encoding='utf-8') as f:
        data = f.read().strip()
    params = dict(p.split('=', 1) for p in data.split('&') if '=' in p)
    system = params.get('param1', '').strip('"').lower()
    rompath = params.get('param2', '').strip('"')
    game = os.path.splitext(os.path.basename(rompath))[0]
    return system, game


def load_layout_buttons(system, btn_count, phys_to_label):
    """Charge le XML du système et retourne liste de tuples (label, color),
       y compris la couleur du joystick."""
    xml_path = os.path.join(SYSTEMS_DIR, f"{system}.xml")
    if not os.path.exists(xml_path):
        logger.warning(f"No XML for system '{system}' at {xml_path}")
        return []

    try:
        tree = ET.parse(xml_path)
        for layout in tree.findall('.//layout'):
            if layout.get('panelButtons') == str(btn_count):
                result = []

                # 1) On prend d'abord la couleur du joystick
                joy_elem = layout.find('joystick')
                if joy_elem is not None:
                    jcol = joy_elem.get('color', DEFAULT_COLOR).upper()
                    if jcol == 'BLACK':
                        jcol = OFF_COLOR
                    result.append(('JOY', jcol))

                # 2) Puis on traite tous les boutons classiques
                for btn in layout.findall('button'):
                    phys = btn.get('physical')
                    id_attr = btn.get('id', '').upper()
                    # label: START/COIN ou via phys_to_label
                    if id_attr in ('START', 'COIN', 'JOY'):
                        label = id_attr
                    else:
                        label = phys_to_label.get(phys, f"B{phys}")

                    color = btn.get('color', DEFAULT_COLOR).upper()
                    if color == 'BLACK':
                        color = OFF_COLOR

                    result.append((label, color))

                return result

    except Exception as e:
        logger.error(f"Error parsing XML for {system}: {e}")
    return []


def find_pico():  # pragma: no cover
    """Scanne les ports série pour détecter le Pico MicroPython prêt."""
    logger.info("🔍 Scanning serial ports for Pico...")
    for port in serial.tools.list_ports.comports():
        try:
            ser = serial.Serial(port.device, BAUDRATE, timeout=1)
            ser.reset_input_buffer(); time.sleep(0.5)
            ser.write(b"ping\n"); time.sleep(0.2)
            data = ser.read_all().lower()
            ser.close()
            if b"pong" in data:
                logger.info(f"✅ Pico found on {port.device}")
                return port.device
        except Exception:
            continue
    logger.error("❌ No Pico detected.")
    return None


class LedEventHandler(FileSystemEventHandler):
    def __init__(self, ser, cfg):
        self.last_system = None
        self.ser = ser
        self.cfg = cfg

    def on_modified(self, event):
        if event.is_directory or os.path.basename(event.src_path) != os.path.basename(ES_EVENT_FILE):
            return
        system, _ = parse_es_event(ES_EVENT_FILE)
        if system == self.last_system:
            return

        # Lecture config.ini en UTF-8 safe
        cfg = configparser.ConfigParser()
        try:
            with open(PANEL_CONFIG_INI, encoding='utf-8', errors='ignore') as f:
                cfg.read_file(f)
        except Exception:
            cfg.read(PANEL_CONFIG_INI)

        # Construire mapping physique->label
        phys_to_label = {}
        total = cfg.getint('Panel', 'buttons_count', fallback=0)
        for i in range(1, total + 1):
            key = f'panel_button_{i}'
            if cfg.has_option('Panel', key):
                phys = cfg.get('Panel', key).rstrip(';')
                phys_to_label[phys] = f"B{i}"
        if cfg.has_option('Panel', 'panel_button_select'):
            phys = cfg.get('Panel', 'panel_button_select').rstrip(';')
            phys_to_label[phys] = 'COIN'
        if cfg.has_option('Panel', 'panel_button_start'):
            phys = cfg.get('Panel', 'panel_button_start').rstrip(';')
            phys_to_label[phys] = 'START'
        if cfg.has_option('Panel', 'panel_button_joy'):
            phys = cfg.get('Panel', 'panel_button_joy').rstrip(';')
            phys_to_label[phys] = 'JOY'

        # Panels actifs et btn_count
        players = cfg.getint('Panel', 'players_count', fallback=1)
        panels = '|'.join(str(i) for i in range(1, players + 1))
        btn_count = cfg.getint('Panel', 'Player1_buttons_count', fallback=cfg.getint('Panel', 'buttons_count', fallback=0))

        # Charger layout XML et construire mapping couleurs
        btns = load_layout_buttons(system, btn_count, phys_to_label)
        if not btns:
            logger.warning(f"No layout for {system} with {btn_count} buttons")
            self.last_system = system
            return

        mapping = ';'.join(f"{lbl}:{clr}" for lbl, clr in btns)
        cmd = f"SetPanelColors={panels},{mapping},default=yes\n"
        try:
            self.ser.write(cmd.encode('utf-8'))
            logger.info(f"➡ Sent: {cmd.strip()}")
        except Exception as e:
            logger.error(f"Error sending command: {e}")

        self.last_system = system


def main():
    # Charger config.ini UTF-8 safe
    cfg = configparser.ConfigParser()
    try:
        with open(PANEL_CONFIG_INI, encoding='utf-8', errors='ignore') as f:
            cfg.read_file(f)
    except Exception:
        cfg.read(PANEL_CONFIG_INI)

    # Détection et connexion au Pico
    pico_port = find_pico()
    if not pico_port:
        return
    ser = serial.Serial(pico_port, BAUDRATE, timeout=1)
    time.sleep(1)
    logger.info(f"Connected to Pico on {pico_port} @ {BAUDRATE}bps")

    # ———————————————————————————————
    #  Envoi de la commande INIT explicite au Pico
    #     INIT=panel=<n>,count=<nb>,select=<i>,start=<j>,joy=<k>
    # ———————————————————————————————
    # panel_id = 1 pour le joueur 1 (ajustez si besoin)
    panel_id = 1

    # btn_count = nombre de boutons physiques configurés pour P1
    btn_count = cfg.getint(
        'Panel',
        'Player1_buttons_count',
        fallback=cfg.getint('Panel', 'buttons_count', fallback=0)
    )

    # canaux physiques pour SELECT (COIN), START et JOY
    coin_ch  = cfg.get('Panel', 'panel_button_select').rstrip(';')
    start_ch = cfg.get('Panel', 'panel_button_start').rstrip(';')
    joy_ch   = cfg.get('Panel', 'panel_button_joy').rstrip(';')

    # Construction de la commande INIT
    init_cmd = (
        f"INIT=panel={panel_id},"
        f"count={btn_count},"
        f"select={coin_ch},"
        f"start={start_ch},"
        f"joy={joy_ch}\n"
    )

    try:
        ser.write(init_cmd.encode('utf-8'))
        logger.info(f"➡ Sent INIT: {init_cmd.strip()}")
    except Exception as e:
        logger.error(f"Failed to send INIT: {e}")
    # ———————————————————————————————

    # Afficher infos panel
    players = cfg.getint('Panel', 'players_count', fallback=1)
    logger.info(f"Config: players={players}, Player1_buttons_count={btn_count}")

    # Lancement du watcher
    observer = Observer()
    handler = LedEventHandler(ser, cfg)
    observer.schedule(handler, os.path.dirname(ES_EVENT_FILE), recursive=False)
    observer.start()
    logger.info("Led Panel Color Manager running...")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()
    ser.close()



if __name__ == '__main__':
    main()