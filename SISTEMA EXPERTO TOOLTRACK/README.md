# TOOLTRACK

Sistema de escritorio para inventario y rastreabilidad de herramientas. La interfaz Tkinter consume una API FastAPI y los datos se guardan localmente en SQLite.

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

Cada configuración TN + SIZE comparte TYPE; permite una variante de punzón y distintas variantes de matriz por CLEARANCE. Cada variante puede tener varios ejemplares físicos, cada uno con su propio ID, LIFE, SO y ubicación. La interfaz presenta una sola entrada **Inventory**, una sola lista y un único formulario para registrar y editar. El campo TYPE cambia sus opciones según la categoría: geometrías para punzones/matrices, HOLDERS A–E para holders y los tipos estándar definidos para accesorios. La API valida las mismas listas y rechaza una geometría o tipo estándar asignado a otra categoría; al editar, permite conservar sin cambios un TYPE no estándar ya existente para no bloquear la corrección de otros datos heredados. Las ampliaciones al catálogo controlado de holders/accesorios quedan para el flujo de administración de catálogo. La lista muestra ID, TN, SIZE, TYPE, categoría, CLEARANCE, LIFE, SO, ubicación y vínculo al catálogo. Los administradores pueden editar o eliminar el registro seleccionado; la eliminación requiere confirmación y está reservada para corregir altas erróneas. Las bajas formales deberán conservar su motivo e historial en un flujo específico posterior.

Cuando una herramienta ya está referenciada por setups, kits, pendientes o movimientos del flujo, Inventory conserva la edición de datos físicos como LIFE/SO, pero bloquea cambios de identidad y ubicación manuales. La ubicación debe cambiarse mediante un movimiento registrado; una herramienta con historia de flujo no se puede eliminar desde la corrección de altas.

Al iniciar la API, SQLAlchemy crea las tablas nuevas que falten, pero los registros heredados no se convierten ni se enlazan automáticamente al catálogo. Los registros heredados permanecen visibles en Inventory y se marcan como **Not linked** hasta que exista un proceso de conciliación aprobado. El endpoint técnico `GET /catalogo/conciliacion/preview` es de solo lectura y no forma parte de la navegación normal de la interfaz. Los controles de escritura del catálogo se ocultan según el rol en Tkinter y la API los exige por permiso. Haz una copia de seguridad antes de iniciar una versión que incluye cambios de esquema.

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

En el primer inicio se registran las máquinas predeterminadas `T-101` a `T-117` y los carros de surtido `C-01` a `C-03` (sin modificar ubicaciones ya existentes); racks, TOGUs, mantenimiento y taller deben registrarse antes de usarse. Todas las rutas exigen sesión y permiso por rol (ver Seguridad).

## Aplicación web (en construcción)

Frontend en `web/` (React + TypeScript + Vite). El backend lo sirve en `http://localhost:8000/` y usa la misma API y los mismos roles que la interfaz Tkinter, que se mantiene hasta que cada módulo web esté probado.

- Compilar: `build_web.bat` (usa Node.js portátil en `%LOCALAPPDATA%\TOOLTRACK\node-v26.13.0-win-x64`; `node_modules` vive en `%LOCALAPPDATA%\TOOLTRACK\web_node_modules`, enlazado con una unión, para que OneDrive no lo sincronice). Después reinicia el backend. Desarrollo: `npm.cmd run dev` dentro de `web/` (puerto 5173 con proxy a la API).
- Módulos listos: Login, Sequence Board (tarjetas con semáforo, cola por máquina, producido, edición, orden, modo TV en `#/tv` con desplazamiento automático), Usuarios, **Kitting completo**, **Tool Tracking** (`#/tracking`), **Inventory** (`#/inventory`), **Part numbers** (`#/programs`), **Machines** (`#/machines`: estado en tiempo real, ficha, estaciones y traspaso entre máquinas compatibles) **Manufacturing** (`#/manufacturing`: máquina, número de parte, datos técnicos, estaciones y herramientas, solicitudes a Tool Crib) y **Requests** (`#/requests`: cola de Tool Crib). Máquinas conserva administración avanzada en Tkinter; Audit Log e importar/exportar CSV de inventario siguen solo en Tkinter.
- Permiso nuevo `tools.store` (guardar en rack): operador, supervisor, técnico de mantenimiento y admin. El técnico de mantenimiento ve solo Tool Tracking.
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
- La base garantiza un solo surtido abierto por máquina y por carro (índice único parcial).

