import hashlib
import re
import secrets
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from db import SessionLocal
from machine_rules import setup_warnings
from models.inventory import (
    ComponenteSurtido,
    EstadoMaquina,
    EventoMaquina,
    Herramienta,
    Maquina,
    MovimientoHerramienta,
    PendienteRecoger,
    ReservaHerramienta,
    RequisitoSetup,
    Setup,
    Surtido,
    Ubicacion,
)
from setup_workflow import (
    SetupParseError,
    SetupReviewRequired,
    clearance_range_for_thickness,
    compare_setup_requirements,
    parse_setup_text,
    validate_setup_requirements,
)


router = APIRouter(prefix="/workflow", tags=["setup workflow"])
LOCATION_TYPES = {"rack", "cart", "machine", "togu", "maintenance", "workshop"}
DEFAULT_MACHINE_CODES = tuple(f"T-{number:03d}" for number in range(101, 118))
DEFAULT_CART_CODES = ("C-01", "C-02", "C-03")
CATEGORY_ALIASES = {
    "punch": "punzon",
    "punzon": "punzon",
    "die": "matriz",
    "matriz": "matriz",
}
COMPONENT_CATEGORIES = ("punzon", "matriz")


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def seed_default_locations():
    db = SessionLocal()
    try:
        default_locations = {
            **{
                code: ("Machine " + code, "machine")
                for code in DEFAULT_MACHINE_CODES
            },
            **{
                code: ("Carro de surtido " + code, "cart")
                for code in DEFAULT_CART_CODES
            },
        }
        existing_codes = {
            code
            for (code,) in db.query(Ubicacion.codigo).filter(
                Ubicacion.codigo.in_(tuple(default_locations))
            ).all()
        }
        missing = [
            Ubicacion(
                codigo=code,
                nombre=name,
                tipo=kind,
                activa=True,
            )
            for code, (name, kind) in default_locations.items()
            if code not in existing_codes
        ]
        if missing:
            db.add_all(missing)
            db.commit()
        return len(missing)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# Kept as a compatibility alias for callers using the earlier helper name.
seed_default_machine_locations = seed_default_locations


def _parse_and_validate(text: str, source_name: str):
    try:
        setup = parse_setup_text(text, source_name=source_name)
        validate_setup_requirements(setup)
        return setup
    except SetupReviewRequired as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except SetupParseError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


def _setup_summary(setup):
    clearance_range = clearance_range_for_thickness(setup.thickness)
    return {
        "source_name": setup.source_name,
        "drawing_name": setup.drawing_name,
        "material": setup.material,
        "thickness": str(setup.thickness),
        "clearance_min": str(clearance_range[0]) if clearance_range else None,
        "clearance_max": str(clearance_range[1]) if clearance_range else None,
        "thickness_out_of_range": clearance_range is None,
        "requirements": [
            {
                "station": requirement.station,
                "tn": requirement.tn,
                "angle": requirement.angle,
            }
            for requirement in setup.requirements
        ],
    }


def _location(db, code: str, expected_types=None):
    location = db.query(Ubicacion).filter(Ubicacion.codigo == code, Ubicacion.activa.is_(True)).first()
    if not location:
        raise HTTPException(status_code=404, detail=f"Active location '{code}' is not registered")
    if expected_types and location.tipo not in expected_types:
        raise HTTPException(
            status_code=422,
            detail=f"Location '{code}' must be one of: {', '.join(sorted(expected_types))}",
        )
    return location


def _category(tool):
    return CATEGORY_ALIASES.get((tool.categoria or "").strip().lower())


def _home_rack(db, tool_id):
    """Rack where the tool was last taken from; None if it never came from a rack."""
    racks = {
        row[0] for row in db.query(Ubicacion.codigo).filter(Ubicacion.tipo == "rack")
    }
    movements = db.query(MovimientoHerramienta.origen_codigo).filter(
        MovimientoHerramienta.herramienta_id == tool_id
    ).order_by(MovimientoHerramienta.id.desc())
    for (origin,) in movements:
        if origin in racks:
            return origin
    return None


def _require_home_rack(db, tool, destination):
    if destination.tipo != "rack":
        return
    home = _home_rack(db, tool.id)
    if home and home != destination.codigo:
        raise HTTPException(
            status_code=409,
            detail=(
                f"La herramienta {tool.id} (TN {tool.tn}) no pertenece al rack "
                f"'{destination.codigo}'. Su lugar es '{home}'."
            ),
        )


TOOL_STATUS_LABELS = {
    "disponible": "Disponible",
    "afilado": "En afilado",
    "danado": "Dañada (en revisión)",
    "baja": "Dada de baja",
}
SERVICE_STATUS_BY_LOCATION = {"togu": "afilado", "workshop": "afilado", "maintenance": "danado"}


def _require_available(tool):
    status = tool.estado or "disponible"
    if status != "disponible":
        raise HTTPException(
            status_code=409,
            detail=f"La herramienta {tool.id} no está disponible: {TOOL_STATUS_LABELS.get(status, status)}",
        )


def _status_on_move(tool, origin_type, destination_type):
    """Sending a tool to a service location flags it; going back to a rack needs a recorded result."""
    if destination_type in SERVICE_STATUS_BY_LOCATION:
        if (tool.estado or "disponible") == "disponible":
            tool.estado = SERVICE_STATUS_BY_LOCATION[destination_type]
    elif destination_type == "rack" and origin_type in SERVICE_STATUS_BY_LOCATION:
        if (tool.estado or "disponible") != "disponible":
            raise HTTPException(
                status_code=409,
                detail=(
                    f"La herramienta {tool.id} está '{TOOL_STATUS_LABELS.get(tool.estado, tool.estado)}'. "
                    "Usa la pestaña «Guardar en rack» e indica el resultado (lista para uso o baja)."
                ),
            )


def _clearance_in_range(tool, clearance_range):
    if not clearance_range or not tool.clearance:
        return False
    try:
        value = Decimal(tool.clearance)
    except (InvalidOperation, TypeError):
        return False
    return clearance_range[0] <= value <= clearance_range[1]


def _append_movement(
    db,
    tool,
    origin_code,
    destination_code,
    movement_type,
    actor,
    details=None,
    kit_id=None,
    setup_id=None,
):
    db.add(MovimientoHerramienta(
        herramienta_id=tool.id,
        origen_codigo=origin_code,
        destino_codigo=destination_code,
        tipo=movement_type,
        surtido_id=kit_id,
        setup_id=setup_id,
        realizado_por=actor,
        creado_en=utcnow(),
        detalle=details,
    ))
    tool.ubicacion = destination_code


def _write_setup(db, parsed_setup, machine_code, actor, state, source_text):
    content_hash = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
    setup_record = Setup(
        maquina_codigo=machine_code,
        nombre_archivo=parsed_setup.source_name or "uploaded.stp",
        drawing_name=parsed_setup.drawing_name,
        material=parsed_setup.material,
        thickness=str(parsed_setup.thickness),
        contenido_hash=content_hash,
        creado_por=actor,
        creado_en=utcnow(),
        estado=state,
    )
    db.add(setup_record)
    db.flush()
    requirement_records = {}
    for requirement in parsed_setup.requirements:
        record = RequisitoSetup(
            setup_id=setup_record.id,
            station=requirement.station,
            tn=requirement.tn,
            angle=requirement.angle,
        )
        db.add(record)
        db.flush()
        requirement_records[requirement.key] = record
    return setup_record, requirement_records


class LocationCreate(BaseModel):
    codigo: str
    nombre: str
    tipo: str
    estandar: bool = False


class LocationStandard(BaseModel):
    estandar: bool


class SetupPreviewRequest(BaseModel):
    nombre_archivo: str
    contenido: str


class SetupComparisonRequest(BaseModel):
    maquina_codigo: str
    nombre_archivo: str
    contenido: str


class SetupFilesComparisonRequest(BaseModel):
    setup_anterior_nombre: str
    setup_anterior_contenido: str
    setup_nuevo_nombre: str
    setup_nuevo_contenido: str


