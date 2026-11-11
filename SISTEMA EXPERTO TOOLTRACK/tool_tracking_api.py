import json
from decimal import Decimal
from types import SimpleNamespace
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import scan_codes
import workflow_api
from models.inventory import (
    EstadoMaquina,
    EstacionMaquina,
    FamiliaParte,
    Herramienta,
    Maquina,
    MovimientoHerramienta,
    PendienteRecoger,
    ProgramaParte,
    RequisitoSetup,
    Setup,
    Surtido,
    Ubicacion,
)

router = APIRouter(prefix="/workflow", tags=["tool tracking"])

RETURN_REASONS = {
    "regreso": "machine_return",
    "afilado": "machine_return_sharpening",
    "dano": "machine_return_damage",
}
RETURN_DESTINATION_TYPES = {"rack", "togu", "maintenance", "workshop"}
OPEN_KIT_STATES = ("preparing", "paused", "ready")
LOCATION_TYPE_LABELS = {
    "rack": "Rack",
    "cart": "Cart",
    "machine": "Machine",
    "togu": "TOGU",
    "maintenance": "Maintenance",
    "workshop": "Workshop",
}


class MachineReturn(BaseModel):
    tool_ids: List[str] = []
    todas: bool = False
    motivo: str = "regreso"
    destino_codigo: str
    realizado_por: str
    nota: Optional[str] = None


@router.get("/manufacturing/machines")
def list_manufacturing_machines():
    db = workflow_api.SessionLocal()
    try:
        locations = db.query(Ubicacion).filter(
            Ubicacion.tipo == "machine",
            Ubicacion.activa.is_(True),
        ).order_by(Ubicacion.codigo).all()
        profiles = {
            row.codigo: row
            for row in db.query(Maquina).filter(
                Maquina.codigo.in_([location.codigo for location in locations])
            ).all()
        } if locations else {}
        return [
            {
                "codigo": location.codigo,
                "nombre": location.nombre,
                "activa": bool(location.activa),
                "tipo_maquina": profiles[location.codigo].tipo_maquina if location.codigo in profiles else None,
                "edificio": profiles[location.codigo].edificio if location.codigo in profiles else None,
                "linea": profiles[location.codigo].linea if location.codigo in profiles else None,
            }
            for location in locations
        ]
    finally:
        db.close()


def _active_setup(db, machine_code):
    state = db.query(EstadoMaquina).filter(
        EstadoMaquina.maquina_codigo == machine_code
    ).first()
    if not state or not state.setup_activo_id:
        return None
    return db.query(Setup).filter(Setup.id == state.setup_activo_id).one()


def _mounted_rows(db, setup):
    rows = []
    requirements = db.query(RequisitoSetup).filter(
        RequisitoSetup.setup_id == setup.id
    ).order_by(RequisitoSetup.station).all()
    for requirement in requirements:
        for category, tool_id in (
            ("punzon", requirement.punch_tool_id),
            ("matriz", requirement.die_tool_id),
        ):
            if not tool_id:
                continue
            tool = db.query(Herramienta).filter(Herramienta.id == tool_id).first()
            if tool:
                rows.append((requirement, category, tool))
    return rows


def setup_program(db, code, setup):
    """Machine profile, normalized machine type, part number and stored program of the active setup."""
    profile = db.query(Maquina).filter(Maquina.codigo == code).first()
    machine_type = workflow_api._machine_kind(profile.tipo_maquina) if profile else ""
    part_number = workflow_api._part_from_name(setup.nombre_archivo).upper() if setup else None
    program = None
    if part_number and machine_type in {"4I", "2I", "45STA"}:
        program = db.query(ProgramaParte).filter(
            ProgramaParte.numero_parte == part_number,
            ProgramaParte.tipo_maquina == machine_type,
        ).first()
    return profile, machine_type, part_number, program


def program_requirements(program):
    """Station requirements of a stored program, shaped like RequisitoSetup rows without an assigned tool."""
    return [
        SimpleNamespace(
            id=None, station=row["station"], tn=row["tn"], angle=row.get("angle"),
            punch_tool_id=None, die_tool_id=None,
        )
        for row in json.loads(program.requisitos_json)
    ]


