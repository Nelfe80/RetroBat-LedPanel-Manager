# main.py — pilote complet non‐bloquant avec macros
# -----------------------------------------------------------------------------
# Exemples de commandes (un par fonction) :
#
# 1. PING
#    PING
#
# 2. SCAN
#    SCAN
#
# 3. SetButton
#    SetButton=1,B1,RED
#    SetButton=1,B2,BLUE,event=yes,2000
#
# 4. SetPanel
#    SetPanel=1,WHITE
#    SetPanel=1,LIME,event=yes,3000
#
# 5. SetPanelColors
#    SetPanelColors=1,START:RED;B1:GREEN
#    SetPanelColors=1|2,START:RED;B1:GREEN,default=yes
#    SetPanelColors=ALL,COIN:YELLOW;B2:ORANGE
#
# 6. RestorePanel
#    RestorePanel=1
#
# 7. GetPanel
#    GetPanel=1
#
# 8. FadePanel
#    FadePanel=1,RED,BLUE,2000
#    FadePanel=1,YELLOW,GREEN,1500,event=yes,1500
#
# 9. FadeButtons
#    FadeButtons=1,B1|B3,ORANGE,PURPLE,1000
#    FadeButtons=1,B2|B4,BLUE,WHITE,500,event=yes,500
#
# 10. BlinkButton
#    BlinkButton=1,B3,PINK,BLACK,300,300
#    BlinkButton=1,B4,YELLOW,BLACK,200,200,event=yes,4000
#
# 11. BlinkPanel
#    BlinkPanel=1,BLUE,BLACK,500,500
#
# 12. StopBlink
#    StopBlink
#
# 13. Wave
#    
#    Wave=ALL,GREEN,BLACK,100,10
#
# 14. Chase
#    Chase=1,ORANGE,100,3
#    Chase=ALL,PINK,50,2
#
# 15. Rainbow
#    Rainbow=1,150,4
#
# 16. MovingRainbow
#    MovingRainbow=1,200,6
# -----------------------------------------------------------------------------

import sys, select, time, math
from machine import Pin, SoftI2C

# ----- Maintenance & heartbeat -----
if Pin(28, Pin.IN, Pin.PULL_UP).value() == 0:
    print("Maintenance mode — stopped."); sys.exit()
led = Pin(25, Pin.OUT)
last_hb = time.ticks_ms()

# ----- Non-blocking REPL -----
poll = select.poll()
poll.register(sys.stdin, select.POLLIN)

# ----- PCA9685 constants -----
PCA_ADDR    = 0x40
MODE1,MODE2 = 0x00,0x01
PRESCALE    = 0xFE
LED0_ON_L   = 0x06; LED0_ON_H   = 0x07
LED0_OFF_L  = 0x08; LED0_OFF_H  = 0x09
ALL_ON_L    = 0xFA; ALL_ON_H    = 0xFB
ALL_OFF_L   = 0xFC; ALL_OFF_H   = 0xFD
RESTART,SLEEP = 0x80,0x10
ALLCALL,OUTDRV = 0x01,0x04

# ----- PCA9685 driver -----
class PCA9685:
    def __init__(self, bus):
        self.i2c,self.addr = bus,PCA_ADDR
        # all off
        for r in (ALL_ON_L,ALL_ON_H,ALL_OFF_L,ALL_OFF_H):
            self.i2c.writeto_mem(self.addr, r, b'\x00')
        # init
        self.i2c.writeto_mem(self.addr, MODE2, bytes([OUTDRV]))
        self.i2c.writeto_mem(self.addr, MODE1, bytes([ALLCALL]))
        time.sleep(0.005)
        m1 = self.i2c.readfrom_mem(self.addr, MODE1,1)[0] & ~SLEEP
        self.i2c.writeto_mem(self.addr, MODE1, bytes([m1]))
        time.sleep(0.005)

    def set_pwm_freq(self, hz):
        prescaleval = 25_000_000/(4096*hz) - 1
        pre = int(math.floor(prescaleval+0.5))
        old = self.i2c.readfrom_mem(self.addr, MODE1,1)[0]
        self.i2c.writeto_mem(self.addr, MODE1, bytes([(old&0x7F)|SLEEP]))
        self.i2c.writeto_mem(self.addr, PRESCALE, bytes([pre]))
        self.i2c.writeto_mem(self.addr, MODE1, bytes([old]))
        time.sleep(0.005)
        self.i2c.writeto_mem(self.addr, MODE1, bytes([old|RESTART]))

    def set_pwm(self, ch, on, off):
        base = LED0_ON_L + 4*ch
        for i,val in enumerate((on&0xFF, on>>8, off&0xFF, off>>8)):
            self.i2c.writeto_mem(self.addr, base+i, bytes([val]))

# ----- I2C buses & PCA9685 init -----
i2c0 = SoftI2C(sda=Pin(0), scl=Pin(1), freq=400000)
i2c1 = SoftI2C(sda=Pin(2), scl=Pin(3), freq=400000)
i2c2 = SoftI2C(sda=Pin(4), scl=Pin(5), freq=400000)
pca0,pca1,pca2 = [PCA9685(b) for b in (i2c0,i2c1,i2c2)]
for p in (pca0,pca1,pca2): p.set_pwm_freq(1000)

