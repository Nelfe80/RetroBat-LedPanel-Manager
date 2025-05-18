import serial
import time
import msvcrt
import re

# CONFIGURATION
PORT        = 'COM4'
BAUDRATE    = 115200
TIMEOUT     = 0.1
MAX_INDEX   = 625      # Nombre total de COL disponibles
STEP_UP     = 1
STEP_DOWN   = -1

# Sélection par défaut pour le contrôle RAW
panel_sel = 1
btn_sel = 'B3'  # reste disponible, mais non utilisé pour RAW panel

# Fonction d'envoi RAW pour un panel entier
def send_raw_panel(ser, panel, b, g, v, r, inv):
    cmd = f"SETPANELRAW={panel},{b},{g},{v},{r},{str(inv).lower()}"
    ser.reset_input_buffer()
    ser.write(cmd.encode('utf-8') + b'\n')
    print(f">>> {cmd}")

# Envoi d'une commande SetPanel=1,COLx
def send_col_command(ser, idx):
    cmd = f"SetPanel=1,COL{idx}"
    ser.reset_input_buffer()
    ser.write(cmd.encode('utf-8') + b'\n')
    print(f">>> {cmd}")

# Demande et parse GETPANELRAW pour mettre à jour l'état interne
def update_raw_panel_state(ser):
    ser.reset_input_buffer()
    ser.write(f"GETPANELRAW={panel_sel}\n".encode('utf-8'))
    while True:
        line = ser.readline().decode('utf-8', 'ignore').strip()
        if not line:
            continue
        print("Pico >", line)
        m = re.search(r'RAW panel → B=(\d+), G=(\d+), V=(\d+), R=(\d+), inv=(true|false)', line, re.IGNORECASE)
        if m:
            b, g, v, r = map(int, m.groups()[:4])
            inv = (m.group(5).lower() == 'true')
            print(f"(Mise à jour interne) → B={b}, G={g}, V={v}, R={r}, inv={inv}")
            return b, g, v, r, inv

def main():
    # Variables RAW locales (seront écrasées par GETPANELRAW)
    b_val, g_val, v_val, r_val = 0, 0, 0, 0
    inv_flag = False

    ser = serial.Serial(PORT, BAUDRATE, timeout=TIMEOUT)
    time.sleep(1)  # laisser le Pico démarrer

    print(f"Port série ouvert sur {PORT} à {BAUDRATE} bauds.")
    print("↑/↓ : COL index, w/x/c/v incr, q/s/d/f décr (inv auto) sur le panel. Ctrl+C pour quitter.\n")

    index = 1
    send_col_command(ser, index)
    # récupère tout de suite l'état RAW du panel pour initialiser
    b_val, g_val, v_val, r_val, inv_flag = update_raw_panel_state(ser)

    try:
        while True:
            if msvcrt.kbhit():
                key = msvcrt.getch()

                # Flèches (séquence 0xE0 + code)
                if key == b'\xe0':
                    arrow = msvcrt.getch()
                    if arrow == b'H':      # flèche Haut
                        index += STEP_UP
                    elif arrow == b'P':    # flèche Bas
                        index += STEP_DOWN
                    else:
                        continue

                    # wrap-around
                    if index < 1:          index = MAX_INDEX
                    elif index > MAX_INDEX: index = 1

                    # envoi SetPanel et MAJ RAW interne
                    send_col_command(ser, index)
                    b_val, g_val, v_val, r_val, inv_flag = update_raw_panel_state(ser)

                # Incrément / décrément RAW panel entier
                elif key in (b'w', b'x', b'c', b'v', b'q', b's', b'd', b'f'):
                    if key == b'w':      # incr Blue
                        b_val = min(b_val + 100, 4095); inv_flag = True
                    elif key == b'x':    # incr Green
                        g_val = min(g_val + 100, 4095); inv_flag = True
                    elif key == b'c':    # incr Violet
                        v_val = min(v_val + 100, 4095); inv_flag = True
                    elif key == b'v':    # incr Red
                        r_val = min(r_val + 100, 4095); inv_flag = True
                    elif key == b'q':    # decr Blue
                        b_val = max(b_val - 100,   0); inv_flag = True
                    elif key == b's':    # decr Green
                        g_val = max(g_val - 100,   0); inv_flag = True
                    elif key == b'd':    # decr Violet
                        v_val = max(v_val - 100,   0); inv_flag = True
                    elif key == b'f':    # decr Red
                        r_val = max(r_val - 100,   0); inv_flag = True

                    # Affichage et envoi RAW panel
                    print(f"RAW panel → B={b_val}, G={g_val}, V={v_val}, R={r_val}, inv={inv_flag}")
                    send_raw_panel(ser, panel_sel, b_val, g_val, v_val, r_val, inv_flag)

                else:
                    continue

            # Lecture non-bloquante des autres réponses Pico
            deadline = time.time() + 0.1
            while time.time() < deadline:
                line = ser.readline().decode('utf-8', 'ignore').strip()
                if line:
                    print("Pico >", line)

    except KeyboardInterrupt:
        print("\nInterrompu par l'utilisateur. Fermeture du port…")

    finally:
        ser.close()
        print("Au revoir.")

if __name__ == '__main__':
    main()
