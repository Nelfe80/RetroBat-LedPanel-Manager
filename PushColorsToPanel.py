# pico_led_controller.py
from machine import Pin, SoftI2C
import _thread
import time
import sys

# ----- PCA9685 constants -----
PCA9685_ADDRESS = 0x40
MODE1, MODE2    = 0x00, 0x01
PRESCALE        = 0xFE
LED0_ON_L       = 0x06
OUTDRV          = 0x04
ALLCALL         = 0x01
SLEEP           = 0x10
RESTART         = 0x80

# ----- Heartbeat on-board LED -----
def _heartbeat():
    led = Pin(25, Pin.OUT)
    while True:
        led.toggle()
        time.sleep(0.5)

# start heartbeat thread
_thread.start_new_thread(_heartbeat, ())

# ----- PCA9685 driver with I²C-fail safety -----
class PCA9685:
    def __init__(self, i2c, addr=PCA9685_ADDRESS):
        self.i2c = i2c
        self.addr = addr
        time.sleep(0.05)  # let device reset
        # try to turn all off
        try:
            self.set_all(0, 0)
        except OSError:
            # no device or timeout, skip
            return
        # configure MODE2 and MODE1
        try:
            self._write(MODE2, OUTDRV)
            self._write(MODE1, ALLCALL)
            time.sleep(0.005)
            m1 = self._read(MODE1) & ~SLEEP
            self._write(MODE1, m1)
            time.sleep(0.005)
        except OSError:
            # ignore I2C errors
            pass

    def set_pwm_freq(self, freq):
        try:
            prescaleval = 25_000_000 / 4096 / freq - 1
            prescale = int(prescaleval + 0.5)
            old = self._read(MODE1)
            self._write(MODE1, (old & 0x7F) | SLEEP)
            self._write(PRESCALE, prescale)
            self._write(MODE1, old)
            time.sleep(0.005)
            self._write(MODE1, old | RESTART)
        except OSError:
            pass

    def set_pwm(self, ch, on, off):
        base = LED0_ON_L + 4 * ch
        for i, val in enumerate((on & 0xFF, on >> 8, off & 0xFF, off >> 8)):
            try:
                self._write(base + i, val)
            except OSError:
                pass

    def set_all(self, on, off):
        for reg, val in ((0xFA, on), (0xFB, on >> 8), (0xFC, off), (0xFD, off >> 8)):
            try:
                self._write(reg, val & 0xFF)
            except OSError:
                pass

    def _write(self, reg, val):
        self.i2c.writeto_mem(self.addr, reg, bytes([val]))

    def _read(self, reg):
        return self.i2c.readfrom_mem(self.addr, reg, 1)[0]

# ----- instantiate PCA9685 boards -----
pins = [
    (0,1),(2,3),(4,5),
    (6,7),(8,9),(10,11),
    (12,13),(14,15),(16,17),
    (18,19),(20,21),(22,23)
]
pca_list = []
for sda, scl in pins:
    try:
        bus = SoftI2C(sda=Pin(sda), scl=Pin(scl), freq=400000)
        if PCA9685_ADDRESS in bus.scan():
            p = PCA9685(bus)
            try:
                p.set_pwm_freq(1000)
            except Exception:
                pass
        else:
            p = None
    except Exception:
        p = None
    pca_list.append(p)

# ----- panel/button map -----
panel_map = {}
for panel in range(1,5):
    base = (panel - 1) * 3
    names = ['START','SELECT','B1','B2'] + ['B3','B4','B5','B6'] + ['B7','B8','B9','B10']
    for idx, name in enumerate(names):
        card = base + (idx // 4)
        ch   = idx % 4
        panel_map[(panel, name)] = (card, ch)

# ----- color definitions -----
color_map = {
    'RED':    (0,    3072, 0,    0,    1),
    'YELLOW': (0,    0,    4095, 4095, 0),
    'BLUE':   (0,    0,    4095, 0,    1),
    'WHITE':  (3072, 2048, 3072, 0,    1),
    'LIME':   (3072, 0,    1024, 0,    1),
    'GREEN':  (3072, 0,    0,    2048, 1),
    'BLACK':  (0,    0,    0,    0,    1),
    'BROWN':  (1024, 0,    2048, 2048, 0),
    'ORANGE': (1024, 4095, 0,    0,    1),
    'CYAN':   (1024, 0,    2048, 0,    1),
    'PURPLE': (0,    3072, 4095, 0,    1),
    'VIOLET': (4095, 3072, 0,    4095, 0),
    'GREY':   (1024, 1024,1024,2048, 0),
    'PINK':   (0,    3072,1024, 0,    1),
}

# ----- apply button color -----
def apply_button(panel, btn, b, g, v, r, inv):
    try:
        key = btn.upper()
        idx, ch = panel_map[(panel, key)]
        pca = pca_list[idx]
        if pca:
            duty = lambda x: 4095-x if inv else x
            pca.set_pwm(ch, 0, duty([b,g,v,r][ch]))
    except Exception as e:
        print(f"[WARN] apply_button error: {e}")

# ----- main REPL loop -----
print("Ready. Send SetAllPanels=..., SetPanel[n]=..., SetButtons=...")
while True:
    try:
        line = input().strip()
    except (EOFError, KeyboardInterrupt):
        break
    if not line:
        continue
    try:
        if line.startswith('SetAllPanels='):
            parts = line.split('=',1)[1].strip('[]').split(';')
            for panel in range(1,5):
                for p in parts:
                    name, val = p.split(':',1)
                    if val.upper() in color_map:
                        b,g,v,r,inv = color_map[val.upper()]
                    else:
                        nums = val.split('(',1)[1].rstrip(')').split(',')
                        b,g,v,r,inv = map(int, nums)
                    apply_button(panel, name, b,g,v,r,inv)
        elif line.startswith('SetPanel['):
            n = int(line.split('[',1)[1].split(']')[0])
            parts = line.split('=',1)[1].strip('[]').split(';')
            for p in parts:
                name, val = p.split(':',1)
                if val.upper() in color_map:
                    b,g,v,r,inv = color_map[val.upper()]
                else:
                    nums = val.split('(',1)[1].rstrip(')').split(',')
                    b,g,v,r,inv = map(int, nums)
                apply_button(n, name, b,g,v,r,inv)
        elif line.startswith('SetButtons='):
            parts = line.split('=',1)[1].strip('[]').split(';')
            panel = None
            for p in parts:
                if p.upper().startswith('PANEL:'):
                    panel = int(p.split(':',1)[1])
            if not panel:
                print("[WARN] Missing PANEL: in SetButtons")
                continue
            for p in parts:
                if ':' not in p: continue
                name, val = p.split(':',1)
                if name.upper() == 'PANEL': continue
                if val.upper() in color_map:
                    b,g,v,r,inv = color_map[val.upper()]
                else:
                    nums = val.split('(',1)[1].rstrip(')').split(',')
                    b,g,v,r,inv = map(int, nums)
                apply_button(panel, name, b,g,v,r,inv)
        else:
            print("Unknown command:", line)
    except Exception as e:
        print(f"[ERROR] parsing '{line}': {e}")

# on exit, turn off all LEDs
for pca in pca_list:
    try:
        if pca:
            pca.set_all(0,0)
    except Exception:
        pass
print("Exiting.")
