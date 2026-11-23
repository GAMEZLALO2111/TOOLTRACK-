import os
import sqlite3
from decimal import Decimal, InvalidOperation
from datetime import datetime

# Rutas relativas (tooltrack.db, CSV) siempre respecto a esta carpeta, sin importar desde dónde se arranque.
os.chdir(os.path.dirname(os.path.abspath(__file__)))

from fastapi import FastAPI, HTTPException, Body, Depends, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import List, Optional
from sqlalchemy import Float, cast, func
from sqlalchemy.exc import IntegrityError
from catalog_reconciliation import preview_legacy_reconciliation
from models.inventory import (
    Auditoria,
    ConfiguracionTN,
    ComponenteSurtido,
    Herramienta,
    HerramientaCatalogo,
    MovimientoHerramienta,
    NumeroHerramienta,
    PendienteRecoger,
    RequisitoSetup,
    ReservaHerramienta,
    SolicitudServicio,
    Ubicacion,
    Usuario,
    VarianteHerramienta,
    Base,
)
from tool_types import ALL_INVENTORY_TYPES, INVENTORY_TYPES_BY_CATEGORY, LEGACY_TYPE_NAMES, PUNCH_DIE_TYPES
from db import DB_PATH, SessionLocal, engine
from workflow_api import router as workflow_router, seed_default_locations
from tool_tracking_api import router as tool_tracking_router
from machines_api import router as machines_router
from programs_api import router as programs_router
from demandas_api import router as demandas_router
from users_api import router as users_router
from kitting_api import router as kitting_router
from service_requests_api import router as service_requests_router
from maintenance_api import router as maintenance_router
from retirement_api import router as retirement_router
from machine_operators_api import router as machine_operators_router
from insights_api import router as insights_router
from planning_api import router as planning_router
from strokes_api import router as strokes_router
from standard_api import router as standard_router
from ctb_api import router as ctb_router
from records_api import router as records_router
from tool_file_api import router as tool_file_router
from tool_params import router as tool_params_router
import tool_params
from tool_images import router as tool_images_router
from auth import AuthMiddleware, normalize_role as _canonical_role
from passwords import hash_password, verify_password, secrets_compare, ensure_password_hash
from planning import calcular_planeacion

# Sin /docs ni /openapi.json: el esquema de la API no se publica.
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(AuthMiddleware)
app.include_router(workflow_router)
app.include_router(tool_tracking_router)
app.include_router(machines_router)
app.include_router(programs_router)
app.include_router(demandas_router)
app.include_router(kitting_router)
app.include_router(service_requests_router)
app.include_router(maintenance_router)
app.include_router(retirement_router)
app.include_router(machine_operators_router)
app.include_router(insights_router)
app.include_router(planning_router)
app.include_router(strokes_router)
app.include_router(standard_router)
app.include_router(ctb_router)
app.include_router(records_router)
app.include_router(tool_file_router)
app.include_router(tool_params_router)
app.include_router(tool_images_router)
# Aplicación web (web/dist). Se compila con build_web.bat; sin compilar, "/" avisa cómo hacerlo.
WEB_DIST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web", "dist")
if os.path.isdir(os.path.join(WEB_DIST, "assets")):
    app.mount("/assets", StaticFiles(directory=os.path.join(WEB_DIST, "assets")), name="web-assets")
if os.path.isdir(os.path.join(WEB_DIST, "brand")):
    app.mount("/brand", StaticFiles(directory=os.path.join(WEB_DIST, "brand")), name="web-brand")


@app.get("/", include_in_schema=False)
def web_index():
    index = os.path.join(WEB_DIST, "index.html")
    if not os.path.isfile(index):
        return JSONResponse({"detail": "The web app is not built yet. Run build_web.bat"}, status_code=404)
    return FileResponse(index, headers={"Cache-Control": "no-cache"})

@app.on_event("startup")
def initialize_workflow_locations():
    seed_default_locations()


DEFAULT_ADMIN_USERNAME = os.getenv("TOOLTRACK_ADMIN_USERNAME", "").strip()
DEFAULT_ADMIN_PASSWORD = os.getenv("TOOLTRACK_ADMIN_PASSWORD", "")


def normalize_role(role: Optional[str]) -> str:
    return _canonical_role(role) or "TOOLCRIB_OPERATOR"


# --- MIGRACIÓN AUTOMÁTICA DE USUARIOS (campo 'role' y 'activo', roles heredados -> roles nuevos) ---
def migrate_usuarios_table(db_path=DB_PATH):
    import sqlite3
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(usuarios)")
    columns = [col[1] for col in cur.fetchall()]
    if 'role' not in columns:
        cur.execute("ALTER TABLE usuarios ADD COLUMN role TEXT DEFAULT 'TOOLCRIB_OPERATOR'")
        if 'is_admin' in columns:
            # Solo en la primera migración: después la columna is_admin es obsoleta y no debe pisar los roles.
            cur.execute("UPDATE usuarios SET role='admin' WHERE is_admin=1")
            cur.execute("UPDATE usuarios SET role='operator' WHERE is_admin=0")
        conn.commit()
    if 'activo' not in columns:
        cur.execute("ALTER TABLE usuarios ADD COLUMN activo INTEGER NOT NULL DEFAULT 1")
        conn.commit()
    for column in ("employee_number", "full_name", "area", "shift"):
        if column not in columns:
            cur.execute(f"ALTER TABLE usuarios ADD COLUMN {column} TEXT")
    cur.execute("DROP INDEX IF EXISTS uq_usuarios_employee_number")
    cur.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_usuarios_employee_number "
        "ON usuarios(lower(employee_number)) WHERE activo = 1"
    )
    for legacy, canonical in (("admin", "SYSTEM_ADMIN"), ("supervisor", "TOOLCRIB_SUPERVISOR"),
                              ("operator", "TOOLCRIB_OPERATOR")):
        cur.execute("UPDATE usuarios SET role=? WHERE lower(role)=?", (canonical, legacy))
    cur.execute("UPDATE usuarios SET role='TOOLCRIB_OPERATOR' WHERE role IS NULL OR trim(role)=''")
    conn.commit()
    conn.close()
migrate_usuarios_table()


def migrate_service_requests_table(db_path=DB_PATH):
    """Service requests gained optional position fields (program problems have none) and two columns."""
    import sqlite3
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    info = {row[1]: row for row in cur.execute("PRAGMA table_info(solicitudes_servicio)").fetchall()}
    if not info:
        conn.close()
        return
    if info["requisito_id"][3] == 1:  # NOT NULL en la versión anterior: se reconstruye (la tabla es pequeña)
        old_columns = list(info)
        cur.execute("DROP INDEX IF EXISTS ix_solicitudes_servicio_estado")
        cur.execute("DROP INDEX IF EXISTS ix_solicitudes_servicio_maquina")
        cur.execute("ALTER TABLE solicitudes_servicio RENAME TO solicitudes_servicio_old")
        conn.commit()
        conn.close()
        from sqlalchemy import create_engine

        target_engine = create_engine(f"sqlite:///{db_path}")
        SolicitudServicio.__table__.create(bind=target_engine)
        target_engine.dispose()
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        new_columns = [row[1] for row in cur.execute("PRAGMA table_info(solicitudes_servicio)").fetchall()]
        shared = ", ".join(column for column in old_columns if column in new_columns)
        cur.execute(f"INSERT INTO solicitudes_servicio ({shared}) SELECT {shared} FROM solicitudes_servicio_old")
        cur.execute("DROP TABLE solicitudes_servicio_old")
    else:
        for column in ("problema", "resolucion"):
            if column not in info:
                cur.execute(f"ALTER TABLE solicitudes_servicio ADD COLUMN {column} TEXT")
        if "recibida_en" not in info:
            cur.execute("ALTER TABLE solicitudes_servicio ADD COLUMN recibida_en DATETIME")
        if "demanda_id" not in info:
            cur.execute("ALTER TABLE solicitudes_servicio ADD COLUMN demanda_id INTEGER")
    conn.commit()
    conn.close()
