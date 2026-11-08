@echo off
rem Ouvre la fenêtre de lancement de séance (PC 2) : OBS, pont OBS, télécommande, voyants Serveur / Scène / OBS / Pont.
rem Les secrets (mot de passe obs-websocket, jeton) sont dans lancer-seance.local.bat (hors git).
cd /d "%~dp0"
if exist ".venv\Scripts\pythonw.exe" (
  start "" ".venv\Scripts\pythonw.exe" "tools\lanceur_seance.py"
) else (
  start "" pythonw "tools\lanceur_seance.py"
)
