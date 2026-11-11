"""Service requests: Manufacturing asks Tool Crib for an action on a tool mounted on a machine.

Flow: open -> attending (Tool Crib took it) -> collected (old tool recovered)
      -> closed (Manufacturing scanned the incoming tool and confirmed the installation).
"""
from decimal import Decimal, InvalidOperation
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import auth
import scan_codes
import tool_tracking_api
import workflow_api
from models.inventory import (
    EstadoMaquina,
    Herramienta,
    PendienteRecoger,
    RequisitoSetup,
    ReservaHerramienta,
    ServicioHerramienta,
    SolicitudServicio,
    Surtido,
    Ubicacion,
    Usuario,
)

router = APIRouter(prefix="/workflow", tags=["service requests"])

SERVICE_TYPES = ("sharpening", "inspection", "replacement", "damage", "performance", "return", "tool_request")
PROGRAM_PROBLEMS = ("wrong_tool", "wrong_station_or_angle", "part_out_of_spec", "program_does_not_run", "other")
NOTE_REQUIRED = {"damage", "performance"}
# Active requests; "replaced" means the machine already has its tool and only the old tool is still in service.
OPEN_STATES = ("open", "attending", "collected", "replaced")
# Requests that leave the machine position without a tool.
POSITION_STATES = ("open", "attending", "collected")
COLLECT_DESTINATIONS = {"rack", "togu", "maintenance", "workshop"}
COLLECT_TOOL_STATUS = {"sharpening": "afilado", "damage": "danado"}
INSTALL_SOURCES = {"rack", "cart", "togu"}
SERVICE_LOCATIONS = {"togu", "maintenance", "workshop"}
CAN_ATTEND_FOR_OTHERS = {"TOOLCRIB_SUPERVISOR", "SYSTEM_ADMIN"}
MAX_NOTE_LENGTH = 500
# A sharpening that removes more than this (inches) is abnormal and needs a justification.
ABNORMAL_REBAJE = Decimal("0.020")
MIN_JUSTIFICATION_LENGTH = 10


class ServiceRequestCreate(BaseModel):
    machine: str
    category: str
    tipo: str
    requirement_id: Optional[int] = None
    station: Optional[str] = None
    tn: Optional[str] = None
    angle: Optional[str] = None
    tool_id: Optional[str] = None
    nota: Optional[str] = None


class MountedRegister(BaseModel):
    category: str
    tool_id: str
    requirement_id: Optional[int] = None
    station: Optional[str] = None
    tn: Optional[str] = None
    angle: Optional[str] = None
    captura: Optional[str] = None


class ServiceCollect(BaseModel):
    destino_codigo: str
    nota: Optional[str] = None


class ServiceInstall(BaseModel):
    tool_id: str
    captura: Optional[str] = None


class ServiceCancel(BaseModel):
    motivo: str


class ProgramProblemCreate(BaseModel):
    machine: str
    problema: str
    station: Optional[str] = None
    nota: Optional[str] = None


class ServiceResolve(BaseModel):
    resolucion: str


class ServiceFinish(BaseModel):
    resultado: str = "ready"
    rebaje: Optional[float] = None
    justificacion: Optional[str] = None
    nota: Optional[str] = None


class ServiceStore(BaseModel):
    rack_code: str
    tool_id: str
    captura: Optional[str] = None


def _user():
    user = auth.current_user()
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user


def _folio(row):
    return f"SR-{row.id:05d}"


def _slot(requirement, category):
    return requirement.punch_tool_id if category == "punzon" else requirement.die_tool_id


def _set_slot(requirement, category, tool_id):
    if category == "punzon":
        requirement.punch_tool_id = tool_id
    else:
        requirement.die_tool_id = tool_id


def _iso(value):
    return value.isoformat() if value else None


def _people(db, rows):
    usernames = {
        name
        for row in rows
        for name in (row.solicitado_por, row.atendido_por, row.recogido_por, row.cerrado_por)
        if name
    }
    if not usernames:
        return {}
    return {
        user.username: user
        for user in db.query(Usuario).filter(Usuario.username.in_(usernames)).all()
    }


