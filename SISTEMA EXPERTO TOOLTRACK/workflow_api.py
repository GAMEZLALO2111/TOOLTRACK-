import hashlib
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from db import SessionLocal
import scan_codes
import tool_params
from machine_rules import setup_warnings
from models.inventory import (
    BajaHerramienta,
    ComponenteSurtido,
    EstadoMaquina,
    EventoMaquina,
    Herramienta,
    Maquina,
    MovimientoHerramienta,
    PendienteRecoger,
    ProgramaParte,
    ReservaHerramienta,
    RequisitoSetup,
    Setup,
    SolicitudServicio,
    Surtido,
    TicketMantenimiento,
    Ubicacion,
)
from setup_workflow import (
    SetupParseError,
    SetupReviewRequired,
    clearance_range_for_thickness,
    compare_setup_requirements,
    pair_positions,
    parse_setup_text,
    validate_setup_requirements,
)
from tn_tolerance import compatible_tns, describe_difference, tn_compatible, tolerance_for


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


_last_now = None


def utcnow():
    """Naive UTC, strictly increasing: kits created in the same clock tick still keep their order in line."""
    global _last_now
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if _last_now is not None and now <= _last_now:
        now = _last_now + timedelta(microseconds=1)
    _last_now = now
    return now


def _tolerance(db=None):
    if db is not None:
        return tolerance_for(db)
    own = SessionLocal()
    try:
        return tolerance_for(own)
    finally:
        own.close()


def _pairs(db, old_items, new_items):
    return pair_positions(old_items, new_items, _tolerance(db))


def _tn_fits(db, required_tn, tool_tn):
    """A tool fits a required TN when it is that TN or is within the dimensional tolerance of it."""
    return required_tn == tool_tn or tn_compatible(required_tn, tool_tn, _tolerance(db))


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
                f"Tool {tool.id} (TN {tool.tn}) does not belong in rack "
                f"'{destination.codigo}'. Its place is '{home}'."
            ),
        )


def _require_no_open_ticket(db, tool):
    """A tool with an open maintenance ticket or a retirement waiting for approval only moves through it."""
    ticket = db.query(TicketMantenimiento).filter(
        TicketMantenimiento.herramienta_id == tool.id,
        TicketMantenimiento.estado != "closed",
    ).first()
    if ticket:
        raise HTTPException(
            status_code=409,
            detail=f"Tool {tool.id} has the open maintenance ticket MT-{ticket.id:05d}; Maintenance has to close it first.",
        )
    retirement = db.query(BajaHerramienta).filter(
        BajaHerramienta.herramienta_id == tool.id, BajaHerramienta.estado == "pending",
    ).first()
    if retirement:
        raise HTTPException(
            status_code=409,
            detail=f"Tool {tool.id} has the retirement BJ-{retirement.id:05d} waiting for approval.",
        )


TOOL_STATUS_LABELS = {
    "disponible": "Available",
    "afilado": "Being sharpened",
    "danado": "Damaged (under review)",
    "baja": "Retired",
}
SERVICE_STATUS_BY_LOCATION = {"togu": "afilado", "workshop": "afilado", "maintenance": "danado"}