migrate_service_requests_table()


def migrate_planning_columns(db_path=DB_PATH):
    """Work order on each demand; the editable UPH on each part number program."""
    import sqlite3
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    for table, additions in (
        ("demandas_planeacion", {"orden_trabajo": "TEXT"}),
        ("programas_parte", {
            "uph_editado": "REAL", "uph_motivo": "TEXT", "uph_editado_por": "TEXT", "uph_editado_en": "DATETIME",
        }),
    ):
        columns = {row[1] for row in cur.execute(f"PRAGMA table_info({table})").fetchall()}
        if not columns:
            continue
        for name, kind in additions.items():
            if name not in columns:
                cur.execute(f"ALTER TABLE {table} ADD COLUMN {name} {kind}")
    conn.commit()
    conn.close()
migrate_planning_columns()


def migrate_movements_table(db_path=DB_PATH):
    """Movements record whether a tool code was scanned or typed."""
    import sqlite3
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    columns = [row[1] for row in cur.execute("PRAGMA table_info(movimientos_herramienta)").fetchall()]
    if columns and "captura" not in columns:
        cur.execute("ALTER TABLE movimientos_herramienta ADD COLUMN captura TEXT")
        conn.commit()
    conn.close()
migrate_movements_table()
# Crear usuario admin principal si no existe
def ensure_admin_user():
    db = SessionLocal()
    try:
        existing_admin = db.query(Usuario).filter(Usuario.role == "SYSTEM_ADMIN").first()
        if existing_admin:
            return
        if not DEFAULT_ADMIN_USERNAME or not DEFAULT_ADMIN_PASSWORD:
            raise RuntimeError(
                "No admin user exists. Set TOOLTRACK_ADMIN_USERNAME and "
                "TOOLTRACK_ADMIN_PASSWORD before starting the API."
            )
        username = DEFAULT_ADMIN_USERNAME.lower()
        existing_user = db.query(Usuario).filter(Usuario.username.ilike(username)).first()
        if existing_user:
            existing_user.role = "SYSTEM_ADMIN"
            existing_user.activo = True
            existing_user.password = hash_password(DEFAULT_ADMIN_PASSWORD)
        else:
            db.add(Usuario(
                username=username,
                password=hash_password(DEFAULT_ADMIN_PASSWORD),
                role="SYSTEM_ADMIN",
            ))
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
ensure_admin_user()


def ensure_password_hashes():
    db = SessionLocal()
    try:
        users = db.query(Usuario).all()
        for user in users:
            if user.password and not user.password.startswith("pbkdf2_sha256$"):
                user.password = hash_password(user.password)
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
ensure_password_hashes()


def ensure_ubicacion_column():
    conn = sqlite3.connect('tooltrack.db')
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(herramientas)")
    columns = [col[1] for col in cursor.fetchall()]
    if 'ubicacion' not in columns:
        cursor.execute("ALTER TABLE herramientas ADD COLUMN ubicacion TEXT")
        conn.commit()
    conn.close()
ensure_ubicacion_column()


def ensure_estandar_column():
    conn = sqlite3.connect('tooltrack.db')
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(ubicaciones)")
    columns = [col[1] for col in cursor.fetchall()]
    if columns and 'estandar' not in columns:
        cursor.execute("ALTER TABLE ubicaciones ADD COLUMN estandar BOOLEAN NOT NULL DEFAULT 0")
        conn.commit()
    conn.close()
ensure_estandar_column()


def ensure_estado_herramienta_column():
    conn = sqlite3.connect('tooltrack.db')
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(herramientas)")
    columns = [row[1] for row in cursor.fetchall()]
    if 'estado' not in columns:
        cursor.execute("ALTER TABLE herramientas ADD COLUMN estado TEXT NOT NULL DEFAULT 'disponible'")
        conn.commit()
    conn.close()


ensure_estado_herramienta_column()


def ensure_estacion_columns():
    conn = sqlite3.connect('tooltrack.db')
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(estaciones_maquina)")
    columns = [row[1] for row in cursor.fetchall()]
    if columns and 'posicion' not in columns:
        cursor.execute("ALTER TABLE estaciones_maquina ADD COLUMN posicion TEXT")
    if columns and 'autoindex' not in columns:
        cursor.execute("ALTER TABLE estaciones_maquina ADD COLUMN autoindex BOOLEAN NOT NULL DEFAULT 0")
    conn.commit()
    conn.close()


ensure_estacion_columns()


def ensure_maquina_columns():
    conn = sqlite3.connect('tooltrack.db')
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(maquinas)")
    columns = [row[1] for row in cursor.fetchall()]
    if columns:
        for name in ('espesor_min', 'edificio', 'blank_max', 'area_pallet'):
            if name not in columns:
                cursor.execute(f"ALTER TABLE maquinas ADD COLUMN {name} TEXT")
    conn.commit()
    conn.close()


ensure_maquina_columns()


def ensure_familia_columns():
    conn = sqlite3.connect('tooltrack.db')
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(familias_parte)")
    columns = [row[1] for row in cursor.fetchall()]
    if columns and 'cliente' not in columns:
        cursor.execute("ALTER TABLE familias_parte ADD COLUMN cliente TEXT")
    conn.commit()
    conn.close()


ensure_familia_columns()


def ensure_surtido_columns():
    conn = sqlite3.connect('tooltrack.db')
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(surtidos)")
    columns = [row[1] for row in cursor.fetchall()]
    if columns and 'motivo' not in columns:
        cursor.execute("ALTER TABLE surtidos ADD COLUMN motivo TEXT")
    if columns and 'anterior_manual' not in columns:
        cursor.execute("ALTER TABLE surtidos ADD COLUMN anterior_manual BOOLEAN NOT NULL DEFAULT 0")
    conn.commit()
    conn.close()


def ensure_planeacion_columns():
    conn = sqlite3.connect('tooltrack.db')
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(programas_parte)")
    columns = {row[1] for row in cursor.fetchall()}
    nuevas = {
        'piezas_blank': 'INTEGER',
        'cycle_time_s': 'INTEGER',
        'cycle_con_lead_s': 'INTEGER',
        'uph_teorico': 'REAL',
    }
    if columns:
        for nombre, tipo in nuevas.items():
            if nombre not in columns:
                cursor.execute(f"ALTER TABLE programas_parte ADD COLUMN {nombre} {tipo}")
        pendientes = cursor.execute(
            "SELECT id, contenido FROM programas_parte WHERE cycle_time_s IS NULL"
        ).fetchall()
        for program_id, contenido in pendientes:
            plan = calcular_planeacion(contenido)
            if plan:
                cursor.execute(
                    "UPDATE programas_parte SET piezas_blank=?, cycle_time_s=?, cycle_con_lead_s=?, uph_teorico=? WHERE id=?",
                    (plan['piezas_blank'], plan['cycle_time_s'], plan['cycle_con_lead_s'], plan['uph_teorico'], program_id),
                )
    conn.commit()
    conn.close()