def _read_many(db, rows):
    people = _people(db, rows)
    request_ids = [row.id for row in rows]
    services = {}
    if request_ids:
        for event in db.query(ServicioHerramienta).filter(
            ServicioHerramienta.solicitud_id.in_(request_ids)
        ).order_by(ServicioHerramienta.id).all():
            services[event.solicitud_id] = event
    tool_ids = {row.herramienta_id for row in rows if row.herramienta_id}
    tools = {
        tool.id: tool
        for tool in db.query(Herramienta).filter(Herramienta.id.in_(tool_ids)).all()
    } if tool_ids else {}

    def name(username):
        person = people.get(username)
        return (person.full_name or username) if person else username

    result = []
    for row in rows:
        requester = people.get(row.solicitado_por)
        service = services.get(row.id)
        old_tool = tools.get(row.herramienta_id)
        result.append({
            "id": row.id,
            "folio": _folio(row),
            "machine": row.maquina_codigo,
            "part_number": row.numero_parte,
            "requirement_id": row.requisito_id,
            "station": row.estacion,
            "category": row.categoria,
            "tn": row.tn,
            "type": row.tipo,
            "state": row.estado,
            "problem": row.problema,
            "resolution": row.resolucion,
            "note": row.nota,
            "tool_id": row.herramienta_id,
            "tool_status": (old_tool.estado or "disponible") if old_tool else None,
            "tool_location": old_tool.ubicacion if old_tool else None,
            "service": {
                "result": service.resultado,
                "rebaje": service.rebaje,
                "abnormal": bool(service.anormal),
                "justification": service.justificacion,
                "note": service.nota,
                "done_by": service.realizado_por,
                "done_at": _iso(service.fecha),
            } if service else None,
            "replacement_tool_id": row.herramienta_nueva_id,
            "destination": row.destino_codigo,
            "requested_by": row.solicitado_por,
            "requested_by_name": name(row.solicitado_por),
            "requested_by_shift": requester.shift if requester else None,
            "requested_at": _iso(row.solicitado_en),
            "attended_by": row.atendido_por,
            "attended_by_name": name(row.atendido_por) if row.atendido_por else None,
            "attended_at": _iso(row.atendido_en),
            "collected_by": row.recogido_por,
            "collected_at": _iso(row.recogido_en),
            "closed_by": row.cerrado_por,
            "closed_at": _iso(row.cerrado_en),
            "cancel_reason": row.motivo_cancelacion,
        })
    return result


def machine_requests(db, machine_code, recent_closed=10):
    """Open requests of a machine plus its latest closed ones (used by the Manufacturing workspace)."""
    active = db.query(SolicitudServicio).filter(
        SolicitudServicio.maquina_codigo == machine_code,
        SolicitudServicio.estado.in_(OPEN_STATES),
    ).order_by(SolicitudServicio.solicitado_en).all()
    closed = db.query(SolicitudServicio).filter(
        SolicitudServicio.maquina_codigo == machine_code,
        ~SolicitudServicio.estado.in_(OPEN_STATES),
    ).order_by(SolicitudServicio.id.desc()).limit(recent_closed).all()
    return _read_many(db, active + closed)


def _get(db, request_id):
    row = db.query(SolicitudServicio).filter(
        SolicitudServicio.id == request_id
    ).with_for_update().first()
    if not row:
        raise HTTPException(status_code=404, detail="Service request not found")
    return row


def _active_setup_matches(db, row):
    setup = tool_tracking_api._active_setup(db, row.maquina_codigo)
    return setup if setup and setup.id == row.setup_id else None


def _open_kit(db, code):
    return db.query(Surtido.id).filter(
        Surtido.maquina_codigo == code,
        Surtido.estado.in_(tool_tracking_api.OPEN_KIT_STATES),
    ).first()


def _position(db, code, setup, item):
    """Requirement row of a position. A setup registered without requirements takes them from its part program."""
    if not db.query(RequisitoSetup.id).filter(RequisitoSetup.setup_id == setup.id).first():
        program = tool_tracking_api.setup_program(db, code, setup)[3]
        if program:
            for row in tool_tracking_api.program_requirements(program):
                db.add(RequisitoSetup(setup_id=setup.id, station=row.station, tn=row.tn, angle=row.angle))
            db.flush()
    query = db.query(RequisitoSetup).filter(RequisitoSetup.setup_id == setup.id)
    if item.requirement_id is not None:
        query = query.filter(RequisitoSetup.id == item.requirement_id)
    elif item.station and item.tn:
        angle = (item.angle or "").strip() or None
        query = query.filter(
            RequisitoSetup.station == item.station.strip(),
            RequisitoSetup.tn == item.tn.strip(),
            RequisitoSetup.angle.is_(None) if angle is None else RequisitoSetup.angle == angle,
        )
    else:
        raise HTTPException(status_code=422, detail="Choose the station position")
    requirement = query.with_for_update().first()
    if not requirement:
        raise HTTPException(status_code=404, detail="That position is not part of the machine's active setup")
    return requirement


def _has_open_request(db, requirement, category):
    return db.query(SolicitudServicio.id).filter(
        SolicitudServicio.requisito_id == requirement.id,
        SolicitudServicio.categoria == category,
        SolicitudServicio.estado.in_(POSITION_STATES),
    ).first() is not None


