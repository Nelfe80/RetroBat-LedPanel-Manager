# main.py — pilotage des boutons B1…B8 sur deux PCA9685

import sys, select, time, math
from machine import Pin, I2C

# ----- Sécurité maintenance (GP28) -----
maintenance_btn = Pin(28, Pin.IN, Pin.PULL_UP)
if maintenance_btn.value() == 0:
    print("Maintenance mode — script stopped.")
    raise SystemExit

# ----- Heartbeat LED (GP25) -----
led = Pin(25, Pin.OUT)
last_hb = time.ticks_ms()

# ----- REPL non bloquant -----
poll = select.poll()
poll.register(sys.stdin, select.POLLIN)

# ----- Constantes PCA9685 -----
PCA_ADDR    = 0x40
MODE1,MODE2 = 0x00,0x01
PRESCALE    = 0xFE
LED0_ON_L   = 0x06
LED0_ON_H   = 0x07
LED0_OFF_L  = 0x08
LED0_OFF_H  = 0x09
ALL_ON_L    = 0xFA
ALL_ON_H    = 0xFB
ALL_OFF_L   = 0xFC
ALL_OFF_H   = 0xFD
RESTART,SLEEP = 0x80,0x10
ALLCALL,OUTDRV = 0x01,0x04

# ----- Classe PCA9685 pour MicroPython -----
class PCA9685:
    def __init__(self, bus):
        self.i2c    = bus
        self.address = PCA_ADDR
        # reset
        self.set_all_pwm(0,0)
        self.write8(MODE2, OUTDRV)
        self.write8(MODE1, ALLCALL)
        time.sleep(0.005)
        m1 = self.read8(MODE1) & ~SLEEP
        self.write8(MODE1, m1)
        time.sleep(0.005)

    def set_pwm_freq(self, freq_hz):
        prescaleval = 25000000.0/(4096.0*freq_hz) - 1
        prescale    = int(math.floor(prescaleval+0.5))
        oldmode     = self.read8(MODE1)
        self.write8(MODE1, (oldmode&0x7F)|SLEEP)
        self.write8(PRESCALE, prescale)
        self.write8(MODE1, oldmode)
        time.sleep(0.005)
        self.write8(MODE1, oldmode|RESTART)

    def set_pwm(self, ch, on, off):
        self.write8(LED0_ON_L  +4*ch, on  &0xFF)
        self.write8(LED0_ON_H  +4*ch, on  >>8)
        self.write8(LED0_OFF_L +4*ch, off &0xFF)
        self.write8(LED0_OFF_H +4*ch, off >>8)

    def set_all_pwm(self, on, off):
        self.write8(ALL_ON_L, on  &0xFF); self.write8(ALL_ON_H, on  >>8)
        self.write8(ALL_OFF_L,off &0xFF); self.write8(ALL_OFF_H,off >>8)

    def write8(self, reg, val):
        self.i2c.writeto_mem(self.address, reg, bytes([val]))

    def read8(self, reg):
        return self.i2c.readfrom_mem(self.address, reg, 1)[0]

# ----- Instanciation de deux PCA9685 sur I2C0 et I2C1 -----
i2c0 = I2C(0, sda=Pin(0), scl=Pin(1), freq=400000)  # GP0/GP1
i2c1 = I2C(1, sda=Pin(2), scl=Pin(3), freq=400000)  # GP2/GP3

pca0 = PCA9685(i2c0); pca0.set_pwm_freq(1000)
pca1 = PCA9685(i2c1); pca1.set_pwm_freq(1000)

# ----- Tes définitions de couleur (B, G, V, R, INV) -----
color_map = {
  'RED':    (0, 3072,   0,    0,  True),
  'YELLOW': (0,    0,4095, 4095, False),
  'BLUE':   (0,    0,4095,    0, True),
  'WHITE':  (3072,2048,3072,  0, True),
  'LIME':   (3072,   0,1024,  0, True),
  'GREEN':  (3072,   0,   0,2048, True),
  'BLACK':  (0,      0,   0,   0, True),
  'BROWN':  (1024,   0,2048,2048, False),
  'ORANGE': (1024,4095,   0,   0, True),
  'CYAN':   (1024,   0,2048,   0, True),
  'PURPLE': (0,   3072,4095,   0, True),
  'VIOLET': (4095,3072,   0,4095, False),
  'GREY':   (1024,1024,1024,2048, False),
  'PINK':   (0,   3072,1024,   0, True),
}

# ----- Map boutons → (index PCA, slot) -----
# slot = 0→ch0–3, slot1→ch4–7, etc.
button_map = {
  (1,'B1'): (0,0), (1,'B2'): (0,1),
  (1,'B3'): (0,2), (1,'B4'): (0,3),
  (1,'B5'): (1,0), (1,'B6'): (1,1),
  (1,'B7'): (1,2), (1,'B8'): (1,3),
}

def set_button_color(panel, btn, col):
    key = col.upper()
    if (panel,btn) not in button_map or key not in color_map:
        print("Unknown panel/btn or color")
        return
    pca_idx, slot = button_map[(panel,btn)]
    base = slot*4
    b,g,v,r,inv = color_map[key]
    vals = (b,g,v,r)
    pca = pca0 if pca_idx==0 else pca1
    for i in range(4):
        raw = vals[i]
        duty = 4095-raw if inv else raw
        pca.set_pwm(base+i, 0, duty)
    print(f"OK: Panel {panel} {btn} → {col}")

print("Ready. SetButton=panel,name,color")

while True:
    # heartbeat
    now = time.ticks_ms()
    if time.ticks_diff(now,last_hb)>=1000:
        led.value(not led.value()); last_hb=now

    if not poll.poll(50):
        time.sleep(0.01); continue

    line = sys.stdin.readline().strip()
    if not line: continue

    if line.startswith("SetButton="):
        try:
            p,name,col = line.split("=",1)[1].split(",")
            set_button_color(int(p), name.upper(), col.upper())
        except Exception as e:
            print("Error:", e)
    else:
        print("Unknown command")
