"""Autenticación por token, roles y permisos aplicados en la API, y bitácora de cambios.

Todo endpoint debe aparecer en ROUTE_RULES; lo que no esté listado se rechaza (403).
"""
import contextvars
import hashlib
import json
import re
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import event, insert
from sqlalchemy.orm import Session, attributes
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

from db import SessionLocal
from models.inventory import Auditoria, Sesion, Usuario

SESSION_HOURS = 12
MAX_BODY_BYTES = 20 * 1024 * 1024
LOGIN_MAX_FAILURES = 5
LOGIN_WINDOW_S = 300

ROLES = (
    "SYSTEM_ADMIN",
    "ENGINEERING",
    "PLANNING",
    "TOOLCRIB_SUPERVISOR",
    "TOOLCRIB_OPERATOR",
    "MAINTENANCE_TECHNICIAN",
)
LEGACY_ROLES = {
    "admin": "SYSTEM_ADMIN",
    "true": "SYSTEM_ADMIN",
    "supervisor": "TOOLCRIB_SUPERVISOR",
    "operator": "TOOLCRIB_OPERATOR",
}
ACTOR_FIELDS = ("realizado_por", "creado_por", "usuario")

_OPERATOR = {
    "inventory.view", "catalog.view", "locations.view", "setup.view", "kitting.view",
    "kitting.operate", "tools.move", "tools.status", "tools.store", "returns.operate",
    "demand.view", "demand.progress", "program.view", "machine.view", "maintenance.view",
}
_SUPERVISOR = _OPERATOR | {
    "inventory.edit", "inventory.adjust", "catalog.edit", "locations.manage",
    "setup.baseline", "demand.mark", "audit.view", "maintenance.operate",
}
ROLE_PERMISSIONS = {
    "TOOLCRIB_OPERATOR": frozenset(_OPERATOR),
    "TOOLCRIB_SUPERVISOR": frozenset(_SUPERVISOR),
    "MAINTENANCE_TECHNICIAN": frozenset({
        "inventory.view", "catalog.view", "locations.view", "maintenance.view",
        "maintenance.operate", "tools.move", "tools.status", "tools.store",
    }),
    "PLANNING": frozenset({
        "inventory.view", "catalog.view", "locations.view", "kitting.view", "demand.view",
        "demand.manage", "demand.mark", "program.view", "machine.view", "setup.view",
    }),
    "ENGINEERING": frozenset({
        "inventory.view", "catalog.view", "locations.view", "kitting.view", "setup.view",
        "demand.view", "demand.mark", "program.view", "program.manage", "machine.view",
        "maintenance.view", "audit.view",
    }),
}
# El administrador puede todo lo que existe (incluye operación para poder probar y dar soporte);
# cada acción queda en la bitácora con su usuario.
ALL_PERMISSIONS = frozenset(set().union(*ROLE_PERMISSIONS.values()) | {
    "user.manage", "machine.manage", "inventory.delete", "demand.manage", "audit.view",
    "locations.manage", "catalog.edit", "setup.baseline", "program.manage", "inventory.edit",
    "inventory.adjust", "demand.mark", "maintenance.operate",
})
ROLE_PERMISSIONS["SYSTEM_ADMIN"] = ALL_PERMISSIONS

PUBLIC = "__public__"
AUTHENTICATED = "__authenticated__"