@router.post("/service-requests", status_code=201)
def create_service_request(item: ServiceRequestCreate):
    user = _user()
    tipo = item.tipo.strip().lower()
    if tipo not in SERVICE_TYPES:
        raise HTTPException(status_code=422, detail="Invalid request type")
    category = workflow_api.CATEGORY_ALIASES.get(item.category.strip().lower())
    if not category:
        raise HTTPException(status_code=422, detail="Invalid tool category")
    note = (item.nota or "").strip()
    if len(note) > MAX_NOTE_LENGTH:
        raise HTTPException(status_code=422, detail=f"The note is limited to {MAX_NOTE_LENGTH} characters")
    if tipo in NOTE_REQUIRED and not note:
        raise HTTPException(status_code=422, detail="Describe what happened so Tool Crib knows what to expect")

    db = workflow_api.SessionLocal()
    try:
        code = item.machine.strip().upper()
        workflow_api._location(db, code, {"machine"})
        setup = tool_tracking_api._active_setup(db, code)
        if not setup:
            raise HTTPException(status_code=409, detail="The machine has no registered setup")
        requirement = _position(db, code, setup, item)
        if _has_open_request(db, requirement, category):
            raise HTTPException(status_code=409, detail="This position already has an open request")

        current = _slot(requirement, category)
        tool = None
        if tipo == "tool_request":
            if current:
                raise HTTPException(
                    status_code=409,
                    detail="This position already has a tool; request an action on that tool instead.",
                )
        else:
            if not current:
                raise HTTPException(
                    status_code=409,
                    detail="This position has no tool; use «Request tool» to ask for one.",
                )
            if item.tool_id and scan_codes.tool_code(item.tool_id).upper() != current.upper():
                raise HTTPException(
                    status_code=409,
                    detail="The tool in this position changed. Refresh the page and try again.",
                )
            tool = db.query(Herramienta).filter(Herramienta.id == current).with_for_update().one()
            if tool.ubicacion != code:
                raise HTTPException(
                    status_code=409,
                    detail=f"Tool {tool.id} is recorded in {tool.ubicacion or 'an unknown location'}, not on {code}.",
                )

        row = SolicitudServicio(
            maquina_codigo=code,
            setup_id=setup.id,
            requisito_id=requirement.id,
            numero_parte=workflow_api._part_from_name(setup.nombre_archivo).upper() or None,
            estacion=requirement.station,
            categoria=category,
            tn=requirement.tn,
            tipo=tipo,
            estado="open",
            nota=note or None,
            herramienta_id=tool.id if tool else None,
            solicitado_por=user.username,
            solicitado_en=workflow_api.utcnow(),
        )
        db.add(row)
        db.flush()
        if tool:
            _set_slot(requirement, category, None)
            workflow_api._append_movement(
                db, tool, code, code, "service_requested", user.username,
                details=f"{_folio(row)}: {tipo} on station {requirement.station} ({category})",
                setup_id=setup.id,
            )
        db.commit()
        return _read_many(db, [row])[0]
    except HTTPException:
        db.rollback()
        raise
    finally:
        db.close()


@router.post("/service-requests/program-problem", status_code=201)
def create_program_problem(item: ProgramProblemCreate):
    """Problem with the part number program: reaches the same queue; Tool Crib or Engineering attends it."""
    user = _user()
    problem = item.problema.strip().lower()
    if problem not in PROGRAM_PROBLEMS:
        raise HTTPException(status_code=422, detail="Choose the program problem from the list")
    note = (item.nota or "").strip()
    if len(note) > MAX_NOTE_LENGTH:
        raise HTTPException(status_code=422, detail=f"The note is limited to {MAX_NOTE_LENGTH} characters")
    if problem == "other" and not note:
        raise HTTPException(status_code=422, detail="Describe the problem")
    db = workflow_api.SessionLocal()
    try:
        code = item.machine.strip().upper()
        workflow_api._location(db, code, {"machine"})
        setup = tool_tracking_api._active_setup(db, code)
        if not setup:
            raise HTTPException(status_code=409, detail="The machine has no registered setup")
        part_number = workflow_api._part_from_name(setup.nombre_archivo).upper() or None
        if db.query(SolicitudServicio.id).filter(
            SolicitudServicio.maquina_codigo == code,
            SolicitudServicio.tipo == "program_problem",
            SolicitudServicio.problema == problem,
            SolicitudServicio.numero_parte == part_number,
            SolicitudServicio.estado.in_(OPEN_STATES),
        ).first():
            raise HTTPException(status_code=409, detail="This problem is already reported and still open")
        row = SolicitudServicio(
            maquina_codigo=code,
            setup_id=setup.id,
            numero_parte=part_number,
            estacion=(item.station or "").strip() or None,
            tipo="program_problem",
            problema=problem,
            estado="open",
            nota=note or None,
            solicitado_por=user.username,
            solicitado_en=workflow_api.utcnow(),
        )
        db.add(row)
        db.commit()
        return _read_many(db, [row])[0]
    except HTTPException:
        db.rollback()
        raise
    finally:
        db.close()