class SetupAssignment(BaseModel):
    station: str
    tn: str
    angle: Optional[str] = None
    punch_id: str
    die_id: str


class SetupBaselineCreate(BaseModel):
    maquina_codigo: str
    nombre_archivo: str
    contenido: str
    realizado_por: str
    herramientas: List[SetupAssignment]


class SurtidoCreate(BaseModel):
    maquina_codigo: str
    carro_codigo: str
    nombre_archivo: str
    contenido: str
    creado_por: str
    setup_anterior_nombre: Optional[str] = None
    setup_anterior_contenido: Optional[str] = None
    corregir_actual: bool = False
    motivo_correccion: Optional[str] = None


class SurtidoToolSelect(BaseModel):
    herramienta_id: str
    realizado_por: str


class SurtidoAction(BaseModel):
    realizado_por: str
    motivo: Optional[str] = None


class DeliveryScan(BaseModel):
    scan_value: str
    machine_scan: str = ""
    realizado_por: str


class PendingPickupMove(BaseModel):
    destino_codigo: str
    realizado_por: str


@router.get("/locations")
def list_locations():
    db = SessionLocal()
    try:
        locations = db.query(Ubicacion).order_by(Ubicacion.tipo, Ubicacion.codigo).all()
        return [
            {
                "codigo": location.codigo,
                "nombre": location.nombre,
                "tipo": location.tipo,
                "activa": location.activa,
                "estandar": bool(location.estandar),
            }
            for location in locations
        ]
    finally:
        db.close()


@router.patch("/locations/{code}/standard")
def set_machine_standard(code: str, item: LocationStandard):
    db = SessionLocal()
    try:
        machine = _location(db, code.strip().upper(), {"machine"})
        machine.estandar = item.estandar
        db.commit()
        return {"codigo": machine.codigo, "estandar": machine.estandar}
    finally:
        db.close()


@router.post("/locations", status_code=201)
def create_location(item: LocationCreate):
    code = item.codigo.strip().upper()
    name = item.nombre.strip()
    kind = item.tipo.strip().lower()
    if not code or not name:
        raise HTTPException(status_code=422, detail="Location code and name are required")
    if kind not in LOCATION_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported location type. Choose: {', '.join(sorted(LOCATION_TYPES))}",
        )
    db = SessionLocal()
    try:
        location = Ubicacion(
            codigo=code, nombre=name, tipo=kind, activa=True,
            estandar=bool(item.estandar) and kind == "machine",
        )
        db.add(location)
        db.commit()
        return {
            "codigo": location.codigo, "nombre": location.nombre, "tipo": location.tipo,
            "activa": True, "estandar": location.estandar,
        }
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail="Location code already exists") from error
    finally:
        db.close()


@router.post("/setups/preview")
def preview_setup(item: SetupPreviewRequest):
    setup = _parse_and_validate(item.contenido, item.nombre_archivo)
    return _setup_summary(setup)


@router.post("/setups/compare-files")
def compare_setup_files(item: SetupFilesComparisonRequest):
    current = _parse_and_validate(
        item.setup_anterior_contenido,
        item.setup_anterior_nombre,
    )
    new = _parse_and_validate(
        item.setup_nuevo_contenido,
        item.setup_nuevo_nombre,
    )
    comparison = compare_setup_requirements(current, new)
    clearance_range = clearance_range_for_thickness(new.thickness)
    current_by_key = {requirement.key: requirement for requirement in current.requirements}
    unchanged = []
    for new_requirement in comparison["unchanged"]:
        old_requirement = current_by_key[new_requirement.key]
        if comparison["thickness_changed"]:
            die_action = "verify_clearance" if clearance_range else "manual_die_selection"
        else:
            die_action = "keep"
        unchanged.append({
            "station": new_requirement.station,
            "previous_station": old_requirement.station,
            "tn": new_requirement.tn,
            "angle": new_requirement.angle,
            "punch_action": "keep",
            "die_action": die_action,
        })
    return {
        "current_setup": _setup_summary(current),
        "new_setup": _setup_summary(new),
        "thickness_changed": comparison["thickness_changed"],
        "unchanged": unchanged,
        "added": [
            {
                "station": requirement.station,
                "tn": requirement.tn,
                "angle": requirement.angle,
                "punch_action": "new",
                "die_action": "new",
            }
            for requirement in comparison["added"]
        ],
        "removed": [
            {
                "station": requirement.station,
                "tn": requirement.tn,
                "angle": requirement.angle,
                "punch_action": "removed",
                "die_action": "removed",
            }
            for requirement in comparison["removed"]
        ],
    }


@router.post("/setups/compare")
def compare_with_machine_setup(item: SetupComparisonRequest):
    parsed = _parse_and_validate(item.contenido, item.nombre_archivo)
    machine_code = item.maquina_codigo.strip().upper()
    db = SessionLocal()
    try:
        _location(db, machine_code, {"machine"})
        machine_state = db.query(EstadoMaquina).filter(
            EstadoMaquina.maquina_codigo == machine_code
        ).first()
        if not machine_state or not machine_state.setup_activo_id:
            raise HTTPException(status_code=409, detail="Machine needs an initial setup baseline first")
        active_setup = db.query(Setup).filter(
            Setup.id == machine_state.setup_activo_id
        ).one()
        current_requirements = db.query(RequisitoSetup).filter(
            RequisitoSetup.setup_id == active_setup.id
        ).all()
        current_by_key = {(row.tn, row.angle): row for row in current_requirements}
        clearance_range = clearance_range_for_thickness(parsed.thickness)
        thickness_changed = Decimal(active_setup.thickness) != parsed.thickness
        rows = []
        new_keys = set()
        for requirement in parsed.requirements:
            key = requirement.key
            previous = current_by_key.get(key)
            if previous is None:
                punch_action = die_action = "new"
            else:
                punch_action = "keep"
                current_die = db.query(Herramienta).filter(
                    Herramienta.id == previous.die_tool_id
                ).first()
                if not thickness_changed:
                    die_action = "keep"
                elif clearance_range and _clearance_in_range(current_die, clearance_range):
                    die_action = "keep"
                else:
                    die_action = "replace"
            rows.append({
                "station": requirement.station,
                "tn": requirement.tn,
                "angle": requirement.angle,
                "punch_action": punch_action,
                "die_action": die_action,
                "current_punch_id": previous.punch_tool_id if previous else None,
                "current_die_id": previous.die_tool_id if previous else None,
            })
            new_keys.add(key)
        removed = [
            {
                "tn": row.tn,
                "angle": row.angle,
                "station": row.station,
                "punch_id": row.punch_tool_id,
                "die_id": row.die_tool_id,
            }
            for key, row in current_by_key.items()
            if key not in new_keys
        ]
        return {
            "machine": machine_code,
            "current_setup_id": active_setup.id,
            "current_drawing": active_setup.drawing_name,
            "current_thickness": active_setup.thickness,
            "new_setup": _setup_summary(parsed),
            "thickness_changed": thickness_changed,
            "warnings": setup_warnings(db, machine_code, parsed),
            "added_or_changed": rows,
            "removed": removed,
            "unchanged_count": sum(
                row["punch_action"] == "keep" and row["die_action"] == "keep"
                for row in rows
            ),
        }
    finally:
        db.close()


