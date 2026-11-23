# MIRA — Manufacturing Intelligence & Resource Administration

> TOOLTRACK es el nombre histórico del prototipo. Las carpetas, variables (`TOOLTRACK_*`), prefijos de QR (`TOOLTRACK-…`) y la base `tooltrack.db` conservan ese nombre a propósito: no se renombran para no romper etiquetas ya impresas ni instalaciones.

Sistema de administración de recursos de manufactura. API FastAPI + SQLite, interfaz web en React (`web/`) y una interfaz de escritorio Tkinter que se mantiene mientras cada módulo web se prueba.

## Visión

MIRA nació para controlar herramientas del Tool Crib y hoy cubre Tool Crib y Punzonado como piloto. La meta es un sistema digital configurable que administre otros recursos y operaciones de manufactura, sin reconstruir lo que ya funciona: la evolución es incremental.

- Centralizar información operativa que hoy vive en Excel y sistemas de requisiciones desconectados.
- Mantener trazabilidad completa de cada recurso y operación (quién, dónde, por qué, cuándo, con qué escaneo).
- Relacionar demanda, productos, programas, máquinas, recursos y disponibilidad.
- Generar alertas, KPIs, gráficas y análisis para decidir (dashboard de gerencia).

Principios de diseño: se separan siempre la definición técnica del recurso, el ejemplar físico individual, el requerimiento del número de parte, la asignación temporal a una máquina, el historial de uso, la solicitud de servicio y la sustitución; nada histórico se sobrescribe; todo se identifica por QR/escáner con ingreso manual como respaldo; cada acción lleva su usuario; una baja nunca es un clic, siempre exige motivo y aprobación.

## Qué hay construido (estado actual)

| Área | Qué hace |
| --- | --- |
| **Surtido (Kitting)** | Comparativo setup actual vs nuevo (el mismo TN es la misma herramienta aunque cambie el ángulo: se conserva y avisa «re-orientar»), kit por máquina/carro, escaneo, claridad de matrices ±0.005 in, *Refresh*, hoja impresa por rack, QR, entrega con QR de máquina, regresos y pendientes de recoger. Al escanear la última herramienta el kit queda solo en «Listo para entregar» (se entrega cuando la máquina lo necesite). Una máquina puede tener hasta 5 kits en fila, surtidos al mismo tiempo por la misma persona o por otra. |
| **Board (plan de producción)** | Una tarjeta por máquina con semáforo, cola, WO, producido, horas con el UPH en uso y quién está en la máquina. Sustituye al Excel del plan. Modo TV. |
| **Manufactura** | Ve el Board completo y Setup en solo lectura; toma su máquina (vence al fin del turno), captura producidas y pide el siguiente número de parte solo en la suya; reporta herramientas y problemas de programa. |
| **Requests (Tool Crib)** | Bandeja única: herramientas (afilado, inspección, reemplazo, daño, desempeño, retorno), problemas de programa y siguiente número de parte. Se elige quién atiende; se guardan tres tiempos (espera, atención, máquina sin herramienta). |
| **Incidentes, Mantenimiento y bajas** | Expediente de daño (operador, turno por reloj, causa, momento), tickets que solo abre Tool Crib y trabaja el técnico, y baja con motivo obligatorio y aprobación de otra persona. |
| **Tools** | Lista única y ficha por herramienta con toda su historia; etiquetas QR; guardado en rack. |
| **Setup (solo lectura para Manufactura)** | Catalog, Part numbers (con UPH editable por Planeación/Ingeniería, con motivo e historial) y Machines. |
| **Records** | Consulta y descarga a Excel de movimientos, solicitudes, incidentes, tickets, bajas, servicios, programas y auditoría. |
| **Dashboard de gerencia** | KPIs con tendencia, gráficas, hallazgos automáticos, costo del paro y PDF; más una base de demostración para presentarlo. |
| **Seguridad** | Roles y permisos aplicados en la API, sesión con token, bitácora de todo cambio. |

Pendientes conocidos: cargar la ubicación de las herramientas sin rack; imprimir etiquetas de herramientas, racks y TOGU; lectura con cámara (requiere HTTPS); conteo de golpes por ejemplar; integración con MES.

## Inicio rápido (sin asistente)

1. Doble clic en `1_Iniciar_Backend.bat` y deja esa ventana abierta (es el servidor).
2. Doble clic en `3_Abrir_Web.bat` (o abre `http://localhost:8000` en el navegador) e inicia sesión. Para la interfaz de escritorio antigua usa `2_Abrir_Interfaz.bat`.
3. Modo TV del tablero: `http://localhost:8000/#/tv`.

- Si cambias algo del frontend (`web/`), ejecuta `build_web.bat` y reinicia el backend. Si solo usas el sistema, no hace falta.
- Si olvidas una contraseña: en PowerShell, dentro de esta carpeta, `python create_admin.py` (crea o restablece un administrador). Otros usuarios se administran en la pestaña Users.
- Si dice "Cannot reach the TOOLTRACK server", el backend no está corriendo (paso 1). Si dice "Too many failed attempts", espera 5 minutos o reinicia el backend.
- Pruebas automáticas: `python -m unittest discover -s tests -p "test_*.py"` (deben terminar en OK).
- Haz copia de `tooltrack.db` antes de actualizar el sistema. El estado del proyecto y la hoja de ruta están en las secciones de abajo.

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

Cada configuración TN + SIZE comparte TYPE; permite una variante de punzón y distintas variantes de matriz por CLEARANCE. Cada variante puede tener varios ejemplares físicos, cada uno con su propio ID, LIFE, SO y ubicación. La interfaz presenta una sola lista de herramientas en **Tools** (el alta con *Register tool* y la edición desde la ficha de cada herramienta) y el **Catalog** aparte. El campo TYPE cambia sus opciones según la categoría: geometrías para punzones/matrices, HOLDERS A–E para holders y los tipos estándar definidos para accesorios. La API valida las mismas listas y rechaza una geometría o tipo estándar asignado a otra categoría; al editar, permite conservar sin cambios un TYPE no estándar ya existente para no bloquear la corrección de otros datos heredados. Las ampliaciones al catálogo controlado de holders/accesorios quedan para el flujo de administración de catálogo. La lista muestra ID, TN, SIZE, TYPE, categoría, CLEARANCE, LIFE, SO, ubicación y vínculo al catálogo. Los administradores pueden editar o eliminar el registro seleccionado; la eliminación requiere confirmación y está reservada para corregir altas erróneas. Las bajas formales deberán conservar su motivo e historial en un flujo específico posterior.

Cuando una herramienta ya está referenciada por setups, kits, pendientes o movimientos del flujo, Inventory conserva la edición de datos físicos como LIFE/SO, pero bloquea cambios de identidad y ubicación manuales. La ubicación debe cambiarse mediante un movimiento registrado; una herramienta con historia de flujo no se puede eliminar desde la corrección de altas.