## Máquinas

Cada máquina tiene perfil (tipo, línea, capacidad, espesor máximo) y estaciones numeradas con tamaño (A-E), posición (DENTRO/CENTRO/AFUERA), AutoIndex y estado (disponible/dañada). Se editan en el menú Máquinas. Las advertencias (espesor, estación no registrada, dañada, tamaño distinto) nunca bloquean el surtido.

El espacio **Manufacturing** (`#/manufacturing`) es la pantalla de inicio de los roles de Punzonado. Permite seleccionar una máquina activa y ver el número de parte en ejecución, sus datos técnicos (cliente, familia, programa, revisión, material, espesor, ciclo, UPH) y una fila por estación con su punzón y matriz. Si el setup activo no tiene requerimientos registrados, las estaciones se toman del programa del número de parte. No sustituye la confirmación de MES.

**Solicitudes de servicio a Tool Crib** (sustituyen los tickets externos para herramientas):

1. **Registrar lo montado:** una sola vez por posición, el operador escanea o escribe el ID del ejemplar que ya está montado (*Register mounted tool*). Debe coincidir en TN y tipo; no se acepta si está en servicio, en un carro, reservado o asignado en otra máquina. Una ubicación anterior no registrada se acepta y no se infiere el movimiento previo.
2. **Solicitar:** al seleccionar una herramienta montada se elige Afilado, Inspección, Reemplazo, Reporte de daño, Problema de desempeño o Retorno (daño y desempeño exigen describir qué pasó). Una posición sin herramienta ofrece *Request tool*. La posición queda sin herramienta hasta que llegue el reemplazo. Se puede solicitar aunque la máquina tenga un kit abierto; en ese caso el kit no se puede entregar mientras haya solicitudes de herramientas abiertas en esa máquina (hay que cerrarlas o cancelarlas).
3. **Tool Crib** (`#/requests`, permisos `service.view`/`service.attend`): *Attending* asigna al usuario y significa que va por la herramienta; *Tool collected* registra el destino del ejemplar retirado (afilado o daño lo marcan como tal). *Retorno* cierra aquí. Solo quien atiende o un supervisor registra la recolección. Mientras la posición no tenga herramienta, la solicitud muestra "Waiting for a tool".
4. **Manufacturing recibe:** escanea el ID que llegó y confirma la instalación. Puede ser el mismo ejemplar ya afilado, directo desde el TOGU, u otro del mismo TN disponible y con holgura válida para matrices. Si regresa el mismo ejemplar, la solicitud se cierra. Si entra una alterna y la herramienta vieja sigue en servicio, la solicitud pasa a *Replaced*: la máquina ya tiene su herramienta y solo falta cerrar el ciclo de la vieja. Si el setup cambió mientras tanto, la instalación se rechaza y Tool Crib cancela la solicitud.
4b. **Resultado del servicio** (*Record result…*, solo Tool Crib): al terminar el afilado o la reparación, Tool Crib registra cuánto se rebajó (pulgadas), notas y si la herramienta queda lista o se da de baja. En afilado el rebaje es obligatorio; si pasa de 0.020 in se considera anormal y exige justificación (mínimo 10 caracteres). En daño y desempeño se exige describir lo encontrado y reparado; la baja exige motivo. El rebaje se descuenta de `LIFE` solo si la herramienta ya tiene valor. La herramienta queda disponible en el TOGU, sin pasar por el rack, para poder volver a su máquina. Cada resultado se guarda en `servicios_herramienta` (`GET /workflow/tool-service-log`) con herramienta, solicitud, máquina, número de parte, estación, rebaje, justificación y quién lo hizo, para los Paretos de daños y ajustes.
4c. **Guardar en rack** (*Store in rack…*): cuando la máquina ya recibió una alterna, Tool Crib guarda la herramienta vieja ya terminada escaneando rack y herramienta, y la solicitud se cierra. Si se da de baja, se cierra al registrar la baja. Todo movimiento queda en el historial de la herramienta.
4d. **Expediente de incidente** (`incidentes_herramienta`): un reporte de daño exige quién estaba en la máquina (lista de usuarios de Punzonado; por defecto quien reporta), causa (`collision`, `normal_wear`, `wrong_setup`, `wrong_material`, `handling`, `lubrication`, `supplier_defect`, `unknown`) y momento (`setup`, `production`, `inspection`, `handling`, `unknown`). Máquina, número de parte, estación, TN y categoría se toman de la solicitud, y el turno del perfil del operador. Cuando Tool Crib tiene la herramienta puede registrar o corregir el incidente con *Record incident…* (`POST /workflow/service-requests/{id}/incident`), también para un afilado que resultó ser más: se conserva lo reportado por Manufactura (`reportado_como`, descripción) y lo hallado por Tool Crib (`hallazgo`). Consulta: `GET /workflow/tool-incidents`; lista de operadores: `GET /workflow/operators`. El ticket de Mantenimiento nunca se genera solo; lo levanta Tool Crib (siguiente fase).
5. **Cancelar:** el solicitante puede cancelar antes de que Tool Crib la tome (la herramienta vuelve a su posición); Tool Crib puede cancelar con motivo.