@router.post("/baselines", status_code=201)
def create_machine_baseline(item: SetupBaselineCreate):
    parsed = _parse_and_validate(item.contenido, item.nombre_archivo)
    machine_code = item.maquina_codigo.strip().upper()
    actor = item.realizado_por.strip()
    if not actor:
        raise HTTPException(status_code=422, detail="Operator name is required")
    db = SessionLocal()
    try:
        _location(db, machine_code, {"machine"})
        machine_state = db.query(EstadoMaquina).filter(
            EstadoMaquina.maquina_codigo == machine_code
        ).first()
        if machine_state and machine_state.setup_activo_id:
            raise HTTPException(status_code=409, detail="Machine already has an active setup")

        assignment_map = {}
        assigned_tool_ids = set()
        for assignment in item.herramientas:
            key = (assignment.tn.strip(), assignment.angle.strip() if assignment.angle else None)
            if key in assignment_map:
                raise HTTPException(status_code=422, detail=f"Duplicate assignment for TN/angle {key}")
            assignment_map[key] = assignment
            for physical_id, category in (
                (assignment.punch_id.strip(), "punzon"),
                (assignment.die_id.strip(), "matriz"),
            ):
                if not physical_id or physical_id in assigned_tool_ids:
                    raise HTTPException(status_code=422, detail="Each setup position requires distinct physical IDs")
                assigned_tool_ids.add(physical_id)
                tool = db.query(Herramienta).filter(Herramienta.id == physical_id).first()
                if not tool:
                    raise HTTPException(status_code=404, detail=f"Tool '{physical_id}' is not in inventory")
                if tool.tn != assignment.tn.strip() or _category(tool) != category:
                    raise HTTPException(
                        status_code=409,
                        detail=f"Tool '{physical_id}' does not match TN {assignment.tn} as {category}",
                    )
                active_assignment = db.query(RequisitoSetup).join(
                    EstadoMaquina,
                    EstadoMaquina.setup_activo_id == RequisitoSetup.setup_id,
                ).filter(
                    EstadoMaquina.maquina_codigo != machine_code,
                    (
                        (RequisitoSetup.punch_tool_id == physical_id)
                        | (RequisitoSetup.die_tool_id == physical_id)
                    ),
                ).first()
                if active_assignment:
                    raise HTTPException(
                        status_code=409,
                        detail=f"Tool '{physical_id}' is already assigned to another machine",
                    )
                pending_pickup = db.query(PendienteRecoger).filter(
                    PendienteRecoger.herramienta_id == physical_id,
                    PendienteRecoger.estado == "pending",
                ).first()
                if pending_pickup:
                    raise HTTPException(
                        status_code=409,
                        detail=f"Tool '{physical_id}' has an unresolved pickup",
                    )
                if category == "matriz":
                    clearance_range = clearance_range_for_thickness(parsed.thickness)
                    if clearance_range and not _clearance_in_range(tool, clearance_range):
                        raise HTTPException(
                            status_code=409,
                            detail=f"Die '{physical_id}' CLEARANCE is outside the range for THICKNESS",
                        )

        expected_keys = {(row.tn, row.angle) for row in parsed.requirements}
        if set(assignment_map) != expected_keys:
            missing = expected_keys - set(assignment_map)
            extra = set(assignment_map) - expected_keys
            raise HTTPException(
                status_code=422,
                detail=f"Baseline assignments must exactly match setup requirements; missing={sorted(missing)}, extra={sorted(extra)}",
            )

        setup_record, requirement_records = _write_setup(
            db, parsed, machine_code, actor, "active", item.contenido
        )
        for key, assignment in assignment_map.items():
            requirement = requirement_records[key]
            requirement.punch_tool_id = assignment.punch_id.strip()
            requirement.die_tool_id = assignment.die_id.strip()
            for physical_id in (requirement.punch_tool_id, requirement.die_tool_id):
                tool = db.query(Herramienta).filter(Herramienta.id == physical_id).one()
                tool.ubicacion = machine_code
                db.add(MovimientoHerramienta(
                    herramienta_id=tool.id,
                    origen_codigo=None,
                    destino_codigo=machine_code,
                    tipo="initial_machine_observation",
                    setup_id=setup_record.id,
                    realizado_por=actor,
                    creado_en=utcnow(),
                    detalle="Initial physical reconciliation; prior movement was not inferred.",
                ))

        if not machine_state:
            machine_state = EstadoMaquina(maquina_codigo=machine_code)
            db.add(machine_state)
        machine_state.setup_activo_id = setup_record.id
        db.commit()
        return {
            "machine": machine_code,
            "active_setup_id": setup_record.id,
            "setup": _setup_summary(parsed),
            "assigned_physical_tools": len(assigned_tool_ids),
        }
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail="Baseline conflicts with existing workflow data") from error
    finally:
        db.close()


def _position_was_returned(db, old_setup_id, tn, category):
    rows = (
        db.query(Herramienta)
        .join(MovimientoHerramienta, MovimientoHerramienta.herramienta_id == Herramienta.id)
        .filter(
            MovimientoHerramienta.setup_id == old_setup_id,
            MovimientoHerramienta.tipo.like("machine_return%"),
            Herramienta.tn == tn,
        )
        .all()
    )
    return any(_category(tool) == category for tool in rows)


def _create_kit_components(db, kit, old_setup, new_setup, new_requirements, actor):
    old_requirements = db.query(RequisitoSetup).filter(
        RequisitoSetup.setup_id == old_setup.id
    ).all()
    old_by_key = {(row.tn, row.angle): row for row in old_requirements}
    clearance_range = clearance_range_for_thickness(Decimal(new_setup.thickness))
    thickness_changed = Decimal(old_setup.thickness) != Decimal(new_setup.thickness)

    for key, new_requirement in new_requirements.items():
        previous = old_by_key.get(key)
        for category in COMPONENT_CATEGORIES:
            old_tool_id = None
            if previous:
                old_tool_id = (
                    previous.punch_tool_id if category == "punzon" else previous.die_tool_id
                )
            returned = bool(
                previous
                and old_tool_id is None
                and _position_was_returned(db, old_setup.id, previous.tn, category)
            )
            if not previous or returned or old_tool_id is None:
                action = "new"
            elif category == "punzon":
                action = "keep"
            elif not thickness_changed:
                action = "keep"
            elif clearance_range is None:
                action = "replace"
            else:
                old_tool = db.query(Herramienta).filter(Herramienta.id == old_tool_id).first()
                action = "keep" if old_tool and _clearance_in_range(old_tool, clearance_range) else "replace"

            component = ComponenteSurtido(
                surtido_id=kit.id,
                requisito_id=new_requirement.id,
                categoria=category,
                accion=action,
                herramienta_anterior_id=old_tool_id,
                herramienta_nueva_id=old_tool_id if action == "keep" else None,
                origen_codigo=None,
                estado="ready" if action == "keep" else "missing",
            )
            db.add(component)
            if action == "keep":
                if category == "punzon":
                    new_requirement.punch_tool_id = old_tool_id
                else:
                    new_requirement.die_tool_id = old_tool_id


