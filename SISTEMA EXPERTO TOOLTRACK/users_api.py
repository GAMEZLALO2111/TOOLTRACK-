from typing import List, Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

import auth
from db import SessionLocal
from models.inventory import Sesion, Usuario
from passwords import hash_password, verify_password

router = APIRouter(tags=["users"])

MIN_PASSWORD_LENGTH = 6


class LoginRequest(BaseModel):
    username: str
    password: str


class UserCreate(BaseModel):
    username: str
    password: str
    role: Optional[str] = None
    is_admin: bool = False


class UserUpdate(BaseModel):
    password: Optional[str] = None
    role: Optional[str] = None
    is_admin: Optional[bool] = None
    activo: Optional[bool] = None


class UserRead(BaseModel):
    username: str
    role: str
    is_admin: bool
    activo: bool


def _read(user: Usuario) -> dict:
    role = auth.normalize_role(user.role) or "UNKNOWN"
    return {"username": user.username, "role": role, "is_admin": role == "SYSTEM_ADMIN", "activo": bool(user.activo)}


def _valid_role(role: Optional[str], is_admin: Optional[bool] = None) -> str:
    if not role and is_admin is not None:
        role = "SYSTEM_ADMIN" if is_admin else "TOOLCRIB_OPERATOR"
    canonical = auth.normalize_role(role)
    if canonical is None:
        raise HTTPException(status_code=400, detail=f"Unknown role: {role}. Valid roles: {', '.join(auth.ROLES)}")
    return canonical


def _active_admins(db, excluding: Optional[str] = None) -> int:
    query = db.query(Usuario).filter(Usuario.activo.is_(True))
    count = 0
    for user in query.all():
        if excluding and user.username == excluding:
            continue
        if auth.normalize_role(user.role) == "SYSTEM_ADMIN":
            count += 1
    return count


def _check_password(password: str):
    if len(password.strip()) < MIN_PASSWORD_LENGTH:
        raise HTTPException(status_code=400, detail=f"Password must have at least {MIN_PASSWORD_LENGTH} characters")


@router.post("/login")
def login(item: LoginRequest):
    username = item.username.strip().lower()
    auth.check_login_allowed(username)
    db = SessionLocal()
    try:
        user = db.query(Usuario).filter(Usuario.username.ilike(username)).first()
        valid = bool(user and user.activo and verify_password(item.password.strip(), user.password))
        role = auth.normalize_role(user.role) if user else None
        if not valid:
            auth.register_login_failure(username)
            db.close()
            auth.write_event(username, None, "LOGIN_FAILED")
            raise HTTPException(status_code=401, detail="Invalid credentials")
        if role is None:
            raise HTTPException(status_code=403, detail="This account has no valid role. Ask a system admin.")
        token, expires = auth.create_session(db, user.username)
        db.commit()
        result = {
            "token": token,
            "expires_at": expires.isoformat(),
            "username": user.username,
            "role": role,
            "is_admin": role == "SYSTEM_ADMIN",
            "permissions": sorted(auth.permissions_for(role)),
        }
    finally:
        db.close()
    auth.clear_login_failures(username)
    auth.write_event(result["username"], role, "LOGIN")
    return result


@router.post("/logout")
def logout(authorization: Optional[str] = Header(None)):
    token = (authorization or "")[7:].strip()
    user = auth.current_user()
    db = SessionLocal()
    try:
        auth.revoke_session(db, token)
        db.commit()
    finally:
        db.close()
    auth.write_event(user.username if user else None, user.role if user else None, "LOGOUT")
    return {"detail": "Signed out"}


@router.get("/me")
def me():
    user = auth.current_user()
    return {"username": user.username, "role": user.role, "permissions": sorted(user.permissions)}


@router.get("/users", response_model=List[UserRead])
def list_users():
    db = SessionLocal()
    try:
        return [_read(user) for user in db.query(Usuario).order_by(Usuario.username).all()]
    finally:
        db.close()


@router.post("/users", response_model=UserRead)
def create_user(item: UserCreate):
    username = item.username.strip().lower()
    if not username:
        raise HTTPException(status_code=400, detail="Username is required")
    _check_password(item.password)
    role = _valid_role(item.role, item.is_admin)
    db = SessionLocal()
    try:
        if db.query(Usuario).filter(Usuario.username.ilike(username)).first():
            raise HTTPException(status_code=400, detail="User already exists")
        user = Usuario(username=username, password=hash_password(item.password.strip()), role=role, activo=True)
        db.add(user)
        db.commit()
        return _read(user)
    finally:
        db.close()


def _apply_update(db, user: Usuario, item: UserUpdate):
    actor = auth.current_user()
    new_role = _valid_role(item.role, item.is_admin) if (item.role or item.is_admin is not None) else None
    losing_admin = (
        auth.normalize_role(user.role) == "SYSTEM_ADMIN"
        and ((new_role is not None and new_role != "SYSTEM_ADMIN") or item.activo is False)
    )
    if losing_admin and _active_admins(db, excluding=user.username) == 0:
        raise HTTPException(status_code=400, detail="At least one active SYSTEM_ADMIN must remain")
    if item.activo is False and actor and actor.username == user.username:
        raise HTTPException(status_code=400, detail="You cannot deactivate your own account")
    if item.password is not None:
        _check_password(item.password)
        user.password = hash_password(item.password.strip())
    if new_role is not None:
        user.role = new_role
    if item.activo is not None:
        user.activo = item.activo


@router.patch("/users/{username}", response_model=UserRead)
def update_user(username: str, item: UserUpdate):
    db = SessionLocal()
    try:
        user = db.query(Usuario).filter(Usuario.username.ilike(username)).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        _apply_update(db, user, item)
        db.commit()
        return _read(user)
    finally:
        db.close()


@router.delete("/users/{username}")
def deactivate_user(username: str):
    """Los usuarios no se borran: se desactivan para conservar su historial."""
    db = SessionLocal()
    try:
        user = db.query(Usuario).filter(Usuario.username.ilike(username)).first()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        _apply_update(db, user, UserUpdate(activo=False))
        db.query(Sesion).filter(Sesion.username == user.username).update({"revocada": True})
        db.commit()
        return {"detail": "User deactivated"}
    finally:
        db.close()