@router.get("/manufacturing/machines/{machine_code}")
def manufacturing_machine_workspace(machine_code: str):
    db = workflow_api.SessionLocal()
    try:
        code = machine_code.strip().upper()
        machine = workflow_api._location(db, code, {"machine"})
        if not machine.activa:
            raise HTTPException(status_code=404, detail=f"Machine {code} is inactive")

        setup = _active_setup(db, code)
        profile, machine_type, part_number, program = setup_program(db, code, setup)
        station_rows = []
        installed_components = 0
        total_components = 0
        import service_requests_api  # evita importación circular al cargar los routers
        machine_requests = service_requests_api.machine_requests(db, code)
        open_by_slot = {
            (request["requirement_id"], request["category"]): request
            for request in machine_requests
            if request["state"] in service_requests_api.POSITION_STATES and request["requirement_id"] is not None
        }
        clearance_range = (
            workflow_api.clearance_range_for_thickness(Decimal(setup.thickness))
            if setup else None
        )
        requirements_source = "none"
        if setup:
            requirements = db.query(RequisitoSetup).filter(
                RequisitoSetup.setup_id == setup.id
            ).all()
            requirements_source = "setup" if requirements else "none"
            if not requirements and program:
                # Sin requerimientos registrados: se muestran los del programa del número de parte.
                requirements = program_requirements(program)
                requirements_source = "program" if requirements else "none"
            requirements.sort(
                key=lambda row: (
                    not row.station.isdigit(),
                    int(row.station) if row.station.isdigit() else 0,
                    row.station,
                )
            )
            machine_stations = {
                row.numero: row
                for row in db.query(EstacionMaquina).filter(
                    EstacionMaquina.maquina_codigo == code
                ).all()
            }
            for requirement in requirements:
                configured_station = machine_stations.get(requirement.station)
                components = []
                for category, tool_id in (
                    ("punzon", requirement.punch_tool_id),
                    ("matriz", requirement.die_tool_id),
                ):
                    tool = db.query(Herramienta).filter(Herramienta.id == tool_id).first() if tool_id else None
                    clearance_valid = (
                        workflow_api._clearance_in_range(tool, clearance_range)
                        if tool and category == "matriz" else None
                    )
                    compatible = bool(
                        tool
                        and tool.tn == requirement.tn
                        and workflow_api._category(tool) == category
                        and (category != "matriz" or clearance_valid)
                    )
                    mounted = bool(tool and tool.ubicacion == code)
                    component_state = (
                        "missing" if not tool
                        else "installed" if mounted and compatible
                        else "incompatible" if mounted
                        else "elsewhere"
                    )
                    total_components += 1
                    installed_components += int(component_state == "installed")
                    components.append({
                        "category": category,
                        "tool_id": tool.id if tool else None,
                        "tn": tool.tn if tool else requirement.tn,
                        "tool_type": tool.type if tool else None,
                        "tool_size": tool.size if tool else None,
                        "location": tool.ubicacion if tool else None,
                        "tool_status": (tool.estado or "disponible") if tool else None,
                        "clearance": tool.clearance if tool else None,
                        "clearance_valid": clearance_valid,
                        "state": component_state,
                        "open_request": open_by_slot.get((requirement.id, category)),
                    })
                station_rows.append({
                    "requirement_id": requirement.id,
                    "station": requirement.station,
                    "tn": requirement.tn,
                    "angle": requirement.angle,
                    "machine_station": {
                        "size": configured_station.tamano,
                        "position": configured_station.posicion,
                        "autoindex": bool(configured_station.autoindex),
                        "state": configured_station.estado,
                        "note": configured_station.nota,
                    } if configured_station else None,
                    "components": components,
                })

        pending_pickups = (
            db.query(PendienteRecoger, Herramienta)
            .join(Herramienta, Herramienta.id == PendienteRecoger.herramienta_id)
            .filter(
                PendienteRecoger.maquina_codigo == code,
                PendienteRecoger.estado == "pending",
            )
            .order_by(PendienteRecoger.creado_en)
            .all()
        )
        active_kit = db.query(Surtido).filter(
            Surtido.maquina_codigo == code,
            Surtido.estado.in_(OPEN_KIT_STATES),
        ).order_by(Surtido.creado_en.desc()).first()
        family = db.query(FamiliaParte).filter(
            FamiliaParte.nombre == program.familia
        ).first() if program and program.familia else None
        family_client = family.cliente if family else None
        return {
            "machine": code,
            "machine_name": machine.nombre,
            "active": bool(machine.activa),
            "building": profile.edificio if profile else None,
            "line": profile.linea if profile else None,
            "machine_type": machine_type or None,
            "part_number": part_number,
            "setup": {
                "id": setup.id,
                "drawing_name": setup.drawing_name,
                "material": setup.material,
                "thickness": setup.thickness,
                "created_at": setup.creado_en.isoformat(),
            } if setup else None,
            "program": {
                "revision": program.revision,
                "client": family_client,
                "family": program.familia,
                "program_type": program.tipo_programa,
                "drawing_name": program.drawing_name,
                "material": program.material,
                "thickness": program.thickness,
                "file_name": program.nombre_archivo,
                "updated_by": program.actualizado_por,
                "updated_at": program.actualizado_en.isoformat(),
                "planning": {
                    "pieces_per_blank": program.piezas_blank,
                    "cycle_time_s": program.cycle_time_s,
                    "cycle_with_lead_s": program.cycle_con_lead_s,
                    "theoretical_uph": program.uph_teorico,
                },
                "requirements": json.loads(program.requisitos_json),
            } if program else None,
            "coverage": {
                "installed": installed_components,
                "required": total_components,
                "complete": total_components > 0 and installed_components == total_components,
            },
            "stations": station_rows,
            "requirements_source": requirements_source,
            "pending_pickups": [
                {
                    "tool_id": tool.id,
                    "tn": tool.tn,
                    "category": workflow_api._category(tool) or tool.categoria,
                    "created_at": pending.creado_en.isoformat(),
                }
                for pending, tool in pending_pickups
            ],
            "active_kit": {
                "id": active_kit.id,
                "state": active_kit.estado,
                "created_at": active_kit.creado_en.isoformat(),
                "updated_at": active_kit.actualizado_en.isoformat(),
            } if active_kit else None,
            "service_requests": machine_requests,
        }
    finally:
        db.close()