@router.post("/kits", status_code=201)
def create_kit(item: SurtidoCreate):
    parsed = _parse_and_validate(item.contenido, item.nombre_archivo)
    previous_name = item.setup_anterior_nombre
    previous_content = item.setup_anterior_contenido
    if bool(previous_name) != bool(previous_content):
        raise HTTPException(
            status_code=422,
            detail="Carga el nombre y el contenido del archivo del setup actual.",
        )
    previous_parsed = (
        _parse_and_validate(previous_content, previous_name)
        if previous_name and previous_content
        else None
    )
    machine_code = item.maquina_codigo.strip().upper()
    cart_code = item.carro_codigo.strip().upper()
    actor = item.creado_por.strip()
    if not actor:
        raise HTTPException(status_code=422, detail="Created-by user is required")
    db = SessionLocal()
    try:
        _location(db, machine_code, {"machine"})
        _location(db, cart_code, {"cart"})
        machine_state = db.query(EstadoMaquina).filter(
            EstadoMaquina.maquina_codigo == machine_code
        ).first()
        if not machine_state or not machine_state.setup_activo_id:
            if not previous_parsed:
                raise HTTPException(
                    status_code=409,
                    detail="Carga el setup actual para iniciar el primer surtido de esta máquina.",
                )
            old_setup, _old_requirements = _write_setup(
                db,
                previous_parsed,
                machine_code,
                actor,
                "active",
                previous_content,
            )
            if not machine_state:
                machine_state = EstadoMaquina(maquina_codigo=machine_code)
                db.add(machine_state)
            machine_state.setup_activo_id = old_setup.id
            db.flush()
        else:
            old_setup = db.query(Setup).filter(
                Setup.id == machine_state.setup_activo_id
            ).one()
            if previous_parsed:
                active_requirements = db.query(RequisitoSetup).filter(
                    RequisitoSetup.setup_id == old_setup.id
                ).all()
                active_keys = {(row.tn, row.angle) for row in active_requirements}
                uploaded_keys = {requirement.key for requirement in previous_parsed.requirements}
                if (
                    active_keys != uploaded_keys
                    or Decimal(old_setup.thickness) != previous_parsed.thickness
                ):
                    if not (item.corregir_actual and (item.motivo_correccion or "").strip()):
                        raise HTTPException(
                            status_code=409,
                            detail=(
                                "SETUP_NO_COINCIDE: el setup actual elegido no coincide con el último "
                                "registrado para esta máquina."
                            ),
                        )
                    old_setup.estado = "superseded"
                    _log_machine_event(
                        db, machine_code, "correction",
                        f"Registrado {_part_from_name(old_setup.nombre_archivo)}; corregido a "
                        f"{_part_from_name(previous_parsed.source_name)}. Motivo: {item.motivo_correccion.strip()}",
                        actor,
                    )
                    old_setup, _old_requirements = _write_setup(
                        db,
                        previous_parsed,
                        machine_code,
                        actor,
                        "active",
                        previous_content,
                    )
                    machine_state.setup_activo_id = old_setup.id
                    db.flush()
        open_kit = db.query(Surtido).filter(
            Surtido.maquina_codigo == machine_code,
            Surtido.estado.in_(("preparing", "paused", "ready")),
        ).first()
        if open_kit:
            raise HTTPException(
                status_code=409,
                detail=f"Machine already has an unfinished kit ({open_kit.id})",
            )
        cart_kit = db.query(Surtido).filter(
            Surtido.carro_codigo == cart_code,
            Surtido.estado.in_(("preparing", "paused", "ready")),
        ).first()
        if cart_kit:
            raise HTTPException(
                status_code=409,
                detail=f"Cart already has an unfinished kit ({cart_kit.id})",
            )
        new_setup, new_requirements = _write_setup(
            db, parsed, machine_code, actor, "draft", item.contenido
        )
        kit = Surtido(
            id=str(uuid.uuid4()),
            maquina_codigo=machine_code,
            carro_codigo=cart_code,
            setup_anterior_id=old_setup.id,
            setup_nuevo_id=new_setup.id,
            estado="preparing",
            creado_por=actor,
            creado_en=utcnow(),
            actualizado_en=utcnow(),
        )
        db.add(kit)
        db.flush()
        _create_kit_components(db, kit, old_setup, new_setup, new_requirements, actor)
        db.commit()
        result = _kit_read(db, kit)
        result["warnings"] = setup_warnings(db, machine_code, parsed)
        return result
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail="Kit conflicts with existing setup data") from error
    finally:
        db.close()


def _kit_read(db, kit):
    components = (
        db.query(ComponenteSurtido, RequisitoSetup)
        .join(RequisitoSetup, RequisitoSetup.id == ComponenteSurtido.requisito_id)
        .filter(ComponenteSurtido.surtido_id == kit.id)
        .order_by(RequisitoSetup.tn, RequisitoSetup.angle, ComponenteSurtido.categoria)
        .all()
    )
    return {
        "id": kit.id,
        "machine": kit.maquina_codigo,
        "cart": kit.carro_codigo,
        "state": kit.estado,
        "reason": kit.motivo,
        "qr_token": kit.qr_token,
        "created_at": kit.creado_en.isoformat(),
        "delivered_at": kit.entregado_en.isoformat() if kit.entregado_en else None,
        "components": [
            {
                "component_id": component.id,
                "requirement_id": requirement.id,
                "station": requirement.station,
                "tn": requirement.tn,
                "angle": requirement.angle,
                "category": component.categoria,
                "action": component.accion,
                "previous_tool_id": component.herramienta_anterior_id,
                "tool_id": component.herramienta_nueva_id,
                "state": component.estado,
                "source_location": component.origen_codigo,
            }
            for component, requirement in components
        ],
    }


@router.get("/kits/{kit_id}")
def get_kit(kit_id: str):
    db = SessionLocal()
    try:
        kit = db.query(Surtido).filter(Surtido.id == kit_id).first()
        if not kit:
            raise HTTPException(status_code=404, detail="Kit not found")
        return _kit_read(db, kit)
    finally:
        db.close()


PART_IN_NAME = re.compile(r"[A-Z]{2,6}-\d{5,9}(?:-\d{2})?_\d{2}-[A-Z]{1,4}")
OPEN_KIT_STATES = ("preparing", "paused", "ready")


def _part_from_name(name):
    base = (name or "").replace("\\", "/").rsplit("/", 1)[-1]
    found = PART_IN_NAME.search(base.upper())
    if found:
        return found.group(0)
    return base[:-4] if base.lower().endswith(".stp") else base


def _machine_kind(raw):
    text = (raw or "").upper().replace(" ", "")
    if "45" in text:
        return "45STA"
    if text.startswith("2"):
        return "2I"
    if text.startswith("4"):
        return "4I"
    return text


def _log_machine_event(db, machine_code, kind, detail, actor):
    db.add(EventoMaquina(
        maquina_codigo=machine_code, tipo=kind, detalle=detail,
        realizado_por=actor, creado_en=utcnow(),
    ))


@router.get("/machines/running")
def machines_running():
    db = SessionLocal()
    try:
        kits = {
            kit.maquina_codigo: kit
            for kit in db.query(Surtido).filter(Surtido.estado.in_(OPEN_KIT_STATES)).all()
        }
        result = []
        machines = db.query(Ubicacion).filter(
            Ubicacion.tipo == "machine", Ubicacion.activa.is_(True)
        ).order_by(Ubicacion.codigo).all()
        for machine in machines:
            profile = db.query(Maquina).filter(Maquina.codigo == machine.codigo).first()
            state = db.query(EstadoMaquina).filter(
                EstadoMaquina.maquina_codigo == machine.codigo
            ).first()
            setup = (
                db.query(Setup).filter(Setup.id == state.setup_activo_id).first()
                if state and state.setup_activo_id else None
            )
            kit = kits.get(machine.codigo)
            result.append({
                "machine": machine.codigo,
                "tipo": _machine_kind(profile.tipo_maquina) if profile else "",
                "numero_parte": _part_from_name(setup.nombre_archivo) if setup else None,
                "thickness": setup.thickness if setup else None,
                "since": setup.creado_en.isoformat() if setup else None,
                "kit_state": kit.estado if kit else None,
                "kit_cart": kit.carro_codigo if kit else None,
            })
        return result
    finally:
        db.close()


class MachineTransfer(BaseModel):
    origen: str
    destino: str
    motivo: str
    realizado_por: str


