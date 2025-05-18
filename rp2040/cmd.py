import serial, time

# Ouvre le port série
ser = serial.Serial('COM4', 115200, timeout=0.1)
time.sleep(1)              # laisse le Pico démarrer et imprimer son prompt

print("Tapez vos commandes (PING, SCAN, SetAllPanels=RED), puis Entrée. Ctrl+C pour quitter.")
try:
    while True:
        cmd = input("> ").strip()
        if not cmd:
            continue

        # 1) vider le buffer pour ne pas lire d'anciennes réponses
        ser.reset_input_buffer()

        # 2) envoyer la commande
        ser.write(cmd.encode('utf-8') + b'\n')

        # 3) lire toutes les lignes pendant 1 seconde
        deadline = time.time() + 1.0
        while time.time() < deadline:
            line = ser.readline().decode('utf-8', 'ignore').strip()
            if line:
                print("Pico >", line)

except KeyboardInterrupt:
    print("\nAu revoir !")
finally:
    ser.close()