def ensure_unique_open_kits(path='tooltrack.db'):
    """Un solo surtido abierto por carro, garantizado por la base. Una máquina puede tener varios en fila."""
    conn = sqlite3.connect(path)
    try:
        conn.execute("DROP INDEX IF EXISTS uq_surtido_abierto_maquina")
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_surtido_abierto_carro ON surtidos(carro_codigo) "
            "WHERE estado IN ('preparing', 'paused', 'ready')"
        )
        conn.commit()
    except sqlite3.IntegrityError:
        # Ya existen surtidos abiertos duplicados: no se bloquea el arranque, pero queda sin candado.
        conn.rollback()
    finally:
        conn.close()


def ensure_tool_inventory_columns(path=None):
    """Altura medida, stripper propio e imágenes; y los tipos con nombre anterior pasan al nombre vigente."""
    conn = sqlite3.connect(path or DB_PATH)
    try:
        for table, new_columns in (
            ("herramientas", {"altura_total": "TEXT", "stripper_override": "TEXT", "imagen": "TEXT"}),
            ("numeros_herramienta", {"imagen": "TEXT"}),
            ("solicitudes_servicio", {"cambio_piloto": "INTEGER NOT NULL DEFAULT 0", "tn_instalado": "TEXT"}),
        ):
            existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            if not existing:
                continue
            for column, kind in new_columns.items():
                if column not in existing:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")
        for old, new in LEGACY_TYPE_NAMES.items():
            for table in ("herramientas", "configuraciones_tn"):
                try:
                    conn.execute(f"UPDATE {table} SET type=? WHERE type=?", (new, old))
                except sqlite3.OperationalError:
                    pass
        conn.commit()
    finally:
        conn.close()


ensure_surtido_columns()
ensure_planeacion_columns()
ensure_unique_open_kits()
ensure_tool_inventory_columns()


def ensure_production_log(path=None):
    """Cierre de demandas y registro histórico de producción; lo ya producido entra como saldo inicial."""
    conn = sqlite3.connect(path or DB_PATH)
    try:
        existing = {row[1] for row in conn.execute("PRAGMA table_info(demandas_planeacion)")}
        if not existing:
            return
        for column, kind in (
            ("estado_cierre", "TEXT"), ("motivo_cierre", "TEXT"), ("cerrado_por", "TEXT"), ("cerrado_en", "DATETIME"),
        ):
            if column not in existing:
                conn.execute(f"ALTER TABLE demandas_planeacion ADD COLUMN {column} {kind}")
        conn.execute("UPDATE demandas_planeacion SET numero_parte = UPPER(numero_parte) WHERE numero_parte != UPPER(numero_parte)")
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='registros_produccion'").fetchone():
            return
        conn.execute(
            """
            INSERT INTO registros_produccion
                (demanda_id, numero_parte, maquina_codigo, piezas, fecha_produccion, fecha_estimada,
                 fuente, capturado_por, capturado_en, nota)
            SELECT d.id, d.numero_parte, d.maquina_codigo, d.piezas_producidas, d.actualizado_en, 1,
                   'saldo_inicial', 'migration', CURRENT_TIMESTAMP,
                   'Produced before the history existed; the date is the last update of the demand'
            FROM demandas_planeacion d
            WHERE d.piezas_producidas > 0
              AND NOT EXISTS (SELECT 1 FROM registros_produccion r WHERE r.demanda_id = d.id)
            """
        )
        conn.commit()
    finally:
        conn.close()


ensure_production_log()


def ensure_machine_availability(path=None):
    """Una máquina se puede deshabilitar con motivo; las columnas nuevas se agregan a la tabla existente."""
    conn = sqlite3.connect(path or DB_PATH)
    try:
        existing = {row[1] for row in conn.execute("PRAGMA table_info(maquinas)")}
        if not existing:
            return
        for column, kind in (
            ("deshabilitada", "BOOLEAN NOT NULL DEFAULT 0"), ("deshab_tipo", "TEXT"), ("deshab_motivo", "TEXT"),
            ("deshab_desde", "DATETIME"), ("deshab_por", "TEXT"), ("regreso_estimado", "DATE"),
        ):
            if column not in existing:
                conn.execute(f"ALTER TABLE maquinas ADD COLUMN {column} {kind}")
        conn.commit()
    finally:
        conn.close()


ensure_machine_availability()


def ensure_stroke_columns(path=None):
    """Contadores de golpes en la herramienta y marca de ticket automático en la solicitud."""
    conn = sqlite3.connect(path or DB_PATH)
    try:
        for table, new_columns in (
            ("herramientas", {"golpes_servicio": "FLOAT NOT NULL DEFAULT 0", "golpes_total": "FLOAT NOT NULL DEFAULT 0"}),
            ("solicitudes_servicio", {"automatico": "BOOLEAN NOT NULL DEFAULT 0", "golpes_al_crear": "FLOAT"}),
        ):
            existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            if not existing:
                continue
            for column, kind in new_columns.items():
                if column not in existing:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")
        conn.commit()
    finally:
        conn.close()


ensure_stroke_columns()

class HerramientaCreate(BaseModel):
    id: str
    tn: Optional[str] = None
    size: Optional[str] = None
    type: Optional[str] = None
    life: Optional[str] = None
    clearance: Optional[str] = None
    so: Optional[str] = None
    proveedor: Optional[str] = None
    categoria: Optional[str] = None
    ubicacion: Optional[str] = None  # Nuevo campo

class HerramientaRead(BaseModel):
    id: str
    tn: Optional[str] = None
    size: Optional[str] = None
    type: Optional[str] = None
    life: Optional[str] = None
    clearance: Optional[str] = None
    so: Optional[str] = None
    proveedor: Optional[str] = None
    categoria: Optional[str] = None
    ubicacion: Optional[str] = None
    class Config:
        from_attributes = True

class NumeroHerramientaCreate(BaseModel):
    tn: str

class ConfiguracionTNCreate(BaseModel):
    tn: str
    size: str
    type: str

class VarianteHerramientaCreate(BaseModel):
    categoria: str
    clearance: Optional[str] = None

class HerramientaCatalogoCreate(BaseModel):
    id: str
    variante_id: int
    life: str
    so: Optional[str] = None
    ubicacion: Optional[str] = None

class RegistroInventarioCreate(BaseModel):
    id: str
    categoria: str
    type: str
    tn: Optional[str] = None
    size: Optional[str] = None
    life: Optional[str] = None
    altura: Optional[str] = None  # Punzones: altura total medida de la punta
    stripper: Optional[str] = None  # Espesor del stripper propio (si no, el de su tamaño)
    clearance: Optional[str] = None
    so: Optional[str] = None
    proveedor: Optional[str] = None
    ubicacion: Optional[str] = None

class RegistroInventarioUpdate(RegistroInventarioCreate):
    pass

class BarcodeScan(BaseModel):
    data: str
    tipo: str  # 'punch' o 'die'

class MantenimientoCreate(BaseModel):
    herramienta_id: str
    tipo: str  # preventivo/correctivo
    descripcion: str
    fecha: str

app.include_router(users_router)

def catalog_variant_read(variant: VarianteHerramienta):
    return {
        "id": variant.id,
        "tn": variant.tn,
        "size": variant.size,
        "type": variant.configuracion.type,
        "categoria": variant.categoria,
        "clearance": variant.clearance,
    }