# ----- Colors (B,G,V,R,INV) -----
color_map = {
    'RED':    (1026, 4095, 0, 1026, True),
    'YELLOW': (3073, 4095, 0, 0, True),
    'BLUE':   (0, 0, 4095, 0, True),
    'WHITE':  (3073, 3073, 4095, 0, True),
    'LIME':   (4095,    0, 2047,    0, True),
    'GREEN':  (3073, 0, 0, 0, True),
    'LEMON':  (3073, 2047, 0, 0, True),
    'TURQUOISE':  (1026, 0, 1026, 0, True),
    'BLACK':  (0,    0,    0,    0, True),
    'BROWN':  (4095, 4095, 2047, 2047, True),
    'GOLD':   (2047, 3073, 1026, 1026, True),
    'ORANGE': (1026, 4095, 0, 0, True),
    'CYAN':   (3073, 0, 4095, 0, True),
    'PURPLE': (0, 2047, 4095, 0, True),
    'VIOLET': (2400, 2873, 3195, 2547, True),
    'GREY':   (2047, 2047, 3073, 1026, True),
    'GRAY':   (4095, 3073, 4095, 2047, True),
    'PINK':   (1026, 3073, 2047, 1026, True),
    'COL1': (0, 0, 0, 0, True),
    'COL2': (0, 0, 0, 1026, True),
    'COL3': (0, 0, 0, 2047, True),
    'COL4': (0, 0, 0, 3073, True),
    'COL5': (0, 0, 0, 4095, True),
    'COL6': (0, 0, 1026, 0, True),
    'COL7': (0, 0, 1026, 1026, True),
    'COL8': (0, 0, 1026, 2047, True),
    'COL9': (0, 0, 1026, 3073, True),
    'COL10': (0, 0, 1026, 4095, True),
    'COL11': (0, 0, 2047, 0, True),
    'COL12': (0, 0, 2047, 1026, True),
    'COL13': (0, 0, 2047, 2047, True),
    'COL14': (0, 0, 2047, 3073, True),
    'COL15': (0, 0, 2047, 4095, True),
    'COL16': (0, 0, 3073, 0, True),
    'COL17': (0, 0, 3073, 1026, True),
    'COL18': (0, 0, 3073, 2047, True),
    'COL19': (0, 0, 3073, 3073, True),
    'COL20': (0, 0, 3073, 4095, True),
    'COL21': (0, 0, 4095, 0, True),
    'COL22': (0, 0, 4095, 1026, True),
    'COL23': (0, 0, 4095, 2047, True),
    'COL24': (0, 0, 4095, 3073, True),
    'COL25': (0, 0, 4095, 4095, True),
    'COL26': (0, 1026, 0, 0, True),
    'COL27': (0, 1026, 0, 1026, True),
    'COL28': (0, 1026, 0, 2047, True),
    'COL29': (0, 1026, 0, 3073, True),
    'COL30': (0, 1026, 0, 4095, True),
    'COL31': (0, 1026, 1026, 0, True),
    'COL32': (0, 1026, 1026, 1026, True),
    'COL33': (0, 1026, 1026, 2047, True),
    'COL34': (0, 1026, 1026, 3073, True),
    'COL35': (0, 1026, 1026, 4095, True),
    'COL36': (0, 1026, 2047, 0, True),
    'COL37': (0, 1026, 2047, 1026, True),
    'COL38': (0, 1026, 2047, 2047, True),
    'COL39': (0, 1026, 2047, 3073, True),
    'COL40': (0, 1026, 2047, 4095, True),
    'COL41': (0, 1026, 3073, 0, True),
    'COL42': (0, 1026, 3073, 1026, True),
    'COL43': (0, 1026, 3073, 2047, True),
    'COL44': (0, 1026, 3073, 3073, True),
    'COL45': (0, 1026, 3073, 4095, True),
    'COL46': (0, 1026, 4095, 0, True),
    'COL47': (0, 1026, 4095, 1026, True),
    'COL48': (0, 1026, 4095, 2047, True),
    'COL49': (0, 1026, 4095, 3073, True),
    'COL50': (0, 1026, 4095, 4095, True),
    'COL51': (0, 2047, 0, 0, True),
    'COL52': (0, 2047, 0, 1026, True),
    'COL53': (0, 2047, 0, 2047, True),
    'COL54': (0, 2047, 0, 3073, True),
    'COL55': (0, 2047, 0, 4095, True),
    'COL56': (0, 2047, 1026, 0, True),
    'COL57': (0, 2047, 1026, 1026, True),
    'COL58': (0, 2047, 1026, 2047, True),
    'COL59': (0, 2047, 1026, 3073, True),
    'COL60': (0, 2047, 1026, 4095, True),
    'COL61': (0, 2047, 2047, 0, True),
    'COL62': (0, 2047, 2047, 1026, True),
    'COL63': (0, 2047, 2047, 2047, True),
    'COL64': (0, 2047, 2047, 3073, True),
    'COL65': (0, 2047, 2047, 4095, True),
    'COL66': (0, 2047, 3073, 0, True),
    'COL67': (0, 2047, 3073, 1026, True),
    'COL68': (0, 2047, 3073, 2047, True),
    'COL69': (0, 2047, 3073, 3073, True),
    'COL70': (0, 2047, 3073, 4095, True),
    'COL71': (0, 2047, 4095, 0, True),
    'COL72': (0, 2047, 4095, 1026, True),
    'COL73': (0, 2047, 4095, 2047, True),
    'COL74': (0, 2047, 4095, 3073, True),
    'COL75': (0, 2047, 4095, 4095, True),
    'COL76': (0, 3073, 0, 0, True),
    'COL77': (0, 3073, 0, 1026, True),
    'COL78': (0, 3073, 0, 2047, True),
    'COL79': (0, 3073, 0, 3073, True),
    'COL80': (0, 3073, 0, 4095, True),
    'COL81': (0, 3073, 1026, 0, True),
    'COL82': (0, 3073, 1026, 1026, True),
    'COL83': (0, 3073, 1026, 2047, True),
    'COL84': (0, 3073, 1026, 3073, True),
    'COL85': (0, 3073, 1026, 4095, True),
    'COL86': (0, 3073, 2047, 0, True),
    'COL87': (0, 3073, 2047, 1026, True),
    'COL88': (0, 3073, 2047, 2047, True),
    'COL89': (0, 3073, 2047, 3073, True),
    'COL90': (0, 3073, 2047, 4095, True),
    'COL91': (0, 3073, 3073, 0, True),
    'COL92': (0, 3073, 3073, 1026, True),
    'COL93': (0, 3073, 3073, 2047, True),
    'COL94': (0, 3073, 3073, 3073, True),
    'COL95': (0, 3073, 3073, 4095, True),
    'COL96': (0, 3073, 4095, 0, True),
    'COL97': (0, 3073, 4095, 1026, True),
    'COL98': (0, 3073, 4095, 2047, True),
    'COL99': (0, 3073, 4095, 3073, True),
    'COL100': (0, 3073, 4095, 4095, True),
    'COL101': (0, 4095, 0, 0, True),
    'COL102': (0, 4095, 0, 1026, True),
    'COL103': (0, 4095, 0, 2047, True),
    'COL104': (0, 4095, 0, 3073, True),
    'COL105': (0, 4095, 0, 4095, True),
    'COL106': (0, 4095, 1026, 0, True),
    'COL107': (0, 4095, 1026, 1026, True),
    'COL108': (0, 4095, 1026, 2047, True),
    'COL109': (0, 4095, 1026, 3073, True),
    'COL110': (0, 4095, 1026, 4095, True),
    'COL111': (0, 4095, 2047, 0, True),
    'COL112': (0, 4095, 2047, 1026, True),
    'COL113': (0, 4095, 2047, 2047, True),
    'COL114': (0, 4095, 2047, 3073, True),
    'COL115': (0, 4095, 2047, 4095, True),
    'COL116': (0, 4095, 3073, 0, True),
    'COL117': (0, 4095, 3073, 1026, True),
    'COL118': (0, 4095, 3073, 2047, True),
    'COL119': (0, 4095, 3073, 3073, True),
    'COL120': (0, 4095, 3073, 4095, True),
    'COL121': (0, 4095, 4095, 0, True),
    'COL122': (0, 4095, 4095, 1026, True),
    'COL123': (0, 4095, 4095, 2047, True),
    'COL124': (0, 4095, 4095, 3073, True),
    'COL125': (0, 4095, 4095, 4095, True),
    'COL126': (1026, 0, 0, 0, True),
    'COL127': (1026, 0, 0, 1026, True),
    'COL128': (1026, 0, 0, 2047, True),
    'COL129': (1026, 0, 0, 3073, True),
    'COL130': (1026, 0, 0, 4095, True),
    'COL131': (1026, 0, 1026, 0, True),
    'COL132': (1026, 0, 1026, 1026, True),
    'COL133': (1026, 0, 1026, 2047, True),
    'COL134': (1026, 0, 1026, 3073, True),
    'COL135': (1026, 0, 1026, 4095, True),
    'COL136': (1026, 0, 2047, 0, True),
    'COL137': (1026, 0, 2047, 1026, True),
    'COL138': (1026, 0, 2047, 2047, True),
    'COL139': (1026, 0, 2047, 3073, True),
    'COL140': (1026, 0, 2047, 4095, True),
    'COL141': (1026, 0, 3073, 0, True),
    'COL142': (1026, 0, 3073, 1026, True),
    'COL143': (1026, 0, 3073, 2047, True),
    'COL144': (1026, 0, 3073, 3073, True),
    'COL145': (1026, 0, 3073, 4095, True),
    'COL146': (1026, 0, 4095, 0, True),
    'COL147': (1026, 0, 4095, 1026, True),
    'COL148': (1026, 0, 4095, 2047, True),
    'COL149': (1026, 0, 4095, 3073, True),
    'COL150': (1026, 0, 4095, 4095, True),
    'COL151': (1026, 1026, 0, 0, True),
    'COL152': (1026, 1026, 0, 1026, True),
    'COL153': (1026, 1026, 0, 2047, True),
    'COL154': (1026, 1026, 0, 3073, True),
    'COL155': (1026, 1026, 0, 4095, True),
    'COL156': (1026, 1026, 1026, 0, True),
    'COL157': (1026, 1026, 1026, 1026, True),
    'COL158': (1026, 1026, 1026, 2047, True),
    'COL159': (1026, 1026, 1026, 3073, True),
    'COL160': (1026, 1026, 1026, 4095, True),
    'COL161': (1026, 1026, 2047, 0, True),
    'COL162': (1026, 1026, 2047, 1026, True),
    'COL163': (1026, 1026, 2047, 2047, True),
    'COL164': (1026, 1026, 2047, 3073, True),
    'COL165': (1026, 1026, 2047, 4095, True),
    'COL166': (1026, 1026, 3073, 0, True),
    'COL167': (1026, 1026, 3073, 1026, True),
    'COL168': (1026, 1026, 3073, 2047, True),
    'COL169': (1026, 1026, 3073, 3073, True),
    'COL170': (1026, 1026, 3073, 4095, True),
    'COL171': (1026, 1026, 4095, 0, True),
    'COL172': (1026, 1026, 4095, 1026, True),
    'COL173': (1026, 1026, 4095, 2047, True),
    'COL174': (1026, 1026, 4095, 3073, True),
    'COL175': (1026, 1026, 4095, 4095, True),
    'COL176': (1026, 2047, 0, 0, True),
    'COL177': (1026, 2047, 0, 1026, True),
    'COL178': (1026, 2047, 0, 2047, True),
    'COL179': (1026, 2047, 0, 3073, True),
    'COL180': (1026, 2047, 0, 4095, True),
    'COL181': (1026, 2047, 1026, 0, True),
    'COL182': (1026, 2047, 1026, 1026, True),
    'COL183': (1026, 2047, 1026, 2047, True),
    'COL184': (1026, 2047, 1026, 3073, True),
    'COL185': (1026, 2047, 1026, 4095, True),
    'COL186': (1026, 2047, 2047, 0, True),
    'COL187': (1026, 2047, 2047, 1026, True),
    'COL188': (1026, 2047, 2047, 2047, True),
    'COL189': (1026, 2047, 2047, 3073, True),
    'COL190': (1026, 2047, 2047, 4095, True),
    'COL191': (1026, 2047, 3073, 0, True),
    'COL192': (1026, 2047, 3073, 1026, True),
    'COL193': (1026, 2047, 3073, 2047, True),
    'COL194': (1026, 2047, 3073, 3073, True),
    'COL195': (1026, 2047, 3073, 4095, True),
    'COL196': (1026, 2047, 4095, 0, True),
    'COL197': (1026, 2047, 4095, 1026, True),
    'COL198': (1026, 2047, 4095, 2047, True),
    'COL199': (1026, 2047, 4095, 3073, True),
    'COL200': (1026, 2047, 4095, 4095, True),
    'COL201': (1026, 3073, 0, 0, True),
    'COL202': (1026, 3073, 0, 1026, True),
    'COL203': (1026, 3073, 0, 2047, True),
    'COL204': (1026, 3073, 0, 3073, True),
    'COL205': (1026, 3073, 0, 4095, True),
    'COL206': (1026, 3073, 1026, 0, True),
    'COL207': (1026, 3073, 1026, 1026, True),
    'COL208': (1026, 3073, 1026, 2047, True),
    'COL209': (1026, 3073, 1026, 3073, True),
    'COL210': (1026, 3073, 1026, 4095, True),
    'COL211': (1026, 3073, 2047, 0, True),
    'COL212': (1026, 3073, 2047, 1026, True),
    'COL213': (1026, 3073, 2047, 2047, True),
    'COL214': (1026, 3073, 2047, 3073, True),
    'COL215': (1026, 3073, 2047, 4095, True),
    'COL216': (1026, 3073, 3073, 0, True),
    'COL217': (1026, 3073, 3073, 1026, True),
    'COL218': (1026, 3073, 3073, 2047, True),
    'COL219': (1026, 3073, 3073, 3073, True),
    'COL220': (1026, 3073, 3073, 4095, True),
    'COL221': (1026, 3073, 4095, 0, True),
    'COL222': (1026, 3073, 4095, 1026, True),
    'COL223': (1026, 3073, 4095, 2047, True),
    'COL224': (1026, 3073, 4095, 3073, True),
    'COL225': (1026, 3073, 4095, 4095, True),
    'COL226': (1026, 4095, 0, 0, True),
    'COL227': (1026, 4095, 0, 1026, True),
    'COL228': (1026, 4095, 0, 2047, True),
    'COL229': (1026, 4095, 0, 3073, True),
    'COL230': (1026, 4095, 0, 4095, True),
    'COL231': (1026, 4095, 1026, 0, True),
    'COL232': (1026, 4095, 1026, 1026, True),
    'COL233': (1026, 4095, 1026, 2047, True),
    'COL234': (1026, 4095, 1026, 3073, True),
    'COL235': (1026, 4095, 1026, 4095, True),
    'COL236': (1026, 4095, 2047, 0, True),
    'COL237': (1026, 4095, 2047, 1026, True),
    'COL238': (1026, 4095, 2047, 2047, True),
    'COL239': (1026, 4095, 2047, 3073, True),
    'COL240': (1026, 4095, 2047, 4095, True),
    'COL241': (1026, 4095, 3073, 0, True),
    'COL242': (1026, 4095, 3073, 1026, True),
    'COL243': (1026, 4095, 3073, 2047, True),
    'COL244': (1026, 4095, 3073, 3073, True),
    'COL245': (1026, 4095, 3073, 4095, True),
    'COL246': (1026, 4095, 4095, 0, True),
    'COL247': (1026, 4095, 4095, 1026, True),
    'COL248': (1026, 4095, 4095, 2047, True),
    'COL249': (1026, 4095, 4095, 3073, True),
    'COL250': (1026, 4095, 4095, 4095, True),
    'COL251': (2047, 0, 0, 0, True),
    'COL252': (2047, 0, 0, 1026, True),
    'COL253': (2047, 0, 0, 2047, True),
    'COL254': (2047, 0, 0, 3073, True),
    'COL255': (2047, 0, 0, 4095, True),
    'COL256': (2047, 0, 1026, 0, True),
    'COL257': (2047, 0, 1026, 1026, True),
    'COL258': (2047, 0, 1026, 2047, True),
    'COL259': (2047, 0, 1026, 3073, True),
    'COL260': (2047, 0, 1026, 4095, True),
    'COL261': (2047, 0, 2047, 0, True),
    'COL262': (2047, 0, 2047, 1026, True),
    'COL263': (2047, 0, 2047, 2047, True),
    'COL264': (2047, 0, 2047, 3073, True),
    'COL265': (2047, 0, 2047, 4095, True),
    'COL266': (2047, 0, 3073, 0, True),
    'COL267': (2047, 0, 3073, 1026, True),
    'COL268': (2047, 0, 3073, 2047, True),
    'COL269': (2047, 0, 3073, 3073, True),
    'COL270': (2047, 0, 3073, 4095, True),
    'COL271': (2047, 0, 4095, 0, True),
    'COL272': (2047, 0, 4095, 1026, True),
    'COL273': (2047, 0, 4095, 2047, True),
    'COL274': (2047, 0, 4095, 3073, True),
    'COL275': (2047, 0, 4095, 4095, True),
    'COL276': (2047, 1026, 0, 0, True),
    'COL277': (2047, 1026, 0, 1026, True),
    'COL278': (2047, 1026, 0, 2047, True),
    'COL279': (2047, 1026, 0, 3073, True),
    'COL280': (2047, 1026, 0, 4095, True),
    'COL281': (2047, 1026, 1026, 0, True),
    'COL282': (2047, 1026, 1026, 1026, True),
    'COL283': (2047, 1026, 1026, 2047, True),
    'COL284': (2047, 1026, 1026, 3073, True),
    'COL285': (2047, 1026, 1026, 4095, True),
    'COL286': (2047, 1026, 2047, 0, True),
    'COL287': (2047, 1026, 2047, 1026, True),
    'COL288': (2047, 1026, 2047, 2047, True),
    'COL289': (2047, 1026, 2047, 3073, True),
    'COL290': (2047, 1026, 2047, 4095, True),
    'COL291': (2047, 1026, 3073, 0, True),
    'COL292': (2047, 1026, 3073, 1026, True),
    'COL293': (2047, 1026, 3073, 2047, True),
    'COL294': (2047, 1026, 3073, 3073, True),
    'COL295': (2047, 1026, 3073, 4095, True),
    'COL296': (2047, 1026, 4095, 0, True),
    'COL297': (2047, 1026, 4095, 1026, True),
    'COL298': (2047, 1026, 4095, 2047, True),
    'COL299': (2047, 1026, 4095, 3073, True),
    'COL300': (2047, 1026, 4095, 4095, True),
    'COL301': (2047, 2047, 0, 0, True),
    'COL302': (2047, 2047, 0, 1026, True),
    'COL303': (2047, 2047, 0, 2047, True),
    'COL304': (2047, 2047, 0, 3073, True),
    'COL305': (2047, 2047, 0, 4095, True),
    'COL306': (2047, 2047, 1026, 0, True),
    'COL307': (2047, 2047, 1026, 1026, True),
    'COL308': (2047, 2047, 1026, 2047, True),
    'COL309': (2047, 2047, 1026, 3073, True),
    'COL310': (2047, 2047, 1026, 4095, True),
    'COL311': (2047, 2047, 2047, 0, True),
    'COL312': (2047, 2047, 2047, 1026, True),
    'COL313': (2047, 2047, 2047, 2047, True),
    'COL314': (2047, 2047, 2047, 3073, True),
    'COL315': (2047, 2047, 2047, 4095, True),
    'COL316': (2047, 2047, 3073, 0, True),
    'COL317': (2047, 2047, 3073, 1026, True),
    'COL318': (2047, 2047, 3073, 2047, True),
    'COL319': (2047, 2047, 3073, 3073, True),
    'COL320': (2047, 2047, 3073, 4095, True),
    'COL321': (2047, 2047, 4095, 0, True),
    'COL322': (2047, 2047, 4095, 1026, True),
    'COL323': (2047, 2047, 4095, 2047, True),
    'COL324': (2047, 2047, 4095, 3073, True),
    'COL325': (2047, 2047, 4095, 4095, True),
    'COL326': (2047, 3073, 0, 0, True),
    'COL327': (2047, 3073, 0, 1026, True),
    'COL328': (2047, 3073, 0, 2047, True),
    'COL329': (2047, 3073, 0, 3073, True),
    'COL330': (2047, 3073, 0, 4095, True),
    'COL331': (2047, 3073, 1026, 0, True),
    'COL332': (2047, 3073, 1026, 1026, True),
    'COL333': (2047, 3073, 1026, 2047, True),
    'COL334': (2047, 3073, 1026, 3073, True),
    'COL335': (2047, 3073, 1026, 4095, True),
    'COL336': (2047, 3073, 2047, 0, True),
    'COL337': (2047, 3073, 2047, 1026, True),
    'COL338': (2047, 3073, 2047, 2047, True),
    'COL339': (2047, 3073, 2047, 3073, True),
    'COL340': (2047, 3073, 2047, 4095, True),
    'COL341': (2047, 3073, 3073, 0, True),
    'COL342': (2047, 3073, 3073, 1026, True),
    'COL343': (2047, 3073, 3073, 2047, True),
    'COL344': (2047, 3073, 3073, 3073, True),
    'COL345': (2047, 3073, 3073, 4095, True),
    'COL346': (2047, 3073, 4095, 0, True),
    'COL347': (2047, 3073, 4095, 1026, True),
    'COL348': (2047, 3073, 4095, 2047, True),
    'COL349': (2047, 3073, 4095, 3073, True),
    'COL350': (2047, 3073, 4095, 4095, True),
    'COL351': (2047, 4095, 0, 0, True),
    'COL352': (2047, 4095, 0, 1026, True),
    'COL353': (2047, 4095, 0, 2047, True),
    'COL354': (2047, 4095, 0, 3073, True),
    'COL355': (2047, 4095, 0, 4095, True),
    'COL356': (2047, 4095, 1026, 0, True),
    'COL357': (2047, 4095, 1026, 1026, True),
    'COL358': (2047, 4095, 1026, 2047, True),
    'COL359': (2047, 4095, 1026, 3073, True),
    'COL360': (2047, 4095, 1026, 4095, True),
    'COL361': (2047, 4095, 2047, 0, True),
    'COL362': (2047, 4095, 2047, 1026, True),
    'COL363': (2047, 4095, 2047, 2047, True),
    'COL364': (2047, 4095, 2047, 3073, True),
    'COL365': (2047, 4095, 2047, 4095, True),
    'COL366': (2047, 4095, 3073, 0, True),
    'COL367': (2047, 4095, 3073, 1026, True),
    'COL368': (2047, 4095, 3073, 2047, True),
    'COL369': (2047, 4095, 3073, 3073, True),
    'COL370': (2047, 4095, 3073, 4095, True),
    'COL371': (2047, 4095, 4095, 0, True),
    'COL372': (2047, 4095, 4095, 1026, True),
    'COL373': (2047, 4095, 4095, 2047, True),
    'COL374': (2047, 4095, 4095, 3073, True),
    'COL375': (2047, 4095, 4095, 4095, True),
    'COL376': (3073, 0, 0, 0, True),
    'COL377': (3073, 0, 0, 1026, True),
    'COL378': (3073, 0, 0, 2047, True),
    'COL379': (3073, 0, 0, 3073, True),
    'COL380': (3073, 0, 0, 4095, True),
    'COL381': (3073, 0, 1026, 0, True),
    'COL382': (3073, 0, 1026, 1026, True),
    'COL383': (3073, 0, 1026, 2047, True),
    'COL384': (3073, 0, 1026, 3073, True),
    'COL385': (3073, 0, 1026, 4095, True),
    'COL386': (3073, 0, 2047, 0, True),
    'COL387': (3073, 0, 2047, 1026, True),
    'COL388': (3073, 0, 2047, 2047, True),
    'COL389': (3073, 0, 2047, 3073, True),
    'COL390': (3073, 0, 2047, 4095, True),
    'COL391': (3073, 0, 3073, 0, True),
    'COL392': (3073, 0, 3073, 1026, True),
    'COL393': (3073, 0, 3073, 2047, True),
    'COL394': (3073, 0, 3073, 3073, True),
    'COL395': (3073, 0, 3073, 4095, True),
    'COL396': (3073, 0, 4095, 0, True),
    'COL397': (3073, 0, 4095, 1026, True),
    'COL398': (3073, 0, 4095, 2047, True),
    'COL399': (3073, 0, 4095, 3073, True),
    'COL400': (3073, 0, 4095, 4095, True),
    'COL401': (3073, 1026, 0, 0, True),
    'COL402': (3073, 1026, 0, 1026, True),
    'COL403': (3073, 1026, 0, 2047, True),
    'COL404': (3073, 1026, 0, 3073, True),
    'COL405': (3073, 1026, 0, 4095, True),
    'COL406': (3073, 1026, 1026, 0, True),
    'COL407': (3073, 1026, 1026, 1026, True),
    'COL408': (3073, 1026, 1026, 2047, True),
    'COL409': (3073, 1026, 1026, 3073, True),
    'COL410': (3073, 1026, 1026, 4095, True),
    'COL411': (3073, 1026, 2047, 0, True),
    'COL412': (3073, 1026, 2047, 1026, True),
    'COL413': (3073, 1026, 2047, 2047, True),
    'COL414': (3073, 1026, 2047, 3073, True),
    'COL415': (3073, 1026, 2047, 4095, True),
    'COL416': (3073, 1026, 3073, 0, True),
    'COL417': (3073, 1026, 3073, 1026, True),
    'COL418': (3073, 1026, 3073, 2047, True),
    'COL419': (3073, 1026, 3073, 3073, True),
    'COL420': (3073, 1026, 3073, 4095, True),
    'COL421': (3073, 1026, 4095, 0, True),
    'COL422': (3073, 1026, 4095, 1026, True),
    'COL423': (3073, 1026, 4095, 2047, True),
    'COL424': (3073, 1026, 4095, 3073, True),
    'COL425': (3073, 1026, 4095, 4095, True),
    'COL426': (3073, 2047, 0, 0, True),
    'COL427': (3073, 2047, 0, 1026, True),
    'COL428': (3073, 2047, 0, 2047, True),
    'COL429': (3073, 2047, 0, 3073, True),
    'COL430': (3073, 2047, 0, 4095, True),
    'COL431': (3073, 2047, 1026, 0, True),
    'COL432': (3073, 2047, 1026, 1026, True),
    'COL433': (3073, 2047, 1026, 2047, True),
    'COL434': (3073, 2047, 1026, 3073, True),
    'COL435': (3073, 2047, 1026, 4095, True),
    'COL436': (3073, 2047, 2047, 0, True),
    'COL437': (3073, 2047, 2047, 1026, True),
    'COL438': (3073, 2047, 2047, 2047, True),
    'COL439': (3073, 2047, 2047, 3073, True),
    'COL440': (3073, 2047, 2047, 4095, True),
    'COL441': (3073, 2047, 3073, 0, True),
    'COL442': (3073, 2047, 3073, 1026, True),
    'COL443': (3073, 2047, 3073, 2047, True),
    'COL444': (3073, 2047, 3073, 3073, True),
    'COL445': (3073, 2047, 3073, 4095, True),
    'COL446': (3073, 2047, 4095, 0, True),
    'COL447': (3073, 2047, 4095, 1026, True),
    'COL448': (3073, 2047, 4095, 2047, True),
    'COL449': (3073, 2047, 4095, 3073, True),
    'COL450': (3073, 2047, 4095, 4095, True),
    'COL451': (3073, 3073, 0, 0, True),
    'COL452': (3073, 3073, 0, 1026, True),
    'COL453': (3073, 3073, 0, 2047, True),
    'COL454': (3073, 3073, 0, 3073, True),
    'COL455': (3073, 3073, 0, 4095, True),
    'COL456': (3073, 3073, 1026, 0, True),
    'COL457': (3073, 3073, 1026, 1026, True),
    'COL458': (3073, 3073, 1026, 2047, True),
    'COL459': (3073, 3073, 1026, 3073, True),
    'COL460': (3073, 3073, 1026, 4095, True),
    'COL461': (3073, 3073, 2047, 0, True),
    'COL462': (3073, 3073, 2047, 1026, True),
    'COL463': (3073, 3073, 2047, 2047, True),
    'COL464': (3073, 3073, 2047, 3073, True),
    'COL465': (3073, 3073, 2047, 4095, True),
    'COL466': (3073, 3073, 3073, 0, True),
    'COL467': (3073, 3073, 3073, 1026, True),
    'COL468': (3073, 3073, 3073, 2047, True),
    'COL469': (3073, 3073, 3073, 3073, True),
    'COL470': (3073, 3073, 3073, 4095, True),
    'COL471': (3073, 3073, 4095, 0, True),
    'COL472': (3073, 3073, 4095, 1026, True),
    'COL473': (3073, 3073, 4095, 2047, True),
    'COL474': (3073, 3073, 4095, 3073, True),
    'COL475': (3073, 3073, 4095, 4095, True),
    'COL476': (3073, 4095, 0, 0, True),
    'COL477': (3073, 4095, 0, 1026, True),
    'COL478': (3073, 4095, 0, 2047, True),
    'COL479': (3073, 4095, 0, 3073, True),
    'COL480': (3073, 4095, 0, 4095, True),
    'COL481': (3073, 4095, 1026, 0, True),
    'COL482': (3073, 4095, 1026, 1026, True),
    'COL483': (3073, 4095, 1026, 2047, True),
    'COL484': (3073, 4095, 1026, 3073, True),
    'COL485': (3073, 4095, 1026, 4095, True),
    'COL486': (3073, 4095, 2047, 0, True),
    'COL487': (3073, 4095, 2047, 1026, True),
    'COL488': (3073, 4095, 2047, 2047, True),
    'COL489': (3073, 4095, 2047, 3073, True),
    'COL490': (3073, 4095, 2047, 4095, True),
    'COL491': (3073, 4095, 3073, 0, True),
    'COL492': (3073, 4095, 3073, 1026, True),
    'COL493': (3073, 4095, 3073, 2047, True),
    'COL494': (3073, 4095, 3073, 3073, True),
    'COL495': (3073, 4095, 3073, 4095, True),
    'COL496': (3073, 4095, 4095, 0, True),
    'COL497': (3073, 4095, 4095, 1026, True),
    'COL498': (3073, 4095, 4095, 2047, True),
    'COL499': (3073, 4095, 4095, 3073, True),
    'COL500': (3073, 4095, 4095, 4095, True),
    'COL501': (4095, 0, 0, 0, True),
    'COL502': (4095, 0, 0, 1026, True),
    'COL503': (4095, 0, 0, 2047, True),
    'COL504': (4095, 0, 0, 3073, True),
    'COL505': (4095, 0, 0, 4095, True),
    'COL506': (4095, 0, 1026, 0, True),
    'COL507': (4095, 0, 1026, 1026, True),
    'COL508': (4095, 0, 1026, 2047, True),
    'COL509': (4095, 0, 1026, 3073, True),
    'COL510': (4095, 0, 1026, 4095, True),
    'COL511': (4095, 0, 2047, 0, True),
    'COL512': (4095, 0, 2047, 1026, True),
    'COL513': (4095, 0, 2047, 2047, True),
    'COL514': (4095, 0, 2047, 3073, True),
    'COL515': (4095, 0, 2047, 4095, True),
    'COL516': (4095, 0, 3073, 0, True),
    'COL517': (4095, 0, 3073, 1026, True),
    'COL518': (4095, 0, 3073, 2047, True),
    'COL519': (4095, 0, 3073, 3073, True),
    'COL520': (4095, 0, 3073, 4095, True),
    'COL521': (4095, 0, 4095, 0, True),
    'COL522': (4095, 0, 4095, 1026, True),
    'COL523': (4095, 0, 4095, 2047, True),
    'COL524': (4095, 0, 4095, 3073, True),
    'COL525': (4095, 0, 4095, 4095, True),
    'COL526': (4095, 1026, 0, 0, True),
    'COL527': (4095, 1026, 0, 1026, True),
    'COL528': (4095, 1026, 0, 2047, True),
    'COL529': (4095, 1026, 0, 3073, True),
    'COL530': (4095, 1026, 0, 4095, True),
    'COL531': (4095, 1026, 1026, 0, True),
    'COL532': (4095, 1026, 1026, 1026, True),
    'COL533': (4095, 1026, 1026, 2047, True),
    'COL534': (4095, 1026, 1026, 3073, True),
    'COL535': (4095, 1026, 1026, 4095, True),
    'COL536': (4095, 1026, 2047, 0, True),
    'COL537': (4095, 1026, 2047, 1026, True),
    'COL538': (4095, 1026, 2047, 2047, True),
    'COL539': (4095, 1026, 2047, 3073, True),
    'COL540': (4095, 1026, 2047, 4095, True),
    'COL541': (4095, 1026, 3073, 0, True),
    'COL542': (4095, 1026, 3073, 1026, True),
    'COL543': (4095, 1026, 3073, 2047, True),
    'COL544': (4095, 1026, 3073, 3073, True),
    'COL545': (4095, 1026, 3073, 4095, True),
    'COL546': (4095, 1026, 4095, 0, True),
    'COL547': (4095, 1026, 4095, 1026, True),
    'COL548': (4095, 1026, 4095, 2047, True),
    'COL549': (4095, 1026, 4095, 3073, True),
    'COL550': (4095, 1026, 4095, 4095, True),
    'COL551': (4095, 2047, 0, 0, True),
    'COL552': (4095, 2047, 0, 1026, True),
    'COL553': (4095, 2047, 0, 2047, True),
    'COL554': (4095, 2047, 0, 3073, True),
    'COL555': (4095, 2047, 0, 4095, True),
    'COL556': (4095, 2047, 1026, 0, True),
    'COL557': (4095, 2047, 1026, 1026, True),
    'COL558': (4095, 2047, 1026, 2047, True),
    'COL559': (4095, 2047, 1026, 3073, True),
    'COL560': (4095, 2047, 1026, 4095, True),
    'COL561': (4095, 2047, 2047, 0, True),
    'COL562': (4095, 2047, 2047, 1026, True),
    'COL563': (4095, 2047, 2047, 2047, True),
    'COL564': (4095, 2047, 2047, 3073, True),
    'COL565': (4095, 2047, 2047, 4095, True),
    'COL566': (4095, 2047, 3073, 0, True),
    'COL567': (4095, 2047, 3073, 1026, True),
    'COL568': (4095, 2047, 3073, 2047, True),
    'COL569': (4095, 2047, 3073, 3073, True),
    'COL570': (4095, 2047, 3073, 4095, True),
    'COL571': (4095, 2047, 4095, 0, True),
    'COL572': (4095, 2047, 4095, 1026, True),
    'COL573': (4095, 2047, 4095, 2047, True),
    'COL574': (4095, 2047, 4095, 3073, True),
    'COL575': (4095, 2047, 4095, 4095, True),
    'COL576': (4095, 3073, 0, 0, True),
    'COL577': (4095, 3073, 0, 1026, True),
    'COL578': (4095, 3073, 0, 2047, True),
    'COL579': (4095, 3073, 0, 3073, True),
    'COL580': (4095, 3073, 0, 4095, True),
    'COL581': (4095, 3073, 1026, 0, True),
    'COL582': (4095, 3073, 1026, 1026, True),
    'COL583': (4095, 3073, 1026, 2047, True),
    'COL584': (4095, 3073, 1026, 3073, True),
    'COL585': (4095, 3073, 1026, 4095, True),
    'COL586': (4095, 3073, 2047, 0, True),
    'COL587': (4095, 3073, 2047, 1026, True),
    'COL588': (4095, 3073, 2047, 2047, True),
    'COL589': (4095, 3073, 2047, 3073, True),
    'COL590': (4095, 3073, 2047, 4095, True),
    'COL591': (4095, 3073, 3073, 0, True),
    'COL592': (4095, 3073, 3073, 1026, True),
    'COL593': (4095, 3073, 3073, 2047, True),
    'COL594': (4095, 3073, 3073, 3073, True),
    'COL595': (4095, 3073, 3073, 4095, True),
    'COL596': (4095, 3073, 4095, 0, True),
    'COL597': (4095, 3073, 4095, 1026, True),
    'COL598': (4095, 3073, 4095, 2047, True),
    'COL599': (4095, 3073, 4095, 3073, True),
    'COL600': (4095, 3073, 4095, 4095, True),
    'COL601': (4095, 4095, 0, 0, True),
    'COL602': (4095, 4095, 0, 1026, True),
    'COL603': (4095, 4095, 0, 2047, True),
    'COL604': (4095, 4095, 0, 3073, True),
    'COL605': (4095, 4095, 0, 4095, True),
    'COL606': (4095, 4095, 1026, 0, True),
    'COL607': (4095, 4095, 1026, 1026, True),
    'COL608': (4095, 4095, 1026, 2047, True),
    'COL609': (4095, 4095, 1026, 3073, True),
    'COL610': (4095, 4095, 1026, 4095, True),
    'COL611': (4095, 4095, 2047, 0, True),
    'COL612': (4095, 4095, 2047, 1026, True),
    'COL613': (4095, 4095, 2047, 2047, True),
    'COL614': (4095, 4095, 2047, 3073, True),
    'COL615': (4095, 4095, 2047, 4095, True),
    'COL616': (4095, 4095, 3073, 0, True),
    'COL617': (4095, 4095, 3073, 1026, True),
    'COL618': (4095, 4095, 3073, 2047, True),
    'COL619': (4095, 4095, 3073, 3073, True),
    'COL620': (4095, 4095, 3073, 4095, True),
    'COL621': (4095, 4095, 4095, 0, True),
    'COL622': (4095, 4095, 4095, 1026, True),
    'COL623': (4095, 4095, 4095, 2047, True),
    'COL624': (4095, 4095, 4095, 3073, True),
    'COL625': (4095, 4095, 4095, 4095, True),
}