Al iniciar la API, SQLAlchemy crea las tablas nuevas que falten, pero los registros heredados no se convierten ni se enlazan automáticamente al catálogo. Los registros heredados permanecen visibles en Inventory y se marcan como **Not linked** hasta que exista un proceso de conciliación aprobado. El endpoint técnico `GET /catalogo/conciliacion/preview` es de solo lectura y no forma parte de la navegación normal de la interfaz. Los controles de escritura del catálogo se ocultan según el rol en Tkinter y la API los exige por permiso. Haz una copia de seguridad antes de iniciar una versión que incluye cambios de esquema.

## Flujo de setup, surtido y movimientos

La opción **Tool Dispensing** usa el flujo de la API `/workflow` y la misma base SQLite:

1. La pestaña **Surtido** guía una sola operación en orden: seleccionar máquina y carro, capturar los números de parte del setup actual y del nuevo, cargar ambos archivos `.stp`, comparar por TN + ángulo y revisar la lista de herramientas por surtir o matrices por validar.
2. Al iniciar el surtido, TOOLTRACK crea y asocia un kit a la máquina y al carro seleccionados, y cambia la vista de preparación por la lista de herramientas pendientes y los controles de escaneo. Si la máquina o el carro ya tienen un kit abierto, la interfaz ofrece retomarlo en vez de crear otro. Si la máquina aún no tiene un setup activo registrado, el archivo del setup actual cargado en el flujo se registra como su setup base. La comparación no requiere IDs ni confirmación.
3. En la vista del surtido, el operador escanea el ID físico de cada herramienta (o lo escribe para una prueba manual). TOOLTRACK busca ese ID en inventario, obtiene su TN y categoría, lo compara con el siguiente componente pendiente compatible y, si también está disponible en un rack y cumple clearance para matrices, lo asigna al carro y lo marca listo automáticamente. No se escanea primero el TN ni se escribe el ID en un segundo paso. Si el mismo TN/categoría aparece varias veces, asigna la herramienta al primer componente pendiente por estación. Las posiciones que permanecen no aparecen como tareas del operador. **Volver a la comparación** regresa a la preparación sin cerrar el kit.
4. Las acciones de pausa, reanudación, cancelación, QR y entrega se muestran junto al kit activo y se habilitan conforme a su estado. El setup activo solo cambia al generar el QR de un kit completo y confirmar su lectura en **Confirmar entrega**.
5. Los salientes permanecen en su máquina como pendientes desde el momento de la entrega y se marcan vencidos a las 72 horas. Recogerlos registra su destino directo a rack, TOGU o mantenimiento.
6. El módulo **Tools** (`#/tools`, tarjeta propia del menú) concentra lo posterior al surtido. Una sola lista de todas las herramientas con búsqueda (acepta el código escaneado) y filtros; al abrir una fila se ve su **ficha**: identidad, dónde está (rack, carro, máquina con su número de parte, TOGU, mantenimiento o taller), acciones según el rol y su historia en secciones separadas (movimientos, solicitudes, incidentes, tickets de Mantenimiento, afilados con rebaje acumulado). Los roles sin acceso a servicio solo ven los movimientos. Al confirmar la entrega las herramientas quedan de inmediato en la máquina. Los regresos a rack se hacen con el botón *Store in rack* de Tools: eliges el rack de una lista y escaneas el ID de cada herramienta; solo se acepta en el rack al que pertenece. Los racks son localidades registradas y se eligen siempre de una lista desplegable; solo se escriben a mano al darlos de alta en **Ubicaciones**. Una máquina se puede marcar como **estándar (torreta)** en Ubicaciones: sus herramientas pendientes de recoger no se marcan VENCIDO a las 72 h, sino como alerta «ESTÁNDAR · reuso esperado». El botón *Returns* de Kitting reúne todo lo que regresa de una máquina: *Waiting for pickup* (pendientes de recoger) y *Return from a machine* (enviar a rack, afilado o mantenimiento). Los QR de máquinas y carros se imprimen con *QR labels* en Tools. Incluye también **Ubicaciones**. Endpoints: `GET /workflow/tools/locate`, `GET /workflow/tools/{id}/file` (la ficha), `GET /workflow/machines/{code}/tools` y `POST /workflow/machines/{code}/return`. Los enlaces antiguos `#/tracking` y `#/inventory` siguen funcionando.

   **Estado de la herramienta:** `disponible`, `afilado`, `danado` o `baja`. Al enviar una herramienta a afilado, daño, TOGU, taller o mantenimiento queda marcada y no se puede surtir. Para regresarla a su rack, en **Guardar en rack** se elige el resultado (*Lista para uso*). En la ficha de la herramienta se puede marcar dañada o liberar (`POST /workflow/tools/{id}/status`). **Dar de baja ya no es un clic**: es una propuesta que otra persona aprueba (ver 4f). Una herramienta dada de baja no se reactiva.

   **Entrega con QR de máquina:** cada máquina lleva pegado su QR (`TOOLTRACK-MACHINE:<código>`, se imprime desde Tool Tracking → Ubicaciones → «QR de la máquina seleccionada»). Para confirmar una entrega se escanea primero la máquina y luego el QR del kit; si el kit es de otra máquina se rechaza.

En el primer inicio se registran las máquinas predeterminadas `T-101` a `T-117` y los carros de surtido `C-01` a `C-03` (sin modificar ubicaciones ya existentes); racks, TOGUs, mantenimiento y taller deben registrarse antes de usarse. Todas las rutas exigen sesión y permiso por rol (ver Seguridad).

## Aplicación web (en construcción)

Frontend en `web/` (React + TypeScript + Vite). El backend lo sirve en `http://localhost:8000/` y usa la misma API y los mismos roles que la interfaz Tkinter, que se mantiene hasta que cada módulo web esté probado.

- Compilar: `build_web.bat` (usa Node.js portátil en `%LOCALAPPDATA%\TOOLTRACK\node-v26.13.0-win-x64`; `node_modules` vive en `%LOCALAPPDATA%\TOOLTRACK\web_node_modules`, enlazado con una unión, para que OneDrive no lo sincronice). Después reinicia el backend. Desarrollo: `npm.cmd run dev` dentro de `web/` (puerto 5173 con proxy a la API).
- Módulos listos: Login, Sequence Board (tarjetas con semáforo, cola por máquina, WO, producido, operadores en la máquina, edición, orden, modo TV en `#/tv` con desplazamiento automático), **Dashboard** (`#/insights`), Usuarios, **Kitting completo**, **Tools** (`#/tools`), **Catalog** (`#/catalog`), **Records** (`#/records`), **Maintenance** (`#/maintenance`: tickets y bajas por aprobar), **Part numbers** (`#/programs`), **Machines** (`#/machines`: estado en tiempo real, ficha, estaciones y traspaso entre máquinas compatibles) **Manufacturing** (`#/manufacturing`: máquina, número de parte, datos técnicos, estaciones y herramientas, plan de la máquina, solicitudes a Tool Crib) y **Requests** (`#/requests`: cola de Tool Crib). Máquinas conserva administración avanzada en Tkinter; Audit Log e importar/exportar CSV de inventario siguen solo en Tkinter.
- Permiso nuevo `tools.store` (guardar en rack): operador, supervisor, técnico de mantenimiento y admin. El técnico de mantenimiento ve Maintenance, Tools y Catalog.
- Interfaz en inglés, adaptable a PC, tablet y celular. La sesión se guarda en `sessionStorage` (se pierde al cerrar la pestaña) y dura 12 h.
- Los estados y marcas que devuelve el backend siguen en español; `web/src/labels.ts` los traduce para mostrarlos.