def catalog_tool_read(tool: Herramienta, variant: VarianteHerramienta):
    return {
        "id": tool.id,
        "tn": tool.tn,
        "size": tool.size,
        "type": tool.type,
        "life": tool.life,
        "clearance": tool.clearance,
        "so": tool.so,
        "categoria": tool.categoria,
        "ubicacion": tool.ubicacion,
        "variante_id": variant.id,
    }

@app.get("/catalogo/tn")
def listar_numeros_herramienta():
    db = SessionLocal()
    try:
        return [{"tn": item.tn} for item in db.query(NumeroHerramienta).order_by(NumeroHerramienta.tn).all()]
    finally:
        db.close()

@app.post("/catalogo/tn", status_code=201)
def crear_numero_herramienta(item: NumeroHerramientaCreate):
    tn = item.tn.strip()
    if not tn:
        raise HTTPException(status_code=422, detail="TN is required")
    db = SessionLocal()
    try:
        if db.query(NumeroHerramienta).filter(NumeroHerramienta.tn == tn).first():
            raise HTTPException(status_code=409, detail="TN already exists")
        db.add(NumeroHerramienta(tn=tn))
        db.commit()
        return {"tn": tn}
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="TN already exists")
    finally:
        db.close()

@app.get("/catalogo/configuraciones")
def listar_configuraciones_tn():
    db = SessionLocal()
    try:
        configurations = (
            db.query(ConfiguracionTN)
            .order_by(ConfiguracionTN.tn, ConfiguracionTN.size)
            .all()
        )
        return [
            {
                "tn": config.tn,
                "size": config.size,
                "type": config.type,
                "variantes": [
                    {
                        "id": variant.id,
                        "categoria": variant.categoria,
                        "clearance": variant.clearance,
                    }
                    for variant in sorted(config.variantes, key=lambda item: (item.categoria, item.clearance or ""))
                ],
            }
            for config in configurations
        ]
    finally:
        db.close()

@app.post("/catalogo/configuraciones", status_code=201)
def crear_configuracion_tn(item: ConfiguracionTNCreate):
    tn = item.tn.strip()
    size = item.size.strip().upper()
    type_ = item.type.strip().upper()
    if not tn or not size or not type_:
        raise HTTPException(status_code=422, detail="TN, SIZE and TYPE are required")
    if type_ not in PUNCH_DIE_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"TYPE '{type_}' is not a supported punch/die geometry",
        )

    db = SessionLocal()
    try:
        if not db.query(NumeroHerramienta).filter(NumeroHerramienta.tn == tn).first():
            raise HTTPException(status_code=404, detail="TN is not registered")
        existing = (
            db.query(ConfiguracionTN)
            .filter(ConfiguracionTN.tn == tn, ConfiguracionTN.size == size)
            .first()
        )
        if existing:
            if existing.type == type_:
                raise HTTPException(status_code=409, detail="TN/SIZE configuration already exists")
            raise HTTPException(status_code=409, detail="TYPE conflicts with the existing TN/SIZE geometry")
        db.add(ConfiguracionTN(tn=tn, size=size, type=type_))
        db.commit()
        return {"tn": tn, "size": size, "type": type_}
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="TN/SIZE configuration already exists")
    finally:
        db.close()

@app.get("/catalogo/variantes")
def listar_variantes_herramienta():
    db = SessionLocal()
    try:
        variants = (
            db.query(VarianteHerramienta)
            .join(ConfiguracionTN)
            .order_by(VarianteHerramienta.tn, VarianteHerramienta.size, VarianteHerramienta.categoria, VarianteHerramienta.clearance)
            .all()
        )
        return [catalog_variant_read(variant) for variant in variants]
    finally:
        db.close()

@app.post("/catalogo/configuraciones/{tn}/{size}/variantes", status_code=201)
def crear_variante_herramienta(tn: str, size: str, item: VarianteHerramientaCreate):
    tn = tn.strip()
    size = size.strip().upper()
    categoria = item.categoria.strip().lower()
    if categoria not in {"punzon", "matriz"}:
        raise HTTPException(status_code=422, detail="CATEGORY must be 'punzon' or 'matriz'")

    clearance = None
    if categoria == "punzon":
        if item.clearance not in (None, ""):
            raise HTTPException(status_code=422, detail="Punch variants cannot have CLEARANCE")
    else:
        if item.clearance is None or not item.clearance.strip():
            raise HTTPException(status_code=422, detail="Die variants require CLEARANCE")
        try:
            clearance_value = Decimal(item.clearance.strip())
        except InvalidOperation:
            raise HTTPException(status_code=422, detail="CLEARANCE must be numeric")
        if not clearance_value.is_finite() or clearance_value < 0:
            raise HTTPException(status_code=422, detail="CLEARANCE must be a non-negative number")
        if clearance_value.normalize().as_tuple().exponent < -3:
            raise HTTPException(status_code=422, detail="CLEARANCE supports at most three decimal places")
        clearance = f"{clearance_value:.3f}"
        if clearance not in CLEARANCE_CHOICES:
            raise HTTPException(status_code=422, detail="CLEARANCE must be chosen from 0.004 to 0.030 in steps of 0.001")

    db = SessionLocal()
    try:
        config = (
            db.query(ConfiguracionTN)
            .filter(ConfiguracionTN.tn == tn, ConfiguracionTN.size == size)
            .first()
        )
        if not config:
            raise HTTPException(status_code=404, detail="TN/SIZE configuration not found")
        existing = (
            db.query(VarianteHerramienta)
            .filter(
                VarianteHerramienta.tn == tn,
                VarianteHerramienta.size == size,
                VarianteHerramienta.categoria == categoria,
                VarianteHerramienta.clearance == clearance,
            )
            .first()
        )
        if existing:
            raise HTTPException(status_code=409, detail="Tool variant already exists")
        variant = VarianteHerramienta(
            tn=tn,
            size=size,
            categoria=categoria,
            clearance=clearance,
        )
        db.add(variant)
        db.commit()
        db.refresh(variant)
        return catalog_variant_read(variant)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Tool variant already exists")
    finally:
        db.close()

@app.get("/catalogo/herramientas")
def listar_herramientas_catalogadas():
    db = SessionLocal()
    try:
        records = (
            db.query(Herramienta, VarianteHerramienta)
            .join(HerramientaCatalogo, HerramientaCatalogo.herramienta_id == Herramienta.id)
            .join(VarianteHerramienta, VarianteHerramienta.id == HerramientaCatalogo.variante_id)
            .order_by(Herramienta.id)
            .all()
        )
        return [catalog_tool_read(tool, variant) for tool, variant in records]
    finally:
        db.close()

@app.get("/catalogo/conciliacion/preview")
def previsualizar_conciliacion_catalogo():
    db = SessionLocal()
    try:
        legacy_tools = [
            {
                "id": tool.id,
                "tn": tool.tn,
                "size": tool.size,
                "type": tool.type,
                "categoria": tool.categoria,
                "clearance": tool.clearance,
            }
            for tool in db.query(Herramienta).order_by(Herramienta.id).all()
        ]
        catalog_tns = [
            item.tn
            for item in db.query(NumeroHerramienta).all()
        ]
        catalog_configurations = [
            {"tn": item.tn, "size": item.size, "type": item.type}
            for item in db.query(ConfiguracionTN).all()
        ]
        catalog_variants = [
            {
                "id": item.id,
                "tn": item.tn,
                "size": item.size,
                "categoria": item.categoria,
                "clearance": item.clearance,
            }
            for item in db.query(VarianteHerramienta).all()
        ]
        catalog_links = [
            {"herramienta_id": item.herramienta_id, "variante_id": item.variante_id}
            for item in db.query(HerramientaCatalogo).all()
        ]
        return preview_legacy_reconciliation(
            legacy_tools,
            catalog_tns,
            catalog_configurations,
            catalog_variants,
            catalog_links,
        )
    finally:
        db.close()