ROUTE_RULES = {
    ("POST", "/login"): PUBLIC,
    ("POST", "/logout"): AUTHENTICATED,
    ("GET", "/me"): AUTHENTICATED,
    ("GET", "/users"): "user.manage",
    ("POST", "/users"): "user.manage",
    ("PATCH", "/users/{username}"): "user.manage",
    ("DELETE", "/users/{username}"): "user.manage",
    ("GET", "/catalogo/tn"): "catalog.view",
    ("GET", "/catalogo/configuraciones"): "catalog.view",
    ("GET", "/catalogo/variantes"): "catalog.view",
    ("GET", "/catalogo/herramientas"): "catalog.view",
    ("GET", "/catalogo/conciliacion/preview"): "catalog.view",
    ("POST", "/catalogo/tn"): "catalog.edit",
    ("POST", "/catalogo/configuraciones"): "catalog.edit",
    ("POST", "/catalogo/configuraciones/{tn}/{size}/variantes"): "catalog.edit",
    ("POST", "/catalogo/herramientas"): "catalog.edit",
    ("POST", "/inventario/herramientas"): "inventory.edit",
    ("PUT", "/inventario/herramientas/{herramienta_id}"): "inventory.edit",
    ("POST", "/herramientas/"): "inventory.edit",
    ("GET", "/herramientas/"): "inventory.view",
    ("GET", "/herramientas/search/"): "inventory.view",
    ("GET", "/herramientas/{herramienta_id}"): "inventory.view",
    ("PUT", "/herramientas/{herramienta_id}"): "inventory.edit",
    ("DELETE", "/herramientas/{herramienta_id}"): "inventory.delete",
    ("PATCH", "/herramientas/{herramienta_id}/ajustar_conteo"): "inventory.adjust",
    ("POST", "/scan-barcode"): "inventory.view",
    ("POST", "/scan_barcode/"): "inventory.view",
    ("GET", "/audit/filter"): "audit.view",
    ("GET", "/dashboard/localizacion"): "inventory.view",
    ("POST", "/mantenimiento/"): "maintenance.operate",
    ("GET", "/mantenimiento/"): "maintenance.view",
    ("GET", "/alertas/vida_util"): "inventory.view",
    ("GET", "/workflow/locations"): "locations.view",
    ("PATCH", "/workflow/locations/{code}/standard"): "locations.manage",
    ("POST", "/workflow/locations"): "locations.manage",
    ("POST", "/workflow/setups/preview"): "setup.view",
    ("POST", "/workflow/setups/compare-files"): "setup.view",
    ("POST", "/workflow/setups/compare"): "setup.view",
    ("POST", "/workflow/baselines"): "setup.baseline",
    ("POST", "/workflow/kits"): "kitting.operate",
    ("GET", "/workflow/kits/{kit_id}"): "kitting.view",
    ("GET", "/workflow/kits"): "kitting.view",
    ("GET", "/workflow/kits/{kit_id}/components/{component_id}/candidates"): "kitting.view",
    ("POST", "/workflow/kits/{kit_id}/scan"): "kitting.operate",
    ("POST", "/workflow/kits/{kit_id}/components/{component_id}/select"): "kitting.operate",
    ("POST", "/workflow/kits/{kit_id}/pause"): "kitting.operate",
    ("POST", "/workflow/kits/{kit_id}/resume"): "kitting.operate",
    ("POST", "/workflow/kits/{kit_id}/cancel"): "kitting.operate",
    ("POST", "/workflow/kits/{kit_id}/qr"): "kitting.operate",
    ("POST", "/workflow/delivery/scan"): "kitting.operate",
    ("GET", "/workflow/machines/running"): "setup.view",
    ("GET", "/workflow/machines/{machine_code}/active-setup"): "setup.view",
    ("POST", "/workflow/machines/transfer"): "kitting.operate",
    ("GET", "/workflow/pending-pickups"): "kitting.view",
    ("GET", "/workflow/tools/movable"): "inventory.view",
    ("POST", "/workflow/tools/{tool_id}/move"): "tools.move",
    ("POST", "/workflow/pending-pickups/{pending_id}/move"): "tools.move",
    ("GET", "/workflow/movements"): "inventory.view",
    ("GET", "/workflow/machines/{machine_code}/tools"): "kitting.view",
    ("POST", "/workflow/machines/{machine_code}/return"): "returns.operate",
    ("POST", "/workflow/tools/{tool_id}/status"): "tools.status",
    ("POST", "/workflow/racks/{rack_code}/store"): "tools.store",
    ("GET", "/workflow/tools/locate"): "inventory.view",
    ("GET", "/workflow/machine-profiles"): "machine.view",
    ("GET", "/workflow/machine-profiles/{code}"): "machine.view",
    ("PUT", "/workflow/machine-profiles/{code}"): "machine.manage",
    ("POST", "/workflow/machine-profiles/{code}/stations"): "machine.manage",
    ("PATCH", "/workflow/machine-profiles/{code}/stations/{number}"): "machine.manage",
    ("DELETE", "/workflow/machine-profiles/{code}/stations/{number}"): "machine.manage",
    ("GET", "/workflow/programs/clients"): "program.view",
    ("POST", "/workflow/programs/clients"): "program.manage",
    ("GET", "/workflow/programs/families"): "program.view",
    ("POST", "/workflow/programs/families"): "program.manage",
    ("GET", "/workflow/programs/log"): "program.view",
    ("GET", "/workflow/programs"): "program.view",
    ("GET", "/workflow/programs/{tipo_maquina}/{numero_parte}"): "program.view",
    ("PUT", "/workflow/programs/{tipo_maquina}/{numero_parte}"): "program.manage",
    ("GET", "/workflow/demandas/board"): "demand.view",
    ("GET", "/workflow/kitting/overview"): "kitting.view",
    ("POST", "/workflow/kitting/plan"): "kitting.view",
    ("POST", "/workflow/kitting/start"): "kitting.operate",
    ("POST", "/workflow/demandas"): "demand.manage",
    ("PATCH", "/workflow/demandas/{demand_id}"): AUTHENTICATED,  # permisos por campo en el endpoint
    ("DELETE", "/workflow/demandas/{demand_id}"): "demand.manage",
}