@router.get("/machines/{machine_code}/tools")
def list_machine_tools(machine_code: str):
    db = workflow_api.SessionLocal()
    try:
        code = machine_code.strip().upper()
        workflow_api._location(db, code, {"machine"})
        setup = _active_setup(db, code)
        if not setup:
            return {"machine": code, "drawing_name": None, "tools": []}
        return {
            "machine": code,
            "drawing_name": setup.drawing_name,
            "tools": [
                {
                    "tool_id": tool.id,
                    "tn": tool.tn,
                    "category": category,
                    "station": requirement.station,
                    "angle": requirement.angle,
                    "clearance": tool.clearance,
                    "on_machine": tool.ubicacion == code,
                }
                for requirement, category, tool in _mounted_rows(db, setup)
            ],
        }
    finally:
        db.close()


@router.post("/machines/{machine_code}/return")
def return_machine_tools(machine_code: str, item: MachineReturn):
    code = machine_code.strip().upper()
    reason = item.motivo.strip().lower()
    if reason not in RETURN_REASONS:
        raise HTTPException(status_code=422, detail="Invalid return reason")
    db = workflow_api.SessionLocal()
    try:
        workflow_api._location(db, code, {"machine"})
        destination = workflow_api._location(
            db, scan_codes.location_code(item.destino_codigo), RETURN_DESTINATION_TYPES
        )
        if db.query(Surtido.id).filter(
            Surtido.maquina_codigo == code,
            Surtido.estado.in_(OPEN_KIT_STATES),
        ).first():
            raise HTTPException(
                status_code=409,
                detail="The machine has an open kit; cancel or deliver it before recording returns.",
            )
        setup = _active_setup(db, code)
        if not setup:
            raise HTTPException(status_code=409, detail="The machine has no registered tools")
        mounted = _mounted_rows(db, setup)
        mounted_ids = {tool.id for _requirement, _category, tool in mounted}
        requested = mounted_ids if item.todas else {value.strip() for value in item.tool_ids if value.strip()}
        if not requested:
            raise HTTPException(status_code=422, detail="Select at least one tool")
        invalid = sorted(requested - mounted_ids)
        if invalid:
            raise HTTPException(
                status_code=409,
                detail=f"These tools are not mounted on {code}: {', '.join(invalid)}",
            )
        if destination.tipo == "rack":
            for _requirement, _category, tool in mounted:
                if tool.id in requested:
                    workflow_api._require_home_rack(db, tool, destination)
        if destination.tipo == "togu":
            if len(requested) > 1:
                raise HTTPException(status_code=409, detail="A TOGU can only hold one tool")
            if db.query(Herramienta.id).filter(Herramienta.ubicacion == destination.codigo).first():
                raise HTTPException(status_code=409, detail=f"TOGU '{destination.codigo}' is occupied")

        movement_type = RETURN_REASONS[reason]
        detail = f"Return from {code} ({setup.drawing_name or 'no part'}): {reason}"
        if item.nota and item.nota.strip():
            detail += f" - {item.nota.strip()}"
        moved = []
        for requirement, category, tool in mounted:
            if tool.id not in requested:
                continue
            workflow_api._append_movement(
                db, tool, code, destination.codigo, movement_type,
                item.realizado_por, details=detail, setup_id=setup.id,
            )
            workflow_api._status_on_move(tool, "machine", destination.tipo)
            if reason == "afilado":
                tool.estado = "afilado"
            elif reason == "dano":
                tool.estado = "danado"
            if category == "punzon":
                requirement.punch_tool_id = None
            else:
                requirement.die_tool_id = None
            moved.append(tool.id)
        db.commit()
        return {
            "machine": code,
            "destination": destination.codigo,
            "reason": reason,
            "returned": sorted(moved),
            "remaining": len(mounted_ids) - len(moved),
        }
    except HTTPException:
        db.rollback()
        raise
    finally:
        db.close()