@app.post("/catalogo/herramientas", status_code=201)
def crear_herramienta_catalogada(item: HerramientaCatalogoCreate):
    tool_id = item.id.strip()
    if not tool_id:
        raise HTTPException(status_code=422, detail="Physical tool ID is required")
    try:
        life_value = Decimal(item.life.strip())
    except InvalidOperation:
        raise HTTPException(status_code=422, detail="LIFE must be numeric")
    if not life_value.is_finite() or life_value < 0:
        raise HTTPException(status_code=422, detail="LIFE must be a non-negative number")

    db = SessionLocal()
    try:
        if db.query(Herramienta).filter(Herramienta.id == tool_id).first():
            raise HTTPException(status_code=409, detail="Physical tool ID already exists")
        variant = db.query(VarianteHerramienta).filter(VarianteHerramienta.id == item.variante_id).first()
        if not variant:
            raise HTTPException(status_code=404, detail="Tool variant not found")
        config = variant.configuracion
        tool = Herramienta(
            id=tool_id,
            tn=variant.tn,
            size=variant.size,
            type=config.type,
            life=format(life_value, "f"),
            clearance=variant.clearance,
            so=item.so.strip() if item.so else None,
            categoria=variant.categoria,
            ubicacion=item.ubicacion.strip() if item.ubicacion else None,
        )
        tool.catalogo = HerramientaCatalogo(variante=variant)
        db.add(tool)
        db.commit()
        return catalog_tool_read(tool, variant)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Physical tool ID already exists")
    finally:
        db.close()

CLEARANCE_CHOICES = tuple(f"{Decimal(step) / 1000:.3f}" for step in range(4, 31))


def _three_decimals(text: str, label: str) -> Decimal:
    try:
        value = Decimal(text.strip())
    except InvalidOperation:
        raise HTTPException(status_code=422, detail=f"{label} must be numeric") from None
    if not value.is_finite() or value < 0:
        raise HTTPException(status_code=422, detail=f"{label} must be a non-negative number")
    if value.normalize().as_tuple().exponent < -3:
        raise HTTPException(status_code=422, detail=f"{label} supports at most three decimal places")
    return value


def _same_decimal(stored: str, value: Decimal) -> bool:
    try:
        return Decimal(str(stored)) == value
    except InvalidOperation:
        return False


def normalize_inventory_item(
    item: RegistroInventarioCreate,
    existing_type: Optional[str] = None,
    existing_category: Optional[str] = None,
    params=None,
    existing_clearance: Optional[str] = None,
):
    params = params or {key: Decimal(value) for key, value in tool_params.DEFAULTS.items()}
    tool_id = item.id.strip()
    categoria_input = item.categoria.strip().lower()
    categoria_aliases = {
        "punch": "punzon",
        "punzon": "punzon",
        "die": "matriz",
        "matriz": "matriz",
        "holder": "holder",
        "accessory": "accesorio",
        "accesorio": "accesorio",
    }
    categoria = categoria_aliases.get(categoria_input)
    type_ = item.type.strip().upper() if item.type else ""
    if not tool_id or not type_:
        raise HTTPException(status_code=422, detail="Physical ID and TYPE are required")
    if not categoria:
        raise HTTPException(status_code=422, detail="Unsupported tool category")
    allowed_types = INVENTORY_TYPES_BY_CATEGORY[categoria]
    legacy_type_can_be_preserved = (
        existing_category == categoria
        and existing_type
        and existing_type.strip().upper() == type_
        and type_ not in ALL_INVENTORY_TYPES
    )
    if type_ not in allowed_types and not legacy_type_can_be_preserved:
        raise HTTPException(
            status_code=422,
            detail=f"TYPE '{type_}' is not valid for category '{categoria}'",
        )

    tn = item.tn.strip() if item.tn else ""
    size = item.size.strip().upper() if item.size else ""
    clearance = None
    life = None
    altura_total = None
    stripper_override = None

    if categoria in {"punzon", "matriz"}:
        if not tn or size not in {"A", "B", "C", "D", "E"}:
            raise HTTPException(status_code=422, detail="TN and SIZE A-E are required for punches and dies")
        height_text = item.altura.strip() if item.altura and item.altura.strip() else ""
        if categoria == "punzon" and height_text:
            height = _three_decimals(height_text, "TOTAL HEIGHT")
            override_text = item.stripper.strip() if item.stripper and item.stripper.strip() else ""
            if override_text:
                stripper_override = f"{_three_decimals(override_text, 'STRIPPER'):.3f}"
            stripper = tool_params.stripper_for(params, size, stripper_override)
            if height <= stripper:
                raise HTTPException(
                    status_code=422,
                    detail=f"TOTAL HEIGHT must be greater than the stripper plate ({stripper:.3f} in)",
                )
            altura_total = f"{height:.3f}"
            life = f"{height - stripper:.3f}"
        else:
            if not item.life or not item.life.strip():
                raise HTTPException(
                    status_code=422,
                    detail="TOTAL HEIGHT is required for punches" if categoria == "punzon" else "HEIGHT is required for dies",
                )
            life = f"{_three_decimals(item.life.strip(), 'LIFE'):.3f}"

        if categoria == "punzon":
            if item.clearance and item.clearance.strip():
                raise HTTPException(status_code=422, detail="Punches cannot have CLEARANCE")
        else:
            if not item.clearance or not item.clearance.strip():
                raise HTTPException(status_code=422, detail="CLEARANCE is required for dies")
            clearance_value = _three_decimals(item.clearance.strip(), "CLEARANCE")
            clearance = f"{clearance_value:.3f}"
            kept_legacy = existing_clearance is not None and _same_decimal(existing_clearance, clearance_value)
            if clearance not in CLEARANCE_CHOICES and not kept_legacy:
                raise HTTPException(
                    status_code=422,
                    detail="CLEARANCE must be chosen from 0.004 to 0.030 in steps of 0.001",
                )

        if item.tn and item.tn.strip() and not type_:
            raise HTTPException(status_code=422, detail="TYPE is required")
    elif any(value and value.strip() for value in (item.tn, item.size, item.clearance)):
        raise HTTPException(
            status_code=422,
            detail="TN, SIZE and CLEARANCE are only used for punch and die tools",
        )
    else:
        life = item.life.strip() if item.life and item.life.strip() else None

    return {
        "id": tool_id,
        "categoria": categoria,
        "tn": tn or None,
        "size": size or None,
        "type": type_,
        "life": life,
        "altura_total": altura_total,
        "stripper_override": stripper_override,
        "clearance": clearance,
        "so": item.so.strip() if item.so and item.so.strip() else None,
        "proveedor": item.proveedor.strip().upper() if item.proveedor and item.proveedor.strip() else None,
        "proveedor_given": item.proveedor is not None,
        "ubicacion": item.ubicacion.strip() if item.ubicacion and item.ubicacion.strip() else None,
    }


