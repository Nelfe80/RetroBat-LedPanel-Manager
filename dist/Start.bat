@echo off
setlocal enabledelayedexpansion
:: force good folder
cd /d %~dp0

:: Vérifier et fermer LPInputsPush.exe si en cours d'exécution
tasklist | find /I "LPInputsPush.exe" > NUL
if not errorlevel 1 taskkill /IM LPInputsPush.exe /F

:: Supprimer le dossier .temp contenant les anciennes instances du LedPanelManager
rd /s /q ".\.tmp"

:: Démarrer LPInputsPush.exe
start LPInputsPush.exe
timeout /t 1 /nobreak >NUL
tasklist | find /I "LPInputsPush.exe" > NUL
timeout /t 1 /nobreak >NUL

:: Démarrer retrobat.exe
start ..\..\retrobat.exe
timeout /t 2 /nobreak >NUL

:: Création d'un fichier VBScript temporaire pour changer le focus sur la fenêtre EmulationStation.
:: Créez le fichier VBScript pour modifier le focus de la fenêtre.
echo Set WshShell = CreateObject("WScript.Shell") > "%temp%\focus.vbs"
echo WshShell.AppActivate "EmulationStation" >> "%temp%\focus.vbs"
:: Lancer le script VBScript en mode asynchrone, de sorte que nous n'attendons pas sa conclusion.
start "" /B "cscript" "//nologo" "%temp%\focus.vbs"
:: Exécution du script VBScript et suppression du fichier temporaire.
timeout /t 1 /nobreak >NUL
del "%temp%\focus.vbs"

endlocal
