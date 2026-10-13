# TOOLTRACK

Sistema de escritorio para inventario y rastreabilidad de herramientas. La interfaz Tkinter consume una API FastAPI y los datos se guardan localmente en SQLite.

## Requisitos

- Windows
- Python 3.12 o posterior
- Git opcional

## Instalación local

Desde la carpeta del proyecto, en PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Configura una cuenta de administrador antes del primer inicio. Puedes definir variables en la terminal actual:

```powershell
$env:TOOLTRACK_ADMIN_USERNAME = "admin"
$env:TOOLTRACK_ADMIN_PASSWORD = "elige-una-clave-larga"
```

No uses los valores de ejemplo de `.env.example` como contraseña real. Las variables deben estar configuradas en la terminal que inicia la API.

## Ejecución

En una terminal, desde la carpeta del proyecto:

```powershell
python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

En otra terminal, activa el mismo entorno virtual y ejecuta:

```powershell
python .\tooltrack_tkinter_ux.py
```

La base `tooltrack.db` y los archivos CSV de auditoría/backup se generan localmente y se excluyen de Git. Haz copias de seguridad privadas antes de migrar o actualizar la aplicación.

## Seguridad

- No publiques bases de datos reales, exportaciones, archivos `.env` ni credenciales.
- La API está configurada para escuchar solo en `127.0.0.1`; no la expongas a una red sin implementar autenticación y autorización de API.
- Las contraseñas nuevas se guardan con PBKDF2-HMAC-SHA256 y sales aleatorias.