def get_or_create_catalog_variant(db, record):
    if record["categoria"] not in {"punzon", "matriz"}:
        return None

    tool_number = db.query(NumeroHerramienta).filter(
        NumeroHerramienta.tn == record["tn"]
    ).first()
    if not tool_number:
        db.add(NumeroHerramienta(tn=record["tn"]))
        db.flush()

    configuration = (
        db.query(ConfiguracionTN)
        .filter(
            ConfiguracionTN.tn == record["tn"],
            ConfiguracionTN.size == record["size"],
        )
        .first()
    )
    if configuration and configuration.type != record["type"]:
        raise HTTPException(
            status_code=409,
            detail=f"TYPE conflicts with existing TN/SIZE configuration ({configuration.type})",
        )
    if not configuration:
        configuration = ConfiguracionTN(
            tn=record["tn"],
            size=record["size"],
            type=record["type"],
        )
        db.add(configuration)
        db.flush()

    clearance = record["clearance"]
    variant = (
        db.query(VarianteHerramienta)
        .filter(
            VarianteHerramienta.tn == record["tn"],
            VarianteHerramienta.size == record["size"],
            VarianteHerramienta.categoria == record["categoria"],
            VarianteHerramienta.clearance == clearance,
        )
        .first()
    )
    if not variant:
        variant = VarianteHerramienta(
            tn=record["tn"],
            size=record["size"],
            categoria=record["categoria"],
            clearance=clearance,
        )
        db.add(variant)
        db.flush()
    return variant


def apply_inventory_record(tool, record, variant):
    tool.tn = record["tn"]
    tool.size = record["size"]
    tool.type = record["type"]
    tool.life = record["life"]
    tool.altura_total = record["altura_total"]
    tool.stripper_override = record["stripper_override"]
    tool.clearance = record["clearance"]
    tool.so = record["so"]
    if record["proveedor_given"]:
        tool.proveedor = record["proveedor"]
    tool.categoria = record["categoria"]
    tool.ubicacion = record["ubicacion"]
    tool.catalogo = HerramientaCatalogo(variante=variant) if variant else None
    return tool


def require_registered_rack(db, tool, requested_location):
    """A tool's stored location can only be set to an active registered rack (or cleared)."""
    requested = (requested_location or "").strip()
    if not requested or (tool is not None and (tool.ubicacion or "") == requested):
        return
    rack = db.query(Ubicacion).filter(
        Ubicacion.codigo == requested, Ubicacion.tipo == "rack", Ubicacion.activa.is_(True)
    ).first()
    if not rack:
        raise HTTPException(
            status_code=422,
            detail=f"Location '{requested}' is not a registered rack. Choose one from the list or register the rack in Locations.",
        )


def reject_untracked_location_change(db, tool, requested_location):
    if tool.ubicacion == requested_location:
        return
    if tool_has_workflow_tracking(db, tool.id):
        raise HTTPException(
            status_code=409,
            detail="Location is movement-managed; record a workflow movement instead",
        )


def tool_has_workflow_tracking(db, tool_id):
    tracked_references = (
        MovimientoHerramienta.herramienta_id,
        PendienteRecoger.herramienta_id,
        ReservaHerramienta.herramienta_id,
        RequisitoSetup.punch_tool_id,
        RequisitoSetup.die_tool_id,
        ComponenteSurtido.herramienta_anterior_id,
        ComponenteSurtido.herramienta_nueva_id,
    )
    return any(
        db.query(tool_column).filter(tool_column == tool_id).first() is not None
        for tool_column in tracked_references
    )


def reject_tracked_identity_change(db, tool, record):
    if not tool_has_workflow_tracking(db, tool.id):
        return

    def identity(values):
        clearance = values.get("clearance")
        if clearance is not None:
            try:
                clearance = f"{Decimal(clearance):.3f}"
            except InvalidOperation:
                pass
        return (
            (values.get("tn") or "").strip(),
            (values.get("size") or "").strip().upper(),
            (values.get("type") or "").strip().upper(),
            (values.get("categoria") or "").strip().lower(),
            clearance,
        )

    current = {
        "tn": tool.tn,
        "size": tool.size,
        "type": tool.type,
        "categoria": tool.categoria,
        "clearance": tool.clearance,
    }
    if identity(current) != identity(record):
        raise HTTPException(
            status_code=409,
            detail="Tool identity is referenced by workflow history and cannot be changed",
        )


DEFAULT_PARAMS = {key: Decimal(value) for key, value in tool_params.DEFAULTS.items()}


def inventory_record_read(tool, variant, params=None):
    params = params or DEFAULT_PARAMS
    stripper = tool_params.stripper_for(params, tool.size, tool.stripper_override) if tool.categoria == "punzon" else None
    return {
        "id": tool.id,
        "tn": tool.tn,
        "size": tool.size,
        "type": tool.type,
        "life": tool.life,
        "altura_total": tool.altura_total,
        "stripper": f"{stripper:.3f}" if stripper is not None else None,
        "stripper_custom": bool(tool.stripper_override),
        "life_alert": tool_params.life_alert(tool.categoria, tool.life, params),
        "clearance": tool.clearance,
        "so": tool.so,
        "proveedor": tool.proveedor,
        "categoria": tool.categoria,
        "ubicacion": tool.ubicacion,
        "catalog_linked": variant is not None,
        "has_image": bool(tool.imagen),
    }


@app.get("/inventario/opciones")
def opciones_inventario():
    """Listas controladas que usan los formularios (categorías, tipos por categoría y tamaños)."""
    db = SessionLocal()
    try:
        suppliers = sorted(
            row[0] for row in db.query(Herramienta.proveedor).filter(Herramienta.proveedor.isnot(None)).distinct()
            if row[0] and row[0].strip()
        )
        types_in_use = sorted(
            row[0] for row in db.query(Herramienta.type).filter(Herramienta.type.isnot(None)).distinct() if row[0]
        )
        params = tool_params.load(db)
    finally:
        db.close()
    return {
        "categorias": list(INVENTORY_TYPES_BY_CATEGORY),
        "tipos": {category: list(types) for category, types in INVENTORY_TYPES_BY_CATEGORY.items()},
        "tipos_en_uso": types_in_use,
        "sizes": ["A", "B", "C", "D", "E"],
        "estados": ["disponible", "afilado", "danado", "baja"],
        "proveedores": suppliers,
        "claridades": list(CLEARANCE_CHOICES),
        "stripper_por_tamano": {size: f"{params['stripper_' + size]:.3f}" for size in tool_params.SIZES},
        "vida_minima_punzon": f"{params['punch_min_life']:.3f}",
        "altura_minima_matriz": f"{params['die_min_height']:.3f}",
    }