@router.post("/service-requests/{request_id}/resolve")
def resolve_program_problem(request_id: int, item: ServiceResolve):
    user = _user()
    resolution = item.resolucion.strip()
    if len(resolution) < 3:
        raise HTTPException(status_code=422, detail="Write what was done to resolve it")
    if len(resolution) > MAX_NOTE_LENGTH:
        raise HTTPException(status_code=422, detail=f"The resolution is limited to {MAX_NOTE_LENGTH} characters")
    db = workflow_api.SessionLocal()
    try:
        row = _get(db, request_id)
        if row.tipo != "program_problem":
            raise HTTPException(status_code=409, detail="Only program problems are resolved this way")
        if row.estado != "attending":
            raise HTTPException(status_code=409, detail="Mark the request as attending before resolving it")
        _require_attendant(user, row)
        row.estado = "closed"
        row.resolucion = resolution
        row.cerrado_por = user.username
        row.cerrado_en = workflow_api.utcnow()
        db.commit()
        return _read_many(db, [row])[0]
    except HTTPException:
        db.rollback()
        raise
    finally:
        db.close()


@router.get("/service-requests")
def list_service_requests(state: str = "active", machine: Optional[str] = None, limit: int = 100):
    if state not in {"active", "closed", "all"}:
        raise HTTPException(status_code=422, detail="state must be active, closed or all")
    db = workflow_api.SessionLocal()
    try:
        query = db.query(SolicitudServicio)
        if machine:
            query = query.filter(SolicitudServicio.maquina_codigo == machine.strip().upper())
        if state == "active":
            query = query.filter(SolicitudServicio.estado.in_(OPEN_STATES)).order_by(SolicitudServicio.solicitado_en)
        else:
            if state == "closed":
                query = query.filter(~SolicitudServicio.estado.in_(OPEN_STATES))
            query = query.order_by(SolicitudServicio.id.desc())
        return _read_many(db, query.limit(max(1, min(limit, 500))).all())
    finally:
        db.close()


@router.post("/service-requests/{request_id}/attend")
def attend_service_request(request_id: int):
    user = _user()
    db = workflow_api.SessionLocal()
    try:
        row = _get(db, request_id)
        if row.tipo != "program_problem" and "tools.move" not in user.permissions:
            raise HTTPException(status_code=403, detail="Only Tool Crib attends tool requests")
        if row.estado == "attending":
            raise HTTPException(status_code=409, detail=f"{row.atendido_por} is already attending this request")
        if row.estado != "open":
            raise HTTPException(status_code=409, detail="This request is no longer open")
        row.estado = "attending"
        row.atendido_por = user.username
        row.atendido_en = workflow_api.utcnow()
        db.commit()
        return _read_many(db, [row])[0]
    except HTTPException:
        db.rollback()
        raise
    finally:
        db.close()


def _require_attendant(user, row):
    if user.username != row.atendido_por and user.role not in CAN_ATTEND_FOR_OTHERS:
        raise HTTPException(
            status_code=403,
            detail=f"{row.atendido_por} is attending this request; only that user or a supervisor can record it.",
        )