6. **Problema con programa:** desde el panel del número de parte, el operador elige el problema (herramienta incorrecta, estación o ángulo incorrecto, pieza fuera de especificación, el programa no corre, otro) y, opcionalmente, la estación. Llega a la misma bandeja de Requests. Tool Crib o Ingeniería lo toman con *Attending* y lo cierran con *Resolve*, escribiendo qué se hizo; el operador ve la resolución. Ingeniería solo atiende problemas de programa, no solicitudes de herramientas. La lista de problemas está en `PROGRAM_PROBLEMS` (`service_requests_api.py` y `web/src/labels.ts`) y es una lista inicial por ajustar.
7. **Avisos y tiempos:** la pestaña Requests muestra cuántas solicitudes esperan que alguien las tome (se pone roja si alguna lleva 15 min o más). Mientras la posición no tiene herramienta, cada fila muestra su tiempo de espera y se vuelve ámbar a los 5 min y roja a los 15 min (`WAIT_WARN_MIN` y `WAIT_LATE_MIN` en `web/src/components/Service.tsx`). El Tablero de secuencia y las tarjetas de máquina muestran "Waiting for tool" con la estación mientras una posición espera su herramienta, para que ninguna máquina corra un número de parte sin ella. Un kit no se puede entregar mientras la máquina tenga solicitudes de herramientas esperando su herramienta.

Pendientes: conteo de golpes por ejemplar (se decidió no contarlos por ahora) y el desglose de otras requisiciones.

## Escaneo y etiquetas

Toda pantalla que recibe un ID de herramienta o un código de ubicación acepta un escaneo o el ingreso manual; la interfaz y el servidor interpretan el código con las mismas reglas (`web/src/scan.ts` y `scan_codes.py`).

- **Lectores:** hoy funcionan los lectores USB o Bluetooth que escriben como teclado (el lector escribe el código y manda Enter; el campo es el mismo que se usa para teclear). La lectura con cámara de tableta o celular queda pendiente: los navegadores solo permiten la cámara en páginas con HTTPS, así que va junto con el paso a servidor.
- **Formato de las etiquetas:** el QR lleva un código que identifica al objeto, no sus datos (la vida, el estado y la ubicación cambian; el sistema muestra los datos vigentes al escanear). Prefijos: `TOOLTRACK-TOOL:<id>` para una herramienta, `TOOLTRACK-LOCATION:<código>` para cualquier ubicación (rack, TOGU, mantenimiento, taller), `TOOLTRACK-MACHINE:<código>` para una máquina, `TOOLTRACK-CART:<código>` para un carro y `TOOLTRACK-KIT:<token>`, la etiqueta desechable del kit que deja de servir al entregarse. El ID o código sin prefijo, tecleado a mano, se acepta siempre, y las etiquetas Code128 anteriores (`ID|TN|SIZE|…`) se leen como herramienta.
- **Tolerancia:** se ignoran espacios, saltos de línea y mayúsculas o minúsculas, y se rechaza una etiqueta de otro tipo con un mensaje claro (por ejemplo, escanear un rack donde se esperaba una herramienta).
- **Cómo se capturó:** cada movimiento guarda si el código se escaneó o se tecleó (`captura`: `scan` o `manual`; vacío en movimientos anteriores). La interfaz lo deduce de la velocidad de escritura: un lector escribe el código completo en milisegundos. Se ve en `GET /workflow/movements` (campo `capture`) y sirve para auditar cuántos ingresos manuales hay.
- **Dónde ya aplica:** registrar lo montado, recibir la herramienta, guardar en rack, destino de la recolección, selector de máquina de Manufacturing, escaneo de herramientas del kit y *Store in rack* de Tool Tracking.
- **Pendiente:** imprimir las etiquetas de herramientas, racks y TOGU con estos formatos (hoy solo se imprimen las de máquina y carro) y la lectura con cámara.