@dataclass(frozen=True)
class AuthUser:
    username: str
    role: str
    permissions: frozenset


CURRENT_USER: contextvars.ContextVar[Optional[AuthUser]] = contextvars.ContextVar("current_user", default=None)
CURRENT_ACTION: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("current_action", default=None)
_login_failures = {}


def utcnow():
    return datetime.utcnow()


def normalize_role(role):
    """Rol canónico, o None si no se reconoce. Vacío = operador de toolcrib (menor privilegio operativo)."""
    if role is None or not str(role).strip():
        return "TOOLCRIB_OPERATOR"
    text = str(role).strip()
    if text.upper() in ROLES:
        return text.upper()
    return LEGACY_ROLES.get(text.lower())


def permissions_for(role):
    return ROLE_PERMISSIONS.get(role, frozenset())


def current_user() -> Optional[AuthUser]:
    return CURRENT_USER.get()


def require_permission(permission: str) -> AuthUser:
    user = CURRENT_USER.get()
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    if permission not in user.permissions:
        raise HTTPException(status_code=403, detail=f"Your role ({user.role}) is not allowed to do this")
    return user


def acting_as(username: str, role: str):
    """Para pruebas y scripts: fija el usuario actual. Devuelve el token para restaurar."""
    return CURRENT_USER.set(AuthUser(username, role, permissions_for(role)))


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(db, username: str, hours: int = SESSION_HOURS):
    token = secrets.token_urlsafe(32)
    now = utcnow()
    db.add(Sesion(
        token_hash=_hash_token(token), username=username, creado_en=now,
        expira_en=now + timedelta(hours=hours), revocada=False,
    ))
    db.query(Sesion).filter(Sesion.expira_en < now - timedelta(days=7)).delete()
    return token, now + timedelta(hours=hours)


def revoke_session(db, token: str):
    row = db.query(Sesion).filter(Sesion.token_hash == _hash_token(token)).first()
    if row:
        row.revocada = True


def resolve_session(token: str) -> Optional[AuthUser]:
    db = SessionLocal()
    try:
        row = db.query(Sesion).filter(Sesion.token_hash == _hash_token(token)).first()
        if not row or row.revocada or row.expira_en < utcnow():
            return None
        user = db.query(Usuario).filter(Usuario.username == row.username).first()
        if not user or not user.activo:
            return None
        role = normalize_role(user.role)
        if role is None:
            return None
        return AuthUser(user.username, role, permissions_for(role))
    finally:
        db.close()


def check_login_allowed(username: str):
    now = time.time()
    recent = [t for t in _login_failures.get(username, []) if now - t < LOGIN_WINDOW_S]
    _login_failures[username] = recent
    if len(recent) >= LOGIN_MAX_FAILURES:
        raise HTTPException(status_code=429, detail="Too many failed attempts. Wait 5 minutes and try again.")


def register_login_failure(username: str):
    _login_failures.setdefault(username, []).append(time.time())


def clear_login_failures(username: str):
    _login_failures.pop(username, None)