@router.post("/service-requests/{request_id}/collect")
def collect_service_tool(request_id: int, item: ServiceCollect):
    """Tool Crib recovered the old tool from the machine: record where it goes."""
    user = _user()
    db = workflow_api.SessionLocal()
    try:
        row = _get(db, request_id)
        if row.estado != "attending":
            raise HTTPException(status_code=409, detail="Mark the request as attending before recording the pickup")
        if row.tipo == "tool_request" or not row.herramienta_id:
            raise HTTPException(status_code=409, detail="This request has no tool to collect")
        _require_attendant(user, row)
        tool = db.query(Herramienta).filter(Herramienta.id == row.herramienta_id).with_for_update().one()
        if tool.ubicacion != row.maquina_codigo:
            raise HTTPException(
                status_code=409,
                detail=f"Tool {tool.id} is recorded in {tool.ubicacion or 'an unknown location'}, not on {row.maquina_codigo}.",
            )
        destination = workflow_api._location(db, scan_codes.location_code(item.destino_codigo), COLLECT_DESTINATIONS)
        workflow_api._require_home_rack(db, tool, destination)
        if destination.tipo == "togu" and db.query(Herramienta.id).filter(
            Herramienta.ubicacion == destination.codigo,
            Herramienta.id != tool.id,
        ).first():
            raise HTTPException(status_code=409, detail=f"TOGU '{destination.codigo}' is occupied")

        workflow_api._status_on_move(tool, "machine", destination.tipo)
        forced_status = COLLECT_TOOL_STATUS.get(row.tipo)
        if forced_status and (tool.estado or "disponible") != "baja":
            tool.estado = forced_status
        detail = f"{_folio(row)}: {row.tipo} from {row.maquina_codigo} station {row.estacion}"
        if item.nota and item.nota.strip():
            detail += f" - {item.nota.strip()}"
        now = workflow_api.utcnow()
        workflow_api._append_movement(
            db, tool, row.maquina_codigo, destination.codigo, f"service_collect_{row.tipo}",
            user.username, details=detail, setup_id=row.setup_id,
        )
        row.destino_codigo = destination.codigo
        row.recogido_por = user.username
        row.recogido_en = now
        if row.tipo == "return":
            row.estado = "closed"
            row.cerrado_por = user.username
            row.cerrado_en = now
        else:
            row.estado = "collected"
        db.commit()
        return _read_many(db, [row])[0]
    except HTTPException:
        db.rollback()
        raise
    finally:
        db.close()


def _candidate(db, setup, tn, category, tool_id, sources, machine_code=None, observed=False):
    """Tool that can be put on a position: right TN/type, usable, not committed elsewhere.

    `observed` is for tools already mounted before MIRA tracked them: an unregistered or empty
    previous location is accepted, but a tool known to be in service, a cart or a machine is not.
    """
    tool = scan_codes.find_tool(db, tool_id, lock=True)
    if not tool:
        raise HTTPException(status_code=404, detail=f"Tool '{tool_id}' is not in the inventory")
    if tool.tn != tn or workflow_api._category(tool) != category:
        raise HTTPException(
            status_code=409,
            detail=f"Tool {tool.id} does not match this position (needs TN {tn}, {category}).",
        )
    workflow_api._require_available(tool)
    location = db.query(Ubicacion).filter(
        Ubicacion.codigo == tool.ubicacion, Ubicacion.activa.is_(True)
    ).first() if tool.ubicacion else None
    if observed:
        known_elsewhere = location and location.codigo != machine_code and location.tipo != "rack"
        if known_elsewhere:
            raise HTTPException(
                status_code=409,
                detail=f"Tool {tool.id} is recorded in {location.codigo}, so it cannot be mounted on this machine.",
            )
    elif not location or location.tipo not in sources:
        raise HTTPException(
            status_code=409,
            detail=f"Tool {tool.id} is in {tool.ubicacion or 'an unknown location'}; it must come from Tool Crib.",
        )
    if category == "matriz":
        try:
            clearance_range = workflow_api.clearance_range_for_thickness(Decimal(setup.thickness))
        except (InvalidOperation, TypeError):
            clearance_range = None
        if clearance_range and not workflow_api._clearance_in_range(tool, clearance_range):
            raise HTTPException(status_code=409, detail="The die CLEARANCE is outside the allowed range")
    if db.query(ReservaHerramienta.herramienta_id).filter(ReservaHerramienta.herramienta_id == tool.id).first():
        raise HTTPException(status_code=409, detail="The tool is reserved by an open kit")
    if db.query(PendienteRecoger.id).filter(
        PendienteRecoger.herramienta_id == tool.id, PendienteRecoger.estado == "pending",
    ).first():
        raise HTTPException(status_code=409, detail="The tool has an unresolved machine pickup")
    if db.query(RequisitoSetup.id).join(
        EstadoMaquina, EstadoMaquina.setup_activo_id == RequisitoSetup.setup_id,
    ).filter(
        (RequisitoSetup.punch_tool_id == tool.id) | (RequisitoSetup.die_tool_id == tool.id)
    ).first():
        raise HTTPException(status_code=409, detail="The tool is assigned to an active machine setup")
    return tool, location