@router.post("/machines/transfer")
def transfer_running_part(item: MachineTransfer):
    origin_code = item.origen.strip().upper()
    dest_code = item.destino.strip().upper()
    reason = item.motivo.strip()
    if not reason:
        raise HTTPException(status_code=422, detail="El motivo del traspaso es obligatorio")
    if origin_code == dest_code:
        raise HTTPException(status_code=422, detail="Origen y destino son la misma máquina")
    db = SessionLocal()
    try:
        _location(db, origin_code, {"machine"})
        _location(db, dest_code, {"machine"})
        origin_state = db.query(EstadoMaquina).filter(
            EstadoMaquina.maquina_codigo == origin_code
        ).first()
        if not origin_state or not origin_state.setup_activo_id:
            raise HTTPException(status_code=409, detail=f"{origin_code} no tiene número de parte registrado")
        dest_state = db.query(EstadoMaquina).filter(
            EstadoMaquina.maquina_codigo == dest_code
        ).first()
        if dest_state and dest_state.setup_activo_id:
            dest_setup = db.query(Setup).filter(Setup.id == dest_state.setup_activo_id).one()
            raise HTTPException(
                status_code=409,
                detail=(
                    f"{dest_code} ya tiene {_part_from_name(dest_setup.nombre_archivo)} corriendo. "
                    "Traspasa ese número primero a otra máquina."
                ),
            )
        for code in (origin_code, dest_code):
            busy = db.query(Surtido).filter(
                Surtido.maquina_codigo == code, Surtido.estado.in_(OPEN_KIT_STATES)
            ).first()
            if busy:
                raise HTTPException(
                    status_code=409,
                    detail=f"{code} tiene un surtido abierto; termínalo o cancélalo antes del traspaso.",
                )
        kinds = []
        for code in (origin_code, dest_code):
            profile = db.query(Maquina).filter(Maquina.codigo == code).first()
            kinds.append(_machine_kind(profile.tipo_maquina) if profile else "")
        if kinds[0] != kinds[1]:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"{origin_code} es {kinds[0] or 'sin tipo'} y {dest_code} es {kinds[1] or 'sin tipo'}: "
                    "el programa solo existe para su tipo de máquina."
                ),
            )
        old_setup = db.query(Setup).filter(Setup.id == origin_state.setup_activo_id).one()
        new_setup = Setup(
            maquina_codigo=dest_code,
            nombre_archivo=old_setup.nombre_archivo,
            drawing_name=old_setup.drawing_name,
            material=old_setup.material,
            thickness=old_setup.thickness,
            contenido_hash=old_setup.contenido_hash,
            creado_por=item.realizado_por,
            creado_en=utcnow(),
            estado="active",
        )
        db.add(new_setup)
        db.flush()
        old_requirements = db.query(RequisitoSetup).filter(
            RequisitoSetup.setup_id == old_setup.id
        ).all()
        moved = 0
        for requirement in old_requirements:
            db.add(RequisitoSetup(
                setup_id=new_setup.id,
                station=requirement.station,
                tn=requirement.tn,
                angle=requirement.angle,
                punch_tool_id=requirement.punch_tool_id,
                die_tool_id=requirement.die_tool_id,
            ))
            for tool_id in (requirement.punch_tool_id, requirement.die_tool_id):
                tool = db.query(Herramienta).filter(Herramienta.id == tool_id).first() if tool_id else None
                if tool and tool.ubicacion == origin_code:
                    _append_movement(
                        db, tool, origin_code, dest_code, "machine_transfer",
                        item.realizado_por, details=reason, setup_id=new_setup.id,
                    )
                    moved += 1
        old_setup.estado = "transferred"
        origin_state.setup_activo_id = None
        if not dest_state:
            dest_state = EstadoMaquina(maquina_codigo=dest_code)
            db.add(dest_state)
        dest_state.setup_activo_id = new_setup.id
        part = _part_from_name(old_setup.nombre_archivo)
        _log_machine_event(
            db, origin_code, "transfer_out", f"{part} traspasado a {dest_code}. Motivo: {reason}", item.realizado_por
        )
        _log_machine_event(
            db, dest_code, "transfer_in", f"{part} recibido de {origin_code}. Motivo: {reason}", item.realizado_por
        )
        db.commit()
        return {"numero_parte": part, "origen": origin_code, "destino": dest_code, "herramientas_movidas": moved}
    except HTTPException:
        db.rollback()
        raise
    finally:
        db.close()


@router.get("/machines/{machine_code}/active-setup")
def machine_active_setup(machine_code: str):
    db = SessionLocal()
    try:
        state = db.query(EstadoMaquina).filter(
            EstadoMaquina.maquina_codigo == machine_code.strip().upper()
        ).first()
        setup = (
            db.query(Setup).filter(Setup.id == state.setup_activo_id).first()
            if state and state.setup_activo_id else None
        )
        return {"nombre_archivo": setup.nombre_archivo if setup else None}
    finally:
        db.close()


@router.get("/kits")
def list_open_kits(machine_code: Optional[str] = None):
    db = SessionLocal()
    try:
        query = db.query(Surtido).filter(
            Surtido.estado.in_(("preparing", "paused", "ready"))
        )
        if machine_code:
            query = query.filter(Surtido.maquina_codigo == machine_code.strip().upper())
        kits = query.order_by(Surtido.creado_en.desc()).all()
        return [_kit_read(db, kit) for kit in kits]
    finally:
        db.close()


@router.get("/kits/{kit_id}/components/{component_id}/candidates")
def list_component_candidates(kit_id: str, component_id: int):
    db = SessionLocal()
    try:
        kit = db.query(Surtido).filter(Surtido.id == kit_id).first()
        component = db.query(ComponenteSurtido).filter(
            ComponenteSurtido.id == component_id,
            ComponenteSurtido.surtido_id == kit_id,
        ).first()
        if not kit or not component:
            raise HTTPException(status_code=404, detail="Kit component not found")
        if component.accion == "keep":
            return []
        requirement = db.query(RequisitoSetup).filter(
            RequisitoSetup.id == component.requisito_id
        ).one()
        setup = db.query(Setup).filter(Setup.id == kit.setup_nuevo_id).one()
        clearance_range = clearance_range_for_thickness(Decimal(setup.thickness))
        query = (
            db.query(Herramienta)
            .join(Ubicacion, Ubicacion.codigo == Herramienta.ubicacion)
            .filter(
                Herramienta.tn == requirement.tn,
                Ubicacion.tipo == "rack",
                Ubicacion.activa.is_(True),
            )
        )
        tools = query.order_by(Herramienta.id).all()
        candidates = []
        for tool in tools:
            if _category(tool) != component.categoria or (tool.estado or "disponible") != "disponible":
                continue
            reserved = db.query(ReservaHerramienta).filter(
                ReservaHerramienta.herramienta_id == tool.id,
                ReservaHerramienta.surtido_id != kit_id,
            ).first()
            if reserved:
                continue
            if component.categoria == "matriz" and clearance_range and not _clearance_in_range(tool, clearance_range):
                continue
            candidates.append({
                "id": tool.id,
                "tn": tool.tn,
                "type": tool.type,
                "size": tool.size,
                "clearance": tool.clearance,
                "life": tool.life,
                "location": tool.ubicacion,
            })
        return candidates
    finally:
        db.close()


def _assign_kit_tool(db, kit, component, tool_id, actor, tool=None):
    if kit.estado not in {"preparing", "paused"}:
        raise HTTPException(status_code=409, detail="El surtido no está aceptando escaneos de herramientas")
    if component.accion == "keep":
        raise HTTPException(status_code=409, detail="Este componente se conserva del setup activo")
    requirement = db.query(RequisitoSetup).filter(
        RequisitoSetup.id == component.requisito_id
    ).one()
    kit_cart = _location(db, kit.carro_codigo, {"cart"})
    normalized_tool_id = tool_id.strip()
    if tool is None:
        tool = db.query(Herramienta).filter(
            Herramienta.id == normalized_tool_id
        ).with_for_update().first()
    if not tool:
        raise HTTPException(status_code=404, detail="El ID físico no está registrado en el inventario")
    if tool.tn != requirement.tn or _category(tool) != component.categoria:
        raise HTTPException(status_code=409, detail="El ID físico no coincide con el TN y tipo requeridos")
    if tool.ubicacion == kit.carro_codigo and component.herramienta_nueva_id == normalized_tool_id:
        return _kit_read(db, kit)
    _require_available(tool)
    rack = db.query(Ubicacion).filter(
        Ubicacion.codigo == tool.ubicacion,
        Ubicacion.tipo == "rack",
        Ubicacion.activa.is_(True),
    ).first()
    if not rack:
        raise HTTPException(status_code=409, detail="La herramienta no está disponible en un rack activo")
    clearance_range = clearance_range_for_thickness(Decimal(
        db.query(Setup).filter(Setup.id == kit.setup_nuevo_id).one().thickness
    ))
    if component.categoria == "matriz" and clearance_range and not _clearance_in_range(tool, clearance_range):
        raise HTTPException(status_code=409, detail="El CLEARANCE de la matriz está fuera del rango permitido")

    reserved = db.query(ReservaHerramienta).filter(
        ReservaHerramienta.herramienta_id == tool.id,
        ReservaHerramienta.surtido_id != kit.id,
    ).first()
    if reserved:
        raise HTTPException(status_code=409, detail="La herramienta está reservada por otro surtido activo")
    active_assignment = db.query(RequisitoSetup.id).join(
        EstadoMaquina,
        EstadoMaquina.setup_activo_id == RequisitoSetup.setup_id,
    ).filter(
        EstadoMaquina.maquina_codigo != kit.maquina_codigo,
        (
            (RequisitoSetup.punch_tool_id == tool.id)
            | (RequisitoSetup.die_tool_id == tool.id)
        ),
    ).first()
    if active_assignment:
        raise HTTPException(status_code=409, detail="La herramienta está asignada al setup activo de otra máquina")

    if component.herramienta_nueva_id:
        old_selected = db.query(Herramienta).filter(
            Herramienta.id == component.herramienta_nueva_id
        ).first()
        if old_selected and old_selected.ubicacion == kit.carro_codigo:
            source = component.origen_codigo or rack.codigo
            _append_movement(
                db,
                old_selected,
                kit.carro_codigo,
                source,
                "kit_selection_reversed",
                actor,
                kit_id=kit.id,
            )
        db.query(ReservaHerramienta).filter(
            ReservaHerramienta.herramienta_id == component.herramienta_nueva_id,
            ReservaHerramienta.surtido_id == kit.id,
        ).delete()

    origin = tool.ubicacion
    _append_movement(
        db,
        tool,
        origin,
        kit_cart.codigo,
        "rack_to_cart_for_kit",
        actor,
        details=f"Reserved for TN {requirement.tn} {component.categoria}",
        kit_id=kit.id,
    )
    component.herramienta_nueva_id = tool.id
    component.origen_codigo = origin
    component.estado = "ready"
    db.add(ReservaHerramienta(
        herramienta_id=tool.id,
        surtido_id=kit.id,
        componente_id=component.id,
    ))
    if component.categoria == "punzon":
        requirement.punch_tool_id = tool.id
    else:
        requirement.die_tool_id = tool.id
    kit.estado = "preparing"
    kit.actualizado_en = utcnow()
    db.commit()
    return _kit_read(db, kit)