@app.get("/inventario/herramientas")
def listar_inventario(
    q: str = "",
    categoria: Optional[str] = None,
    estado: Optional[str] = None,
    ubicacion: Optional[str] = None,
    vinculo: Optional[str] = None,
    f_id: Optional[str] = None,
    f_tn: Optional[str] = None,
    f_type: Optional[str] = None,
    f_size: Optional[str] = None,
    f_clearance: Optional[str] = None,
    f_life: Optional[str] = None,
    f_so: Optional[str] = None,
    f_supplier: Optional[str] = None,
    f_alert: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
):
    """Inventario físico paginado, con filtros por columna, estado y vínculo al catálogo."""
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    db = SessionLocal()
    try:
        params = tool_params.load(db)
        base = db.query(Herramienta, HerramientaCatalogo.variante_id).outerjoin(
            HerramientaCatalogo, HerramientaCatalogo.herramienta_id == Herramienta.id
        )
        text = (q or "").strip()
        if text:
            like = f"%{text}%"
            base = base.filter(
                Herramienta.id.ilike(like) | Herramienta.tn.ilike(like) | Herramienta.type.ilike(like)
                | Herramienta.so.ilike(like) | Herramienta.ubicacion.ilike(like) | Herramienta.proveedor.ilike(like)
            )
        for column, value in (
            (Herramienta.id, f_id), (Herramienta.tn, f_tn), (Herramienta.so, f_so),
            (Herramienta.life, f_life),
        ):
            if value and value.strip():
                base = base.filter(column.ilike(f"%{value.strip()}%"))
        for column, value in (
            (Herramienta.type, f_type), (Herramienta.size, f_size), (Herramienta.proveedor, f_supplier),
        ):
            if value and value.strip():
                base = base.filter(func.upper(column) == value.strip().upper())
        if f_clearance and f_clearance.strip():
            try:
                wanted = f"{Decimal(f_clearance.strip()):.3f}"
            except InvalidOperation:
                wanted = f_clearance.strip()
            base = base.filter(Herramienta.clearance == wanted)
        if f_alert == "low":
            life_number = cast(Herramienta.life, Float)
            base = base.filter(
                Herramienta.life.isnot(None),
                ((Herramienta.categoria == "punzon") & (life_number <= float(params["punch_min_life"])))
                | ((Herramienta.categoria == "matriz") & (life_number <= float(params["die_min_height"]))),
            )
        if categoria:
            base = base.filter(Herramienta.categoria == categoria.strip().lower())
        if estado:
            base = base.filter(Herramienta.estado == estado.strip().lower())
        if ubicacion == "-":
            base = base.filter(Herramienta.ubicacion.is_(None) | (Herramienta.ubicacion == ""))
        elif ubicacion:
            base = base.filter(Herramienta.ubicacion == ubicacion.strip())
        if vinculo == "linked":
            base = base.filter(HerramientaCatalogo.variante_id.isnot(None))
        elif vinculo == "unlinked":
            base = base.filter(HerramientaCatalogo.variante_id.is_(None))
        total = base.count()
        rows = base.order_by(Herramienta.id).limit(limit).offset(offset).all()
        everything = db.query(Herramienta).count()
        linked = db.query(HerramientaCatalogo).count()
        return {
            "total": total,
            "limit": limit,
            "offset": offset,
            "summary": {"total": everything, "linked": linked, "unlinked": everything - linked},
            "items": [
                {**inventory_record_read(tool, variant_id, params), "estado": tool.estado or "disponible"}
                for tool, variant_id in rows
            ],
        }
    finally:
        db.close()


@app.post("/inventario/herramientas", status_code=201)
def registrar_herramienta_inventario(item: RegistroInventarioCreate):
    db = SessionLocal()
    try:
        params = tool_params.load(db)
        record = normalize_inventory_item(item, params=params)
        if db.query(Herramienta).filter(Herramienta.id == record["id"]).first():
            raise HTTPException(status_code=409, detail="Physical tool ID already exists")
        require_registered_rack(db, None, record["ubicacion"])
        variant = get_or_create_catalog_variant(db, record)
        tool = apply_inventory_record(Herramienta(id=record["id"]), record, variant)
        db.add(tool)
        db.commit()
        return inventory_record_read(tool, variant, params)
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Catalog identity or physical tool ID conflicts with an existing record")
    finally:
        db.close()


@app.put("/inventario/herramientas/{herramienta_id}")
def actualizar_herramienta_inventario(herramienta_id: str, item: RegistroInventarioUpdate):
    db = SessionLocal()
    try:
        tool = db.query(Herramienta).filter(Herramienta.id == herramienta_id).first()
        if not tool:
            raise HTTPException(status_code=404, detail="Physical tool not found")
        params = tool_params.load(db)
        record = normalize_inventory_item(
            item,
            existing_type=tool.type,
            existing_category=tool.categoria,
            params=params,
            existing_clearance=tool.clearance,
        )
        reject_untracked_location_change(db, tool, record["ubicacion"])
        require_registered_rack(db, tool, record["ubicacion"])
        reject_tracked_identity_change(db, tool, record)
        if record["id"] != herramienta_id:
            raise HTTPException(status_code=409, detail="Physical tool ID cannot be changed")
        variant = get_or_create_catalog_variant(db, record)
        apply_inventory_record(tool, record, variant)
        db.commit()
        return inventory_record_read(tool, variant, params)
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Catalog identity conflicts with an existing record")
    finally:
        db.close()

@app.post("/herramientas/", response_model=HerramientaRead)
def crear_herramienta(herramienta: HerramientaCreate):
    db = SessionLocal()
    try:
        require_registered_rack(db, None, herramienta.ubicacion)
    except HTTPException:
        db.close()
        raise
    # Formatear life y clearance
    life = herramienta.life
    clearance = herramienta.clearance
    try:
        life_fmt = f"{float(life):.3f}" if life is not None else None
    except:
        life_fmt = life
    try:
        clearance_fmt = f"{float(clearance):.3f}" if clearance is not None else None
    except:
        clearance_fmt = clearance
    db_h = Herramienta(
        id=herramienta.id,
        tn=herramienta.tn,
        size=herramienta.size,
        type=herramienta.type,
        life=life_fmt,
        clearance=clearance_fmt,
        so=herramienta.so,
        proveedor=herramienta.proveedor,
        categoria=herramienta.categoria,
        ubicacion=herramienta.ubicacion
    )
    db.add(db_h)
    db.commit()
    db.refresh(db_h)
    db.close()
    return db_h

@app.get("/herramientas/", response_model=List[HerramientaRead])
def listar_herramientas():
    db = SessionLocal()
    herramientas = db.query(Herramienta).all()
    db.close()
    return herramientas

@app.get("/herramientas/search/")
def buscar_herramientas(q: str = ""):  # búsqueda avanzada
    db = SessionLocal()
    query = db.query(Herramienta)
    if q:
        q = f"%{q}%"
        herramientas = query.filter(
            (Herramienta.id.like(q)) |
            (Herramienta.tn.like(q)) |
            (Herramienta.size.like(q)) |
            (Herramienta.type.like(q)) |
            (Herramienta.life.like(q)) |
            (Herramienta.clearance.like(q)) |
            (Herramienta.so.like(q)) |
            (Herramienta.categoria.like(q)) |
            (Herramienta.ubicacion.like(q))
        ).all()
    else:
        herramientas = query.all()
    db.close()
    return [h.__dict__ for h in herramientas]

@app.get("/herramientas/{herramienta_id}", response_model=HerramientaRead)
def leer_herramienta(herramienta_id: str):
    db = SessionLocal()
    h = db.query(Herramienta).filter(Herramienta.id == herramienta_id).first()
    db.close()
    if h is None:
        raise HTTPException(status_code=404, detail="Herramienta no encontrada")
    return h