## Seguridad

- No publiques bases de datos reales, exportaciones, archivos `.env` ni credenciales.
- La API sigue configurada para escuchar solo en `127.0.0.1`. Para abrirla a otras PCs falta resolver con TI dónde vive el servidor y la base de datos (fuera de OneDrive).
- Las contraseñas se guardan con PBKDF2-HMAC-SHA256 y sales aleatorias (mínimo 6 caracteres al crear o cambiar).
- **Sesión con token:** `POST /login` devuelve un token (dura 12 h; en la base solo se guarda su hash). Todas las rutas lo exigen (`Authorization: Bearer ...`), `POST /logout` lo revoca y 5 intentos fallidos bloquean 5 minutos. Sin `/docs` ni `/openapi.json`.
- **Roles** (un rol por usuario; los permisos se aplican en la API, no solo en la interfaz): `SYSTEM_ADMIN`, `ENGINEERING`, `PLANNING`, `TOOLCRIB_SUPERVISOR`, `TOOLCRIB_OPERATOR`, `MAINTENANCE_TECHNICIAN`, `PUNCHING_OPERATOR` y `PUNCHING_TECHNICIAN`. Los dos roles de Punzonado consultan Manufacturing y pueden enviar solicitudes de servicio y recibir herramientas (`service.request`); no operan Tool Crib. Los roles de Tool Crib atienden la cola de solicitudes (`service.view`, `service.attend`). La administración captura número de empleado, nombre completo, área, turno y rol; el turno se elige entre Turno 1, Turno 2, Turno 3 y Turno Mixto. El rol y los datos del usuario se editan juntos. Los números de empleado son únicos entre cuentas activas y pueden reutilizarse tras desactivar una cuenta; la desactivación conserva el historial y revoca sus sesiones. Users también ofrece desactivar todas las cuentas excepto la sesión actual. La matriz está en `auth.py` (`ROLE_PERMISSIONS`, `ROUTE_RULES`); una ruta que no esté listada se rechaza y una prueba falla si se agrega una ruta sin regla. El administrador tiene todos los permisos para poder dar soporte; cada acción queda registrada.
- **Bitácora** (`auditoria`): cada cambio en la base guarda usuario autenticado, rol, acción (ruta), fecha y valores anteriores y nuevos (las contraseñas nunca). También se registran inicios y cierres de sesión, intentos fallidos y accesos denegados. El nombre del actor en los cuerpos (`realizado_por`, `creado_por`, `usuario`) siempre se sustituye por el del token. Se consulta en View Audit Log.
- Los usuarios no se borran: se desactivan, y siempre debe quedar al menos un `SYSTEM_ADMIN` activo.
- Migración de roles: `admin`→`SYSTEM_ADMIN`, `supervisor`→`TOOLCRIB_SUPERVISOR`, `operator`→`TOOLCRIB_OPERATOR` (solo la primera vez).
- La base garantiza un solo surtido abierto por carro (índice único parcial). Una máquina puede tener varios en fila (máximo 5, `MAX_OPEN_KITS_PER_MACHINE`).

## Máquinas

Cada máquina tiene perfil (tipo, línea, capacidad, espesor máximo) y estaciones numeradas con tamaño (A-E), posición (DENTRO/CENTRO/AFUERA), AutoIndex y estado (disponible/dañada). Se editan en el menú Máquinas. Las advertencias (espesor, estación no registrada, dañada, tamaño distinto) nunca bloquean el surtido.

El espacio **Manufacturing** (`#/manufacturing`) es la pantalla de inicio de los roles de Punzonado. Permite seleccionar una máquina activa y ver el número de parte en ejecución, sus datos técnicos (cliente, familia, programa, revisión, material, espesor, ciclo, UPH) y una fila por estación con su punzón y matriz. Si el setup activo no tiene requerimientos registrados, las estaciones se toman del programa del número de parte. No sustituye la confirmación de MES.

**Solicitudes de servicio a Tool Crib** (sustituyen los tickets externos para herramientas):

