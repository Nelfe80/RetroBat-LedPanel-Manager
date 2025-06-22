Works for Mame64 standalone
First step :
Run extract_and_generate_cfg.exe -> run to create your <game name>_inputs.cfg files in \bios\mame\cfg folder
This may take some time depending on the number of roms you have.
It generates a file that extracts all the inputs from your game so that we can then create the link between the game, the panel leds and your arcade button panel.
A log file is generated to help you understand why certain files don't work. If the rom doesn't launch on RetroBat Mame, the input file won't be generated because either files are missing from your rom's .zip or .7z folder, or the machine bios required by the game is missing.

Dans retroarch-core-options.cfg, pour configurer le Jaguar Controller Pro, il faut  :
virtualjaguar_alt_inputs = "enabled" si remapping
virtualjaguar_p1_retropad_x = "num_7"
virtualjaguar_p1_retropad_l1 = "num_8"
virtualjaguar_p1_retropad_r1 = "num_9"