@router.post("/manufacturing/machines/{machine_code}/mounted", status_code=201)
def register_mounted_tool(machine_code: str, item: MountedRegister):
    """Manufacturing records which physical tool is already mounted on a position (one-time reconciliation)."""
    user = _user()
    category = workflow_api.CATEGORY_ALIASES.get(item.category.strip().lower())
    if not category:
        raise HTTPException(status_code=422, detail="Invalid tool category")
    tool_id = scan_codes.tool_code(item.tool_id)
    if not tool_id:
        raise HTTPException(status_code=422, detail="Scan or type the ID of the tool mounted on this position")
    db = workflow_api.SessionLocal()
    try:
        code = machine_code.strip().upper()
        workflow_api._location(db, code, {"machine"})
        if _open_kit(db, code):
            raise HTTPException(
                status_code=409,
                detail="The machine has an open kit; wait until it is delivered or cancelled before registering tools.",
            )
        setup = tool_tracking_api._active_setup(db, code)
        if not setup:
            raise HTTPException(status_code=409, detail="The machine has no registered setup")
        requirement = _position(db, code, setup, item)
        if _slot(requirement, category):
            raise HTTPException(status_code=409, detail="This position already has a tool recorded")
        if _has_open_request(db, requirement, category):
            raise HTTPException(status_code=409, detail="This position has an open request; receive the tool through it")
        tool, location = _candidate(
            db, setup, requirement.tn, category, tool_id, {"rack"}, machine_code=code, observed=True,
        )
        workflow_api._append_movement(
            db, tool, location.codigo if location else None, code, "machine_observed", user.username,
            details=f"Registered as mounted on station {requirement.station} ({category}); the earlier movement was not inferred.",
            setup_id=setup.id, capture=scan_codes.capture_mode(item.captura),
        )
        _set_slot(requirement, category, tool.id)
        db.commit()
        return {
            "machine": code,
            "station": requirement.station,
            "category": category,
            "tool_id": tool.id,
            "requirement_id": requirement.id,
        }
    except HTTPException:
        db.rollback()
        raise
    finally:
        db.close()


@router.post("/service-requests/{request_id}/install")
def install_service_tool(request_id: int, item: ServiceInstall):
    """Manufacturing scanned the tool Tool Crib brought and confirms it is installed."""
    user = _user()
    tool_id = scan_codes.tool_code(item.tool_id)
    if not tool_id:
        raise HTTPException(status_code=422, detail="Scan or type the ID of the tool you received")
    db = workflow_api.SessionLocal()
    try:
        row = _get(db, request_id)
        ready = row.estado == "collected" or (row.tipo == "tool_request" and row.estado == "attending")
        if not ready:
            raise HTTPException(
                status_code=409,
                detail="Tool Crib has not finished this request yet." if row.estado in OPEN_STATES
                else "This request is already closed",
            )
        setup = _active_setup_matches(db, row)
        if not setup:
            raise HTTPException(
                status_code=409,
                detail="The setup on this machine changed after the request was opened; ask Tool Crib to cancel it.",
            )
        requirement = db.query(RequisitoSetup).filter(
            RequisitoSetup.id == row.requisito_id
        ).with_for_update().one()
        if _slot(requirement, row.categoria):
            raise HTTPException(status_code=409, detail="This position already has a tool assigned")
        tool, location = _candidate(db, setup, row.tn, row.categoria, tool_id, INSTALL_SOURCES)
        workflow_api._append_movement(
            db, tool, location.codigo, row.maquina_codigo, "service_install", user.username,
            details=f"{_folio(row)}: installed on station {row.estacion} ({row.categoria})"
                    + (f", replaces {row.herramienta_id}" if row.herramienta_id else ""),
            setup_id=setup.id, capture=scan_codes.capture_mode(item.captura),
        )
        _set_slot(requirement, row.categoria, tool.id)
        row.herramienta_nueva_id = tool.id
        old_tool = db.query(Herramienta).filter(Herramienta.id == row.herramienta_id).first() if row.herramienta_id else None
        old_location = db.query(Ubicacion).filter(Ubicacion.codigo == old_tool.ubicacion).first() if old_tool else None
        old_tool_resolved = (
            old_tool is None
            or old_tool.id == tool.id
            or (old_tool.estado or "disponible") == "baja"
            or (old_location is not None and old_location.tipo == "rack")
        )
        if old_tool_resolved:
            row.estado = "closed"
            row.cerrado_por = user.username
            row.cerrado_en = workflow_api.utcnow()
        else:
            row.estado = "replaced"
        db.commit()
        result = _read_many(db, [row])[0]
        result["same_tool"] = row.herramienta_id == tool.id
        return result
    except HTTPException:
        db.rollback()
        raise
    finally:
        db.close()


def _require_toolcrib(user):
    if "tools.move" not in user.permissions:
        raise HTTPException(status_code=403, detail="Only Tool Crib records the work done on a tool")


def _service_tool(db, row):
    """The old tool of a request, locked, once Tool Crib has it in a service location."""
    if row.estado not in ("collected", "replaced") or not row.herramienta_id or row.tipo in ("tool_request", "return", "program_problem"):
        raise HTTPException(status_code=409, detail="This request has no tool in service")
    tool = db.query(Herramienta).filter(Herramienta.id == row.herramienta_id).with_for_update().one()
    location = db.query(Ubicacion).filter(Ubicacion.codigo == tool.ubicacion).first()
    if not location or location.tipo not in SERVICE_LOCATIONS:
        raise HTTPException(
            status_code=409,
            detail=f"Tool {tool.id} is in {tool.ubicacion or 'an unknown location'}, not in a service location.",
        )
    return tool, location