1. **Registrar lo montado:** una sola vez por posición, el operador escanea o escribe el ID del ejemplar que ya está montado (*Register mounted tool*). Debe coincidir en TN y tipo; no se acepta si está en servicio, en un carro, reservado o asignado en otra máquina. Una ubicación anterior no registrada se acepta y no se infiere el movimiento previo.
2. **Solicitar:** al seleccionar una herramienta montada se elige Afilado, Inspección, Reemplazo, Reporte de daño, Problema de desempeño o Retorno (daño y desempeño exigen describir qué pasó). Una posición sin herramienta ofrece *Request tool*. La posición queda sin herramienta hasta que llegue el reemplazo. Se puede solicitar aunque la máquina tenga un kit abierto; en ese caso el kit no se puede entregar mientras haya solicitudes de herramientas abiertas en esa máquina (hay que cerrarlas o cancelarlas).
3. **Tool Crib** (`#/requests`, permisos `service.view`/`service.attend`): *Attending* abre una lista de las personas activas de Tool Crib (`GET /workflow/toolcrib-users`); como las pantallas son compartidas, se elige quién atiende y esa persona queda como *Atendió* (un supervisor puede reasignarla). *Tool collected* registra el destino del ejemplar retirado (afilado o daño lo marcan como tal). *Retorno* cierra aquí. Cualquier cuenta de Tool Crib puede registrar los pasos de una solicitud de herramienta; los problemas de programa los sigue cerrando quien los tomó. Mientras la posición no tenga herramienta, la solicitud muestra "Waiting for a tool". Cada solicitud conserva tres tiempos: espera a Tool Crib (solicitud→atención), atención de Tool Crib (atención→herramienta recibida en máquina) y máquina sin herramienta (solicitud→recibida).
4. **Manufacturing recibe:** escanea el ID que llegó y confirma la instalación. Puede ser el mismo ejemplar ya afilado, directo desde el TOGU, u otro del mismo TN disponible y con holgura válida para matrices. Si regresa el mismo ejemplar, la solicitud se cierra. Si entra una alterna y la herramienta vieja sigue en servicio, la solicitud pasa a *Replaced*: la máquina ya tiene su herramienta y solo falta cerrar el ciclo de la vieja. Si el setup cambió mientras tanto, la instalación se rechaza y Tool Crib cancela la solicitud.
4b. **Resultado del servicio** (*Record result…*, solo Tool Crib): un solo botón con las salidas posibles. *Ready for use*: registra cuánto se rebajó (pulgadas) y notas; en afilado el rebaje es obligatorio y si pasa de 0.020 in se considera anormal y exige justificación (mínimo 10 caracteres); en daño y desempeño se exige describir lo encontrado y reparado. *Needs Maintenance*: abre el ticket (4e). *Cannot be used: retire it*: propone la baja (4f; no aparece en afilados). Ambas piden los datos del incidente y el motivo. El rebaje se descuenta de `LIFE` solo si la herramienta ya tiene valor. La herramienta lista queda disponible en el TOGU, sin pasar por el rack, para poder volver a su máquina. Cada resultado se guarda en `servicios_herramienta` (`GET /workflow/tool-service-log`) con herramienta, solicitud, máquina, número de parte, estación, rebaje, justificación y quién lo hizo, para los Paretos de daños y ajustes.
4c. **Guardar en rack** (*Store in rack…*): cuando la máquina ya recibió una alterna, Tool Crib guarda la herramienta vieja ya terminada escaneando rack y herramienta, y la solicitud se cierra. Todo movimiento queda en el historial de la herramienta.
4d. **Expediente de incidente** (`incidentes_herramienta`): un reporte de daño exige quién estaba en la máquina (lista de usuarios de Punzonado; por defecto quien reporta), causa (`collision`, `normal_wear`, `wrong_setup`, `wrong_material`, `handling`, `lubrication`, `supplier_defect`, `unknown`) y momento (`setup`, `production`, `inspection`, `handling`, `unknown`). Máquina, número de parte, estación, TN y categoría se toman de la solicitud, y el turno del perfil del operador. Cuando Tool Crib tiene la herramienta puede registrar o corregir el incidente con *Record incident…* (`POST /workflow/service-requests/{id}/incident`), también para un afilado que resultó ser más: se conserva lo reportado por Manufactura (`reportado_como`, descripción) y lo hallado por Tool Crib (`hallazgo`). Consulta: `GET /workflow/tool-incidents`; lista de operadores: `GET /workflow/operators`. El ticket de Mantenimiento nunca se genera solo; lo levanta siempre Tool Crib (ver 4e).
4e. **Mantenimiento** (`tickets_mantenimiento`, pestaña *Maintenance*): Tool Crib abre el ticket con *Send to Maintenance…* sobre una solicitud cuya herramienta ya tiene (aunque Manufactura solo pidió afilado) o con *New ticket* para una herramienta que ya tiene en Tool Crib (origen máquina o hallada en Tool Crib). Pide los mismos datos del expediente (operador y turno, causa, momento, máquina, parte, estación) más el motivo; la herramienta pasa a MANTENIMIENTO como dañada y deja de ofrecer «Record result…» (el afilado). Si la máquina ya tenía alterna, la solicitud se cierra; si no, se cierra cuando Manufactura escanea la alterna. El técnico (`MAINTENANCE_TECHNICIAN`, permiso `maintenance.operate`; también el supervisor) **recibe** escaneando la herramienta, **evalúa** (diagnóstico, causa según Mantenimiento, reparable o no) y **libera** con el trabajo realizado; la herramienta queda disponible en MANTENIMIENTO hasta que Tool Crib la guarda en su rack o la instala. Si no es reparable el ticket se cierra y la herramienta sigue dañada en MANTENIMIENTO. Mientras un ticket está abierto la herramienta solo se mueve por él (guardar, mover y cambiar estado se rechazan). API: `GET/POST /workflow/maintenance/tickets` y `/{id}/receive|evaluate|release`.
4f. **Baja con aprobación** (`bajas_herramienta`, pestaña *Maintenance → Retirements*): ya no existe la baja de un clic ni se puede eliminar una herramienta (`DELETE /herramientas/{id}` responde 409): toda baja obliga a escribir el motivo y queda en el historial. Tool Crib la **propone** solo desde donde tiene sentido: (1) *Record result…* de una solicitud, opción «Cannot be used: retire it» (no existe en solicitudes de afilado: si el afilado resulta ser más, se elige *Needs Maintenance*); (2) el ticket de Mantenimiento que el técnico cerró como irreparable (*Retire…*, reutiliza el expediente del ticket y solo pide el motivo); (3) la ficha de la herramienta (*Retire…*, solo si Tool Crib la tiene y no está en una solicitud, ticket o baja pendiente); (4) *Damaged…* en la lista de herramientas al surtir un kit; (5) *Damaged: retire…* al guardar una herramienta que entra en Tools → Store. Piden los mismos datos del incidente que el ticket (operador y turno, causa, momento, máquina, parte, estación) más el motivo. Una herramienta que está en una solicitud abierta solo se da de baja o se manda a Mantenimiento desde esa solicitud. La herramienta queda dañada y bloqueada mientras espera; si la máquina ya tenía alterna, la solicitud se cierra, y si no, se cierra cuando Manufactura escanea la alterna. **Un supervisor de Tool Crib o el técnico de Mantenimiento la aprueba o la rechaza** (`retirements.approve`); nadie aprueba su propia propuesta. Aprobar deja la herramienta en `baja`; rechazar exige nota y restaura el estado previo. Folio `BJ-00001`. API: `GET/POST /workflow/retirements` (con `request_id`, `ticket_id` o `tool_id`) y `/{id}/approve|reject`. Movimientos: `retirement_proposed`, `retirement_approved`, `retirement_rejected`.
5. **Cancelar:** el solicitante puede cancelar antes de que Tool Crib la tome (la herramienta vuelve a su posición); Tool Crib puede cancelar con motivo.

6. **Problema con programa:** desde el panel del número de parte, el operador elige el problema (herramienta incorrecta, estación o ángulo incorrecto, pieza fuera de especificación, el programa no corre, otro) y, opcionalmente, la estación. Llega a la misma bandeja de Requests. Tool Crib o Ingeniería lo toman con *Attending* y lo cierran con *Resolve*, escribiendo qué se hizo; el operador ve la resolución. Ingeniería solo atiende problemas de programa, no solicitudes de herramientas. La lista de problemas está en `PROGRAM_PROBLEMS` (`service_requests_api.py` y `web/src/labels.ts`) y es una lista inicial por ajustar.
7. **Avisos y tiempos:** la pestaña Requests muestra cuántas solicitudes esperan que alguien las tome (se pone roja si alguna lleva 15 min o más). Mientras la posición no tiene herramienta, cada fila muestra su tiempo de espera y se vuelve ámbar a los 5 min y roja a los 15 min (`WAIT_WARN_MIN` y `WAIT_LATE_MIN` en `web/src/components/Service.tsx`). El Tablero de secuencia y las tarjetas de máquina muestran "Waiting for tool" con la estación mientras una posición espera su herramienta, para que ninguna máquina corra un número de parte sin ella. Un kit no se puede entregar mientras la máquina tenga solicitudes de herramientas esperando su herramienta.

