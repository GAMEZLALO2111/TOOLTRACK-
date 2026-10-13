import hashlib
import hmac
import os
import secrets
import sqlite3
from fastapi import FastAPI, HTTPException, Body, Depends, Query
from pydantic import BaseModel
from typing import List, Optional
from models.inventory import Herramienta, Usuario, Base
from db import SessionLocal, engine

app = FastAPI()

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
