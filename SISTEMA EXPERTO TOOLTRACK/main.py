import hashlib
import hmac
import os
import secrets
import sqlite3
from decimal import Decimal, InvalidOperation
from fastapi import FastAPI, HTTPException, Body, Depends, Query
from pydantic import BaseModel
from typing import List, Optional
from sqlalchemy.exc import IntegrityError
from catalog_reconciliation import preview_legacy_reconciliation
from models.inventory import (
    ConfiguracionTN,
    ComponenteSurtido,
    Herramienta,
    HerramientaCatalogo,
    MovimientoHerramienta,
    NumeroHerramienta,
    PendienteRecoger,
    RequisitoSetup,
    ReservaHerramienta,
    Usuario,
    VarianteHerramienta,
    Base,
)
from tool_types import ALL_INVENTORY_TYPES, INVENTORY_TYPES_BY_CATEGORY, PUNCH_DIE_TYPES
from db import SessionLocal, engine
from workflow_api import router as workflow_router, seed_default_locations
from tool_tracking_api import router as tool_tracking_router
from machines_api import router as machines_router
from programs_api import router as programs_router

app = FastAPI()
app.include_router(workflow_router)
app.include_router(tool_tracking_router)
app.include_router(machines_router)
app.include_router(programs_router)


@app.on_event("startup")
def initialize_workflow_locations():
    seed_default_locations()


DEFAULT_ADMIN_USERNAME = os.getenv("TOOLTRACK_ADMIN_USERNAME", "").strip()
DEFAULT_ADMIN_PASSWORD = os.getenv("TOOLTRACK_ADMIN_PASSWORD", "")


def normalize_role(role: Optional[str]) -> str:
    if not role:
        return "operator"
    normalized = str(role).strip().lower()
    if normalized in {"admin", "supervisor", "operator"}:
        return normalized
    if normalized == "true":
        return "admin"
    return "operator"


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 200_000)
    return f"pbkdf2_sha256${salt}${digest.hex()}"


def verify_password(raw_password: str, stored_password: Optional[str]) -> bool:
    if not stored_password:
        return False
    if stored_password.startswith("pbkdf2_sha256$"):
        try:
            _, salt, digest_hex = stored_password.split("$", 2)
            expected = hashlib.pbkdf2_hmac("sha256", raw_password.encode("utf-8"), salt.encode("utf-8"), 200_000).hex()
            return secrets_compare(expected, digest_hex)
        except ValueError:
            return False
    return stored_password == raw_password


def secrets_compare(a: str, b: str) -> bool:
    return hmac.compare_digest(a, b)


def ensure_password_hash(password: str) -> str:
    return hash_password(password)

# --- MIGRACIÓN AUTOMÁTICA DE USUARIOS PARA CAMPO 'role' ---
def migrate_usuarios_table():
    import sqlite3
    conn = sqlite3.connect('tooltrack.db')
    cur = conn.cursor()
    # Verificar si existe la columna 'role'
    cur.execute("PRAGMA table_info(usuarios)")
    columns = [col[1] for col in cur.fetchall()]
    if 'role' not in columns:
        # Agregar columna 'role'
        cur.execute("ALTER TABLE usuarios ADD COLUMN role TEXT DEFAULT 'operator'")
        conn.commit()
    # Migrar datos de is_admin a role
    if 'is_admin' in columns:
        cur.execute("UPDATE usuarios SET role='admin' WHERE is_admin=1")
        cur.execute("UPDATE usuarios SET role='operator' WHERE is_admin=0")
        conn.commit()
        # Opcional: eliminar columna is_admin (SQLite no soporta DROP COLUMN directo)
        # Se puede dejar como está, ya no se usará
    conn.close()
migrate_usuarios_table()