8. **Plan de producción y máquina del operador** (sustituye al Excel del plan): el Board es el plan. Cada demanda lleva máquina, número de parte, cantidad, **orden de trabajo (WO)**, producido y horas (el resto de los datos viene del .stp del número de parte). Punzonado ve el Board completo (todas las máquinas, solo lectura) y, en *Setup*, Catalog, Part numbers y Machines también en solo lectura. Para trabajar una máquina el operador la **toma** (*Take this machine* en la cola de la máquina, o en *Manufacturing*): queda registrado quién está en cada máquina (`asignaciones_maquina`, se ve en la tarjeta del Board), el registro vence al terminar el turno, al tomar otra máquina o con *Leave machine*, y hay que tomarla cada vez que se cambia de máquina. Solo quien está en la máquina puede (a) capturar sus **piezas producidas** (Tool Crib y Planeación lo hacen en cualquiera) y (b) pedir el **siguiente número de parte** (*Request kit*, `POST /workflow/manufacturing/machines/{código}/part-request`; solo partes de la cola de esa máquina). La solicitud llega a la misma bandeja de Requests con tipo *Next part number*: Tool Crib la toma (elige quién atiende), pulsa *Start kit…* (abre Kitting con máquina y parte ya capturadas) y se cierra sola al entregar el kit de esa parte (o con *Resolve…* / *Cancel*). Los reportes de herramienta y de programa siguen igual. **Turnos automáticos** por reloj: Turno 1 07:00–15:00, Turno 2 15:00–23:00, Turno 3 23:00–07:00 (`shifts.py`); se usan en solicitudes, incidentes y registros de máquina (ya no el turno del perfil del usuario).
9. **UPH editable** (Part numbers → abrir un número de parte → *Planning* → *Edit UPH*, solo Ingeniería, Planeación y Administrador, permiso `program.uph`): se conserva el UPH que sale del .stp y se puede fijar el UPH que usa el plan (las horas del Board pasan a piezas ÷ UPH). Cada cambio exige motivo y queda en el historial del número de parte (acción `uph`); dejar el valor del .stp se hace con *Back to the .stp value*. Cargar una nueva revisión del .stp no borra el UPH editado. API: `PUT /workflow/programs/{tipo}/{numero_parte}/uph`.
10. **Dashboard de gerencia** (pestaña *Dashboard*, permiso `insights.view`: supervisor de Tool Crib, Ingeniería, Planeación y Administrador; `GET /insights/overview?days=7|14|30|60|90|180|365`). No captura nada nuevo: calcula todo con lo que el sistema ya registra y compara cada indicador con el periodo anterior del mismo largo (la flecha solo aparece si el periodo anterior tiene al menos 5 casos). Indicadores: horas de máquina perdidas esperando herramienta, respuesta promedio de Tool Crib y % atendido en 15 min, solicitudes, daños, bajas, kits entregados y su tiempo, disponibilidad de herramientas, rebaje anormal y tiempo de respuesta de Mantenimiento. Gráficas: actividad diaria, horas perdidas por máquina, respuesta por turno, tipos de solicitud, Pareto de causas de daño, daño por máquina/turno/momento/número de parte/TN, estado del inventario, herramientas que necesitan atención (candidatas a revisar o retirar), Mantenimiento, kitting y plan de producción (máquinas por semáforo, horas en cola, avance). Arriba aparecen «Key findings» redactados automáticamente. Si se captura el costo por hora-máquina (se guarda solo en ese navegador) se muestra el costo del paro por herramienta y cuánto cambió. *Print / save as PDF* imprime el tablero en horizontal.
11. **Dashboard de operaciones** (arriba del dashboard de gerencia; `GET /insights/operations?days=…`, mismo permiso `insights.view`; `insights_ops.py`). Muestra el alcance de MIRA más allá de Tool Crib, todo calculado de lo ya registrado: *MIRA at a glance* (piezas, cumplimiento del plan, utilización, máquinas deshabilitadas, tickets automáticos, FG clear to build, movimientos), producción y cumplimiento (piezas contra el forecast por semana y por FG con el make item limitante, producción por turno y por día, pronóstico de las próximas 4 semanas), utilización y paros por máquina (corriendo, esperando herramienta, deshabilitada, ociosa; máquinas fuera de servicio con motivo), golpes y tickets automáticos (herramientas por nivel, tiempo de respuesta), Clear to build por FG con demanda (máquinas necesarias, herramientas a comprar, veredicto) y trazabilidad (movimientos por día, qué se movió, cómo se capturó, quién, % de herramientas tocadas).
12. **Demo para gerencia**: `make_demo_database.py --source tooltrack.db --target demo_mira.db` copia la base (nunca la modifica) y le agrega historial sintético de 60 días por defecto (el dashboard muestra 30 y los compara con los 30 anteriores; `--days`, `--scale` para el volumen y `--seed` lo cambian): solicitudes, daños, afilados, tickets, bajas y kits, y con `demo_history.py` FGs con forecast, producción por máquina/turno/día que sigue al forecast según la capacidad, paros de máquina (una deshabilitada hoy), golpes por herramienta con límites y tickets automáticos, inventario extra para que el Clear to build muestre FG verde, con riesgos y con faltantes, y miles de movimientos de trazabilidad. Todo es sintético. `4_Iniciar_Demo_Gerencia.bat` la crea si falta y la sirve en `http://127.0.0.1:8001` (usuarios `demo_admin` con todos los permisos y `demo_manager`, clave `demo1234`). Borrar `demo_mira.db` la regenera. Los archivos `*.db` no se versionan.

Pendientes: el desglose de otras requisiciones.

## Records (consulta y descarga)

Pestaña *Records*, solo para supervisor de Tool Crib, Ingeniería y Administrador (permiso `records.view`). Cada tipo de registro es una lista aparte, con filtros de fechas, herramienta, máquina y usuario donde aplican, y botón *Download .xlsx* que respeta los filtros; *Download all* baja un libro con una hoja por registro (`GET /records/workbook`).

| Registro | Contenido |
| --- | --- |
| Tool movements | Tres grupos que no se mezclan: Kitting and machine, Service and Maintenance, Storage and status |
| Service requests | Todas las solicitudes con tiempos de atención y total, causa y ticket |
| Damage incidents | Quién, dónde, parte, estación, turno, causa y momento |
| Maintenance tickets | Motivo, diagnóstico, trabajo, horas abiertas |
| Retirements | Bajas propuestas, quién las propuso y decidió, horas para decidir |
| Sharpening and repairs | Rebaje, anormales y justificación, vida antes y después |
| Part number changes | Bitácora de programas |
| System audit | Cambios del sistema por usuario |

API: `GET /records`, `GET /records/{kind}` (con `limit` y `offset`) y `GET /records/{kind}/download`. Las horas salen en la zona horaria del servidor; la hoja *Info* del Excel guarda quién exportó, cuándo y con qué filtros. Los textos escritos por personas nunca se convierten en fórmulas de Excel. Requiere `openpyxl` (en `requirements.txt`).

## Navegación de la web

Menú por rol (cada uno ve solo lo suyo): **Board · Dashboard · Kitting · Requests · Maintenance · Tools · Records · Setup · Manufacturing · Users**. *Dashboard* es el tablero de gerencia (supervisor de Tool Crib, Ingeniería, Planeación y administrador). *Setup* agrupa Catalog, Part numbers y Machines con una tira de pestañas (Punzonado lo ve en solo lectura); si un rol solo ve una de las tres, aparece directa. Atajos: la tarjeta de máquina del Board lleva a Kitting con el comparativo ya hecho (Tool Crib) o al alta de demanda (Planeación); el ID de cualquier herramienta o máquina en Requests, Maintenance y Records abre su ficha o su página. Las pantallas no llevan párrafos explicativos: título, el siguiente paso como botón principal y mensajes de error o confirmación. Los enlaces antiguos (`#/tracking`, `#/inventory`, `#/kitting/pending`, `#/kitting/labels`) siguen funcionando.

## Escaneo y etiquetas

Toda pantalla que recibe un ID de herramienta o un código de ubicación acepta un escaneo o el ingreso manual; la interfaz y el servidor interpretan el código con las mismas reglas (`web/src/scan.ts` y `scan_codes.py`).

