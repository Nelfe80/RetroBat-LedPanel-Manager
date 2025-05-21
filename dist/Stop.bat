@echo off
setlocal enabledelayedexpansion

:: Vérifier et fermer LPEvents.exe si en cours d'exécution
tasklist | find /I "LPEvents.exe" > NUL
if not errorlevel 1 taskkill /IM LPEvents.exe /F

endlocal
