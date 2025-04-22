import os
import subprocess
import time
import sys
from datetime import datetime

# --- Résolution des chemins ---
BASE_DIR = os.path.dirname(os.path.abspath(sys.argv[0]))
MAME_EXE = os.path.join(BASE_DIR, "..", "..", "emulators", "mame", "mame.exe")
DUMP_SCRIPT = os.path.join(BASE_DIR, "dump_inputs.lua")
ROMS_DIR = os.path.join(BASE_DIR, "..", "..", "roms", "mame")
CFG_OUTPUT_DIR = os.path.join(BASE_DIR, "..", "..", "bios", "mame", "cfg")
LOG_FILE = os.path.join(BASE_DIR, "inputgen.log")

# --- Logging ---
def log(msg):
    timestamp = datetime.now().strftime("[%Y-%m-%d %H:%M:%S]")
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"{timestamp} {msg}\n")
    print(msg)

# --- Génération pour une ROM ---
def generate_input_cfg(rom_name):
    output_file = os.path.join(CFG_OUTPUT_DIR, f"{rom_name}_inputs.cfg")
    if os.path.isfile(output_file):
        log(f"⏭️  {rom_name}_inputs.cfg déjà présent, ignoré.")
        return

    log(f"🎮 Traitement de {rom_name}...")

    try:
        args = [
            MAME_EXE, rom_name,
            "-skip_gameinfo", "-noreadconfig", "-nowindow",
            "-seconds_to_run", "2",
            "-autoboot_script", DUMP_SCRIPT,
            "-rompath", "..\\..\\bios;..\\..\\roms\\mame",
            "-samplepath", "..\\..\\bios\\mame\\samples",
            "-cfg_directory", "..\\..\\bios\\mame\\cfg",
            "-inipath", "..\\..\\bios\\mame\\ini",
            "-hashpath", "..\\..\\bios\\mame\\hash",
            "-state_directory", "..\\..\\saves\\mame\\states",
            "-nvram_directory", "..\\..\\saves\\mame\\nvram",
            "-homepath", "..\\..\\bios\\mame",
            "-ctrlrpath", "..\\..\\saves\\mame\\ctrlr"
        ]

        subprocess.run(args, cwd=os.path.dirname(MAME_EXE), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        dump_file = os.path.join(os.path.dirname(MAME_EXE), "dump_inputs.txt")
        if not os.path.isfile(dump_file):
            log(f"❌ Échec dump_inputs pour {rom_name} (fichier manquant)")
            return

        entries = []
        with open(dump_file, "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("FIELD:"):
                    parts = line.strip().split("|")
                    if len(parts) == 4:
                        label = parts[0][6:]
                        type_code = label.upper().replace(" ", "_")
                        tag = parts[2]
                        mask = parts[3]
                        entries.append({
                            "type": type_code,
                            "tag": tag,
                            "mask": mask,
                            "defvalue": mask
                        })

        with open(output_file, "w", encoding="utf-8") as out:
            out.write('<?xml version="1.0"?>\n')
            out.write('<mameconfig version="10">\n')
            out.write(f'    <system name="{rom_name}">\n')
            out.write('        <input>\n')
            for e in entries:
                out.write(f'            <port tag="{e["tag"]}" type="{e["type"]}" mask="{e["mask"]}" defvalue="{e["defvalue"]}"/>\n')
            out.write('        </input>\n')
            out.write('    </system>\n')
            out.write('</mameconfig>\n')

        log(f"✅ {rom_name}_inputs.cfg généré avec {len(entries)} entrées.")
        os.remove(dump_file)

    except Exception as e:
        log(f"⚠️ Erreur avec {rom_name} : {e}")

# --- Liste toutes les ROMs .zip/.7z ---
def main():
    log("🚀 Début du traitement")
    if not os.path.isdir(ROMS_DIR):
        log("❌ Dossier ROM introuvable.")
        return

    roms = []
    for f in os.listdir(ROMS_DIR):
        if f.endswith(".zip") or f.endswith(".7z"):
            roms.append(os.path.splitext(f)[0])

    if not roms:
        log("❌ Aucune ROM .zip ou .7z trouvée.")
        return

    for rom in roms:
        generate_input_cfg(rom)
        time.sleep(0.3)

    log("✅ Fin du traitement.\n")

if __name__ == "__main__":
    main()
