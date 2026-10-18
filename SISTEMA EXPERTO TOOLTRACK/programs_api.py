import hashlib
import json
import re
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import workflow_api
from models.inventory import BitacoraPrograma, Cliente, FamiliaParte, ProgramaParte

router = APIRouter(prefix="/workflow/programs", tags=["programs"])

MACHINE_TYPES = ("4I", "2I", "45STA")
PROGRAM_KINDS = ("NPI", "SUSTAINING")
PART_NUMBER_FORMAT = re.compile(r"^[A-Z]{2,6}-\d{5,9}(?:-\d{2})?_\d{2}-[A-Z]{1,4}$")


class FamilyCreate(BaseModel):
    nombre: str
    cliente: str


class ClientCreate(BaseModel):
    nombre: str


class ProgramSave(BaseModel):
    familia: Optional[str] = None
    tipo_programa: str
    nombre_archivo: Optional[str] = None
    contenido: str
    usuario: str
    motivo: Optional[str] = None


def _machine_type(value):
    value = (value or "").strip().upper()
    if value not in MACHINE_TYPES:
        raise HTTPException(status_code=422, detail="Tipo de máquina no válido (4I, 2I o 45STA).")
    return value


def _part_number(value):
    value = (value or "").strip().upper()
    if not PART_NUMBER_FORMAT.match(value):
        raise HTTPException(
            status_code=422,
            detail="Número de parte con formato inválido. Debe ser como TYEH-1193217_01-SW, sin prefijos ni sufijos extra.",
        )
    return value


def _requirements(parsed):
    return [
        {"station": row.station, "tn": row.tn, "angle": row.angle}
        for row in parsed.requirements
    ]


def _diff(old, new):
    """Human readable summary of what changed between two programs."""
    lines = []
    if old["thickness"] != new["thickness"]:
        lines.append(f"Espesor {old['thickness']} -> {new['thickness']}")
    old_map = {row["station"]: row for row in old["requisitos"]}
    new_map = {row["station"]: row for row in new["requisitos"]}
    for station in sorted(set(old_map) | set(new_map), key=lambda s: (not s.isdigit(), int(s) if s.isdigit() else 0, s)):
        before, after = old_map.get(station), new_map.get(station)
        if before and not after:
            lines.append(f"Estación {station}: se quitó {before['tn']}")
        elif after and not before:
            lines.append(f"Estación {station}: se agregó {after['tn']}")
        elif (before["tn"], before["angle"]) != (after["tn"], after["angle"]):
            lines.append(
                f"Estación {station}: {before['tn']} ({before['angle']}) -> {after['tn']} ({after['angle']})"
            )
    return "\n".join(lines)


def _read(program, with_content=False, clients=None):
    data = {
        "numero_parte": program.numero_parte,
        "tipo_maquina": program.tipo_maquina,
        "cliente": (clients or {}).get(program.familia),
        "familia": program.familia,
        "tipo_programa": program.tipo_programa,
        "revision": program.revision,
        "nombre_archivo": program.nombre_archivo,
        "drawing_name": program.drawing_name,
        "material": program.material,
        "thickness": program.thickness,
        "actualizado_por": program.actualizado_por,
        "actualizado_en": program.actualizado_en.isoformat(),
        "requisitos": json.loads(program.requisitos_json),
    }
    if with_content:
        data["contenido"] = program.contenido
    return data


def _client_map(db):
    return {row.nombre: row.cliente for row in db.query(FamiliaParte).all()}


@router.get("/clients")
def list_clients():
    db = workflow_api.SessionLocal()
    try:
        return [row.nombre for row in db.query(Cliente).order_by(Cliente.nombre).all()]
    finally:
        db.close()


@router.post("/clients")
def create_client(item: ClientCreate):
    name = (item.nombre or "").strip().upper()
    if not name:
        raise HTTPException(status_code=422, detail="Escribe el nombre del cliente.")
    db = workflow_api.SessionLocal()
    try:
        if db.query(Cliente).filter(Cliente.nombre == name).first():
            raise HTTPException(status_code=409, detail="El cliente ya existe.")
        db.add(Cliente(nombre=name))
        db.commit()
        return {"nombre": name}
    finally:
        db.close()


@router.get("/families")
def list_families(cliente: Optional[str] = None):
    db = workflow_api.SessionLocal()
    try:
        query = db.query(FamiliaParte)
        if cliente and cliente.strip():
            query = query.filter(FamiliaParte.cliente == cliente.strip().upper())
        return [{"nombre": row.nombre, "cliente": row.cliente} for row in query.order_by(FamiliaParte.nombre).all()]
    finally:
        db.close()


@router.post("/families")
def create_family(item: FamilyCreate):
    name = (item.nombre or "").strip().upper()
    client = (item.cliente or "").strip().upper()
    if not name:
        raise HTTPException(status_code=422, detail="Escribe el nombre de la familia.")
    db = workflow_api.SessionLocal()
    try:
        if not client or not db.query(Cliente).filter(Cliente.nombre == client).first():
            raise HTTPException(status_code=422, detail="Elige un cliente existente para la familia.")
        if db.query(FamiliaParte).filter(FamiliaParte.nombre == name).first():
            raise HTTPException(status_code=409, detail="La familia ya existe.")
        db.add(FamiliaParte(nombre=name, cliente=client))
        db.commit()
        return {"nombre": name, "cliente": client}
    finally:
        db.close()


