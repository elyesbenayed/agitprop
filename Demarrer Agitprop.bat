@echo off
chcp 65001 >nul
title Agitprop
cd /d "%~dp0"
if not exist "venv\Scripts\python.exe" (
  echo [ERREUR] Environnement Python introuvable ^(dossier venv^).
  echo Ouvrez une invite ici et lancez : python -m venv venv ^&^& venv\Scripts\pip install -r requirements.txt
  pause
  exit /b 1
)
echo.
echo   Agitprop - La France Humaniste
echo   Dashboard : http://localhost:8000
echo   Les envois programmes partent tant que cette fenetre reste ouverte.
echo   Fermez cette fenetre pour arreter.
echo.
start "" "http://localhost:8000"
"venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
pause
