"""Who is on each machine, and what Manufacturing asks of the plan from it.

The operator logs into a machine each time he starts working on it; the login lasts until the shift ends
(or until he takes another machine or leaves). Only an operator on a machine can record its produced pieces
or request its next part number, and his reports carry who was on the machine.
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import func

import auth
import service_requests_api as service
import shifts
import workflow_api
from models.inventory import AsignacionMaquina, DemandaPlaneacion, SolicitudServicio, Usuario

router = APIRouter(prefix="/workflow/manufacturing", tags=["manufacturing"])


class PartRequestCreate(BaseModel):
    demand_id: int


def _user():
    user = auth.current_user()
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user


def _iso(value):
    return value.isoformat() if value else None


def current_assignments(db, codes=None, username=None):
    """Logins that are still valid: not left and not past the end of their shift."""
    query = db.query(AsignacionMaquina).filter(
        AsignacionMaquina.fin.is_(None), AsignacionMaquina.vence > workflow_api.utcnow(),
    )
    if codes is not None:
        query = query.filter(AsignacionMaquina.maquina_codigo.in_(list(codes)))
    if username is not None:
        query = query.filter(func.lower(AsignacionMaquina.usuario) == username.lower())
    return query.order_by(AsignacionMaquina.inicio).all()


def is_on_machine(db, username, code):
    return any(row.maquina_codigo == code for row in current_assignments(db, [code], username))


def operators_by_machine(db, codes):
    rows = current_assignments(db, codes)
    names = {
        person.username: (person.full_name or person.username)
        for person in db.query(Usuario).filter(Usuario.username.in_({row.usuario for row in rows})).all()
    } if rows else {}
    result = {}
    for row in rows:
        result.setdefault(row.maquina_codigo, []).append({
            "username": row.usuario,
            "full_name": names.get(row.usuario, row.usuario),
            "shift": row.turno,
            "since": _iso(row.inicio),
        })
    return result


def _read(db, row):
    person = db.query(Usuario).filter(Usuario.username == row.usuario).first()
    return {
        "machine": row.maquina_codigo,
        "username": row.usuario,
        "full_name": (person.full_name if person else None) or row.usuario,
        "shift": row.turno,
        "since": _iso(row.inicio),
        "until": _iso(row.vence),
    }


def _close(rows, now, reason):
    for row in rows:
        row.fin = now
        row.fin_motivo = reason


@router.post("/machines/{machine_code}/take")
def take_machine(machine_code: str):
    user = _user()
    db = workflow_api.SessionLocal()
    try:
        code = workflow_api._location(db, machine_code.strip().upper(), {"machine"}).codigo
        now = workflow_api.utcnow()
        mine = current_assignments(db, username=user.username)
        same = next((row for row in mine if row.maquina_codigo == code), None)
        _close([row for row in mine if row is not same], now, "took another machine")
        if same is None:
            same = AsignacionMaquina(
                usuario=user.username, maquina_codigo=code, turno=shifts.shift_for(now),
                inicio=now, vence=shifts.shift_end(now),
            )
            db.add(same)
        db.commit()
        return _read(db, same)
    except HTTPException:
        db.rollback()
        raise
    finally:
        db.close()


@router.post("/machines/{machine_code}/leave")
def leave_machine(machine_code: str):
    user = _user()
    db = workflow_api.SessionLocal()
    try:
        code = machine_code.strip().upper()
        _close([row for row in current_assignments(db, [code], user.username)], workflow_api.utcnow(), "left")
        db.commit()
        return {"ok": True}
    finally:
        db.close()


@router.get("/my-machine")
def my_machine():
    user = _user()
    db = workflow_api.SessionLocal()
    try:
        rows = current_assignments(db, username=user.username)
        return _read(db, rows[-1]) if rows else None
    finally:
        db.close()


@router.post("/machines/{machine_code}/part-request", status_code=201)
def request_part_number(machine_code: str, item: PartRequestCreate):
    """The operator on a machine asks Tool Crib to prepare the next part number of its plan."""
    user = _user()
    db = workflow_api.SessionLocal()
    try:
        code = workflow_api._location(db, machine_code.strip().upper(), {"machine"}).codigo
        if not is_on_machine(db, user.username, code):
            raise HTTPException(status_code=403, detail=f"Take machine {code} first: only its operator can request its part number")
        demand = db.query(DemandaPlaneacion).filter(
            DemandaPlaneacion.id == item.demand_id, DemandaPlaneacion.activa.is_(True),
        ).first()
        if not demand or demand.maquina_codigo != code:
            raise HTTPException(status_code=404, detail="That part number is not in the plan of this machine")
        if demand.piezas_producidas >= demand.cantidad:
            raise HTTPException(status_code=409, detail="That part number is already finished")
        if db.query(SolicitudServicio.id).filter(
            SolicitudServicio.tipo == "part_request", SolicitudServicio.demanda_id == demand.id,
            SolicitudServicio.estado.in_(service.OPEN_STATES),
        ).first():
            raise HTTPException(status_code=409, detail="This part number is already requested")
        row = SolicitudServicio(
            maquina_codigo=code, numero_parte=demand.numero_parte.upper(), demanda_id=demand.id,
            tipo="part_request", estado="open", solicitado_por=user.username, solicitado_en=workflow_api.utcnow(),
        )
        db.add(row)
        db.commit()
        return service._read_many(db, [row])[0]
    except HTTPException:
        db.rollback()
        raise
    finally:
        db.close()