- **Lectores:** hoy funcionan los lectores USB o Bluetooth que escriben como teclado (el lector escribe el código y manda Enter; el campo es el mismo que se usa para teclear). La lectura con cámara de tableta o celular queda pendiente: los navegadores solo permiten la cámara en páginas con HTTPS, así que va junto con el paso a servidor.
- **Formato de las etiquetas:** el QR lleva un código que identifica al objeto, no sus datos (la vida, el estado y la ubicación cambian; el sistema muestra los datos vigentes al escanear). Prefijos: `TOOLTRACK-TOOL:<id>` para una herramienta, `TOOLTRACK-LOCATION:<código>` para cualquier ubicación (rack, TOGU, mantenimiento, taller), `TOOLTRACK-MACHINE:<código>` para una máquina, `TOOLTRACK-CART:<código>` para un carro y `TOOLTRACK-KIT:<token>`, la etiqueta desechable del kit que deja de servir al entregarse. El ID o código sin prefijo, tecleado a mano, se acepta siempre, y las etiquetas Code128 anteriores (`ID|TN|SIZE|…`) se leen como herramienta.
- **Tolerancia:** se ignoran espacios, saltos de línea y mayúsculas o minúsculas, y se rechaza una etiqueta de otro tipo con un mensaje claro (por ejemplo, escanear un rack donde se esperaba una herramienta).
- **Cómo se capturó:** cada movimiento guarda si el código se escaneó o se tecleó (`captura`: `scan` o `manual`; vacío en movimientos anteriores). La interfaz lo deduce de la velocidad de escritura: un lector escribe el código completo en milisegundos. Se ve en `GET /workflow/movements` (campo `capture`) y sirve para auditar cuántos ingresos manuales hay.
- **Dónde ya aplica:** registrar lo montado, recibir la herramienta, guardar en rack, destino de la recolección, selector de máquina de Manufacturing, escaneo de herramientas del kit y *Store in rack* de Tool Tracking.
- **Pendiente:** imprimir las etiquetas de herramientas, racks y TOGU con estos formatos (hoy solo se imprimen las de máquina y carro) y la lectura con cámara.

## Inventario de herramientas: altura, claridad, tolerancia y cambio de piloto

- **Alta de herramienta:** el tipo (ROUND, 1 RADIUS, 2 RADIUS, 4 RADIUS…) lo manda el catálogo del TN y tamaño; no se captura libre. Se registra el proveedor (lista con los existentes o uno nuevo) y la claridad de matrices se elige de una lista 0.004 a 0.030. La tabla de Tools filtra por columna.
- **Altura y vida:** de un punzón se mide la altura total de la punta y el sistema resta el stripper plate de su tamaño (A 0.250, B 0.250, C 0.300, D 0.350, E 0.350) para obtener la vida utilizable; una herramienta puede traer su propio stripper. En matrices la medida es la altura. Todo es editable en Tools > Life settings (`/inventario/parametros`).
- **Alertas de mínimo:** punzón con 0.200 in. o menos de punta sobre el stripper y matriz de 1.050 in. o menos. No detienen surtido, envío ni uso: la herramienta se marca «At minimum» y aparece en el Dashboard, sección *Next purchase*, con cuánto stock sano queda del mismo TN.
- **Afilado:** el rebaje se elige de una lista 0.000 a 0.050; desde 0.020 exige justificación y resta de la altura medida.
- **Tolerancia de ±0.003 in.** (editable): los TN de las series 1 a 4 llevan su medida (1xxxx redondo, 2xxxx cuadrado, 3xxxx rectangular, 4xxxx oblongo), así que un TN dentro de la tolerancia en cada dimensión cuenta como el requerido (352×252 por 350×250). Aplica igual al surtir, escanear, comparar contra lo montado, registrar lo montado y la cobertura de Manufacturing, y siempre se muestra el TN sustituto y su diferencia. Los TN sin medida (series 5 a 9) solo coinciden exactos.
- **Cambio de piloto:** en una solicitud de reemplazo o de herramienta sobre una posición redonda, Manufacturing marca «Pilot change» (con nota obligatoria). Tool Crib puede entregar cualquier redondo aunque exceda la tolerancia; la solicitud conserva el TN pedido contra el instalado y se muestra en Requests y en la posición de la máquina.
- **Imagen:** cada herramienta puede tener foto (o heredar la del TN) y se ve al consultar su ficha. Se guardan en `tool_images/`, junto a la base, fuera de git.

## Máquinas deshabilitadas

Una máquina se deshabilita con motivo (mantenimiento, inhabilitada u otro con texto), con fecha estimada de regreso opcional y con historial de quién y cuándo (ficha de la máquina, botón *Disable…*; permiso `machine.availability` para Planning, Engineering, supervisores de Tool Crib y administrador). Mientras está deshabilitada:

- Planeación no cuenta su capacidad; con fecha de regreso la cuenta desde los días de la semana en que ya está de vuelta.
- El Board la muestra marcada y no deja agregar demandas; Kitting no deja iniciar kits para ella; Manufactura no puede levantar tickets ni pedir el siguiente número de parte desde ella; la propuesta de Planeación no le envía líneas.
- Lo que ya está corriendo o en cola se conserva tal cual. Es distinto de «Inactive», que retira la ubicación.

## Clear to build (Planning → Clear to build)

Responde «¿podemos construir N de este FG por semana con los programas, máquinas y herramientas que tenemos?» y, si no, dice exactamente qué falta.

- **Entrada:** FG, FG por semana, turnos/horas/días/eficiencia (precargados de los parámetros de Planeación) y cuántas máquinas de cada tipo (4 Index, 2 Index, 45 Sta). MIRA toma las habilitadas con menos horas ya comprometidas en el Board (opción para no contarlas).
- **Programas:** cada make del BOM debe tener programa con UPH en algún tipo de máquina elegido; avisa de UPH atípicos.
- **Máquinas (`ctb_api.py`):** reparte las piezas por semana entre las máquinas compatibles (tipo y espesor, más rápida primero), con setup por parte y máquina. Muestra carga por máquina, el mínimo de máquinas necesarias por tipo, cuántas faltan en el escenario y cuántos FG/semana aguanta.
- **Herramientas:** cada máquina corre una parte a la vez y reutiliza sus herramientas entre partes, así que el pico de un TN es la suma, por máquina, de lo máximo que usa alguna de sus partes (cada estación = 1 punzón + 1 matriz). Solo cuenta el TN exacto (sin tolerancia, porque alimenta compras). Se descuentan las herramientas no disponibles; las de afilado se muestran como «vuelven de servicio», las montadas en otras máquinas aparte, y las de vida mínima como riesgo.
- **Resultado:** semáforo general (*Clear to build*, *con riesgos*, *Not clear to build*), tres tarjetas (Programs, Machines, Tools), lista «What is missing» con TN, cantidades y partes afectadas, aviso del efecto de usar menos máquinas (menos juegos de herramienta) y descarga en Excel (Summary, Tools to buy, Make items, Machines).
- **Límites:** un FG a la vez y una semana tipo (no recorre el forecast); la capacidad usa los turnos/horas del escenario para todas las máquinas; el mínimo de máquinas es una estimación.