def _decimal(value, label):
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError):
        raise HTTPException(status_code=422, detail=f"{label} must be a number")
    if not number.is_finite():
        raise HTTPException(status_code=422, detail=f"{label} must be a number")
    return number


@router.post("/service-requests/{request_id}/finish-service")
def finish_tool_service(request_id: int, item: ServiceFinish):
    """Tool Crib records the result of the work on the old tool: how much was ground off, repair notes, ready or retired."""
    user = _user()
    _require_toolcrib(user)
    result_kind = item.resultado.strip().lower()
    if result_kind not in ("ready", "retired"):
        raise HTTPException(status_code=422, detail="Result must be ready or retired")
    note = (item.nota or "").strip()
    justification = (item.justificacion or "").strip()
    if len(note) > MAX_NOTE_LENGTH or len(justification) > MAX_NOTE_LENGTH:
        raise HTTPException(status_code=422, detail=f"Notes are limited to {MAX_NOTE_LENGTH} characters")

    db = workflow_api.SessionLocal()
    try:
        row = _get(db, request_id)
        tool, location = _service_tool(db, row)
        if db.query(ServicioHerramienta.id).filter(ServicioHerramienta.solicitud_id == row.id).first():
            raise HTTPException(status_code=409, detail="The result of this service is already recorded")

        rebaje = None
        if item.rebaje is not None:
            rebaje = _decimal(item.rebaje, "The amount ground off").quantize(Decimal("0.001"))
            if rebaje < 0:
                raise HTTPException(status_code=422, detail="The amount ground off cannot be negative")
        abnormal = False
        if result_kind == "ready":
            if row.tipo == "sharpening" and rebaje is None:
                raise HTTPException(status_code=422, detail="Record how much was ground off (use 0 if nothing was removed)")
            if row.tipo in ("damage", "performance") and not note:
                raise HTTPException(status_code=422, detail="Describe what was found and repaired")
        elif not note:
            raise HTTPException(status_code=422, detail="Explain why the tool is being retired")
        abnormal = rebaje is not None and rebaje > ABNORMAL_REBAJE
        if abnormal and len(justification) < MIN_JUSTIFICATION_LENGTH:
            raise HTTPException(
                status_code=422,
                detail=f"More than {ABNORMAL_REBAJE} in. was ground off, which is abnormal: explain what happened "
                       f"(at least {MIN_JUSTIFICATION_LENGTH} characters).",
            )

        life_before = tool.life
        life_after = None
        if rebaje is not None:
            try:
                current = Decimal(str(tool.life))
                if current.is_finite():
                    life_after = format(max(Decimal("0"), current - rebaje), ".3f")
                    tool.life = life_after
            except (InvalidOperation, TypeError):
                life_before = None
        now = workflow_api.utcnow()
        db.add(ServicioHerramienta(
            herramienta_id=tool.id, solicitud_id=row.id, tipo=row.tipo, resultado=result_kind,
            maquina_codigo=row.maquina_codigo, numero_parte=row.numero_parte, estacion=row.estacion,
            tn=row.tn, categoria=row.categoria, rebaje=float(rebaje) if rebaje is not None else None,
            life_antes=life_before if life_after is not None else None, life_despues=life_after,
            anormal=abnormal, justificacion=justification or None, nota=note or None,
            realizado_por=user.username, fecha=now,
        ))
        summary = f"{_folio(row)}: {row.tipo} finished"
        if rebaje is not None:
            summary += f", {rebaje} in. ground off"
        if note:
            summary += f" - {note}"
        if result_kind == "ready":
            tool.estado = "disponible"
            movement = "service_completed"
        else:
            tool.estado = "baja"
            movement = "service_retired"
            if row.estado == "replaced":
                row.estado = "closed"
                row.cerrado_por = user.username
                row.cerrado_en = now
        workflow_api._append_movement(
            db, tool, location.codigo, location.codigo, movement, user.username,
            details=summary, setup_id=row.setup_id,
        )
        db.commit()
        return _read_many(db, [row])[0]
    except HTTPException:
        db.rollback()
        raise
    finally:
        db.close()