# ----- Button map & slot list -----
button_map = {
  (1,'B1'):(pca0,0),(1,'B2'):(pca0,1),
  (1,'B3'):(pca0,2),(1,'B4'):(pca0,3),
  (1,'B5'):(pca1,0),(1,'B6'):(pca1,1),
  (1,'B7'):(pca1,2),(1,'B8'):(pca1,3),
  (1,'B9'):(pca2,0),(1,'B10'):(pca2,1),
  (1,'B11'):(pca2,2),(1,'B12'):(pca2,3),
}
# prepare slot_list for macros
slot_list=[]
for (panel,btn),(pca,slot) in button_map.items():
    bus_idx=[pca0,pca1,pca2].index(pca)
    idx=bus_idx*4 + slot
    slot_list.append((panel,btn,pca,slot,idx))
slot_list.sort(key=lambda x: x[4])
max_idx = max(x[4] for x in slot_list)

# ----- State & task queues -----
current_colors       = {}   # (panel,btn)->color
default_panel_colors = {}   # panel->{btn:color}
blink_tasks          = []
restore_tasks        = []
macro_tasks          = []

panel_id         = None
btn_count_global = None
coin_idx         = None
start_idx        = None
joy_idx          = None

# ----- PWM helpers -----
def compute_off(col):
    b,g,v,r,inv = color_map[col]
    vals=(b,g,v,r)
    return [(4095-x) if inv else x for x in vals]