## Torreta estándar (Planning → Standard turret)

Es una **propuesta para los programadores**: MIRA no cambia ninguna máquina, setup ni .stp. Dada una máquina y un conjunto de partes (a mano o las que la propuesta de Planeación le asigna a esa máquina), calcula en qué estación va cada TN para que al armar el estándar en Radan se editen lo menos posible los .stp de cada parte.

- **Cálculo (`standard_turret.py`, `standard_api.py`):** cada TN es una herramienta. Los TN de las series 1 a 4 dentro de la tolerancia (±0.003) comparten herramienta y estación (se muestra «also covers» y la diferencia); una parte que usa el mismo TN dos veces (ángulos distintos) ocupa dos estaciones. Una estación admite una herramienta; el tamaño A–E debe coincidir y una A puede ir en una B con adaptador. Un TN sin tamaño en el inventario se acepta en cualquier estación y se avisa. Solo cuentan las estaciones disponibles (no las dañadas); el sufijo `s` de los .stp (`305s`) se empata con la estación `305` y se conserva.
- **Criterio:** primero cubrir la mayor cantidad de usos de herramienta con las estaciones que hay y después conservar cada TN donde más partes ya lo tienen en su .stp (asignación óptima, no avara).
- **Si no caben** (por ejemplo más de las ~58 estaciones): lista qué TN quedan fuera y por qué (sin estación libre o sin estación de su tamaño), marca esas posiciones como «off standard: mount it by hand» y sugiere, en orden, qué partes dejar fuera del estándar para que quepa más.
- **Salida:** pantalla con la tabla estación→TN y, por parte, qué líneas del .stp cambian (estación actual → estación del estándar y el TN sustituto); y descarga Excel con las hojas Standard, Parts, Not placed y Summary. Avisa de partes sin programa, con espesor fuera del rango de la máquina o con estaciones que la máquina no tiene.
- **Aún no hace:** no genera los .stp corregidos, no usa la posición (DENTRO/CENTRO/AFUERA) ni exige autoindex como regla (solo lo conserva por el .stp actual), y el kitting todavía no se compara contra el estándar.

## Golpes por herramienta y ticket automático

- **Golpes por ciclo:** en *Part numbers* → ficha del programa, cada estación tiene «Strokes per blank» (golpes por blank/ciclo), editable (permiso `program.uph`); vacío vuelve al valor por defecto (1, marcado «default»). Cuando el .stp traiga la columna, será el valor inicial. Tabla `golpes_programa`.
- **Acumulación (`strokes_api.py`):** cada pieza producida registrada (captura en el Board) suma `golpes de la estación × piezas / piezas por blank` al punzón y a la matriz montados en esa estación (se emparejan por estación, TN y ángulo del setup activo). Una corrección negativa resta. Cada herramienta guarda su acumulado desde el último servicio y el total de su vida; el detalle está en `golpes_herramienta` (solo se agrega).
- **Límites por TN y tipo** (botón *Stroke limits* en Part numbers): golpes antes del primer mantenimiento y entre afilados; vacío = sin alerta. Antes de su primer servicio aplica el primer mantenimiento; después, el intervalo de afilado.
- **Alertas:** al 90% la herramienta aparece como «Near the limit» en Requests, Dashboard y su ficha. Al 100% el sistema levanta una solicitud de afilado automática (solicitante `SYSTEM`, marcada «Automatic · strokes», una por herramienta) para que Tool Crib lleve el reemplazo; la herramienta sigue montada hasta entonces y la máquina no cuenta como detenida.
- **Reinicio:** al terminar un afilado o liberar un mantenimiento el contador vuelve a 0 y empieza el intervalo de afilado. Un supervisor puede ajustarlo a mano con motivo (*Adjust strokes…* en la ficha, permiso `inventory.adjust`).

## Planeación de capacidad (menú Planning)

Responde «¿cumplimos con el forecast?» por FG, familia y semana, en lugar del Excel de los planners.

- **FG y BOM:** un FG (rack, shelf…) se arma con números de parte *make* y las piezas de cada uno por unidad de FG. Se captura en una pantalla aparte (Setup y la carga de programas no cambian), eligiendo partes ya guardadas o tecleando un make que aún no tiene programa. Dos números de FG con el mismo BOM pueden compartirlo. También se importa desde Excel (columnas FG, MAKE y PIEZAS).
- **Forecast:** piezas por FG por semana (la semana empieza en lunes), capturadas en la pestaña Forecast.
- **Capacidad por máquina:** turnos × horas por turno × días × eficiencia, editable por máquina (también se puede excluir una máquina). Una máquina sin captura propia usa valores por defecto marcados «default», que hay que revisar.
- **Cálculo (`capacity_plan.py`):**
  - La demanda es neta: se resta lo producido desde el lunes de la semana actual (registro de producción) y lo pendiente en el Board; lo ya cubierto se usa contra las primeras semanas y el excedente pasa a las siguientes.
  - Las horas de cada parte salen de su UPH (el editado por Planeación, o el del .stp) por tipo de máquina, más un tiempo fijo de setup por parte y máquina.
  - Cada parte va a las máquinas compatibles (tipo con programa y espesor dentro del rango de la máquina cuando está capturado); lo que no cabe se arrastra a la semana siguiente como atraso y las horas libres adelantan demanda de las semanas siguientes (límite editable).
  - Los horas que el Board ya tiene comprometidas se restan de la capacidad de esa máquina desde la primera semana.
- **Resultado:** semáforo por semana (*Meets*, *Tight* desde 90% de uso, *Short* si queda atraso) y por FG, con desglose por make, máquina y semana. Las partes sin programa o sin UPH se marcan «No program» y no se cuentan como horas; los avisos listan lo que hay que revisar antes de confiar en el resultado.
- **Permisos:** `planning.view` (Planning, Engineering) y `planning.manage` (solo Planning); cada cambio queda en la bitácora.
- **Propuesta de reparto (pestaña Proposal):** por semana, qué parte corre en qué máquina y cuántas piezas, con las horas y el setup de cada máquina. El planner revisa, puede cambiar la máquina (solo entre las compatibles) o las piezas, y envía al Board las líneas que acepta; se crean como demandas con el comentario «From the capacity proposal». Lo que se adelanta de semanas futuras aparece marcado «Built ahead» y sin seleccionar. Una vez enviadas, el cálculo las cuenta como ya programadas y descuenta sus horas de esas máquinas. Lo que no cabe se lista con su razón (sin programa, sin máquina compatible o sin capacidad).
- **Aún no hace:** tiempo de setup según las herramientas que cambian (hoy es fijo por parte y máquina), ni el límite de herramientas únicas por máquina.

## Producción registrada y demandas del Board

Cada captura de piezas producidas agrega un registro (`registros_produccion`) con parte, máquina, piezas, fecha, turno, usuario y fuente (`manual` hoy; `baan` y `mespro` quedan previstos con un ID externo para no contar dos veces). Nunca se edita ni se borra: quitar una demanda del Board pide motivo y estado (terminada, cancelada o reemplazada) y lo producido se conserva. Se permite producir más que la cantidad de la demanda. `GET /workflow/demandas/production` lo consulta.

