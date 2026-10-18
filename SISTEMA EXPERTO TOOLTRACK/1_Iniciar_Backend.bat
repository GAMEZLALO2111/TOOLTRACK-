@echo off
title TOOLTRACK - Backend (no cierres esta ventana)
cd /d "%~dp0"
echo Iniciando backend de TOOLTRACK en http://127.0.0.1:8000
echo Deja esta ventana abierta mientras uses el sistema. Ctrl+C para detener.
echo.
python -m uvicorn main:app --host 127.0.0.1 --port 8000
pause