# Crear usuario admin principal si no existe
def ensure_admin_user():
    db = SessionLocal()
    try:
        existing_admin = db.query(Usuario).filter(Usuario.role == "admin").first()
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
            existing_user.role = "admin"
            existing_user.password = hash_password(DEFAULT_ADMIN_PASSWORD)
        else:
            db.add(Usuario(
                username=username,
                password=hash_password(DEFAULT_ADMIN_PASSWORD),
                role="admin",
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
    conn.commit()
    conn.close()


ensure_surtido_columns()

class HerramientaCreate(BaseModel):
    id: str
    tn: Optional[str] = None
    size: Optional[str] = None
    type: Optional[str] = None
    life: Optional[str] = None
    clearance: Optional[str] = None
    so: Optional[str] = None
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
    clearance: Optional[str] = None
    so: Optional[str] = None
    ubicacion: Optional[str] = None

class RegistroInventarioUpdate(RegistroInventarioCreate):
    pass

class UsuarioCreate(BaseModel):
    username: str
    password: str
    is_admin: bool = False
    role: Optional[str] = None

    @property
    def resolved_role(self) -> str:
        if self.role:
            return normalize_role(self.role)
        return 'admin' if self.is_admin else 'operator'

    @property
    def safe_username(self) -> str:
        return self.username.strip()

class UsuarioRead(BaseModel):
    username: str
    is_admin: bool
    role: Optional[str] = None
    class Config:
        from_attributes = True

class UsuarioLogin(BaseModel):
    username: str
    password: str

class BarcodeScan(BaseModel):
    data: str
    tipo: str  # 'punch' o 'die'

class MantenimientoCreate(BaseModel):
    herramienta_id: str
    tipo: str  # preventivo/correctivo
    descripcion: str
    fecha: str

@app.post("/login")
def login(user: UsuarioLogin):
    db = SessionLocal()
    username = user.username.strip().lower()
    password = user.password.strip()
    u = db.query(Usuario).filter(Usuario.username.ilike(username)).first()
    db.close()
    if not u or not verify_password(password, u.password):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    role = normalize_role(u.role)
    return {"username": u.username, "is_admin": role == "admin", "role": role}

@app.get("/users", response_model=List[UsuarioRead])
def listar_usuarios():
    db = SessionLocal()
    usuarios = []
    for user in db.query(Usuario).all():
        role = normalize_role(user.role)
        usuarios.append({
            "username": user.username,
            "is_admin": role == "admin",
            "role": role,
        })
    db.close()
    return usuarios

@app.post("/users", response_model=UsuarioRead)
def crear_usuario(usuario: UsuarioCreate):
    db = SessionLocal()
    username = usuario.safe_username.lower()
    if not username or not usuario.password.strip():
        db.close()
        raise HTTPException(status_code=400, detail="Username and password are required")
    if db.query(Usuario).filter(Usuario.username.ilike(username)).first():
        db.close()
        raise HTTPException(status_code=400, detail="User already exists")
    role = normalize_role(usuario.role if usuario.role else ('admin' if usuario.is_admin else 'operator'))
    db_u = Usuario(username=username, password=hash_password(usuario.password.strip()), role=role)
    db.add(db_u)
    db.commit()
    db.refresh(db_u)
    db.close()
    return {"username": db_u.username, "is_admin": role == "admin", "role": role}

@app.patch("/users/{username}", response_model=UsuarioRead)
def editar_usuario(username: str, usuario: UsuarioCreate = Body(...)):
    db = SessionLocal()
    u = db.query(Usuario).filter(Usuario.username.ilike(username)).first()
    if not u:
        db.close()
        raise HTTPException(status_code=404, detail="User not found")
    if not usuario.password.strip():
        db.close()
        raise HTTPException(status_code=400, detail="Password is required")
    u.password = hash_password(usuario.password.strip())
    u.role = normalize_role(usuario.role if usuario.role else ('admin' if usuario.is_admin else 'operator'))
    db.commit()
    db.refresh(u)
    db.close()
    return {"username": u.username, "is_admin": u.role == "admin", "role": u.role}

@app.delete("/users/{username}")
def eliminar_usuario(username: str):
    db = SessionLocal()
    u = db.query(Usuario).filter(Usuario.username == username).first()
    if not u:
        db.close()
        raise HTTPException(status_code=404, detail="User not found")
    db.delete(u)
    db.commit()
    db.close()
    return {"detail": "User deleted"}

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

def normalize_inventory_item(
    item: RegistroInventarioCreate,
    existing_type: Optional[str] = None,
    existing_category: Optional[str] = None,
):
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

    if categoria in {"punzon", "matriz"}:
        if not tn or size not in {"A", "B", "C", "D", "E"}:
            raise HTTPException(status_code=422, detail="TN and SIZE A-E are required for punches and dies")
        if not item.life or not item.life.strip():
            raise HTTPException(status_code=422, detail="LIFE is required for punches and dies")
        try:
            life_value = Decimal(item.life.strip())
        except InvalidOperation:
            raise HTTPException(status_code=422, detail="LIFE must be numeric")
        if not life_value.is_finite() or life_value < 0:
            raise HTTPException(status_code=422, detail="LIFE must be a non-negative number")
        if life_value.normalize().as_tuple().exponent < -3:
            raise HTTPException(status_code=422, detail="LIFE supports at most three decimal places")
        life = f"{life_value:.3f}"

        if categoria == "punzon":
            if item.clearance and item.clearance.strip():
                raise HTTPException(status_code=422, detail="Punches cannot have CLEARANCE")
        else:
            if not item.clearance or not item.clearance.strip():
                raise HTTPException(status_code=422, detail="CLEARANCE is required for dies")
            try:
                clearance_value = Decimal(item.clearance.strip())
            except InvalidOperation:
                raise HTTPException(status_code=422, detail="CLEARANCE must be numeric")
            if not clearance_value.is_finite() or clearance_value < 0:
                raise HTTPException(status_code=422, detail="CLEARANCE must be a non-negative number")
            if clearance_value.normalize().as_tuple().exponent < -3:
                raise HTTPException(status_code=422, detail="CLEARANCE supports at most three decimal places")
            clearance = f"{clearance_value:.3f}"

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
        "clearance": clearance,
        "so": item.so.strip() if item.so and item.so.strip() else None,
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
    tool.clearance = record["clearance"]
    tool.so = record["so"]
    tool.categoria = record["categoria"]
    tool.ubicacion = record["ubicacion"]
    tool.catalogo = HerramientaCatalogo(variante=variant) if variant else None
    return tool


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


def inventory_record_read(tool, variant):
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
        "catalog_linked": variant is not None,
    }


@app.post("/inventario/herramientas", status_code=201)
def registrar_herramienta_inventario(item: RegistroInventarioCreate):
    record = normalize_inventory_item(item)
    db = SessionLocal()
    try:
        if db.query(Herramienta).filter(Herramienta.id == record["id"]).first():
            raise HTTPException(status_code=409, detail="Physical tool ID already exists")
        variant = get_or_create_catalog_variant(db, record)
        tool = apply_inventory_record(Herramienta(id=record["id"]), record, variant)
        db.add(tool)
        db.commit()
        return inventory_record_read(tool, variant)
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
        record = normalize_inventory_item(
            item,
            existing_type=tool.type,
            existing_category=tool.categoria,
        )
        reject_untracked_location_change(db, tool, record["ubicacion"])
        reject_tracked_identity_change(db, tool, record)
        if record["id"] != herramienta_id:
            raise HTTPException(status_code=409, detail="Physical tool ID cannot be changed")
        variant = get_or_create_catalog_variant(db, record)
        apply_inventory_record(tool, record, variant)
        db.commit()
        return inventory_record_read(tool, variant)
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
    h.categoria = herramienta.categoria
    h.ubicacion = herramienta.ubicacion
    db.commit()
    db.refresh(h)
    db.close()
    return h

@app.delete("/herramientas/{herramienta_id}")
def eliminar_herramienta(herramienta_id: str):
    db = SessionLocal()
    h = db.query(Herramienta).filter(Herramienta.id == herramienta_id).first()
    if h is None:
        db.close()
        raise HTTPException(status_code=404, detail="Herramienta no encontrada")
    if tool_has_workflow_tracking(db, herramienta_id):
        db.close()
        raise HTTPException(
            status_code=409,
            detail="Tool has workflow history and cannot be deleted; use the formal retirement flow",
        )
    db.delete(h)
    db.commit()
    db.close()
    return {"ok": True}

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
    import csv
    resultados = []
    with open("audit_log.csv", mode='r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            if user and row["User"] != user:
                continue
            if action and row["Action"] != action:
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