def write_event(username, role, action, entidad=None, entidad_id=None, antes=None, despues=None):
    """Registro explícito (inicio de sesión, accesos denegados, etc.) en su propia transacción."""
    db = SessionLocal()
    try:
        db.execute(insert(Auditoria.__table__).values(
            fecha=utcnow(), usuario=username or "anonymous", rol=role, accion=action,
            entidad=entidad, entidad_id=entidad_id,
            antes=None if antes is None else json.dumps(antes, default=str, ensure_ascii=False),
            despues=None if despues is None else json.dumps(despues, default=str, ensure_ascii=False),
        ))
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()


# ---------- bitácora automática de cambios (valores anteriores y nuevos) ----------
_AUDIT_SKIP = {"auditoria", "sesiones"}
_SUMMARY_THRESHOLD = 20


_SENSITIVE_COLUMNS = {"password"}


def _clip(value):
    if isinstance(value, str) and len(value) > 200:
        return value[:200] + f"…(+{len(value) - 200} chars)"
    if isinstance(value, (bytes, bytearray)):
        return f"<{len(value)} bytes>"
    return value


def _row_values(obj):
    mapper = attributes.instance_state(obj).mapper
    return {
        c.key: ("***" if c.key in _SENSITIVE_COLUMNS else _clip(getattr(obj, c.key, None)))
        for c in mapper.column_attrs
    }


def _pk(obj):
    mapper = attributes.instance_state(obj).mapper
    return ",".join(str(value) for value in mapper.primary_key_from_instance(obj))


def _changes(obj):
    state = attributes.instance_state(obj)
    changed = {}
    for column in state.mapper.column_attrs:
        history = attributes.get_history(obj, column.key)
        if history.has_changes():
            if column.key in _SENSITIVE_COLUMNS:
                changed[column.key] = ("***", "***")
                continue
            old = history.deleted[0] if history.deleted else None
            new = history.added[0] if history.added else getattr(obj, column.key, None)
            changed[column.key] = (_clip(old), _clip(new))
    return changed


@event.listens_for(Session, "after_flush")
def _audit_after_flush(session, flush_context):
    user = CURRENT_USER.get()
    actor = user.username if user else "system"
    role = user.role if user else None
    action = CURRENT_ACTION.get() or "SYSTEM"
    now = utcnow()
    rows = []

    def add(kind, table, entity_id, antes, despues):
        rows.append(dict(
            fecha=now, usuario=actor, rol=role, accion=f"{action} [{kind}]", entidad=table,
            entidad_id=entity_id,
            antes=None if antes is None else json.dumps(antes, default=str, ensure_ascii=False),
            despues=None if despues is None else json.dumps(despues, default=str, ensure_ascii=False),
        ))

    inserted = {}
    for obj in session.new:
        table = getattr(obj, "__tablename__", None)
        if table and table not in _AUDIT_SKIP:
            inserted.setdefault(table, []).append(obj)
    for table, objs in inserted.items():
        if len(objs) > _SUMMARY_THRESHOLD:
            ids = [_pk(o) for o in objs]
            add("INSERT", table, f"{ids[0]}..{ids[-1]}", None, {"rows": len(objs), "sample": _row_values(objs[0])})
        else:
            for obj in objs:
                add("INSERT", table, _pk(obj), None, _row_values(obj))

    for obj in session.dirty:
        table = getattr(obj, "__tablename__", None)
        if not table or table in _AUDIT_SKIP or not session.is_modified(obj, include_collections=False):
            continue
        changed = _changes(obj)
        if changed:
            add("UPDATE", table, _pk(obj),
                {k: v[0] for k, v in changed.items()}, {k: v[1] for k, v in changed.items()})

    deleted = {}
    for obj in session.deleted:
        table = getattr(obj, "__tablename__", None)
        if table and table not in _AUDIT_SKIP:
            deleted.setdefault(table, []).append(obj)
    for table, objs in deleted.items():
        if len(objs) > _SUMMARY_THRESHOLD:
            add("DELETE", table, f"{len(objs)} rows", {"rows": len(objs)}, None)
        else:
            for obj in objs:
                add("DELETE", table, _pk(obj), _row_values(obj), None)

    if rows:
        session.connection().execute(insert(Auditoria.__table__), rows)


# ---------- middleware ----------
def _headers(scope):
    return {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}