@router.post("/kits/{kit_id}/scan")
def scan_kit_tool(kit_id: str, item: SurtidoToolSelect):
    db = SessionLocal()
    try:
        kit = db.query(Surtido).filter(Surtido.id == kit_id).with_for_update().first()
        if not kit:
            raise HTTPException(status_code=404, detail="Kit not found")
        tool_id = item.herramienta_id.strip()
        if not tool_id:
            raise HTTPException(status_code=422, detail="Escanea un ID físico de herramienta")
        tool = db.query(Herramienta).filter(
            Herramienta.id == tool_id
        ).with_for_update().first()
        if not tool:
            raise HTTPException(status_code=404, detail="El ID físico no está registrado en el inventario")
        if kit.estado not in {"preparing", "paused"}:
            raise HTTPException(status_code=409, detail="El surtido no está aceptando escaneos de herramientas")

        component = db.query(ComponenteSurtido).join(
            RequisitoSetup,
            RequisitoSetup.id == ComponenteSurtido.requisito_id,
        ).filter(
            ComponenteSurtido.surtido_id == kit.id,
            ComponenteSurtido.estado == "missing",
            ComponenteSurtido.accion.in_(("new", "replace")),
            ComponenteSurtido.categoria == _category(tool),
            RequisitoSetup.tn == tool.tn,
        ).order_by(
            RequisitoSetup.station,
            RequisitoSetup.id,
            ComponenteSurtido.id,
        ).with_for_update().first()
        if not component:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"El ID físico {tool.id} (TN {tool.tn}, tipo "
                    f"{_category(tool) or 'desconocido'}) no coincide con ninguna herramienta pendiente del surtido"
                ),
            )
        return _assign_kit_tool(
            db,
            kit,
            component,
            tool_id,
            item.realizado_por,
            tool=tool,
        )
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail="No se pudo reservar la herramienta escaneada") from error
    finally:
        db.close()


@router.post("/kits/{kit_id}/components/{component_id}/select")
def select_kit_tool(kit_id: str, component_id: int, item: SurtidoToolSelect):
    db = SessionLocal()
    try:
        kit = db.query(Surtido).filter(Surtido.id == kit_id).with_for_update().first()
        component = db.query(ComponenteSurtido).filter(
            ComponenteSurtido.id == component_id,
            ComponenteSurtido.surtido_id == kit_id,
        ).with_for_update().first()
        if not kit or not component:
            raise HTTPException(status_code=404, detail="Kit component not found")
        result = _assign_kit_tool(
            db,
            kit,
            component,
            item.herramienta_id,
            item.realizado_por,
        )
        return result
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail="Could not reserve the selected physical tool") from error
    finally:
        db.close()


@router.post("/kits/{kit_id}/pause")
def pause_kit(kit_id: str, item: SurtidoAction):
    return _set_kit_state(kit_id, "paused", item.realizado_por, item.motivo)


@router.post("/kits/{kit_id}/resume")
def resume_kit(kit_id: str, item: SurtidoAction):
    return _set_kit_state(kit_id, "preparing", item.realizado_por)


def _set_kit_state(kit_id, state, actor, reason=None):
    db = SessionLocal()
    try:
        kit = db.query(Surtido).filter(Surtido.id == kit_id).first()
        if not kit:
            raise HTTPException(status_code=404, detail="Kit not found")
        if kit.estado not in {"preparing", "paused"}:
            raise HTTPException(status_code=409, detail="Kit cannot change state")
        kit.estado = state
        if reason and reason.strip():
            kit.motivo = f"{actor}: {reason.strip()}"
        kit.actualizado_en = utcnow()
        db.commit()
        return _kit_read(db, kit)
    finally:
        db.close()


@router.post("/kits/{kit_id}/cancel")
def cancel_kit(kit_id: str, item: SurtidoAction):
    db = SessionLocal()
    try:
        kit = db.query(Surtido).filter(Surtido.id == kit_id).first()
        if not kit:
            raise HTTPException(status_code=404, detail="Kit not found")
        if kit.estado not in {"preparing", "paused", "ready"}:
            raise HTTPException(status_code=409, detail="Only an unfinished kit can be cancelled")
        components = db.query(ComponenteSurtido).filter(
            ComponenteSurtido.surtido_id == kit.id,
            ComponenteSurtido.herramienta_nueva_id.isnot(None),
            ComponenteSurtido.accion != "keep",
        ).all()
        for component in components:
            tool = db.query(Herramienta).filter(
                Herramienta.id == component.herramienta_nueva_id
            ).one()
            if tool.ubicacion == kit.carro_codigo:
                destination = component.origen_codigo
                _append_movement(
                    db,
                    tool,
                    kit.carro_codigo,
                    destination,
                    "kit_cancelled_return_to_rack",
                    item.realizado_por,
                    kit_id=kit.id,
                )
            component.estado = "cancelled"
            db.query(ReservaHerramienta).filter(
                ReservaHerramienta.herramienta_id == component.herramienta_nueva_id,
                ReservaHerramienta.surtido_id == kit.id,
            ).delete()
            component.herramienta_nueva_id = None
        kit.estado = "cancelled"
        if item.motivo and item.motivo.strip():
            kit.motivo = f"{item.realizado_por}: {item.motivo.strip()}"
        kit.qr_token = None
        kit.actualizado_en = utcnow()
        db.query(Setup).filter(Setup.id == kit.setup_nuevo_id).update({"estado": "cancelled"})
        db.commit()
        return _kit_read(db, kit)
    except HTTPException:
        db.rollback()
        raise
    finally:
        db.close()


def _kit_blockers(db, kit, components):
    # Each position needs a punch and a die; kept tools must still be on the machine.
    blockers = []
    by_requirement = {}
    for component in components:
        by_requirement.setdefault(component.requisito_id, {})[component.categoria] = component
    for requisito_id, parts in by_requirement.items():
        requirement = db.query(RequisitoSetup).filter(RequisitoSetup.id == requisito_id).first()
        label = f"{requirement.tn} (estacion {requirement.station})" if requirement else str(requisito_id)
        for category in COMPONENT_CATEGORIES:
            component = parts.get(category)
            name = "punzon" if category == "punzon" else "matriz"
            if component is None:
                blockers.append(f"{label}: falta {name}")
                continue
            tool_id = component.herramienta_nueva_id
            if component.estado != "ready" or not tool_id:
                blockers.append(f"{label}: falta escanear {name}")
                continue
            if component.accion == "keep":
                tool = db.query(Herramienta).filter(Herramienta.id == tool_id).first()
                if not tool or tool.ubicacion != kit.maquina_codigo or tool.estado != "disponible":
                    blockers.append(
                        f"{label}: el {name} {tool_id} ya no esta montado en {kit.maquina_codigo}"
                    )
    return blockers