## Números de parte

Menú Números de parte: programas por tipo de máquina (4I, 2I, 45 STA). Formato fijo tipo TYEH-1193217_01-SW, familia (lista administrable) y NPI/Sustaining. Se guardan los datos leídos del .stp y el archivo (hoja de setup). Si el número ya existe se reemplaza, con motivo obligatorio; todo queda en la Bitácora de cambios (qué estaciones cambiaron, quién y cuándo).

Planeación por número de parte (`planning.py`): piezas por blank (`sym`) y run time por blank tomados del .stp. Cycle time = run time; cycle c/lead = cycle + 180 s (lead time por blank); UPH teórico = 3600 ÷ cycle × piezas por blank. Se guardan en `programas_parte` y se muestran en la ventana de programas.

## Tablero de secuencia (B1)

Menú Tablero de secuencia (`demand_board_ui.py`, API `demandas_api.py`, prefijo `/workflow/demandas`). Una tarjeta por máquina con semáforo, ordenadas por las que paran más pronto; clic en la tarjeta abre la cola completa de esa máquina (surtir, producido, editar, subir/bajar, quitar).

- Semáforo según las HR que le faltan a la NP que corre: rojo < 2 h, amarillo 2–4 h, verde > 4 h; azul = paro programado; gris = sin cola. Rojo también si la máquina está vacía, terminó su NP o se acabó la cola; amarillo si corre una NP que no está en la cola o no hay programa para calcular.
- Si la máquina está en rojo/amarillo y la siguiente NP no está surtida, la tarjeta pide "SURTIR SIGUIENTE YA" y abre Tool Dispensing con máquina y NP preseleccionadas.
- HR de una demanda = cantidad × cycle c/lead ÷ (piezas por blank × 3600). Pendiente se calcula con la cantidad menos lo producido.
- TIE TOTAL de la máquina = suma de HR de sus demandas; CUBIERTO = suma de HR de las demandas en TERMINADO.
- Estado automático: TERMINADO (pendiente 0), CORRIENDO (la máquina tiene montado ese NP; si el NP se repite solo la primera), SURTIENDO (surtido en proceso), FALTA HERRAMIENTAS (surtido pausado), EN MÁQUINA (surtido listo para entregar), NO INICIADO.
- Marcas manuales con comentario obligatorio: PARO PROGRAMADO, URGENTE, LÁSER, CORTE DE BLANCK (Planning, Engineering y supervisor de toolcrib).
- Permisos por campo en el PATCH de una demanda: cantidad y orden → `demand.manage` (Planning); producido → `demand.progress` (toolcrib); marca y comentario → `demand.mark`. Límite de 1,000,000 de piezas, NP en mayúsculas, producido no puede superar la cantidad y no se aceptan máquinas desactivadas.
- Quitar una demanda la desactiva (no se borra).
- Se actualiza solo cada 30 s; si el backend no responde lo avisa en la barra superior sin mensajes emergentes.
- Pendiente: el lead time y la integración con MESSPRO quedan para después; la producción se captura a mano.

## Surtido desde programas guardados y traspaso

- El surtido ya no carga `.stp`: se elige máquina, se busca el número de parte (solo los del tipo de esa máquina), se compara y se elige un carro libre.
- Al elegir la máquina se muestra qué número de parte corre ("▶ Corriendo en ..."). Si el operador elige otro como "actual", se pide confirmar y motivo; la corrección queda en la bitácora de la máquina (`eventos_maquina`).
- Panel "Estado de carros": libre/surtiendo/pausado/listo, qué falta, y botones Retomar / Liberar. Pausar o cancelar exige motivo.
- Botón "Máquinas en tiempo real / Traspaso": lista qué corre en cada máquina y permite traspasar un número de parte a otra máquina del mismo tipo y vacía, con motivo obligatorio. Las herramientas montadas se mueven con él. Endpoints: `GET /workflow/machines/running`, `POST /workflow/machines/transfer`.