def _require_available(tool):
    status = tool.estado or "disponible"
    if status != "disponible":
        raise HTTPException(
            status_code=409,
            detail=f"Tool {tool.id} is not available: {TOOL_STATUS_LABELS.get(status, status)}",
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
                    f"Tool {tool.id} is '{TOOL_STATUS_LABELS.get(tool.estado, tool.estado)}'. "
                    "Use «Store in rack» and record the result (ready for use or retired)."
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
    capture=None,
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
        captura=capture,
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


def _manual_reference(db, parsed_setup, source_text, machine_code, actor, carry_from):
    """Baseline chosen by hand for one kit: a reference copy of that program, not the machine record.

    The physical tools of the expected previous setup are carried over by TN, so «keep» still means
    a tool that is really there.
    """
    reference, reference_requirements = _write_setup(db, parsed_setup, machine_code, actor, "reference", source_text)
    carried = db.query(RequisitoSetup).filter(RequisitoSetup.setup_id == carry_from.id).all()
    for old, new in _pairs(db, carried, list(reference_requirements.values()))[0]:
        new.punch_tool_id = old.punch_tool_id
        new.die_tool_id = old.die_tool_id
    db.flush()
    return reference


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
    comparacion_manual: bool = False


class SurtidoToolSelect(BaseModel):
    herramienta_id: str
    realizado_por: str
    captura: Optional[str] = None


class SurtidoAction(BaseModel):
    realizado_por: str
    motivo: Optional[str] = None


class DeliveryScan(BaseModel):
    scan_value: str
    machine_scan: str = ""
    realizado_por: str
    fuera_de_orden: bool = False
    motivo_orden: Optional[str] = None


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
    comparison = compare_setup_requirements(current, new, _tolerance())
    clearance_range = clearance_range_for_thickness(new.thickness)
    unchanged = []
    for old_requirement, new_requirement in comparison["pairs"]:
        if comparison["thickness_changed"]:
            die_action = "verify_clearance" if clearance_range else "manual_die_selection"
        else:
            die_action = "keep"
        unchanged.append({
            "station": new_requirement.station,
            "previous_station": old_requirement.station,
            "tn": new_requirement.tn,
            "angle": new_requirement.angle,
            "previous_angle": old_requirement.angle,
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
        pairs, _added, removed_rows = _pairs(db, current_requirements, parsed.requirements)
        previous_by_new = {id(new): old for old, new in pairs}
        clearance_range = clearance_range_for_thickness(parsed.thickness)
        thickness_changed = Decimal(active_setup.thickness) != parsed.thickness
        rows = []
        for requirement in parsed.requirements:
            previous = previous_by_new.get(id(requirement))
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
                "previous_angle": previous.angle if previous else None,
                "punch_action": punch_action,
                "die_action": die_action,
                "current_punch_id": previous.punch_tool_id if previous else None,
                "current_die_id": previous.die_tool_id if previous else None,
            })
        removed = [
            {
                "tn": row.tn,
                "angle": row.angle,
                "station": row.station,
                "punch_id": row.punch_tool_id,
                "die_id": row.die_tool_id,
            }
            for row in removed_rows
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
                if not _tn_fits(db, assignment.tn.strip(), tool.tn) or _category(tool) != category:
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
    pairs, _added, _removed = _pairs(db, old_requirements, list(new_requirements.values()))
    previous_by_new = {new.id: old for old, new in pairs}
    clearance_range = clearance_range_for_thickness(Decimal(new_setup.thickness))
    thickness_changed = Decimal(old_setup.thickness) != Decimal(new_setup.thickness)

    for key, new_requirement in new_requirements.items():
        previous = previous_by_new.get(new_requirement.id)
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
    manual_baseline = False
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
            queued = _open_kits(db, machine_code)
            if len(queued) >= MAX_OPEN_KITS_PER_MACHINE:
                raise HTTPException(
                    status_code=409,
                    detail=f"{machine_code} already has {len(queued)} kits in line (maximum {MAX_OPEN_KITS_PER_MACHINE}). "
                           "Deliver or cancel one first.",
                )
            if queued:
                # By default the new kit is compared against the one before it in line, not against what is
                # mounted today. The user may pick the mounted part or any kit in line instead.
                old_setup = db.query(Setup).filter(Setup.id == queued[-1].setup_nuevo_id).one()
                if previous_parsed:
                    wanted = _part_from_name(previous_name).upper()
                    candidates = [db.query(Setup).filter(Setup.id == machine_state.setup_activo_id).one()] + [
                        db.query(Setup).filter(Setup.id == other.setup_nuevo_id).one() for other in queued
                    ]
                    chosen = next(
                        (row for row in reversed(candidates) if _part_from_name(row.nombre_archivo).upper() == wanted),
                        None,
                    )
                    if chosen is None:
                        if not item.comparacion_manual:
                            raise HTTPException(
                                status_code=409,
                                detail=f"{machine_code} has a line of kits: compare against the part mounted on it or "
                                       "against one of the kits in line, or choose a manual comparison.",
                            )
                        chosen = _manual_reference(db, previous_parsed, previous_content, machine_code, actor, old_setup)
                    if chosen.id != old_setup.id:
                        old_setup = chosen
                        manual_baseline = True
                        _log_machine_event(
                            db, machine_code, "kit_manual_baseline",
                            f"Kit of {_part_from_name(item.nombre_archivo)} compared against {wanted} chosen by the user",
                            actor,
                        )
            elif previous_parsed:
                active_requirements = db.query(RequisitoSetup).filter(
                    RequisitoSetup.setup_id == old_setup.id
                ).all()
                active_keys = {(row.tn, row.angle) for row in active_requirements}
                uploaded_keys = {requirement.key for requirement in previous_parsed.requirements}
                if (
                    active_keys != uploaded_keys
                    or Decimal(old_setup.thickness) != previous_parsed.thickness
                ) and item.comparacion_manual:
                    # Compare only: the machine record is left as it is; the kit is delivered against what is mounted.
                    old_setup = _manual_reference(db, previous_parsed, previous_content, machine_code, actor, old_setup)
                    manual_baseline = True
                    _log_machine_event(
                        db, machine_code, "kit_manual_baseline",
                        f"Kit of {_part_from_name(item.nombre_archivo)} compared against "
                        f"{_part_from_name(previous_name).upper()} chosen by the user",
                        actor,
                    )
                elif (
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
                    old_setup, corrected_requirements = _write_setup(
                        db,
                        previous_parsed,
                        machine_code,
                        actor,
                        "active",
                        previous_content,
                    )
                    for earlier, corrected in _pairs(db, active_requirements, list(corrected_requirements.values()))[0]:
                        corrected.punch_tool_id = earlier.punch_tool_id
                        corrected.die_tool_id = earlier.die_tool_id
                    machine_state.setup_activo_id = old_setup.id
                    db.flush()
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
            anterior_manual=manual_baseline,
            creado_por=actor,
            creado_en=utcnow(),
            actualizado_en=utcnow(),
        )
        db.add(kit)
        db.flush()
        _create_kit_components(db, kit, old_setup, new_setup, new_requirements, actor)
        db.flush()
        _try_mark_ready(db, kit)
        db.commit()
        result = _kit_read(db, kit)
        result["warnings"] = setup_warnings(db, machine_code, parsed)
        return result
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError as error:
        db.rollback()
        if "uq_surtido_abierto" in str(error):
            raise HTTPException(
                status_code=409,
                detail="Another kit was just started for this cart. Refresh and check the open kits.",
            ) from error
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
    new_setup = db.query(Setup).filter(Setup.id == kit.setup_nuevo_id).first()
    old_requirements = db.query(RequisitoSetup).filter(RequisitoSetup.setup_id == kit.setup_anterior_id).all()
    new_requirements = [requirement for _component, requirement in components]
    seen = set()
    new_requirements = [r for r in new_requirements if not (r.id in seen or seen.add(r.id))]
    previous_by_new = {new.id: old for old, new in _pairs(db, old_requirements, new_requirements)[0]}
    queue = _open_kits(db, kit.maquina_codigo) if kit.estado in OPEN_KIT_STATES else []
    ahead = []
    for other in queue:
        if (other.creado_en, other.id) >= (kit.creado_en, kit.id):
            break
        other_setup = db.query(Setup).filter(Setup.id == other.setup_nuevo_id).first()
        ahead.append({
            "kit_id": other.id,
            "part_number": _part_from_name(other_setup.nombre_archivo).upper() if other_setup else None,
            "cart": other.carro_codigo,
            "state": other.estado,
        })
    clearance_range = None
    if new_setup:
        try:
            clearance_range = clearance_range_for_thickness(Decimal(new_setup.thickness))
        except (InvalidOperation, TypeError):
            clearance_range = None
    tool_ids = {component.herramienta_nueva_id for component, _r in components if component.herramienta_nueva_id}
    tools_by_id = (
        {tool.id: tool for tool in db.query(Herramienta).filter(Herramienta.id.in_(tool_ids)).all()}
        if tool_ids else {}
    )
    params = tool_params.load(db)

    def tool_tn(component):
        tool = tools_by_id.get(component.herramienta_nueva_id)
        return tool.tn if tool else None

    def tool_alert(component):
        tool = tools_by_id.get(component.herramienta_nueva_id)
        return bool(tool and tool_params.life_alert(component.categoria, tool.life, params))

    return {
        "id": kit.id,
        "machine": kit.maquina_codigo,
        "cart": kit.carro_codigo,
        "state": kit.estado,
        "reason": kit.motivo,
        "part_number": _part_from_name(new_setup.nombre_archivo).upper() if new_setup else None,
        "thickness": new_setup.thickness if new_setup else None,
        "clearance_min": str(clearance_range[0]) if clearance_range else None,
        "clearance_max": str(clearance_range[1]) if clearance_range else None,
        "qr_token": kit.qr_token,
        "queue_position": len(ahead) + 1 if kit.estado in OPEN_KIT_STATES else None,
        "queue_total": len(queue),
        "anterior_manual": bool(kit.anterior_manual),
        "ahead": ahead,
        "alerts": _kit_alerts(db, kit),
        "created_at": kit.creado_en.isoformat(),
        "delivered_at": kit.entregado_en.isoformat() if kit.entregado_en else None,
        "components": [
            {
                "component_id": component.id,
                "requirement_id": requirement.id,
                "station": requirement.station,
                "tn": requirement.tn,
                "angle": requirement.angle,
                "previous_angle": (
                    previous_by_new[requirement.id].angle if requirement.id in previous_by_new else None
                ),
                "reorient": (
                    component.accion == "keep" and requirement.id in previous_by_new
                    and previous_by_new[requirement.id].angle != requirement.angle
                ),
                "category": component.categoria,
                "action": component.accion,
                "previous_tool_id": component.herramienta_anterior_id,
                "tool_id": component.herramienta_nueva_id,
                "tool_tn": tool_tn(component),
                "substitute": bool(tool_tn(component) and tool_tn(component) != requirement.tn),
                "tn_difference": describe_difference(requirement.tn, tool_tn(component)),
                "life_alert": tool_alert(component),
                "previous_tn": previous_by_new[requirement.id].tn if requirement.id in previous_by_new else None,
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
MAX_OPEN_KITS_PER_MACHINE = 5


def _open_kits(db, machine_code):
    """Open kits of a machine in the order they will be delivered (the order they were started)."""
    return db.query(Surtido).filter(
        Surtido.maquina_codigo == machine_code, Surtido.estado.in_(OPEN_KIT_STATES)
    ).order_by(Surtido.creado_en, Surtido.id).all()


def _expected_previous_setup_id(db, kit):
    """What this kit is compared against: the kit before it in line, or what is mounted on the machine
    (or the setup the user chose by hand when the kit was started)."""
    if kit.anterior_manual:
        return kit.setup_anterior_id
    before = [other for other in _open_kits(db, kit.maquina_codigo)
              if (other.creado_en, other.id) < (kit.creado_en, kit.id)]
    if before:
        return before[-1].setup_nuevo_id
    state = db.query(EstadoMaquina).filter(EstadoMaquina.maquina_codigo == kit.maquina_codigo).first()
    return state.setup_activo_id if state else None


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
        kits = {}
        kit_counts = {}
        for kit in db.query(Surtido).filter(Surtido.estado.in_(OPEN_KIT_STATES)).order_by(
            Surtido.creado_en, Surtido.id
        ).all():
            kits.setdefault(kit.maquina_codigo, kit)
            kit_counts[kit.maquina_codigo] = kit_counts.get(kit.maquina_codigo, 0) + 1
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
                "kit_count": kit_counts.get(machine.codigo, 0),
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
        return _component_candidates(db, kit, component)
    finally:
        db.close()


def _component_candidates(db, kit, component):
    if component.accion == "keep":
        return []
    requirement = db.query(RequisitoSetup).filter(
        RequisitoSetup.id == component.requisito_id
    ).one()
    setup = db.query(Setup).filter(Setup.id == kit.setup_nuevo_id).one()
    clearance_range = clearance_range_for_thickness(Decimal(setup.thickness))
    tolerance = _tolerance(db)
    tns = compatible_tns(db, requirement.tn, tolerance)
    query = (
        db.query(Herramienta)
        .join(Ubicacion, Ubicacion.codigo == Herramienta.ubicacion)
        .filter(
            Herramienta.tn.in_(tns),
            Ubicacion.tipo == "rack",
            Ubicacion.activa.is_(True),
        )
    )
    tools = sorted(query.order_by(Herramienta.id).all(), key=lambda row: tns.index(row.tn))
    params = tool_params.load(db)
    candidates = []
    for tool in tools:
        if _category(tool) != component.categoria or (tool.estado or "disponible") != "disponible":
            continue
        reserved = db.query(ReservaHerramienta).filter(
            ReservaHerramienta.herramienta_id == tool.id,
            ReservaHerramienta.surtido_id != kit.id,
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
            "substitute": tool.tn != requirement.tn,
            "tn_difference": describe_difference(requirement.tn, tool.tn),
            "life_alert": tool_params.life_alert(component.categoria, tool.life, params),
        })
    return candidates


@router.get("/kits/{kit_id}/pick-sheet")
def get_pick_sheet(kit_id: str):
    db = SessionLocal()
    try:
        kit = db.query(Surtido).filter(Surtido.id == kit_id).first()
        if not kit:
            raise HTTPException(status_code=404, detail="Kit not found")
        summary = _kit_read(db, kit)
        rows = (
            db.query(ComponenteSurtido, RequisitoSetup)
            .join(RequisitoSetup, RequisitoSetup.id == ComponenteSurtido.requisito_id)
            .filter(
                ComponenteSurtido.surtido_id == kit.id,
                ComponenteSurtido.accion != "keep",
                ComponenteSurtido.estado == "missing",
            )
            .all()
        )
        suggested_ids = set()
        items = []
        for component, requirement in rows:
            candidates = [
                candidate for candidate in _component_candidates(db, kit, component)
                if candidate["id"] not in suggested_ids
            ]
            chosen = candidates[:3]
            if chosen:
                suggested_ids.add(chosen[0]["id"])
            items.append({
                "station": requirement.station,
                "tn": requirement.tn,
                "angle": requirement.angle,
                "category": component.categoria,
                "clearance_min": summary["clearance_min"] if component.categoria == "matriz" else None,
                "clearance_max": summary["clearance_max"] if component.categoria == "matriz" else None,
                "suggested": chosen[0] if chosen else None,
                "alternates": chosen[1:],
            })

        def sort_key(item):
            suggested = item["suggested"]
            return (
                suggested is None,
                suggested["location"] if suggested else "",
                item["tn"],
                item["category"],
                item["station"] or "",
            )

        items.sort(key=sort_key)
        return {
            "kit_id": kit.id,
            "machine": summary["machine"],
            "cart": summary["cart"],
            "part_number": summary["part_number"],
            "thickness": summary["thickness"],
            "clearance_min": summary["clearance_min"],
            "clearance_max": summary["clearance_max"],
            "created_at": summary["created_at"],
            "items": items,
        }
    finally:
        db.close()


def _assign_kit_tool(db, kit, component, tool_id, actor, tool=None, capture=None):
    if kit.estado not in {"preparing", "paused"}:
        raise HTTPException(status_code=409, detail="This kit is not accepting tool scans")
    if component.accion == "keep":
        raise HTTPException(status_code=409, detail="This component stays from the active setup")
    requirement = db.query(RequisitoSetup).filter(
        RequisitoSetup.id == component.requisito_id
    ).one()
    kit_cart = _location(db, kit.carro_codigo, {"cart"})
    if tool is None:
        tool = scan_codes.find_tool(db, tool_id, lock=True)
    if not tool:
        raise HTTPException(status_code=404, detail="That tool ID is not in the inventory")
    normalized_tool_id = tool.id
    if not _tn_fits(db, requirement.tn, tool.tn) or _category(tool) != component.categoria:
        raise HTTPException(status_code=409, detail="That tool ID does not match the required TN and type")
    if tool.ubicacion == kit.carro_codigo and component.herramienta_nueva_id == normalized_tool_id:
        return _kit_read(db, kit)
    _require_available(tool)
    rack = db.query(Ubicacion).filter(
        Ubicacion.codigo == tool.ubicacion,
        Ubicacion.tipo == "rack",
        Ubicacion.activa.is_(True),
    ).first()
    if not rack:
        if not tool.ubicacion:
            raise HTTPException(
                status_code=409,
                detail=f"Tool {tool.id} has no rack recorded yet. Place it in its rack with Tools > Store in rack and try again.",
            )
        raise HTTPException(status_code=409, detail="The tool is not available in an active rack")
    clearance_range = clearance_range_for_thickness(Decimal(
        db.query(Setup).filter(Setup.id == kit.setup_nuevo_id).one().thickness
    ))
    if component.categoria == "matriz" and clearance_range and not _clearance_in_range(tool, clearance_range):
        raise HTTPException(status_code=409, detail="The die CLEARANCE is outside the allowed range")

    reserved = db.query(ReservaHerramienta).filter(
        ReservaHerramienta.herramienta_id == tool.id,
        ReservaHerramienta.surtido_id != kit.id,
    ).first()
    if reserved:
        raise HTTPException(status_code=409, detail="The tool is reserved by another open kit")
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
        raise HTTPException(status_code=409, detail="The tool is assigned to the active setup of another machine")

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
        details=f"Reserved for TN {requirement.tn} {component.categoria}" + (
            f" (substitute TN {tool.tn}, {describe_difference(requirement.tn, tool.tn)} in. from the required size)"
            if tool.tn != requirement.tn else ""
        ),
        kit_id=kit.id,
        capture=capture,
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
    db.flush()
    _try_mark_ready(db, kit)
    db.commit()
    return _kit_read(db, kit)


@router.post("/kits/{kit_id}/scan")
def scan_kit_tool(kit_id: str, item: SurtidoToolSelect):
    db = SessionLocal()
    try:
        kit = db.query(Surtido).filter(Surtido.id == kit_id).with_for_update().first()
        if not kit:
            raise HTTPException(status_code=404, detail="Kit not found")
        tool_id = scan_codes.tool_code(item.herramienta_id)
        if not tool_id:
            raise HTTPException(status_code=422, detail="Scan or type a physical tool ID")
        tool = scan_codes.find_tool(db, tool_id, lock=True)
        if not tool:
            raise HTTPException(status_code=404, detail="That tool ID is not in the inventory")
        if kit.estado not in {"preparing", "paused"}:
            raise HTTPException(status_code=409, detail="This kit is not accepting tool scans")

        tolerance = _tolerance(db)
        candidates = db.query(ComponenteSurtido, RequisitoSetup).join(
            RequisitoSetup,
            RequisitoSetup.id == ComponenteSurtido.requisito_id,
        ).filter(
            ComponenteSurtido.surtido_id == kit.id,
            ComponenteSurtido.estado == "missing",
            ComponenteSurtido.accion.in_(("new", "replace")),
            ComponenteSurtido.categoria == _category(tool),
        ).order_by(
            RequisitoSetup.station,
            RequisitoSetup.id,
            ComponenteSurtido.id,
        ).with_for_update().all()
        matches = [
            component for component, requirement in candidates
            if requirement.tn == tool.tn or tn_compatible(requirement.tn, tool.tn, tolerance)
        ]
        exact = [component for component, requirement in candidates if requirement.tn == tool.tn]
        component = (exact or matches or [None])[0]
        if not component:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Tool {tool.id} (TN {tool.tn}, type "
                    f"{_category(tool) or 'unknown'}) does not match any pending tool in this kit"
                ),
            )
        return _assign_kit_tool(
            db,
            kit,
            component,
            tool_id,
            item.realizado_por,
            tool=tool,
            capture=scan_codes.capture_mode(item.captura),
        )
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(status_code=409, detail="The scanned tool could not be reserved") from error
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
            capture=scan_codes.capture_mode(item.captura),
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


def _kit_baseline_outdated(db, kit, old_setup_id=None):
    """True when a position already supplied by the previous setup or kit is still asked as new."""
    old_setup_id = old_setup_id or _expected_previous_setup_id(db, kit)
    old_requirements = db.query(RequisitoSetup).filter(RequisitoSetup.setup_id == old_setup_id).all()
    new_requirements = db.query(RequisitoSetup).filter(RequisitoSetup.setup_id == kit.setup_nuevo_id).all()
    components = {
        (component.requisito_id, component.categoria): component
        for component in db.query(ComponenteSurtido).filter(ComponenteSurtido.surtido_id == kit.id).all()
    }
    for old, new in _pairs(db, old_requirements, new_requirements)[0]:
        for category in COMPONENT_CATEGORIES:
            old_tool_id = old.punch_tool_id if category == "punzon" else old.die_tool_id
            component = components.get((new.id, category))
            if (
                old_tool_id and component is not None and component.accion == "new"
                and not _position_was_returned(db, old.setup_id, old.tn, category)
            ):
                return True
    return False


def _kit_alerts(db, kit):
    """Warnings for an open kit whose comparison no longer matches the line in front of it."""
    if kit.estado not in OPEN_KIT_STATES:
        return []
    expected = _expected_previous_setup_id(db, kit)
    if expected != kit.setup_anterior_id:
        return ["The kit before this one changed, was cancelled or was delivered: refresh this kit to compare it again."]
    if _kit_baseline_outdated(db, kit, expected):
        return ["The previous kit or the machine already has tools this kit asks for again: refresh it to reuse them."]
    return []


@router.post("/kits/{kit_id}/refresh")
def refresh_kit(kit_id: str, item: SurtidoAction):
    actor = item.realizado_por.strip()
    db = SessionLocal()
    try:
        kit = db.query(Surtido).filter(Surtido.id == kit_id).with_for_update().first()
        if not kit:
            raise HTTPException(status_code=404, detail="Kit not found")
        if kit.estado not in OPEN_KIT_STATES:
            raise HTTPException(status_code=409, detail="Only an unfinished kit can be refreshed")
        result = _refresh_open_kit(db, kit, actor)
        db.commit()
        return result
    except HTTPException:
        db.rollback()
        raise
    finally:
        db.close()


def _refresh_open_kit(db, kit, actor, against_setup_id=None):
    """Compares the kit again against its place in line (or a given setup) and re-reads the saved program.

    What was already scanned and still applies stays on the cart; what changed is released and the
    result says which tools go back to their rack. The caller commits.
    """
    draft = db.query(Setup).filter(Setup.id == kit.setup_nuevo_id).one()
    expected_id = against_setup_id or _expected_previous_setup_id(db, kit)
    old_setup = db.query(Setup).filter(Setup.id == expected_id).one()
    part = _part_from_name(draft.nombre_archivo).upper()
    profile = db.query(Maquina).filter(Maquina.codigo == kit.maquina_codigo).first()
    program = db.query(ProgramaParte).filter(
        ProgramaParte.tipo_maquina == (_machine_kind(profile.tipo_maquina) if profile else ""),
        ProgramaParte.numero_parte == part,
    ).first()
    stale = expected_id != kit.setup_anterior_id or _kit_baseline_outdated(db, kit, expected_id)
    if not program and not stale:
        raise HTTPException(status_code=409, detail=f"There is no saved program for {part} to refresh from")
    program_changed = bool(program) and (
        hashlib.sha256(program.contenido.encode("utf-8")).hexdigest() != draft.contenido_hash
    )
    if not program_changed and not stale:
        return {"kit": _kit_read(db, kit), "changed": False, "returned": [], "added": [], "removed": [], "kept": 0}

    parsed = _parse_and_validate(program.contenido, f"{program.numero_parte}.stp") if program_changed else None
    previous = {}
    for component, requirement in (
        db.query(ComponenteSurtido, RequisitoSetup)
        .join(RequisitoSetup, RequisitoSetup.id == ComponenteSurtido.requisito_id)
        .filter(ComponenteSurtido.surtido_id == kit.id)
        .all()
    ):
        if component.accion != "keep" and component.herramienta_nueva_id and component.estado == "ready":
            previous[(requirement.tn, requirement.angle, component.categoria)] = (
                component.herramienta_nueva_id, component.origen_codigo, requirement.station,
            )
    old_keys = {
        (row.tn, row.angle): row.station
        for row in db.query(RequisitoSetup).filter(RequisitoSetup.setup_id == draft.id).all()
    }

    db.query(ReservaHerramienta).filter(ReservaHerramienta.surtido_id == kit.id).delete()
    db.query(ComponenteSurtido).filter(ComponenteSurtido.surtido_id == kit.id).delete()
    if parsed is not None:
        draft.estado = "superseded"
        new_setup, new_requirements = _write_setup(db, parsed, kit.maquina_codigo, actor, "draft", program.contenido)
        kit.setup_nuevo_id = new_setup.id
    else:
        new_setup = draft
        rows = db.query(RequisitoSetup).filter(RequisitoSetup.setup_id == draft.id).all()
        for row in rows:
            row.punch_tool_id = row.die_tool_id = None
        new_requirements = {(row.tn, row.angle): row for row in rows}
    kit.setup_anterior_id = old_setup.id
    db.flush()
    _create_kit_components(db, kit, old_setup, new_setup, new_requirements, actor)
    db.flush()

    try:
        clearance_range = clearance_range_for_thickness(Decimal(new_setup.thickness))
    except (InvalidOperation, TypeError):
        clearance_range = None
    kept = 0
    applied = set()
    for component, requirement in (
        db.query(ComponenteSurtido, RequisitoSetup)
        .join(RequisitoSetup, RequisitoSetup.id == ComponenteSurtido.requisito_id)
        .filter(ComponenteSurtido.surtido_id == kit.id, ComponenteSurtido.accion != "keep")
        .all()
    ):
        key = (requirement.tn, requirement.angle, component.categoria)
        chosen = previous.get(key)
        if not chosen:
            continue
        tool = db.query(Herramienta).filter(Herramienta.id == chosen[0]).first()
        if (
            not tool or tool.ubicacion != kit.carro_codigo or not _tn_fits(db, requirement.tn, tool.tn)
            or (component.categoria == "matriz" and clearance_range and not _clearance_in_range(tool, clearance_range))
        ):
            continue
        component.herramienta_nueva_id = tool.id
        component.origen_codigo = chosen[1]
        component.estado = "ready"
        db.add(ReservaHerramienta(herramienta_id=tool.id, surtido_id=kit.id, componente_id=component.id))
        if component.categoria == "punzon":
            requirement.punch_tool_id = tool.id
        else:
            requirement.die_tool_id = tool.id
        applied.add(key)
        kept += 1

    returned = []
    for key, (tool_id, origin, station) in previous.items():
        if key in applied:
            continue
        tool = db.query(Herramienta).filter(Herramienta.id == tool_id).first()
        if tool and tool.ubicacion == kit.carro_codigo:
            destination = origin or _home_rack(db, tool.id)
            if destination:
                _append_movement(
                    db, tool, kit.carro_codigo, destination, "kit_refresh_returned", actor,
                    details="Kit refreshed: this position changed", kit_id=kit.id,
                )
            returned.append({
                "tool_id": tool.id, "to": destination, "station": station,
                "tn": key[0], "category": key[2],
            })
    new_keys = {(row.tn, row.angle): row.station for row in new_requirements.values()}
    added = [{"station": station, "tn": tn, "angle": angle} for (tn, angle), station in new_keys.items() if (tn, angle) not in old_keys]
    removed = [{"station": station, "tn": tn, "angle": angle} for (tn, angle), station in old_keys.items() if (tn, angle) not in new_keys]
    kit.estado = "paused" if kit.estado == "paused" else "preparing"
    kit.qr_token = None
    kit.actualizado_en = utcnow()
    db.flush()
    _try_mark_ready(db, kit)
    _log_machine_event(
        db, kit.maquina_codigo, "kit_refresh",
        f"{part}: kit compared again; {kept} kept, {len(returned)} returned to racks", actor,
    )
    result = _kit_read(db, kit)
    result["warnings"] = setup_warnings(db, kit.maquina_codigo, parsed) if parsed else []
    return {"kit": result, "changed": True, "returned": returned, "added": added, "removed": removed, "kept": kept}


def _kit_blockers(db, kit, components, strict=True):
    # Each position needs a punch and a die; kept tools must be on the machine. While the kit waits in
    # line (not strict), a kept tool may still be on the cart of the kit before it.
    blockers = []
    open_requests = db.query(SolicitudServicio).filter(
        SolicitudServicio.maquina_codigo == kit.maquina_codigo,
        SolicitudServicio.requisito_id.isnot(None),
        SolicitudServicio.estado.in_(("open", "attending", "collected")),
    ).order_by(SolicitudServicio.id).all()
    for request in open_requests:
        blockers.append(
            f"service request SR-{request.id:05d} (station {request.estacion}) is still open on "
            f"{kit.maquina_codigo}; close or cancel it before delivering"
        )
    by_requirement = {}
    for component in components:
        by_requirement.setdefault(component.requisito_id, {})[component.categoria] = component
    for requisito_id, parts in by_requirement.items():
        requirement = db.query(RequisitoSetup).filter(RequisitoSetup.id == requisito_id).first()
        label = f"{requirement.tn} (station {requirement.station})" if requirement else str(requisito_id)
        for category in COMPONENT_CATEGORIES:
            component = parts.get(category)
            name = "punch" if category == "punzon" else "die"
            if component is None:
                blockers.append(f"{label}: {name} is missing")
                continue
            tool_id = component.herramienta_nueva_id
            if component.estado != "ready" or not tool_id:
                blockers.append(f"{label}: {name} still to scan")
                continue
            if component.accion == "keep":
                tool = db.query(Herramienta).filter(Herramienta.id == tool_id).first()
                on_machine = bool(tool) and tool.ubicacion == kit.maquina_codigo and tool.estado == "disponible"
                waiting_in_line = (
                    not strict and bool(tool) and db.query(ReservaHerramienta.herramienta_id).join(
                        Surtido, Surtido.id == ReservaHerramienta.surtido_id
                    ).filter(
                        ReservaHerramienta.herramienta_id == tool_id,
                        ReservaHerramienta.surtido_id != kit.id,
                        Surtido.maquina_codigo == kit.maquina_codigo,
                        Surtido.estado.in_(OPEN_KIT_STATES),
                    ).first() is not None
                )
                if not (on_machine or waiting_in_line):
                    blockers.append(
                        f"{label}: {name} {tool_id} is no longer mounted on {kit.maquina_codigo}"
                    )
    return blockers


def _try_mark_ready(db, kit):
    """A finished kit goes to 'ready' by itself (with its delivery QR); delivery to the machine can wait."""
    if kit.estado != "preparing":
        return False
    components = db.query(ComponenteSurtido).filter(ComponenteSurtido.surtido_id == kit.id).all()
    if not components or any(
        component.estado != "ready" or (component.accion != "keep" and not component.herramienta_nueva_id)
        for component in components
    ):
        return False
    if _kit_blockers(db, kit, components, strict=False):
        return False
    if not kit.qr_token:
        kit.qr_token = secrets.token_urlsafe(32)
    kit.estado = "ready"
    kit.actualizado_en = utcnow()
    return True


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
        blockers = _kit_blockers(db, kit, components, strict=False)
        if missing or blockers:
            raise HTTPException(
                status_code=409,
                detail="Kit incomplete: " + "; ".join(blockers or [f"components {missing}"]),
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
        scanned_machine = scan_codes.machine_code(item.machine_scan)
        if not scanned_machine:
            raise HTTPException(status_code=422, detail="Scan the QR on the machine where the kit is delivered first")
        if scanned_machine != kit.maquina_codigo:
            raise HTTPException(
                status_code=409,
                detail=f"This kit is for machine {kit.maquina_codigo}, but {scanned_machine} was scanned",
            )
        mounted_state = db.query(EstadoMaquina).filter(
            EstadoMaquina.maquina_codigo == kit.maquina_codigo
        ).first()
        queue = _open_kits(db, kit.maquina_codigo)
        head = queue[0] if queue else kit
        if kit.id != head.id or kit.setup_anterior_id != (mounted_state.setup_activo_id if mounted_state else None):
            reason = (item.motivo_orden or "").strip()
            if not (item.fuera_de_orden and reason):
                if kit.id != head.id:
                    head_setup = db.query(Setup).filter(Setup.id == head.setup_nuevo_id).first()
                    head_part = _part_from_name(head_setup.nombre_archivo).upper() if head_setup else head.id
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            f"OUT_OF_ORDER: deliver {head_part} (cart {head.carro_codigo}) first, it is next in line "
                            f"on {kit.maquina_codigo}."
                        ),
                    )
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"OUT_OF_ORDER: this kit was compared by hand against a part that is not what is mounted on {kit.maquina_codigo}. "
                        "Deliver it with a reason to compare it again against what is mounted."
                        if kit.anterior_manual else
                        f"OUT_OF_ORDER: what is mounted on {kit.maquina_codigo} changed after this kit was compared. "
                        "Refresh the kit, or deliver it anyway with a reason."
                    ),
                )
            # Exceptional case chosen by Tool Crib: compare again against what is really mounted.
            outcome = _refresh_open_kit(
                db, kit, item.realizado_por, against_setup_id=mounted_state.setup_activo_id if mounted_state else None
            )
            _log_machine_event(
                db, kit.maquina_codigo, "delivery_out_of_order",
                f"{_part_from_name(db.query(Setup).filter(Setup.id == kit.setup_nuevo_id).one().nombre_archivo)} "
                f"delivered out of line. Reason: {reason}", item.realizado_por,
            )
            db.commit()
            if kit.estado != "ready":
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"The kit was compared again with what is mounted and now needs "
                        f"{sum(c['state'] == 'missing' for c in outcome['kit']['components'])} more tool(s); "
                        "it went back to in progress."
                    ),
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
        old_requirements = db.query(RequisitoSetup).filter(
            RequisitoSetup.setup_id == old_setup.id
        ).all()
        new_for_old = {old.id: new for old, new in _pairs(db, old_requirements, new_requirements)[0]}
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
            new_requirement = new_for_old.get(old_requirement.id)
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
                    if new_component.accion != "keep":
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
        delivered_part = _part_from_name(new_setup.nombre_archivo).upper()
        for part_request in db.query(SolicitudServicio).filter(
            SolicitudServicio.maquina_codigo == kit.maquina_codigo,
            SolicitudServicio.tipo == "part_request",
            SolicitudServicio.numero_parte == delivered_part,
            SolicitudServicio.estado.in_(("open", "attending")),
        ).all():
            part_request.estado = "closed"
            part_request.resolucion = "Kit delivered to the machine"
            part_request.cerrado_por = part_request.atendido_por or part_request.solicitado_por
            part_request.cerrado_en = now
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
        _require_no_open_ticket(db, tool)
        destination = _location(
            db,
            scan_codes.location_code(item.destino_codigo),
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
        destination_code = scan_codes.location_code(item.destino_codigo)
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
                "capture": row.captura,
            }
            for row in rows
        ]
    finally:
        db.close()