class RackStore(BaseModel):
    tool_id: str
    realizado_por: str
    resultado: Optional[str] = None
    captura: Optional[str] = None


class ToolStatusChange(BaseModel):
    estado: str
    realizado_por: str
    nota: Optional[str] = None


@router.post("/tools/{tool_id}/status")
def change_tool_status(tool_id: str, item: ToolStatusChange):
    """Flag a tool as damaged, clear it after inspection, or retire it (baja)."""
    target = item.estado.strip().lower()
    if target not in {"disponible", "danado", "baja"}:
        raise HTTPException(status_code=422, detail="Invalid status (disponible, danado or baja)")
    db = workflow_api.SessionLocal()
    try:
        tool = scan_codes.find_tool(db, tool_id, lock=True)
        if not tool:
            raise HTTPException(status_code=404, detail="That tool is not in the inventory")
        current = tool.estado or "disponible"
        if current == "baja":
            raise HTTPException(status_code=409, detail=f"Tool {tool.id} is already retired")
        if current == target:
            raise HTTPException(status_code=409, detail=f"The tool is already '{target}'")
        workflow_api._require_no_open_ticket(db, tool)
        location = db.query(Ubicacion).filter(Ubicacion.codigo == tool.ubicacion).first()
        if not location or location.tipo not in {"rack", "togu", "maintenance", "workshop"}:
            raise HTTPException(
                status_code=409,
                detail=f"The tool is in {tool.ubicacion or 'an unknown location'}; first return it to a rack, TOGU or workshop.",
            )
        if target == "disponible" and location.tipo != "rack":
            raise HTTPException(
                status_code=409,
                detail="To make it available it must be in its rack (use «Store in rack» with the result).",
            )
        workflow_api._append_movement(
            db, tool, location.codigo, location.codigo, f"status_{target}",
            item.realizado_por,
            details=(item.nota or "").strip() or f"Status change: {current} -> {target}",
        )
        tool.estado = target
        db.commit()
        return {"tool_id": tool.id, "estado": tool.estado, "location": location.codigo}
    except HTTPException:
        db.rollback()
        raise
    finally:
        db.close()