_compiled_cache = (0, [])


def _compile_rules():
    global _compiled_cache
    if _compiled_cache[0] == len(ROUTE_RULES):
        return _compiled_cache[1]
    compiled = []
    for (method, template), rule in ROUTE_RULES.items():
        parts = re.split(r"(\{[^/}]+\})", template)
        params = sum(1 for part in parts if part.startswith("{"))
        regex = "".join("[^/]+" if part.startswith("{") else re.escape(part) for part in parts)
        compiled.append((params, method, re.compile(f"^{regex}$"), template, rule))
    compiled.sort(key=lambda item: item[0])  # las rutas literales ganan a las que tienen parámetros
    _compiled_cache = (len(ROUTE_RULES), compiled)
    return compiled


def match_rule(method, path):
    """(plantilla, regla) de la primera ruta que coincide, o (None, None) si la ruta no está autorizada."""
    for _params, rule_method, regex, template, rule in _compile_rules():
        if rule_method == method and regex.match(path):
            return template, rule
    return None, None


async def _read_body(receive):
    chunks, size = [], 0
    while True:
        message = await receive()
        if message["type"] != "http.request":
            return None
        body = message.get("body", b"")
        size += len(body)
        if size > MAX_BODY_BYTES:
            return None
        chunks.append(body)
        if not message.get("more_body", False):
            return b"".join(chunks)


class AuthMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        method = scope["method"]
        if method == "OPTIONS":
            await self.app(scope, receive, send)
            return
        path = scope["path"]
        if method in ("GET", "HEAD") and (path == "/" or path.startswith("/assets/")):
            # Archivos de la aplicación web (sin datos): la API sigue exigiendo sesión.
            await self.app(scope, receive, send)
            return
        template, rule = match_rule(method, path)
        if rule is None:
            await self._reject(scope, receive, send, 403, "Route not authorized", None,
                               f"{method} {scope['path']}")
            return
        if rule == PUBLIC:
            await self.app(scope, receive, send)
            return

        headers = _headers(scope)
        auth_header = headers.get("authorization", "")
        token = auth_header[7:].strip() if auth_header.lower().startswith("bearer ") else ""
        user = await run_in_threadpool(resolve_session, token) if token else None
        if user is None:
            await self._reject(scope, receive, send, 401, "Session expired or not signed in", None, None)
            return
        if rule != AUTHENTICATED and rule not in user.permissions:
            await self._reject(scope, receive, send, 403,
                               f"Your role ({user.role}) is not allowed to do this", user, f"{method} {template}")
            return

        receive_fn = receive
        if method in ("POST", "PUT", "PATCH", "DELETE") and "json" in headers.get("content-type", ""):
            body = await _read_body(receive)
            if body is None:
                await self._reject(scope, receive, send, 413, "Request body too large", user, None)
                return
            body = self._stamp_actor(body, user.username)
            scope = dict(scope)
            scope["headers"] = [(k, v) for k, v in scope["headers"] if k.lower() != b"content-length"] + [
                (b"content-length", str(len(body)).encode())]
            sent = False

            async def receive_fn():
                nonlocal sent
                if not sent:
                    sent = True
                    return {"type": "http.request", "body": body, "more_body": False}
                return {"type": "http.disconnect"}

        user_token = CURRENT_USER.set(user)
        action_token = CURRENT_ACTION.set(f"{method} {template}")
        try:
            await self.app(scope, receive_fn, send)
        finally:
            CURRENT_USER.reset(user_token)
            CURRENT_ACTION.reset(action_token)

    @staticmethod
    def _stamp_actor(body: bytes, username: str) -> bytes:
        try:
            data = json.loads(body) if body else None
        except ValueError:
            return body
        if isinstance(data, dict):
            for field in ACTOR_FIELDS:
                if field in data:
                    data[field] = username
            return json.dumps(data, ensure_ascii=False).encode("utf-8")
        return body

    async def _reject(self, scope, receive, send, status, detail, user, action):
        if action and status == 403 and user:
            await run_in_threadpool(
                write_event, user.username if user else None, user.role if user else None,
                "ACCESS_DENIED", "route", action, None, {"detail": detail})
        response = JSONResponse({"detail": detail}, status_code=status)
        await response(scope, receive, send)
