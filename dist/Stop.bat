@echo off
setlocal enabledelayedexpansion

:: Vérifier et fermer LPInputsPush.exe si en cours d'exécution
tasklist | find /I "LPInputsPush.exe" > NUL
if not errorlevel 1 taskkill /IM LPInputsPush.exe /F

endlocal