@router.post("/racks/{rack_code}/store")
def store_tool_in_rack(rack_code: str, item: RackStore):
    """Scan rack, then scan tool: stores it only in the rack it belongs to."""
    db = workflow_api.SessionLocal()
    capture = scan_codes.capture_mode(item.captura)
    try:
        rack = workflow_api._location(db, scan_codes.location_code(rack_code), {"rack"})
        tool = scan_codes.find_tool(db, item.tool_id, lock=True)
        if not tool:
            raise HTTPException(status_code=404, detail=f"Tool '{item.tool_id}' is not in the inventory")
        workflow_api._require_home_rack(db, tool, rack)
        workflow_api._require_no_open_ticket(db, tool)
        origin = db.query(Ubicacion).filter(Ubicacion.codigo == tool.ubicacion).first()
        if not origin or origin.tipo not in {"machine", "togu", "maintenance", "workshop"}:
            raise HTTPException(
                status_code=409,
                detail=f"Tool {tool.id} is in {tool.ubicacion or 'an unknown location'}; there is nothing to store.",
            )
        if origin.tipo == "machine":
            pending = db.query(PendienteRecoger).filter(
                PendienteRecoger.herramienta_id == tool.id,
                PendienteRecoger.estado == "pending",
            ).with_for_update().first()
            if pending:
                workflow_api._append_movement(
                    db, tool, origin.codigo, rack.codigo, "pending_pickup_to_rack",
                    item.realizado_por, details=f"Picked up from {origin.codigo} and stored in its rack",
                    kit_id=pending.surtido_id, setup_id=pending.setup_origen_id, capture=capture,
                )
                pending.estado = "collected"
                pending.recogido_en = workflow_api.utcnow()
                kind = "pending_pickup"
            else:
                if db.query(Surtido.id).filter(
                    Surtido.maquina_codigo == origin.codigo,
                    Surtido.estado.in_(OPEN_KIT_STATES),
                ).first():
                    raise HTTPException(
                        status_code=409,
                        detail="The machine has an open kit; cancel or deliver it before recording returns.",
                    )
                setup = _active_setup(db, origin.codigo)
                mounted = [
                    (requirement, category)
                    for requirement, category, mounted_tool in (_mounted_rows(db, setup) if setup else [])
                    if mounted_tool.id == tool.id
                ]
                if not mounted:
                    raise HTTPException(
                        status_code=409,
                        detail=f"Tool {tool.id} is not mounted on {origin.codigo}",
                    )
                requirement, category = mounted[0]
                workflow_api._append_movement(
                    db, tool, origin.codigo, rack.codigo, "machine_return",
                    item.realizado_por,
                    details=f"Return from {origin.codigo} ({setup.drawing_name or 'no part'}): regreso",
                    setup_id=setup.id, capture=capture,
                )
                if category == "punzon":
                    requirement.punch_tool_id = None
                else:
                    requirement.die_tool_id = None
                kind = "machine_return"
        else:
            result_choice = (item.resultado or "").strip().lower()
            if origin.tipo in {"togu", "maintenance", "workshop"}:
                current = tool.estado or "disponible"
                if current == "baja":
                    raise HTTPException(status_code=409, detail=f"Tool {tool.id} is retired; it is not stored in a rack.")
                if current != "disponible":
                    if result_choice not in {"disponible", "baja"}:
                        raise HTTPException(
                            status_code=409,
                            detail=(
                                f"Tool {tool.id} comes from '{workflow_api.TOOL_STATUS_LABELS.get(current, current)}'. "
                                "Choose the result: ready for use or retired."
                            ),
                        )
                    if result_choice == "baja":
                        workflow_api._append_movement(
                            db, tool, origin.codigo, origin.codigo, "status_baja",
                            item.realizado_por, details=f"Retired from {current}", capture=capture,
                        )
                        tool.estado = "baja"
                        db.commit()
                        return {
                            "tool_id": tool.id, "tn": tool.tn, "from": origin.codigo,
                            "to": origin.codigo, "kind": "baja",
                        }
                    tool.estado = "disponible"
            workflow_api._append_movement(
                db, tool, origin.codigo, rack.codigo, f"{origin.tipo}_to_rack",
                item.realizado_por, details="Stored in its rack", capture=capture,
            )
            kind = f"{origin.tipo}_to_rack"
        db.commit()
        return {
            "tool_id": tool.id,
            "tn": tool.tn,
            "from": origin.codigo,
            "to": rack.codigo,
            "kind": kind,
        }
    except HTTPException:
        db.rollback()
        raise
    finally:
        db.close()


