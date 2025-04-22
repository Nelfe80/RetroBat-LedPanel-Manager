Le projet fonctionne en quatre grandes étapes, déclenchées à chaque sélection de jeu dans EmulationStation :

1. **Écoute de l’événement**  
   Un observer surveille le fichier `ESEvent.arg` généré par ES au moment où un jeu est « sélectionné » (event=game‑selected). Dès qu’il change, on lit son contenu pour en extraire :
   - l’émulateur (param1),  
   - le chemin de la ROM (param2) → on en retire le nom de base du fichier.

2. **Filtrage et chargement des configurations**  
   - Si l’émulateur n’est pas `mame`, on ignore l’événement.  
   - On lit `config.ini` pour récupérer :
     - le nombre de boutons physiques par joueur (`buttons_count`),  
     - le nombre de joueurs (`players_count`),  
     - d’éventuels mappages manuels (section `[Mapping]`).  
   - On parse `es_input.cfg` pour construire un dictionnaire `{contrôleur → (id, type)}` :  
     - `type` ∈ {`button`,`axis`,`key`}.

3. **Préparation du layout et des ports MAME**  
   - On ouvre `<jeu>_inputs.cfg` pour récupérer la liste des ports MAME (`<port type="…" mask="…" …>`).  
   - On détecte le nombre maximal de joueurs supporté par le jeu (via les ports `P1_…`, `P2_…`).  
   - On construit dynamiquement une **table de correspondance logique** (`gameButton` → regex sur `port.type`).  
   - On choisit dans `<jeu>.xml` le `<layout>` dont `panelButtons` correspond au nombre de boutons physiques configuré.

4. **Injection et écriture du nouveau `.cfg`**  
   - On sauvegarde l’ancien `<jeu>.cfg` en `<jeu>_backup.cfg`.  
   - On reconstruit la balise `<input>` du `.cfg` :  
     1. on y ajoute les directions joystick (`P1_UP`, `P1_DOWN`, etc.),  
     2. on y ajoute chaque bouton du layout :  
        - on cherche le port MAME correspondant (via regex),  
        - on crée `<port …>` avec `<newseq>` dont le contenu dépend du type ES (`JOYCODE_BUTTON#`, `KEYCODE_<TOUCHE>`, ou vide pour un axe).  
   - On formate (indentation) et on écrit le `.cfg` mis à jour.

Le résultat : au lancement de chaque jeu MAME, le fichier de configuration des touches est automatiquement adapté au panel arcade de l’utilisateur, tout en conservant une sauvegarde de l’original.
