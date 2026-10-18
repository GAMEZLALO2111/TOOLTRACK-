from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError

import workflow_api
from machine_rules import STATION_STATES
from models.inventory import EstacionMaquina, EstadoMaquina, Maquina, Setup, Ubicacion

router = APIRouter(prefix="/workflow/machine-profiles", tags=["machines"])


class MachineProfile(BaseModel):
    tipo_maquina: Optional[str] = None
    linea: Optional[str] = None
    capacidad: Optional[str] = None
    espesor_max: Optional[str] = None
    espesor_min: Optional[str] = None
    edificio: Optional[str] = None
    blank_max: Optional[str] = None
    area_pallet: Optional[str] = None
    notas: Optional[str] = None


class StationCreate(BaseModel):
    numero: str
    tamano: Optional[str] = None
    posicion: Optional[str] = None
    autoindex: bool = False
    estado: str = "disponible"
    nota: Optional[str] = None


class StationUpdate(BaseModel):
    tamano: Optional[str] = None
    posicion: Optional[str] = None
    autoindex: Optional[bool] = None
    estado: Optional[str] = None
    nota: Optional[str] = None


POSITIONS = ("DENTRO", "CENTRO", "AFUERA")


def _clean_position(value):
    position = (value or "").strip().upper()
    if position and position not in POSITIONS:
        raise HTTPException(status_code=422, detail="Posición no válida (DENTRO, CENTRO o AFUERA).")
    return position or None


def _clean(value):
    value = (value or "").strip()
    return value or None


def _station_read(station):
    return {
        "numero": station.numero,
        "tamano": station.tamano,
        "posicion": station.posicion,
        "autoindex": bool(station.autoindex),
        "estado": station.estado,
        "nota": station.nota,
    }


def _profile_read(db, location):
    profile = db.query(Maquina).filter(Maquina.codigo == location.codigo).first()
    stations = db.query(EstacionMaquina).filter(
        EstacionMaquina.maquina_codigo == location.codigo
    ).all()
    stations.sort(key=lambda row: (not row.numero.isdigit(), int(row.numero) if row.numero.isdigit() else 0, row.numero))
    state = db.query(EstadoMaquina).filter(EstadoMaquina.maquina_codigo == location.codigo).first()
    setup = (
        db.query(Setup).filter(Setup.id == state.setup_activo_id).first()
        if state and state.setup_activo_id else None
    )
    return {
        "codigo": location.codigo,
        "nombre": location.nombre,
        "activa": location.activa,
        "estandar": bool(location.estandar),
        "tipo_maquina": profile.tipo_maquina if profile else None,
        "linea": profile.linea if profile else None,
        "capacidad": profile.capacidad if profile else None,
        "espesor_max": profile.espesor_max if profile else None,
        "espesor_min": profile.espesor_min if profile else None,
        "edificio": profile.edificio if profile else None,
        "blank_max": profile.blank_max if profile else None,
        "area_pallet": profile.area_pallet if profile else None,
        "notas": profile.notas if profile else None,
        "numero_parte": setup.drawing_name if setup else None,
        "estaciones_total": len(stations),
        "estaciones_disponibles": sum(row.estado == "disponible" for row in stations),
        "estaciones_danadas": sum(row.estado != "disponible" for row in stations),
        "estaciones": [_station_read(row) for row in stations],
    }


@router.get("")
def list_machine_profiles():
    db = workflow_api.SessionLocal()
    try:
        machines = db.query(Ubicacion).filter(Ubicacion.tipo == "machine").order_by(Ubicacion.codigo).all()
        return [_profile_read(db, machine) for machine in machines]
    finally:
        db.close()


@router.get("/{code}")
def get_machine_profile(code: str):
    db = workflow_api.SessionLocal()
    try:
        return _profile_read(db, workflow_api._location(db, code.strip().upper(), {"machine"}))
    finally:
        db.close()