@router.post("/kits/{kit_id}/qr")
def generate_kit_qr_token(kit_id: str):
    db = SessionLocal()
    try:
        kit = db.query(Surtido).filter(Surtido.id == kit_id).first()
        if not kit:
            raise HTTPException(status_code=404, detail="Kit not found")
        if kit.estado not in {"preparing", "paused", "ready"}:
            raise HTTPException(status_code=409, detail="Kit is not eligible for delivery")
        components = db.query(ComponenteSurtido).filter(
            ComponenteSurtido.surtido_id == kit.id
        ).all()
        missing = [
            component.id
            for component in components
            if component.estado != "ready"
            or (component.accion != "keep" and not component.herramienta_nueva_id)
        ]
        blockers = _kit_blockers(db, kit, components)
        if missing or blockers:
            raise HTTPException(
                status_code=409,
                detail="Kit incompleto: " + "; ".join(blockers or [f"componentes {missing}"]),
            )
        if not kit.qr_token:
            kit.qr_token = secrets.token_urlsafe(32)
        kit.estado = "ready"
        kit.actualizado_en = utcnow()
        db.commit()
        return {
            "kit_id": kit.id,
            "machine": kit.maquina_codigo,
            "qr_token": kit.qr_token,
            "scan_value": f"TOOLTRACK-KIT:{kit.qr_token}",
        }
    except HTTPException:
        db.rollback()
        raise
    finally:
        db.close()


@router.post("/delivery/scan")
def confirm_kit_delivery(item: DeliveryScan):
    prefix = "TOOLTRACK-KIT:"
    value = item.scan_value.strip()
    if not value:
        raise HTTPException(status_code=422, detail="Scanned value is not a TOOLTRACK kit QR")
    token = value[len(prefix):].strip() if value.startswith(prefix) else value
    db = SessionLocal()
    try:
        kit = db.query(Surtido).filter(Surtido.qr_token == token).with_for_update().first()
        if not kit:
            raise HTTPException(status_code=404, detail="Kit QR is not recognized")
        if kit.estado != "ready":
            raise HTTPException(status_code=409, detail="Kit is not ready for delivery or was already delivered")
        machine_prefix = "TOOLTRACK-MACHINE:"
        scanned_machine = item.machine_scan.strip()
        if scanned_machine.startswith(machine_prefix):
            scanned_machine = scanned_machine[len(machine_prefix):]
        scanned_machine = scanned_machine.strip().upper()
        if not scanned_machine:
            raise HTTPException(status_code=422, detail="Escanea primero el QR de la máquina donde se entrega el kit")
        if scanned_machine != kit.maquina_codigo:
            raise HTTPException(
                status_code=409,
                detail=f"Este kit es para la máquina {kit.maquina_codigo}, pero se escaneó {scanned_machine}",
            )
        old_setup = db.query(Setup).filter(Setup.id == kit.setup_anterior_id).one()
        new_setup = db.query(Setup).filter(Setup.id == kit.setup_nuevo_id).one()
        machine = _location(db, kit.maquina_codigo, {"machine"})
        cart = _location(db, kit.carro_codigo, {"cart"})
        components = db.query(ComponenteSurtido).filter(
            ComponenteSurtido.surtido_id == kit.id
        ).all()
        if any(
            component.estado != "ready"
            or (component.accion != "keep" and not component.herramienta_nueva_id)
            for component in components
        ) or _kit_blockers(db, kit, components):
            raise HTTPException(status_code=409, detail="Kit became incomplete before delivery")

        new_requirements = db.query(RequisitoSetup).filter(
            RequisitoSetup.setup_id == new_setup.id
        ).all()
        new_by_key = {(row.tn, row.angle): row for row in new_requirements}
        old_requirements = db.query(RequisitoSetup).filter(
            RequisitoSetup.setup_id == old_setup.id
        ).all()
        component_by_req_cat = {
            (component.requisito_id, component.categoria): component
            for component in components
        }
        selected_ids = set()
        for requirement in new_requirements:
            for category in COMPONENT_CATEGORIES:
                component = component_by_req_cat[(requirement.id, category)]
                physical_id = component.herramienta_nueva_id
                if physical_id and physical_id in selected_ids:
                    raise HTTPException(status_code=409, detail="A physical tool cannot serve two setup positions")
                if physical_id:
                    selected_ids.add(physical_id)
                if category == "punzon":
                    requirement.punch_tool_id = physical_id
                else:
                    requirement.die_tool_id = physical_id

        now = utcnow()
        for old_requirement in old_requirements:
            new_requirement = new_by_key.get((old_requirement.tn, old_requirement.angle))
            for category in COMPONENT_CATEGORIES:
                old_tool_id = (
                    old_requirement.punch_tool_id
                    if category == "punzon"
                    else old_requirement.die_tool_id
                )
                if not old_tool_id:
                    continue
                replacement_id = None
                if new_requirement:
                    new_component = component_by_req_cat[(new_requirement.id, category)]
                    if new_component.accion == "replace":
                        replacement_id = new_component.herramienta_nueva_id
                removed_position = new_requirement is None
                if removed_position or replacement_id:
                    tool = db.query(Herramienta).filter(Herramienta.id == old_tool_id).one()
                    db.add(PendienteRecoger(
                        herramienta_id=old_tool_id,
                        maquina_codigo=machine.codigo,
                        setup_origen_id=old_setup.id,
                        surtido_id=kit.id,
                        creado_en=now,
                        estado="pending",
                    ))
                    db.add(MovimientoHerramienta(
                        herramienta_id=old_tool_id,
                        origen_codigo=machine.codigo,
                        destino_codigo=machine.codigo,
                        tipo="removed_from_setup_pending_pickup",
                        surtido_id=kit.id,
                        setup_id=old_setup.id,
                        realizado_por=item.realizado_por,
                        creado_en=now,
                        detalle=f"Removed from TN {old_requirement.tn}, angle {old_requirement.angle or 'unspecified'}; awaiting direct pickup.",
                    ))
                    tool.ubicacion = machine.codigo

        for component in components:
            if component.accion == "keep":
                continue
            tool = db.query(Herramienta).filter(
                Herramienta.id == component.herramienta_nueva_id
            ).one()
            if tool.ubicacion != cart.codigo:
                raise HTTPException(status_code=409, detail=f"Tool {tool.id} is no longer on the kit cart")
            _append_movement(
                db,
                tool,
                cart.codigo,
                machine.codigo,
                "cart_to_machine_delivery",
                item.realizado_por,
                details=f"Delivered for setup {new_setup.drawing_name or new_setup.id}",
                kit_id=kit.id,
                setup_id=new_setup.id,
            )
            db.query(ReservaHerramienta).filter(
                ReservaHerramienta.herramienta_id == tool.id,
                ReservaHerramienta.surtido_id == kit.id,
            ).delete()
        old_setup.estado = "superseded"
        new_setup.estado = "active"
        machine_state = db.query(EstadoMaquina).filter(
            EstadoMaquina.maquina_codigo == machine.codigo
        ).one()
        machine_state.setup_activo_id = new_setup.id
        kit.estado = "delivered"
        kit.entregado_en = now
        kit.actualizado_en = now
        db.commit()
        return {
            "kit_id": kit.id,
            "machine": machine.codigo,
            "active_setup_id": new_setup.id,
            "active_drawing": new_setup.drawing_name,
            "pending_pickups_created": db.query(PendienteRecoger).filter(
                PendienteRecoger.surtido_id == kit.id,
                PendienteRecoger.estado == "pending",
            ).count(),
            "delivered_tools": sum(component.accion != "keep" for component in components),
        }
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail="Delivery conflicts with active tool assignments") from error
    finally:
        db.close()