def _describe_tool(db, tool, location_types, active_setups, pending_by_tool, open_kits):
    code = tool.ubicacion
    kind = location_types.get(code)
    place = f"{LOCATION_TYPE_LABELS.get(kind, 'Location')} {code}" if code else "No location"
    status = place
    drawing = None
    station = None
    angle = None
    is_pending = False
    if kind == "machine":
        pending = pending_by_tool.get(tool.id)
        if pending:
            is_pending = True
            status = f"Waiting to be picked up at {code}"
            drawing = pending[1]
        else:
            setup = active_setups.get(code)
            if setup:
                for requirement in db.query(RequisitoSetup).filter(
                    RequisitoSetup.setup_id == setup.id
                ).all():
                    if tool.id in (requirement.punch_tool_id, requirement.die_tool_id):
                        drawing = setup.drawing_name
                        station = requirement.station
                        angle = requirement.angle
                        break
            status = f"On machine {code}"
    elif kind == "cart":
        kit = open_kits.get(code)
        if kit:
            status = f"On cart {code} (kit for {kit.maquina_codigo})"
    elif kind in {"togu", "maintenance", "workshop"}:
        status = place
    last = db.query(MovimientoHerramienta).filter(
        MovimientoHerramienta.herramienta_id == tool.id
    ).order_by(MovimientoHerramienta.id.desc()).first()
    estado = tool.estado or "disponible"
    estado_label = workflow_api.TOOL_STATUS_LABELS.get(estado, estado)
    if estado == "disponible":
        if kind == "machine":
            estado_label = "In use (machine)"
        elif kind == "cart":
            estado_label = "In a kit (cart)"
    return {
        "tool_id": tool.id,
        "tn": tool.tn,
        "category": workflow_api._category(tool) or tool.categoria,
        "type": tool.type,
        "size": tool.size,
        "clearance": tool.clearance,
        "location": code,
        "location_type": kind,
        "status": status,
        "pending_pickup": is_pending,
        "drawing_name": drawing,
        "station": station,
        "angle": angle,
        "last_movement_at": last.creado_en.isoformat() if last else None,
        "home_rack": workflow_api._home_rack(db, tool.id),
        "estado": estado,
        "estado_label": estado_label,
    }


def _location_context(db):
    """Lookups `_describe_tool` needs, built once per request."""
    location_types = {code: kind for code, kind in db.query(Ubicacion.codigo, Ubicacion.tipo).all()}
    active_setups = {}
    for state in db.query(EstadoMaquina).all():
        if state.setup_activo_id:
            active_setups[state.maquina_codigo] = db.query(Setup).filter(
                Setup.id == state.setup_activo_id
            ).one()
    pending_by_tool = {}
    for pending, setup in db.query(PendienteRecoger, Setup).join(
        Setup, Setup.id == PendienteRecoger.setup_origen_id
    ).filter(PendienteRecoger.estado == "pending").all():
        pending_by_tool[pending.herramienta_id] = (pending.maquina_codigo, setup.drawing_name)
    open_kits = {
        kit.carro_codigo: kit
        for kit in db.query(Surtido).filter(Surtido.estado.in_(OPEN_KIT_STATES)).all()
    }
    return location_types, active_setups, pending_by_tool, open_kits


@router.get("/tools/locate")
def locate_tools(q: Optional[str] = None, limit: int = 300):
    db = workflow_api.SessionLocal()
    try:
        query = db.query(Herramienta)
        text = (q or "").strip()
        if text:
            like = f"%{text}%"
            query = query.filter(
                (Herramienta.id == text) | Herramienta.tn.ilike(like) | Herramienta.id.ilike(like)
            )
        tools = query.order_by(Herramienta.tn, Herramienta.id).limit(max(1, min(limit, 500))).all()
        context = _location_context(db)
        return [_describe_tool(db, tool, *context) for tool in tools]
    finally:
        db.close()