def apply_raw(pca,slot,offs):
    base=slot*4
    for i,o in enumerate(offs):
        pca.set_pwm(base+i,0,int(o))

# init all BLACK
for (panel,btn),(pca,slot) in button_map.items():
    current_colors[(panel,btn)]='BLACK'
    apply_raw(pca,slot,compute_off('BLACK'))

# ----- Core command implementations -----
def set_button(panel,btn,col):
    key=col.upper()
    if (panel,btn) not in button_map or key not in color_map:
        print("Error unknown",panel,btn,col); return
    pca,slot=button_map[(panel,btn)]
    apply_raw(pca,slot,compute_off(key))
    current_colors[(panel,btn)]=key
    print(f"OK: {panel},{btn} → {key}")

raw_panel_state = {}  # stocke pour chaque panel [b,g,v,r,inv]

def set_button_raw(panel, btn, b, g, v, r, inv=True):
    """
    Affecte directement une couleur brute au bouton spécifié
    et met à jour raw_panel_state pour tout le panel.
    """
    key = (panel, btn.upper())
    if key not in button_map:
        print(f"Error: bouton {panel},{btn} inconnu.")
        return

    # calcule et applique PWM
    offs = [(4095 - x) if inv else x for x in (b, g, v, r)]
    pca, slot = button_map[key]
    apply_raw(pca, slot, offs)

    # on considère que tout le panel prend cette même valeur brute
    raw_panel_state[panel] = [b, g, v, r, inv]
    print(f"OK raw button {panel},{btn} → {(b, g, v, r, inv)}")

