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

## Catálogo piloto (API)

El catálogo se está incorporando de forma aditiva, conservando las rutas antiguas de `/herramientas/`:

- `POST/GET /catalogo/tn`: alta y consulta de Tool Numbers.
- `POST/GET /catalogo/configuraciones`: alta y consulta de combinaciones TN + SIZE + TYPE.
- `POST /catalogo/configuraciones/{tn}/{size}/variantes`: alta de punzón o variante de matriz/CLEARANCE.
- `POST/GET /catalogo/herramientas`: alta y consulta de ejemplares físicos ligados a una variante de catálogo.
- `POST /inventario/herramientas`: alta oficial en una sola operación; crea o reutiliza la estructura TN/configuración/variante y el ejemplar físico de forma atómica.
- `PUT /inventario/herramientas/{id}`: edición atómica del ejemplar y de su vínculo con el catálogo; el ID físico no se puede cambiar.
- `DELETE /herramientas/{id}`: elimina el ejemplar físico y su vínculo de catálogo.
- `GET /catalogo/conciliacion/preview`: vista de solo lectura de cómo podrían conciliarse los registros heredados con el catálogo.

Cada configuración TN + SIZE comparte TYPE; permite una variante de punzón y distintas variantes de matriz por CLEARANCE. Cada variante puede tener varios ejemplares físicos, cada uno con su propio ID, LIFE, SO y ubicación. La interfaz presenta una sola entrada **Inventory**, una sola lista y un único formulario para registrar y editar. El campo TYPE cambia sus opciones según la categoría: geometrías para punzones/matrices, HOLDERS A–E para holders y los tipos estándar definidos para accesorios. La API valida las mismas listas y rechaza una geometría o tipo estándar asignado a otra categoría; al editar, permite conservar sin cambios un TYPE no estándar ya existente para no bloquear la corrección de otros datos heredados. Las ampliaciones al catálogo controlado de holders/accesorios quedan para el flujo de administración de catálogo. La lista muestra ID, TN, SIZE, TYPE, categoría, CLEARANCE, LIFE, SO, ubicación y vínculo al catálogo. Los administradores pueden editar o eliminar el registro seleccionado; la eliminación requiere confirmación y está reservada para corregir altas erróneas. Las bajas formales deberán conservar su motivo e historial en un flujo específico posterior.

Cuando una herramienta ya está referenciada por setups, kits, pendientes o movimientos del flujo, Inventory conserva la edición de datos físicos como LIFE/SO, pero bloquea cambios de identidad y ubicación manuales. La ubicación debe cambiarse mediante un movimiento registrado; una herramienta con historia de flujo no se puede eliminar desde la corrección de altas.

Al iniciar la API, SQLAlchemy crea las tablas nuevas que falten, pero los registros heredados no se convierten ni se enlazan automáticamente al catálogo. Los registros heredados permanecen visibles en Inventory y se marcan como **Not linked** hasta que exista un proceso de conciliación aprobado. El endpoint técnico `GET /catalogo/conciliacion/preview` es de solo lectura y no forma parte de la navegación normal de la interfaz. Los controles de escritura del catálogo se ocultan a usuarios no administradores en Tkinter; la API todavía no exige autorización por usuario en estos endpoints, por lo que debe seguir ejecutándose solo en localhost. Haz una copia de seguridad antes de iniciar una versión que incluye cambios de esquema.

## Flujo de setup, surtido y movimientos

La opción **Tool Dispensing** usa el flujo de la API `/workflow` y la misma base SQLite:

1. La pestaña **Surtido** guía una sola operación en orden: seleccionar máquina y carro, capturar los números de parte del setup actual y del nuevo, cargar ambos archivos `.stp`, comparar por TN + ángulo y revisar la lista de herramientas por surtir o matrices por validar.
2. Al iniciar el surtido, TOOLTRACK crea y asocia un kit a la máquina y al carro seleccionados, y cambia la vista de preparación por la lista de herramientas pendientes y los controles de escaneo. Si la máquina o el carro ya tienen un kit abierto, la interfaz ofrece retomarlo en vez de crear otro. Si la máquina aún no tiene un setup activo registrado, el archivo del setup actual cargado en el flujo se registra como su setup base. La comparación no requiere IDs ni confirmación.
3. En la vista del surtido, el operador escanea el ID físico de cada herramienta (o lo escribe para una prueba manual). TOOLTRACK busca ese ID en inventario, obtiene su TN y categoría, lo compara con el siguiente componente pendiente compatible y, si también está disponible en un rack y cumple clearance para matrices, lo asigna al carro y lo marca listo automáticamente. No se escanea primero el TN ni se escribe el ID en un segundo paso. Si el mismo TN/categoría aparece varias veces, asigna la herramienta al primer componente pendiente por estación. Las posiciones que permanecen no aparecen como tareas del operador. **Volver a la comparación** regresa a la preparación sin cerrar el kit.
4. Las acciones de pausa, reanudación, cancelación, QR y entrega se muestran junto al kit activo y se habilitan conforme a su estado. El setup activo solo cambia al generar el QR de un kit completo y confirmar su lectura en **Confirmar entrega**.
5. Los salientes permanecen en su máquina como pendientes desde el momento de la entrega y se marcan vencidos a las 72 horas. Recogerlos registra su destino directo a rack, TOGU o mantenimiento.
6. El módulo **Tool Tracking** (tarjeta propia del menú) concentra lo posterior al surtido. Busca por ID o TN y muestra dónde está cada herramienta (rack, carro, máquina con su número de parte, TOGU, mantenimiento o taller) junto con su historial. Al confirmar la entrega las herramientas quedan de inmediato en la máquina. Los regresos a rack se hacen en la pestaña **Guardar en rack** de Tool Tracking: eliges el rack de una lista y escaneas el ID de cada herramienta; solo se acepta en el rack al que pertenece. Los racks son localidades registradas y se eligen siempre de una lista desplegable; solo se escriben a mano al darlos de alta en **Ubicaciones**. Una máquina se puede marcar como **estándar (torreta)** en Ubicaciones: sus herramientas pendientes de recoger no se marcan VENCIDO a las 72 h, sino como alerta «ESTÁNDAR · reuso esperado». La pestaña **Retornos de máquina** envía herramientas a afilado o por daño. Incluye también **Pendientes de recoger** y **Ubicaciones**. Endpoints: `GET /workflow/tools/locate`, `GET /workflow/machines/{code}/tools` y `POST /workflow/machines/{code}/return`.

   **Estado de la herramienta:** `disponible`, `afilado`, `danado` o `baja`. Al enviar una herramienta a afilado, daño, TOGU, taller o mantenimiento queda marcada y no se puede surtir. Para regresarla a su rack, en **Guardar en rack** se elige el resultado (*Lista para uso* o *Dar de baja*). En **Buscar herramienta** se puede marcar dañada, liberar o dar de baja (`POST /workflow/tools/{id}/status`). Una herramienta dada de baja no se reactiva.

   **Entrega con QR de máquina:** cada máquina lleva pegado su QR (`TOOLTRACK-MACHINE:<código>`, se imprime desde Tool Tracking → Ubicaciones → «QR de la máquina seleccionada»). Para confirmar una entrega se escanea primero la máquina y luego el QR del kit; si el kit es de otra máquina se rechaza.

En el primer inicio se registran las máquinas predeterminadas `T-101` a `T-117` y los carros de surtido `C-01` a `C-03` (sin modificar ubicaciones ya existentes); racks, TOGUs, mantenimiento y taller deben registrarse antes de usarse. La API no proporciona autorización por rol para estas rutas; conserva el servicio en localhost y opera con la cuenta administradora mientras se define el control de permisos.

## Seguridad

- No publiques bases de datos reales, exportaciones, archivos `.env` ni credenciales.
- La API está configurada para escuchar solo en `127.0.0.1`; no la expongas a una red sin implementar autenticación y autorización de API.
- Las contraseñas nuevas se guardan con PBKDF2-HMAC-SHA256 y sales aleatorias.

## Máquinas

Cada máquina tiene perfil (tipo, línea, capacidad, espesor máximo) y estaciones numeradas con tamaño (A-E), posición (DENTRO/CENTRO/AFUERA), AutoIndex y estado (disponible/dañada). Se editan en el menú Máquinas. Las advertencias (espesor, estación no registrada, dañada, tamaño distinto) nunca bloquean el surtido.

## Números de parte

Menú Números de parte: programas por tipo de máquina (4I, 2I, 45 STA). Formato fijo tipo TYEH-1193217_01-SW, familia (lista administrable) y NPI/Sustaining. Se guardan los datos leídos del .stp y el archivo (hoja de setup). Si el número ya existe se reemplaza, con motivo obligatorio; todo queda en la Bitácora de cambios (qué estaciones cambiaron, quién y cuándo).

## Surtido desde programas guardados y traspaso

- El surtido ya no carga `.stp`: se elige máquina, se busca el número de parte (solo los del tipo de esa máquina), se compara y se elige un carro libre.
- Al elegir la máquina se muestra qué número de parte corre ("▶ Corriendo en ..."). Si el operador elige otro como "actual", se pide confirmar y motivo; la corrección queda en la bitácora de la máquina (`eventos_maquina`).
- Panel "Estado de carros": libre/surtiendo/pausado/listo, qué falta, y botones Retomar / Liberar. Pausar o cancelar exige motivo.
- Botón "Máquinas en tiempo real / Traspaso": lista qué corre en cada máquina y permite traspasar un número de parte a otra máquina del mismo tipo y vacía, con motivo obligatorio. Las herramientas montadas se mueven con él. Endpoints: `GET /workflow/machines/running`, `POST /workflow/machines/transfer`.