@router.get("/log")
def list_program_log(numero_parte: Optional[str] = None, tipo_maquina: Optional[str] = None):
    db = workflow_api.SessionLocal()
    try:
        query = db.query(BitacoraPrograma)
        if numero_parte and numero_parte.strip():
            query = query.filter(BitacoraPrograma.numero_parte.like(f"%{numero_parte.strip().upper()}%"))
        if tipo_maquina and tipo_maquina.strip():
            query = query.filter(BitacoraPrograma.tipo_maquina == tipo_maquina.strip().upper())
        rows = query.order_by(BitacoraPrograma.id.desc()).limit(500).all()
        return [
            {
                "fecha": row.fecha.isoformat(),
                "numero_parte": row.numero_parte,
                "tipo_maquina": row.tipo_maquina,
                "accion": row.accion,
                "revision": row.revision,
                "motivo": row.motivo,
                "usuario": row.usuario,
                "detalle": row.detalle,
            }
            for row in rows
        ]
    finally:
        db.close()


@router.get("")
def list_programs(tipo_maquina: Optional[str] = None, buscar: Optional[str] = None):
    db = workflow_api.SessionLocal()
    try:
        query = db.query(ProgramaParte)
        if tipo_maquina:
            query = query.filter(ProgramaParte.tipo_maquina == _machine_type(tipo_maquina))
        if buscar and buscar.strip():
            query = query.filter(ProgramaParte.numero_parte.like(f"%{buscar.strip().upper()}%"))
        return [_read(row, clients=_client_map(db)) for row in query.order_by(ProgramaParte.numero_parte).all()]
    finally:
        db.close()


@router.get("/{tipo_maquina}/{numero_parte}")
def get_program(tipo_maquina: str, numero_parte: str):
    db = workflow_api.SessionLocal()
    try:
        program = db.query(ProgramaParte).filter(
            ProgramaParte.tipo_maquina == _machine_type(tipo_maquina),
            ProgramaParte.numero_parte == numero_parte.strip().upper(),
        ).first()
        if not program:
            raise HTTPException(status_code=404, detail="Ese número de parte no está cargado para este tipo de máquina.")
        return _read(program, with_content=True, clients=_client_map(db))
    finally:
        db.close()


@router.put("/{tipo_maquina}/{numero_parte}")
def save_program(tipo_maquina: str, numero_parte: str, item: ProgramSave):
    machine_type = _machine_type(tipo_maquina)
    part_number = _part_number(numero_parte)
    kind = (item.tipo_programa or "").strip().upper()
    if kind not in PROGRAM_KINDS:
        raise HTTPException(status_code=422, detail="Tipo de programa no válido (NPI o Sustaining).")
    user = (item.usuario or "").strip()
    if not user:
        raise HTTPException(status_code=422, detail="Falta el usuario.")
    parsed = workflow_api._parse_and_validate(item.contenido, item.nombre_archivo or f"{part_number}.stp")

    db = workflow_api.SessionLocal()
    try:
        family = (item.familia or "").strip().upper() or None
        if not family:
            raise HTTPException(status_code=422, detail="Elige la familia/FG (que ya trae su cliente).")
        if not db.query(FamiliaParte).filter(FamiliaParte.nombre == family).first():
            raise HTTPException(status_code=422, detail=f"La familia {family} no existe. Agrégala primero.")
        new_data = {"thickness": str(parsed.thickness), "requisitos": _requirements(parsed)}
        program = db.query(ProgramaParte).filter(
            ProgramaParte.tipo_maquina == machine_type, ProgramaParte.numero_parte == part_number
        ).first()
        reason = (item.motivo or "").strip()
        now = workflow_api.utcnow()
        if program:
            if not reason:
                raise HTTPException(
                    status_code=422,
                    detail="Este número de parte ya existe. Escribe el motivo del cambio.",
                )
            detail = _diff(
                {"thickness": program.thickness, "requisitos": json.loads(program.requisitos_json)}, new_data
            ) or "Sin cambios en estaciones ni espesor."
            if (program.familia or None) != family:
                detail += f"\nFamilia {program.familia or '—'} -> {family or '—'}"
            if program.tipo_programa != kind:
                detail += f"\nTipo {program.tipo_programa} -> {kind}"
            program.revision += 1
            action = "actualizado"
        else:
            program = ProgramaParte(numero_parte=part_number, tipo_maquina=machine_type, revision=1)
            db.add(program)
            reason = reason or "Alta inicial"
            detail = f"{len(new_data['requisitos'])} estaciones, espesor {new_data['thickness']}"
            action = "alta"
        program.familia = family
        program.tipo_programa = kind
        program.nombre_archivo = item.nombre_archivo
        program.drawing_name = parsed.drawing_name
        program.material = parsed.material
        program.thickness = new_data["thickness"]
        program.contenido = item.contenido
        program.contenido_hash = hashlib.sha256(item.contenido.encode("utf-8")).hexdigest()
        program.requisitos_json = json.dumps(new_data["requisitos"])
        program.actualizado_por = user
        program.actualizado_en = now
        db.flush()
        db.add(BitacoraPrograma(
            numero_parte=part_number, tipo_maquina=machine_type, accion=action,
            revision=program.revision, motivo=reason, usuario=user, fecha=now, detalle=detail,
        ))
        db.commit()
        return _read(program, clients=_client_map(db))
    except HTTPException:
        db.rollback()
        raise
    finally:
        db.close()