def set_panel_raw(panel, b, g, v, r, inv=True):
    """
    Affecte directement la même couleur brute à tous les boutons d'un panel
    en délégant à set_button_raw, et met à jour raw_panel_state.
    """
    for (p, btn), _ in button_map.items():
        if p == panel:
            set_button_raw(panel, btn, b, g, v, r, inv)

    # mise à jour de l'état brut du panel
    raw_panel_state[panel] = [b, g, v, r, inv]
    print(f"OK raw panel {panel} → {(b, g, v, r, inv)}")

def get_panel_raw(panel):
    """
    Affiche l'état brut (B,G,V,R,inv) du panel demandé.
    """
    vals = raw_panel_state.get(panel)
    if not vals:
        print(f"Error: panel {panel} sans données RAW")
        return
    b,g,v,r,inv = vals
    print(f"RAW panel → B={b}, G={g}, V={v}, R={r}, inv={inv}")

def set_panel(panel,col,event=False,duration=None):
    orig={}
    if event:
        for (p,btn) in button_map:
            if p==panel: orig[btn]=current_colors[(p,btn)]
    key=col.upper() if col.upper() in color_map else 'BLACK'
    for (p,btn),(pca,slot) in button_map.items():
        if p!=panel: continue
        if default_panel_colors.get(panel,{}).get(btn)=='BLACK':
            continue
        apply_raw(pca,slot,compute_off(key))
        current_colors[(p,btn)]=key
    print(f"OK: SetPanel panel {panel} → {key}")
    if event:
        restore_tasks.append({'type':'panel','panel':panel,'original':orig,'end':time.ticks_add(time.ticks_ms(),duration)})
    b_raw, g_raw, v_raw, r_raw, inv_raw = color_map[key]
    raw_panel_state[panel] = [b_raw, g_raw, v_raw, r_raw, inv_raw]

