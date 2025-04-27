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
#    Wave=1,RED,BLACK,200,5
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
  'RED':    (0,3072,0,   0,    True),
  'YELLOW': (0,0,   4095,4095,False),
  'BLUE':   (0,0,   4095,0,    True),
  'WHITE':  (3072,2048,3072,0, True),
  'LIME':   (3072,0,   1024,0, True),
  'GREEN':  (3072,0,   0,   2048,True),
  'BLACK':  (0,   0,   0,   0,    True),
  'BROWN':  (1024,0,   2048,2048,False),
  'ORANGE': (1024,4095,0,   0,    True),
  'CYAN':   (1024,0,   2048,0,    True),
  'PURPLE': (0,   3072,4095,0,    True),
  'VIOLET': (4095,3072,0,   4095, False),
  'GREY':   (1024,1024,1024,2048,False),
  'PINK':   (0,   3072,1024,0,    True),
}

# ----- Button map & slot list -----
button_map = {
  (1,'START'):(pca0,0),(1,'COIN'):(pca0,1),
  (1,'B1'):(pca0,2),(1,'B2'):(pca0,3),
  (1,'B3'):(pca1,0),(1,'B4'):(pca1,1),
  (1,'B5'):(pca1,2),(1,'B6'):(pca1,3),
  (1,'B7'):(pca2,0),(1,'B8'):(pca2,1),
  (1,'B9'):(pca2,2),(1,'B10'):(pca2,3),
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

def set_panel_colors(panels,list_str,save_default=False):
    items={}
    for pair in list_str.split(';'):
        if ':' in pair:
            b,c=pair.split(':',1)
            items[b.strip().upper()]=c.strip().upper()
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