@router.get("/pending-pickups")
def list_pending_pickups():
    db = SessionLocal()
    try:
        rows = (
            db.query(PendienteRecoger, Herramienta, Setup)
            .join(Herramienta, Herramienta.id == PendienteRecoger.herramienta_id)
            .join(Setup, Setup.id == PendienteRecoger.setup_origen_id)
            .filter(PendienteRecoger.estado == "pending")
            .order_by(PendienteRecoger.creado_en)
            .all()
        )
        now = utcnow()
        standard_machines = {
            row[0] for row in db.query(Ubicacion.codigo).filter(Ubicacion.estandar.is_(True))
        }
        return [
            {
                "pending_id": pending.id,
                "tool_id": tool.id,
                "tn": tool.tn,
                "category": tool.categoria,
                "clearance": tool.clearance,
                "machine": pending.maquina_codigo,
                "drawing_name": setup.drawing_name,
                "created_at": pending.creado_en.isoformat(),
                "age_hours": round((now - pending.creado_en).total_seconds() / 3600, 1),
                "standard_machine": pending.maquina_codigo in standard_machines,
                "overdue": (
                    pending.maquina_codigo not in standard_machines
                    and (now - pending.creado_en).total_seconds() >= 3 * 24 * 3600
                ),
                "home_rack": _home_rack(db, tool.id),
            }
            for pending, tool, setup in rows
        ]
    finally:
        db.close()


@router.get("/tools/movable")
def list_movable_tools():
    db = SessionLocal()
    try:
        punch_assignments = db.query(
            RequisitoSetup.punch_tool_id.label("tool_id")
        ).join(
            EstadoMaquina,
            EstadoMaquina.setup_activo_id == RequisitoSetup.setup_id,
        ).filter(RequisitoSetup.punch_tool_id.isnot(None)).union(
            db.query(
                RequisitoSetup.die_tool_id.label("tool_id")
            ).join(
                EstadoMaquina,
                EstadoMaquina.setup_activo_id == RequisitoSetup.setup_id,
            ).filter(RequisitoSetup.die_tool_id.isnot(None))
        ).subquery()
        rows = (
            db.query(Herramienta, Ubicacion)
            .join(Ubicacion, Ubicacion.codigo == Herramienta.ubicacion)
            .filter(
                Ubicacion.tipo.in_(("togu", "maintenance", "workshop")),
                Ubicacion.activa.is_(True),
                ~Herramienta.id.in_(select(punch_assignments.c.tool_id)),
            )
            .order_by(Herramienta.id)
            .all()
        )
        return [
            {
                "tool_id": tool.id,
                "tn": tool.tn,
                "category": tool.categoria,
                "clearance": tool.clearance,
                "location": location.codigo,
                "location_type": location.tipo,
                "home_rack": _home_rack(db, tool.id),
            }
            for tool, location in rows
        ]
    finally:
        db.close()


@router.post("/tools/{tool_id}/move")
def move_available_tool(tool_id: str, item: PendingPickupMove):
    db = SessionLocal()
    try:
        tool = db.query(Herramienta).filter(
            Herramienta.id == tool_id
        ).with_for_update().first()
        if not tool:
            raise HTTPException(status_code=404, detail="Physical tool is not in inventory")
        origin = _location(db, tool.ubicacion, {"togu", "maintenance", "workshop"})
        destination = _location(
            db,
            item.destino_codigo.strip().upper(),
            {"rack", "togu", "maintenance", "workshop"},
        )
        if origin.codigo == destination.codigo:
            raise HTTPException(status_code=409, detail="Tool is already at the selected location")
        _require_home_rack(db, tool, destination)
        _status_on_move(tool, origin.tipo, destination.tipo)
        pending = db.query(PendienteRecoger.id).filter(
            PendienteRecoger.herramienta_id == tool.id,
            PendienteRecoger.estado == "pending",
        ).first()
        if pending:
            raise HTTPException(
                status_code=409,
                detail="Tool has an unresolved machine pickup; complete that pickup first",
            )
        active_assignment = db.query(RequisitoSetup.id).join(
            EstadoMaquina,
            EstadoMaquina.setup_activo_id == RequisitoSetup.setup_id,
        ).filter(
            (RequisitoSetup.punch_tool_id == tool.id)
            | (RequisitoSetup.die_tool_id == tool.id)
        ).first()
        if active_assignment:
            raise HTTPException(status_code=409, detail="Tool is assigned to an active machine setup")
        if destination.tipo == "togu":
            occupied = db.query(Herramienta.id).filter(
                Herramienta.ubicacion == destination.codigo,
                Herramienta.id != tool.id,
            ).first()
            if occupied:
                raise HTTPException(status_code=409, detail=f"TOGU '{destination.codigo}' is occupied")
        _append_movement(
            db,
            tool,
            origin.codigo,
            destination.codigo,
            f"{origin.tipo}_to_{destination.tipo}",
            item.realizado_por,
            details="Recorded tool movement",
        )
        db.commit()
        return {
            "tool_id": tool.id,
            "from": origin.codigo,
            "to": destination.codigo,
            "type": f"{origin.tipo}_to_{destination.tipo}",
        }
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail="Could not record the tool movement") from error
    finally:
        db.close()


@router.post("/pending-pickups/{pending_id}/move")
def move_pending_pickup(pending_id: int, item: PendingPickupMove):
    db = SessionLocal()
    try:
        pending = db.query(PendienteRecoger).filter(
            PendienteRecoger.id == pending_id
        ).with_for_update().first()
        if not pending or pending.estado != "pending":
            raise HTTPException(status_code=404, detail="Pending pickup is not active")
        destination_code = item.destino_codigo.strip().upper()
        destination = _location(db, destination_code, {"rack", "togu", "maintenance"})
        tool = db.query(Herramienta).filter(
            Herramienta.id == pending.herramienta_id
        ).with_for_update().one()
        _require_home_rack(db, tool, destination)
        if destination.tipo == "togu":
            occupied = db.query(Herramienta).filter(
                Herramienta.ubicacion == destination.codigo
            ).first()
            if occupied:
                raise HTTPException(status_code=409, detail=f"TOGU '{destination.codigo}' is occupied")
        origin = pending.maquina_codigo
        _status_on_move(tool, "machine", destination.tipo)
        _append_movement(
            db,
            tool,
            origin,
            destination.codigo,
            f"pending_pickup_to_{destination.tipo}",
            item.realizado_por,
            details=f"Collected from machine {pending.maquina_codigo}",
            kit_id=pending.surtido_id,
            setup_id=pending.setup_origen_id,
        )
        pending.estado = "collected"
        pending.recogido_en = utcnow()
        db.commit()
        return {
            "pending_id": pending.id,
            "tool_id": tool.id,
            "from": origin,
            "to": destination.codigo,
            "to_type": destination.tipo,
            "collected_at": pending.recogido_en.isoformat(),
        }
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail="Could not record the pickup movement") from error
    finally:
        db.close()


@router.get("/movements")
def list_movements(tool_id: Optional[str] = None, machine_code: Optional[str] = None):
    db = SessionLocal()
    try:
        query = db.query(MovimientoHerramienta)
        if tool_id:
            query = query.filter(MovimientoHerramienta.herramienta_id == tool_id)
        if machine_code:
            query = query.filter(
                (MovimientoHerramienta.origen_codigo == machine_code)
                | (MovimientoHerramienta.destino_codigo == machine_code)
            )
        rows = query.order_by(MovimientoHerramienta.creado_en.desc()).limit(500).all()
        return [
            {
                "id": row.id,
                "tool_id": row.herramienta_id,
                "from": row.origen_codigo,
                "to": row.destino_codigo,
                "type": row.tipo,
                "kit_id": row.surtido_id,
                "setup_id": row.setup_id,
                "by": row.realizado_por,
                "created_at": row.creado_en.isoformat(),
                "details": row.detalle,
            }
            for row in rows
        ]
    finally:
        db.close()