def set_panel_colors(panels,list_str,save_default=False):
    items={}
    for pair in list_str.split(';'):
        if ':' in pair:
            b,c=pair.split(':',1)
            items[b.strip().upper()]=c.strip().upper()
    
    # Remplacement COIN/START/JOY → B{idx}
    if coin_idx is not None and 'COIN' in items:
        items[f"B{coin_idx}"] = items.pop('COIN')
    if start_idx is not None and 'START' in items:
        items[f"B{start_idx}"] = items.pop('START')
    if joy_idx is not None and 'JOY' in items:
        items[f"B{joy_idx}"] = items.pop('JOY')
    
    for panel in panels:
        if save_default:
            default_panel_colors[panel]={}
        for (p,btn),(pca,slot) in button_map.items():
            if p!=panel: continue
            key=items.get(btn,'BLACK')
            if key not in color_map: key='BLACK'
            apply_raw(pca,slot,compute_off(key))
            current_colors[(p,btn)]=key
            if save_default:
                default_panel_colors[panel][btn]=key
    print(f"OK: SetPanelColors panels {panels}")

def restore_panel(panel):
    defs=default_panel_colors.get(panel,{})
    for (p,btn),(pca,slot) in button_map.items():
        if p!=panel: continue
        key=defs.get(btn,'BLACK')
        if key not in color_map: key='BLACK'
        apply_raw(pca,slot,compute_off(key))
        current_colors[(p,btn)]=key
        print(f"OK: {panel},{btn} → {key}")

