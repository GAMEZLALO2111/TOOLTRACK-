from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import workflow_api
from models.inventory import (
    EstadoMaquina,
    Herramienta,
    MovimientoHerramienta,
    PendienteRecoger,
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
    "cart": "Carro",
    "machine": "Máquina",
    "togu": "TOGU",
    "maintenance": "Mantenimiento",
    "workshop": "Taller",
}


class MachineReturn(BaseModel):
    tool_ids: List[str] = []
    todas: bool = False
    motivo: str = "regreso"
    destino_codigo: str
    realizado_por: str
    nota: Optional[str] = None


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
        raise HTTPException(status_code=422, detail="Motivo de retorno no válido")
    db = workflow_api.SessionLocal()
    try:
        workflow_api._location(db, code, {"machine"})
        destination = workflow_api._location(
            db, item.destino_codigo.strip().upper(), RETURN_DESTINATION_TYPES
        )
        if db.query(Surtido.id).filter(
            Surtido.maquina_codigo == code,
            Surtido.estado.in_(OPEN_KIT_STATES),
        ).first():
            raise HTTPException(
                status_code=409,
                detail="La máquina tiene un surtido abierto; cancélalo o entrégalo antes de registrar retornos.",
            )
        setup = _active_setup(db, code)
        if not setup:
            raise HTTPException(status_code=409, detail="La máquina no tiene herramientas registradas")
        mounted = _mounted_rows(db, setup)
        mounted_ids = {tool.id for _requirement, _category, tool in mounted}
        requested = mounted_ids if item.todas else {value.strip() for value in item.tool_ids if value.strip()}
        if not requested:
            raise HTTPException(status_code=422, detail="Selecciona al menos una herramienta")
        invalid = sorted(requested - mounted_ids)
        if invalid:
            raise HTTPException(
                status_code=409,
                detail=f"Estas herramientas no están montadas en {code}: {', '.join(invalid)}",
            )
        if destination.tipo == "rack":
            for _requirement, _category, tool in mounted:
                if tool.id in requested:
                    workflow_api._require_home_rack(db, tool, destination)
        if destination.tipo == "togu":
            if len(requested) > 1:
                raise HTTPException(status_code=409, detail="Un TOGU solo puede recibir una herramienta")
            if db.query(Herramienta.id).filter(Herramienta.ubicacion == destination.codigo).first():
                raise HTTPException(status_code=409, detail=f"El TOGU '{destination.codigo}' está ocupado")

        movement_type = RETURN_REASONS[reason]
        detail = f"Retorno desde {code} ({setup.drawing_name or 'sin parte'}): {reason}"
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


class ToolStatusChange(BaseModel):
    estado: str
    realizado_por: str
    nota: Optional[str] = None