@router.put("/{code}")
def save_machine_profile(code: str, item: MachineProfile):
    db = workflow_api.SessionLocal()
    try:
        location = workflow_api._location(db, code.strip().upper(), {"machine"})
        for label, raw in (("máximo", item.espesor_max), ("mínimo", item.espesor_min)):
            if _clean(raw):
                try:
                    if float(raw) <= 0:
                        raise ValueError
                except ValueError:
                    raise HTTPException(status_code=422, detail=f"El espesor {label} debe ser un número positivo (pulgadas).")
        if _clean(item.espesor_max) and _clean(item.espesor_min) and float(item.espesor_min) > float(item.espesor_max):
            raise HTTPException(status_code=422, detail="El espesor mínimo no puede ser mayor que el máximo.")
        profile = db.query(Maquina).filter(Maquina.codigo == location.codigo).first()
        if not profile:
            profile = Maquina(codigo=location.codigo)
            db.add(profile)
        profile.tipo_maquina = _clean(item.tipo_maquina)
        profile.linea = _clean(item.linea)
        profile.capacidad = _clean(item.capacidad)
        profile.espesor_max = _clean(item.espesor_max)
        profile.espesor_min = _clean(item.espesor_min)
        profile.edificio = _clean(item.edificio)
        profile.blank_max = _clean(item.blank_max)
        profile.area_pallet = _clean(item.area_pallet)
        profile.notas = _clean(item.notas)
        db.commit()
        return _profile_read(db, location)
    except HTTPException:
        db.rollback()
        raise
    finally:
        db.close()


def _validate_state(value):
    state = (value or "disponible").strip().lower()
    if state not in STATION_STATES:
        raise HTTPException(status_code=422, detail="Estado de estación no válido (disponible o danada).")
    return state


@router.post("/{code}/stations", status_code=201)
def add_station(code: str, item: StationCreate):
    number = item.numero.strip()
    if not number or not number.replace(" ", "").isalnum():
        raise HTTPException(status_code=422, detail="El número de estación es obligatorio (solo letras y números).")
    db = workflow_api.SessionLocal()
    try:
        location = workflow_api._location(db, code.strip().upper(), {"machine"})
        station = EstacionMaquina(
            maquina_codigo=location.codigo,
            numero=number,
            tamano=(_clean(item.tamano) or "").upper() or None,
            posicion=_clean_position(item.posicion),
            autoindex=bool(item.autoindex),
            estado=_validate_state(item.estado),
            nota=_clean(item.nota),
        )
        db.add(station)
        db.commit()
        return _station_read(station)
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail=f"La estación {number} ya existe en esta máquina.") from error
    finally:
        db.close()


def _get_station(db, code, number):
    station = db.query(EstacionMaquina).filter(
        EstacionMaquina.maquina_codigo == code.strip().upper(),
        EstacionMaquina.numero == number.strip(),
    ).first()
    if not station:
        raise HTTPException(status_code=404, detail="Estación no encontrada")
    return station


@router.patch("/{code}/stations/{number}")
def update_station(code: str, number: str, item: StationUpdate):
    db = workflow_api.SessionLocal()
    try:
        station = _get_station(db, code, number)
        if item.tamano is not None:
            station.tamano = item.tamano.strip().upper() or None
        if item.posicion is not None:
            station.posicion = _clean_position(item.posicion)
        if item.autoindex is not None:
            station.autoindex = item.autoindex
        if item.estado is not None:
            station.estado = _validate_state(item.estado)
        if item.nota is not None:
            station.nota = _clean(item.nota)
        db.commit()
        return _station_read(station)
    except HTTPException:
        db.rollback()
        raise
    finally:
        db.close()


@router.delete("/{code}/stations/{number}")
def delete_station(code: str, number: str):
    db = workflow_api.SessionLocal()
    try:
        station = _get_station(db, code, number)
        db.delete(station)
        db.commit()
        return {"deleted": number}
    finally:
        db.close()