def get_panel(panel):
    res={btn:col for (p,btn),col in current_colors.items() if p==panel}
    print("Panel",panel,"state:",res)

# ----- Fade processing -----
def fade_panel(panel,c1,c2,dur,event=False):
    off1,off2=compute_off(c1),compute_off(c2)
    steps=min(50,max(1,dur//20));delay=dur//steps
    orig={}
    if event:
        for (p,btn) in button_map:
            if p==panel: orig[btn]=current_colors[(p,btn)]
    for s in range(steps+1):
        t=s/steps; interm=[off1[i]+(off2[i]-off1[i])*t for i in range(4)]
        for (p,btn),(pca,slot) in button_map.items():
            if p!=panel: continue
            if default_panel_colors.get(panel,{}).get(btn)=='BLACK': continue
            apply_raw(pca,slot,interm)
        time.sleep_ms(delay)
    set_panel(panel,c2)
    if event:
        restore_tasks.append({'type':'panel','panel':panel,'original':orig,'end':time.ticks_add(time.ticks_ms(),dur)})

def fade_buttons(panel,btns,c1,c2,dur,event=False):
    off1,off2=compute_off(c1),compute_off(c2)
    steps=min(50,max(1,dur//20));delay=dur//steps
    orig={btn:current_colors[(panel,btn)] for btn in btns}
    for s in range(steps+1):
        t=s/steps; interm=[off1[i]+(off2[i]-off1[i])*t for i in range(4)]
        for btn in btns:
            key=(panel,btn)
            if key not in button_map: continue
            if default_panel_colors.get(panel,{}).get(btn)=='BLACK': continue
            pca,slot=button_map[key];apply_raw(pca,slot,interm)
        time.sleep_ms(delay)
    for btn in btns: set_button(panel,btn,c2)
    if event:
        restore_tasks.append({'type':'buttons','panel':panel,'original':orig,'end':time.ticks_add(time.ticks_ms(),dur)})

# ----- Blink processing -----
def add_blink(panel,btn,c1,c2,on_ms,off_ms,duration=None,event=False):
    now=time.ticks_ms()
    blink_tasks.append({'panel':panel,'btn':btn,'c1':c1,'c2':c2,'on':on_ms,'off':off_ms,
                        'end':time.ticks_add(now,duration) if duration else None,
                        'next':now,'state':False,'event':event})

def stop_all_effects():
    blink_tasks.clear()
    macro_tasks.clear()

def process_blinks():
    now=time.ticks_ms()
    for t in blink_tasks[:]:
        if t['end'] and time.ticks_diff(now,t['end'])>=0:
            blink_tasks.remove(t)
            if t['event']:
                # restore immediate
                if t['btn']:
                    orig={t['btn']:current_colors[(t['panel'],t['btn'])]}
                    restore_tasks.append({'type':'buttons','panel':t['panel'],'original':orig,'end':now})
                else:
                    orig={btn:current_colors[(t['panel'],btn)] for (p,btn) in button_map if p==t['panel']}
                    restore_tasks.append({'type':'panel','panel':t['panel'],'original':orig,'end':now})
            continue
        if time.ticks_diff(now,t['next'])>=0:
            t['state']=not t['state']
            col=t['c2'] if t['state'] else t['c1']
            if t['btn']:
                set_button(t['panel'],t['btn'],col)
            else:
                set_panel(t['panel'],col)
            delay=t['on'] if t['state'] else t['off']
            t['next']=time.ticks_add(now,delay)

def process_restores():
    now=time.ticks_ms()
    for r in restore_tasks[:]:
        if time.ticks_diff(now,r['end'])>=0:
            if r['type']=='buttons':
                for btn,col in r['original'].items():
                    set_button(r['panel'],btn,col)
            else:
                for btn,col in r['original'].items():
                    set_button(r['panel'],btn,col)
            restore_tasks.remove(r)

# ----- Macro scheduling & processing -----
def schedule_wave(panels,c1,c2,step,loops):
    macro_tasks.append({'type':'wave','panels':panels,'c1':c1,'c2':c2,
                        'step':step,'loops':loops,'loop':0,'shift':0,'next':time.ticks_ms()})

def schedule_chase(panels,c,step,loops):
    macro_tasks.append({'type':'chase','panels':panels,'c':c,'step':step,
                        'loops':loops,'loop':0,'pos':0,'last':None,'next':time.ticks_ms()})

def schedule_rainbow(panels,step,loops,moving=False):
    macro_tasks.append({'type':'rainbow','panels':panels,'step':step,'loops':loops,
                        'loop':0,'pos':0,'moving':moving,'next':time.ticks_ms()})

def process_macros():
    now=time.ticks_ms()
    cols=list(color_map.keys()); ncol=len(cols)
    for m in macro_tasks[:]:
        if now < m['next']: continue
        if m['type']=='wave':
            for (p,btn,pca,slot,idx) in slot_list:
                if p in m['panels']:
                    offs = compute_off(m['c1']) if idx==m['shift'] else compute_off(m['c2'])
                    apply_raw(pca,slot,offs)
            m['shift']+=1
            if m['shift']>max_idx:
                m['shift']=0; m['loop']+=1
            if m['loop']>=m['loops']:
                macro_tasks.remove(m)
            else:
                m['next']=time.ticks_add(now,m['step'])

        elif m['type']=='chase':
            if m['last'] is not None:
                lp=slot_list[m['last']]
                _p,btn,_pc,sl,_=lp
                df=default_panel_colors.get(_p,{}).get(btn,'BLACK')
                apply_raw(_pc,sl,compute_off(df))
            lp=slot_list[m['pos']]
            p,btn,pca,slot,_=lp
            if p in m['panels']:
                apply_raw(pca,slot,compute_off(m['c']))
            m['last']=m['pos']; m['pos']+=1
            if m['pos']>=len(slot_list):
                m['pos']=0; m['loop']+=1
            if m['loop']>=m['loops']:
                if m['last'] is not None:
                    lp=slot_list[m['last']]; _p,btn,_pc,sl,_=lp
                    df=default_panel_colors.get(_p,{}).get(btn,'BLACK')
                    apply_raw(_pc,sl,compute_off(df))
                macro_tasks.remove(m)
            else:
                m['next']=time.ticks_add(now,m['step'])

        elif m['type']=='rainbow':
            for (p,btn,pca,slot,idx) in slot_list:
                if p in m['panels']:
                    if default_panel_colors.get(p,{}).get(btn)=='BLACK':
                        continue
                    if m['moving']:
                        col=cols[(idx+m['pos'])%ncol]
                    else:
                        col=cols[m['pos']%ncol]
                    apply_raw(pca,slot,compute_off(col))
            m['pos']+=1
            if m['pos']>=ncol:
                m['pos']=0; m['loop']+=1
            if m['loop']>=m['loops']:
                macro_tasks.remove(m)
            else:
                m['next']=time.ticks_add(now,m['step'])

# ----- Main REPL loop -----
print("Commands: PING, SCAN, SetButton=, SetPanel=, SetPanelColors=, RestorePanel=, GetPanel=, FadePanel=, FadeButtons=, BlinkButton=, BlinkPanel=, StopBlink, Wave=, Chase=, Rainbow=, MovingRainbow=")

while True:
    # heartbeat & async tasks
    if time.ticks_diff(time.ticks_ms(), last_hb) >= 1000:
        led.value(not led.value()); last_hb=time.ticks_ms()
    process_blinks()
    process_restores()
    process_macros()

    if not poll.poll(50):
        time.sleep(0.01)
        continue

    line=sys.stdin.readline().strip()
    if not line:
        continue

    # on nouvelle commande, arrête blinks & macros
    stop_all_effects()

    # parse cmd
    if '=' in line:
        cmd,rest=line.split('=',1)
    else:
        cmd,rest=line,''
    cmd=cmd.strip().upper()
    args=[p.strip() for p in rest.split(',') if p.strip()]

    # event parsing
    event=False; dur=None
    low=[p.lower() for p in args]
    if 'event=yes' in low:
        idx=low.index('event=yes')
        if idx==len(args)-1 or not args[idx+1].isdigit():
            print("Error: duration required"); continue
        event=True; dur=int(args[idx+1])
        del args[idx:idx+2]

    try:
        if cmd == 'INIT':
            params = {}
            for part in args:
                if '=' in part:
                    k, v = part.split('=', 1)
                    params[k.strip().lower()] = v.strip()
            try:
                panel_id         = int(params['panel'])
                btn_count_global = int(params['count'])
                coin_idx         = int(params['select'])
                start_idx        = int(params['start'])
                joy_idx          = int(params['joy'])
                print("OK: INIT →",
                      f"panel_id={panel_id}, btn_count={btn_count_global},",
                      f"COIN→B{coin_idx}, START→B{start_idx}, JOY→B{joy_idx}")
            except KeyError as e:
                print(f"Error: param INIT manquant → {e}")
            except ValueError as e:
                print(f"Error: valeur INIT invalide → {e}")
            continue
        if cmd=='PING':
            led.value(not led.value()); print("PONG")

        elif cmd=='SCAN':
            print("Bus0:",i2c0.scan(),"Bus1:",i2c1.scan(),"Bus2:",i2c2.scan())

        elif cmd=='SETBUTTON':
            panel=int(args[0]); btn=args[1].upper(); col=args[2].upper()
            if event:
                orig={btn:current_colors[(panel,btn)]}
                restore_tasks.append({'type':'buttons','panel':panel,'original':orig,'end':time.ticks_add(time.ticks_ms(),dur)})
            set_button(panel,btn,col)
            
        elif cmd=='SETBUTTONRAW':
            # syntaxe : SETBUTTONRAW=panel,btn,B,G,V,R,inv
            try:
                panel = int(args[0])
                btn   = args[1].upper()
                b, g, v, r = map(int, args[2:6])
            except (IndexError, ValueError):
                print("Error: syntaxe SETBUTTONRAW=panel,btn,B,G,V,R,inv (B–R entiers)")
                continue
            inv_flag = False
            if len(args) >= 7 and args[6].lower() in ('true','1'):
                inv_flag = True
            set_button_raw(panel, btn, b, g, v, r, inv_flag)
            
        elif cmd=='SETPANELRAW':
            panel=int(args[0])
            b,g,v,r = map(int,args[1:5])
            inv = args[5].lower() in ('true','1') if len(args)>5 else True
            set_panel_raw(panel,b,g,v,r,inv)
        
        elif cmd=='GETPANELRAW':
            panel=int(args[0]); get_panel_raw(panel)
        
        elif cmd=='SETPANEL':
            panel=int(args[0]); col=args[1].upper()
            set_panel(panel,col,event=event,duration=dur)

        elif cmd=='SETPANELCOLORS':
            panels=[1] if args[0].upper()=='ALL' else [int(x) for x in args[0].split('|')]
            list_str=args[1]
            save_def=('default=yes' in low)
            set_panel_colors(panels,list_str,save_def)

        elif cmd=='RESTOREPANEL':
            restore_panel(int(args[0]))

        elif cmd=='GETPANEL':
            get_panel(int(args[0]))

        elif cmd=='FADEPANEL':
            panel,c1,c2,d=int(args[0]),args[1].upper(),args[2].upper(),int(args[3])
            fade_panel(panel,c1,c2,d,event=event)

        elif cmd=='FADEBUTTONS':
            panel=int(args[0]); btns=[b.upper() for b in args[1].split('|')]
            c1,c2,d=args[2].upper(),args[3].upper(),int(args[4])
            fade_buttons(panel,btns,c1,c2,d,event=event)

        elif cmd=='BLINKBUTTON':
            panel=int(args[0]); btn=args[1].upper()
            c1,c2=args[2].upper(),args[3].upper()
            onm,offm=int(args[4]),int(args[5])
            add_blink(panel,btn,c1,c2,onm,offm,duration=dur,event=event)

        elif cmd=='BLINKPANEL':
            panel=int(args[0]); c1,c2=args[1].upper(),args[2].upper()
            onm,offm=int(args[3]),int(args[4])
            add_blink(panel,None,c1,c2,onm,offm,duration=dur,event=event)

        elif cmd=='STOPBLINK':
            stop_all_effects(); print("OK: effects stopped")

        elif cmd=='WAVE':
            panels=[1] if args[0].upper()=='ALL' else [int(x) for x in args[0].split('|')]
            schedule_wave(panels,args[1].upper(),args[2].upper(),int(args[3]),int(args[4]))

        elif cmd=='CHASE':
            panels=[1] if args[0].upper()=='ALL' else [int(x) for x in args[0].split('|')]
            schedule_chase(panels,args[1].upper(),int(args[2]),int(args[3]))

        elif cmd=='RAINBOW':
            panels=[1] if args[0].upper()=='ALL' else [int(x) for x in args[0].split('|')]
            schedule_rainbow(panels,int(args[1]),int(args[2]),moving=False)

        elif cmd=='MOVINGRAINBOW':
            panels=[1] if args[0].upper()=='ALL' else [int(x) for x in args[0].split('|')]
            schedule_rainbow(panels,int(args[1]),int(args[2]),moving=True)

        else:
            print("Unknown command:",cmd)

    except Exception as e:
        print("Error:",e)
