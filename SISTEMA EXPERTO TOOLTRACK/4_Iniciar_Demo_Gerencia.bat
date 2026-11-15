@echo off
title MIRA - DEMO para gerencia (datos de ejemplo, NO es la base real)
cd /d "%~dp0"
if not exist demo_mira.db (
  echo Creando la base de demostracion a partir de una COPIA de tooltrack.db ...
  python make_demo_database.py --source tooltrack.db --target demo_mira.db
  echo.
)
echo DEMO en http://127.0.0.1:8001  (usuario demo_manager, clave demo1234, abre la pestana Dashboard)
echo Los datos de historial son sinteticos. La base real no se toca.
echo Borra demo_mira.db si quieres regenerarla. Ctrl+C para detener.
echo.
set TOOLTRACK_DB_PATH=%~dp0demo_mira.db
python -m uvicorn main:app --host 127.0.0.1 --port 8001
pause