@router.post("/service-requests/{request_id}/store")
def store_serviced_tool(request_id: int, item: ServiceStore):
    """The machine already got a replacement: the finished old tool goes back to its rack and the request closes."""
    user = _user()
    _require_toolcrib(user)
    db = workflow_api.SessionLocal()
    try:
        row = _get(db, request_id)
        if row.estado != "replaced":
            raise HTTPException(
                status_code=409,
                detail="Only a request whose machine already has a replacement stores the old tool in a rack.",
            )
        tool, _location = _service_tool(db, row)
        finished = db.query(ServicioHerramienta.id).filter(ServicioHerramienta.solicitud_id == row.id).first()
        if not finished or (tool.estado or "disponible") != "disponible":
            raise HTTPException(status_code=409, detail="Record the result of the service before storing the tool")
        scanned = scan_codes.tool_code(item.tool_id)
        if scanned.upper() != tool.id.upper():
            raise HTTPException(
                status_code=409,
                detail=f"That is not the tool of this request: scan {tool.id}, the one going to the rack.",
            )
        tool_id = tool.id
    finally:
        db.close()

    stored = tool_tracking_api.store_tool_in_rack(
        scan_codes.location_code(item.rack_code),
        tool_tracking_api.RackStore(tool_id=tool_id, realizado_por=user.username, captura=item.captura),
    )
    db = workflow_api.SessionLocal()
    try:
        row = _get(db, request_id)
        row.estado = "closed"
        row.cerrado_por = user.username
        row.cerrado_en = workflow_api.utcnow()
        db.commit()
        result = _read_many(db, [row])[0]
        result["stored"] = stored
        return result
    except HTTPException:
        db.rollback()
        raise
    finally:
        db.close()


@router.get("/tool-service-log")
def list_tool_service_log(tool_id: Optional[str] = None, limit: int = 200):
    """Service history per physical tool (sharpening, repairs, retirements) for Pareto analysis."""
    db = workflow_api.SessionLocal()
    try:
        query = db.query(ServicioHerramienta)
        if tool_id:
            query = query.filter(ServicioHerramienta.herramienta_id == scan_codes.tool_code(tool_id).upper())
        rows = query.order_by(ServicioHerramienta.id.desc()).limit(max(1, min(limit, 1000))).all()
        return [
            {
                "id": row.id,
                "tool_id": row.herramienta_id,
                "request": f"SR-{row.solicitud_id:05d}" if row.solicitud_id else None,
                "type": row.tipo,
                "result": row.resultado,
                "machine": row.maquina_codigo,
                "part_number": row.numero_parte,
                "station": row.estacion,
                "tn": row.tn,
                "category": row.categoria,
                "rebaje": row.rebaje,
                "life_before": row.life_antes,
                "life_after": row.life_despues,
                "abnormal": bool(row.anormal),
                "justification": row.justificacion,
                "note": row.nota,
                "done_by": row.realizado_por,
                "done_at": _iso(row.fecha),
            }
            for row in rows
        ]
    finally:
        db.close()


@router.post("/service-requests/{request_id}/cancel")
def cancel_service_request(request_id: int, item: ServiceCancel):
    user = _user()
    reason = item.motivo.strip()
    if len(reason) < 3:
        raise HTTPException(status_code=422, detail="Give a reason for cancelling")
    db = workflow_api.SessionLocal()
    try:
        row = _get(db, request_id)
        if row.estado not in OPEN_STATES:
            raise HTTPException(status_code=409, detail="This request is already closed")
        by_toolcrib = "service.attend" in user.permissions and (
            row.tipo == "program_problem" or "tools.move" in user.permissions
        )
        by_owner = (
            "service.request" in user.permissions
            and row.solicitado_por == user.username
            and row.estado == "open"
        )
        if not (by_toolcrib or by_owner):
            raise HTTPException(status_code=403, detail="You can only cancel your own request before Tool Crib takes it")

        restored = False
        if row.herramienta_id and row.estado in {"open", "attending"}:
            setup = _active_setup_matches(db, row)
            requirement = db.query(RequisitoSetup).filter(
                RequisitoSetup.id == row.requisito_id
            ).with_for_update().first()
            tool = db.query(Herramienta).filter(Herramienta.id == row.herramienta_id).with_for_update().first()
            if setup and requirement and tool and not _slot(requirement, row.categoria) \
                    and tool.ubicacion == row.maquina_codigo:
                _set_slot(requirement, row.categoria, tool.id)
                workflow_api._append_movement(
                    db, tool, row.maquina_codigo, row.maquina_codigo, "service_cancelled", user.username,
                    details=f"{_folio(row)} cancelled: {reason}", setup_id=setup.id,
                )
                restored = True
        row.estado = "cancelled"
        row.motivo_cancelacion = reason
        row.cerrado_por = user.username
        row.cerrado_en = workflow_api.utcnow()
        db.commit()
        result = _read_many(db, [row])[0]
        result["restored"] = restored
        return result
    except HTTPException:
        db.rollback()
        raise
    finally:
        db.close()