@app.put("/herramientas/{herramienta_id}", response_model=HerramientaRead)
def actualizar_herramienta(herramienta_id: str, herramienta: HerramientaCreate):
    db = SessionLocal()
    h = db.query(Herramienta).filter(Herramienta.id == herramienta_id).first()
    if h is None:
        db.close()
        raise HTTPException(status_code=404, detail="Herramienta no encontrada")
    try:
        reject_untracked_location_change(db, h, herramienta.ubicacion)
        require_registered_rack(db, h, herramienta.ubicacion)
    except HTTPException:
        db.close()
        raise
    catalog_link = (
        db.query(HerramientaCatalogo)
        .filter(HerramientaCatalogo.herramienta_id == herramienta_id)
        .first()
    )
    if catalog_link:
        variant = catalog_link.variante
        config = variant.configuracion
        requested_clearance = herramienta.clearance
        if requested_clearance is not None:
            try:
                requested_clearance = f"{Decimal(requested_clearance):.3f}"
            except InvalidOperation:
                pass
        catalog_identity = (
            variant.tn,
            variant.size,
            config.type,
            variant.categoria,
            variant.clearance,
        )
        requested_identity = (
            (herramienta.tn or "").strip(),
            (herramienta.size or "").strip().upper(),
            (herramienta.type or "").strip().upper(),
            (herramienta.categoria or "").strip().lower(),
            requested_clearance,
        )
        if catalog_identity != requested_identity:
            db.close()
            raise HTTPException(
                status_code=409,
                detail="Catalog identity is managed by the catalog; only physical details can be edited here",
            )
    try:
        reject_tracked_identity_change(db, h, {
            "tn": herramienta.tn,
            "size": herramienta.size,
            "type": herramienta.type,
            "categoria": herramienta.categoria,
            "clearance": herramienta.clearance,
        })
    except HTTPException:
        db.close()
        raise
    # Formatear life y clearance
    try:
        h.life = f"{float(herramienta.life):.3f}" if herramienta.life is not None else None
    except:
        h.life = herramienta.life
    try:
        h.clearance = f"{float(herramienta.clearance):.3f}" if herramienta.clearance is not None else None
    except:
        h.clearance = herramienta.clearance
    h.tn = herramienta.tn
    h.size = herramienta.size
    h.type = herramienta.type
    h.so = herramienta.so
    h.proveedor = herramienta.proveedor
    h.categoria = herramienta.categoria
    h.ubicacion = herramienta.ubicacion
    db.commit()
    db.refresh(h)
    db.close()
    return h

@app.delete("/herramientas/{herramienta_id}")
def eliminar_herramienta(herramienta_id: str):
    """A tool is never erased: it leaves service through a retirement that records who, where and why."""
    db = SessionLocal()
    try:
        if db.query(Herramienta.id).filter(Herramienta.id == herramienta_id).first() is None:
            raise HTTPException(status_code=404, detail="Herramienta no encontrada")
    finally:
        db.close()
    raise HTTPException(
        status_code=409,
        detail="A tool cannot be deleted. Retire it from its tool file (Retire…) and give the reason.",
    )

@app.patch("/herramientas/{herramienta_id}/ajustar_conteo", response_model=HerramientaRead)
def ajustar_conteo_herramienta(herramienta_id: int, conteo: int):
    db = SessionLocal()
    h = db.query(Herramienta).filter(Herramienta.id == herramienta_id).first()
    if not h:
        db.close()
        raise HTTPException(status_code=404, detail="Herramienta no encontrada")
    h.conteo = conteo
    db.commit()
    db.refresh(h)
    db.close()
    return h

@app.post("/scan-barcode", response_model=HerramientaRead)
def scan_barcode(scan: BarcodeScan):
    if scan.tipo == "punch":
        # ID|TN|SIZE|TYPE|LIFE|SO
        parts = scan.data.split("|")
        if len(parts) < 6:
            raise HTTPException(status_code=400, detail="Invalid barcode data for punch")
        data = {
            "id": parts[0],
            "tn": parts[1],
            "size": parts[2],
            "type": parts[3],
            "life": parts[4],
            "so": parts[5],
            "categoria": "punzon"
        }
    elif scan.tipo == "die":
        # ID|TN|SIZE|TYPE|LIFE|CLEARANCE|SO
        parts = scan.data.split("|")
        if len(parts) < 7:
            raise HTTPException(status_code=400, detail="Invalid barcode data for die")
        data = {
            "id": parts[0],
            "tn": parts[1],
            "size": parts[2],
            "type": parts[3],
            "life": parts[4],
            "clearance": parts[5],
            "so": parts[6],
            "categoria": "matriz"
        }
    else:
        raise HTTPException(status_code=400, detail="Invalid tipo")
    return data

@app.post("/scan_barcode/")
def scan_barcode(barcode: str):
    # Simulación: buscar herramienta por ID igual al código escaneado
    db = SessionLocal()
    h = db.query(Herramienta).filter(Herramienta.id == barcode).first()
    db.close()
    if h:
        return {"found": True, "herramienta": h.__dict__}
    else:
        return {"found": False}

@app.get("/audit/filter")
def filtrar_auditoria(user: str = Query(None), action: str = Query(None), fecha: str = Query(None)):
    """Bitácora unificada: cambios de la base de datos (auditoria) + registro CSV heredado de inventario."""
    import csv
    resultados = []
    db = SessionLocal()
    try:
        query = db.query(Auditoria).order_by(Auditoria.id.desc())
        if user:
            query = query.filter(Auditoria.usuario == user)
        if action:
            query = query.filter(Auditoria.accion.like(f"%{action}%"))
        if fecha:
            try:
                since = datetime.fromisoformat(fecha)
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid date. Use YYYY-MM-DD")
            query = query.filter(Auditoria.fecha >= since)
        for row in query.limit(1000).all():
            detail = row.despues if row.antes is None else f"{row.antes} -> {row.despues}"
            resultados.append({
                "Timestamp": row.fecha.strftime("%Y-%m-%d %H:%M:%S"),
                "User": row.usuario,
                "Action": row.accion,
                "ToolID": f"{row.entidad or ''} {row.entidad_id or ''}".strip(),
                "Details": detail or "",
            })
    finally:
        db.close()
    if os.path.isfile("audit_log.csv"):
        with open("audit_log.csv", mode='r', encoding='utf-8') as f:
            for row in csv.DictReader(f):
                if user and row["User"] != user:
                    continue
                if action and action not in row["Action"]:
                    continue
                if fecha and not row["Timestamp"].startswith(fecha):
                    continue
                resultados.append(row)
    return resultados

@app.get("/dashboard/localizacion")
def dashboard_localizacion():
    db = SessionLocal()
    herramientas = db.query(Herramienta).all()
    db.close()
    racks = {}
    for h in herramientas:
        ubic = h.ubicacion or 'Sin ubicación'
        if ubic not in racks:
            racks[ubic] = []
        racks[ubic].append(h.id)
    return [{"ubicacion": ubic, "cantidad": len(ids), "ids": ids} for ubic, ids in racks.items()]

@app.post("/mantenimiento/")
def registrar_mantenimiento(m: MantenimientoCreate):
    import csv
    file = "mantenimiento_log.csv"
    exists = os.path.isfile(file)
    with open(file, mode='a', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        if not exists:
            writer.writerow(["HerramientaID", "Tipo", "Descripción", "Fecha"])
        writer.writerow([m.herramienta_id, m.tipo, m.descripcion, m.fecha])
    return {"ok": True}

@app.get("/mantenimiento/")
def listar_mantenimientos(herramienta_id: str = Query(None)):
    import csv
    file = "mantenimiento_log.csv"
    resultados = []
    if not os.path.isfile(file):
        return resultados
    with open(file, mode='r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            if herramienta_id and row["HerramientaID"] != herramienta_id:
                continue
            resultados.append(row)
    return resultados

@app.get("/alertas/vida_util")
def alertas_vida_util(threshold: float = Query(0.3)):
    db = SessionLocal()
    herramientas = db.query(Herramienta).all()
    db.close()
    alertas = []
    for h in herramientas:
        try:
            life = float(h.life)
            if life <= threshold:
                alertas.append({"id": h.id, "tn": h.tn, "life": h.life, "ubicacion": h.ubicacion})
        except:
            continue
    return alertas
