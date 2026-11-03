@echo off
title TOOLTRACK - Compilar aplicacion web
cd /d "%~dp0web"
set "NODE_DIR=%LOCALAPPDATA%\TOOLTRACK\node-v26.13.0-win-x64"
if not exist "%NODE_DIR%\node.exe" (
  echo No se encontro Node.js en %NODE_DIR%
  echo Instala Node.js LTS desde https://nodejs.org o copia la carpeta portatil en esa ruta.
  pause
  exit /b 1
)
set "PATH=%NODE_DIR%;%PATH%"
if not exist node_modules\react call npm.cmd install
call npm.cmd run build
echo.
echo Listo. Reinicia el backend y abre http://localhost:8000
pause