## Kitting: comparación manual y Pilot change

- **Comparar contra:** además del modo automático (lo montado o el último kit de la fila, y el atajo desde la tarjeta de la máquina), Kitting permite elegir a mano cualquier número de parte guardado. La comparación solo aplica a ese kit y no cambia lo que el sistema tiene registrado en la máquina; al entregar se vuelve a comparar contra lo realmente montado. Con la casilla «también corregir lo registrado» se usa el flujo de corrección con motivo.
- **Pilot change:** en una posición redonda (TN 1xxxx), Manufacturing ofrece «Pilot change» al solicitar un reemplazo o una herramienta. Tool Crib puede instalar cualquier redondo aunque exceda la tolerancia; el ticket guarda el TN pedido y el instalado, y se ve en Requests y en la posición de la máquina.

## Números de parte

Menú Números de parte: programas por tipo de máquina (4I, 2I, 45 STA). Formato fijo tipo TYEH-1193217_01-SW, familia (lista administrable) y NPI/Sustaining. Se guardan los datos leídos del .stp y el archivo (hoja de setup). Si el número ya existe se reemplaza, con motivo obligatorio; todo queda en la Bitácora de cambios (qué estaciones cambiaron, quién y cuándo).

Planeación por número de parte (`planning.py`): piezas por blank (`sym`) y run time por blank tomados del .stp. Cycle time = run time; cycle c/lead = cycle + 180 s (lead time por blank); UPH teórico = 3600 ÷ cycle × piezas por blank. Se guardan en `programas_parte` y se muestran en la ventana de programas.

## Tablero de secuencia (B1)

Menú Tablero de secuencia (`demand_board_ui.py`, API `demandas_api.py`, prefijo `/workflow/demandas`). Una tarjeta por máquina con semáforo, ordenadas por las que paran más pronto; clic en la tarjeta abre la cola completa de esa máquina (surtir, producido, editar, subir/bajar, quitar).

- Semáforo según las HR que le faltan a la NP que corre: rojo < 2 h, amarillo 2–4 h, verde > 4 h; azul = paro programado; gris = sin cola. Rojo también si la máquina está vacía, terminó su NP o se acabó la cola; amarillo si corre una NP que no está en la cola o no hay programa para calcular.
- Si la máquina está en rojo/amarillo y la siguiente NP no está surtida, la tarjeta pide "SURTIR SIGUIENTE YA" y abre Tool Dispensing con máquina y NP preseleccionadas.
- HR de una demanda = cantidad × cycle c/lead ÷ (piezas por blank × 3600). Pendiente se calcula con la cantidad menos lo producido.
- TIE TOTAL de la máquina = suma de HR de sus demandas; CUBIERTO = suma de HR de las demandas en TERMINADO.
- Estado automático: TERMINADO (pendiente 0), CORRIENDO (la máquina tiene montado ese NP; si el NP se repite solo la primera), SURTIENDO (surtido en proceso), FALTA HERRAMIENTAS (surtido pausado), KIT LISTO (surtido completo, listo para entregar), NO INICIADO.
- Marcas manuales con comentario obligatorio: PARO PROGRAMADO, URGENTE, LÁSER, CORTE DE BLANCK (Planning, Engineering y supervisor de toolcrib).
- Permisos por campo en el PATCH de una demanda: cantidad, orden y orden de trabajo (WO) → `demand.manage` (Planning); producido → `demand.progress` (Tool Crib en cualquier máquina; el operador de Punzonado solo en la máquina que tomó); marca y comentario → `demand.mark`. Límite de 1,000,000 de piezas, NP en mayúsculas, producido no puede superar la cantidad y no se aceptan máquinas desactivadas.
- HR con UPH editado: si Planeación o Ingeniería fijó un UPH para el número de parte, HR = piezas ÷ UPH; si no, se usa la fórmula del .stp de arriba.
- Quitar una demanda la desactiva (no se borra).
- Se actualiza solo cada 30 s; si el backend no responde lo avisa en la barra superior sin mensajes emergentes.
- Pendiente: el lead time y la integración con MESSPRO quedan para después; la producción se captura a mano.

## Surtido desde programas guardados y traspaso

- El surtido ya no carga `.stp`: se elige máquina, se busca el número de parte (solo los del tipo de esa máquina), se compara y se elige un carro libre.
- Al elegir la máquina se muestra qué número de parte corre ("▶ Corriendo en ..."). Si el operador elige otro como "actual", se pide confirmar y motivo; la corrección queda en la bitácora de la máquina (`eventos_maquina`).
- Panel "Estado de carros": libre/surtiendo/pausado/listo, qué falta, y botones Retomar / Liberar. Pausar o cancelar exige motivo.
- Botón "Máquinas en tiempo real / Traspaso": lista qué corre en cada máquina y permite traspasar un número de parte a otra máquina del mismo tipo y vacía, con motivo obligatorio. Las herramientas montadas se mueven con él. Endpoints: `GET /workflow/machines/running`, `POST /workflow/machines/transfer`.
- **Claridad de matrices:** el rango se calcula con el espesor ±0.005 in (unión de las bandas que toca ese margen, `THICKNESS_TOLERANCE` en `setup_workflow.py`), para que una milésima no cambie de banda. El surtido muestra número de parte, espesor y rango de claridad, y la columna *Clearance* en las matrices.
- **Refresh del surtido:** vuelve a leer el programa vigente (`POST /workflow/kits/{id}/refresh`). Si Ingeniería lo cambió, conserva lo ya surtido que sigue siendo válido y avisa qué herramientas regresar a su rack, cuáles son nuevas y cuáles se quitaron. Si no cambió, no toca nada.
- **Hoja para surtir:** *Print pick sheet* (`GET /workflow/kits/{id}/pick-sheet`) imprime en horizontal lo que falta, ordenado por rack: herramienta sugerida (ID y rack), hasta 2 alternas, claridad requerida y espacio para el ID surtido. Solo sugiere; no reserva nada.
- **Mismo TN, otro ángulo:** el comparativo empareja primero TN + ángulo y después el mismo TN con ángulo distinto (`pair_positions` en `setup_workflow.py`); esa herramienta se conserva en la máquina y el kit indica «was 90°: re-orient». Si un kit abierto había pedido otra vez algo que ya está montado, *Compare again* lo recalcula.
- **Kit completo:** al escanear la última herramienta el kit pasa solo a `ready` y genera su QR; no hay que entregarlo de inmediato. En el Board el estado es «KIT LISTO».
- **Fila de kits por máquina:** se pueden iniciar varios kits de la misma máquina (misma persona o distintas, cada uno en su carro). Cada kit se compara contra el anterior de la fila y no contra lo montado hoy. La entrega es en orden de fila y según lo montado; en casos extraordinarios Tool Crib puede entregar fuera de orden con motivo obligatorio (`fuera_de_orden` + `motivo_orden` en `POST /workflow/delivery/scan`): el kit se vuelve a comparar contra lo montado y solo se entrega si sigue completo. Si un kit anterior se cancela, se refresca o se entrega fuera de orden, los kits detrás reciben una alerta (no se recalculan solos) con el botón *Compare again*.