@router.post("/tools/{tool_id}/status")
def change_tool_status(tool_id: str, item: ToolStatusChange):
    """Flag a tool as damaged, clear it after inspection, or retire it (baja)."""
    target = item.estado.strip().lower()
    if target not in {"disponible", "danado", "baja"}:
        raise HTTPException(status_code=422, detail="Estado no válido (disponible, danado o baja)")
    db = workflow_api.SessionLocal()
    try:
        tool = db.query(Herramienta).filter(
            Herramienta.id == tool_id.strip()
        ).with_for_update().first()
        if not tool:
            raise HTTPException(status_code=404, detail="La herramienta no existe en inventario")
        current = tool.estado or "disponible"
        if current == "baja":
            raise HTTPException(status_code=409, detail=f"La herramienta {tool.id} ya está dada de baja")
        if current == target:
            raise HTTPException(status_code=409, detail=f"La herramienta ya está en estado '{target}'")
        location = db.query(Ubicacion).filter(Ubicacion.codigo == tool.ubicacion).first()
        if not location or location.tipo not in {"rack", "togu", "maintenance", "workshop"}:
            raise HTTPException(
                status_code=409,
                detail=f"La herramienta está en {tool.ubicacion or 'ubicación desconocida'}; primero regrésala a un rack, TOGU o taller.",
            )
        if target == "disponible" and location.tipo != "rack":
            raise HTTPException(
                status_code=409,
                detail="Para dejarla disponible debe estar en su rack (usa «Guardar en rack» con el resultado).",
            )
        workflow_api._append_movement(
            db, tool, location.codigo, location.codigo, f"status_{target}",
            item.realizado_por,
            details=(item.nota or "").strip() or f"Cambio de estado: {current} -> {target}",
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
    try:
        rack = workflow_api._location(db, rack_code.strip().upper(), {"rack"})
        tool = db.query(Herramienta).filter(
            Herramienta.id == item.tool_id.strip()
        ).with_for_update().first()
        if not tool:
            raise HTTPException(status_code=404, detail=f"La herramienta '{item.tool_id}' no existe en inventario")
        workflow_api._require_home_rack(db, tool, rack)
        origin = db.query(Ubicacion).filter(Ubicacion.codigo == tool.ubicacion).first()
        if not origin or origin.tipo not in {"machine", "togu", "maintenance", "workshop"}:
            raise HTTPException(
                status_code=409,
                detail=f"La herramienta {tool.id} está en {tool.ubicacion or 'ubicación desconocida'}; no hay nada que guardar.",
            )
        if origin.tipo == "machine":
            pending = db.query(PendienteRecoger).filter(
                PendienteRecoger.herramienta_id == tool.id,
                PendienteRecoger.estado == "pending",
            ).with_for_update().first()
            if pending:
                workflow_api._append_movement(
                    db, tool, origin.codigo, rack.codigo, "pending_pickup_to_rack",
                    item.realizado_por, details=f"Recogida de {origin.codigo} y guardada en su rack",
                    kit_id=pending.surtido_id, setup_id=pending.setup_origen_id,
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
                        detail="La máquina tiene un surtido abierto; cancélalo o entrégalo antes de registrar retornos.",
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
                        detail=f"La herramienta {tool.id} no está montada en {origin.codigo}",
                    )
                requirement, category = mounted[0]
                workflow_api._append_movement(
                    db, tool, origin.codigo, rack.codigo, "machine_return",
                    item.realizado_por,
                    details=f"Retorno desde {origin.codigo} ({setup.drawing_name or 'sin parte'}): regreso",
                    setup_id=setup.id,
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
                    raise HTTPException(status_code=409, detail=f"La herramienta {tool.id} está dada de baja; no se guarda en rack.")
                if current != "disponible":
                    if result_choice not in {"disponible", "baja"}:
                        raise HTTPException(
                            status_code=409,
                            detail=(
                                f"La herramienta {tool.id} viene de '{workflow_api.TOOL_STATUS_LABELS.get(current, current)}'. "
                                "Indica el resultado: lista para uso o baja."
                            ),
                        )
                    if result_choice == "baja":
                        workflow_api._append_movement(
                            db, tool, origin.codigo, origin.codigo, "status_baja",
                            item.realizado_por, details=f"Dada de baja desde {current}",
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
                item.realizado_por, details="Guardada en su rack",
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
    place = f"{LOCATION_TYPE_LABELS.get(kind, 'Ubicación')} {code}" if code else "Sin ubicación"
    status = place
    drawing = None
    station = None
    angle = None
    if kind == "machine":
        pending = pending_by_tool.get(tool.id)
        if pending:
            status = f"Pendiente de recoger en {code}"
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
            status = f"En máquina {code}"
    elif kind == "cart":
        kit = open_kits.get(code)
        if kit:
            status = f"En carro {code} (surtido para {kit.maquina_codigo})"
    elif kind in {"togu", "maintenance", "workshop"}:
        status = place
    last = db.query(MovimientoHerramienta).filter(
        MovimientoHerramienta.herramienta_id == tool.id
    ).order_by(MovimientoHerramienta.id.desc()).first()
    estado = tool.estado or "disponible"
    estado_label = workflow_api.TOOL_STATUS_LABELS.get(estado, estado)
    if estado == "disponible":
        if kind == "machine":
            estado_label = "En uso (máquina)"
        elif kind == "cart":
            estado_label = "En kit (carro)"
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
        "drawing_name": drawing,
        "station": station,
        "angle": angle,
        "last_movement_at": last.creado_en.isoformat() if last else None,
        "home_rack": workflow_api._home_rack(db, tool.id),
        "estado": estado,
        "estado_label": estado_label,
    }


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
        return [
            _describe_tool(db, tool, location_types, active_setups, pending_by_tool, open_kits)
            for tool in tools
        ]
    finally:
        db.close()
