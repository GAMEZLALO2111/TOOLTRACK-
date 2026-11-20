"""Service requests: Manufacturing asks Tool Crib for an action on a tool mounted on a machine.

Flow: open -> attending (Tool Crib took it) -> collected (old tool recovered)
      -> closed (Manufacturing scanned the incoming tool and confirmed the installation).
"""
from decimal import Decimal, InvalidOperation
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import func

import auth
import scan_codes
import shifts
import tool_tracking_api
import workflow_api
from tn_tolerance import is_round
from models.inventory import (
    BajaHerramienta,
    DemandaPlaneacion,
    EstadoMaquina,
    Herramienta,
    IncidenteHerramienta,
    PendienteRecoger,
    RequisitoSetup,
    ReservaHerramienta,
    ServicioHerramienta,
    SolicitudServicio,
    Surtido,
    TicketMantenimiento,
    Ubicacion,
    Usuario,
)

router = APIRouter(prefix="/workflow", tags=["service requests"])

SERVICE_TYPES = ("sharpening", "inspection", "replacement", "damage", "performance", "return", "tool_request")
PROGRAM_PROBLEMS = ("wrong_tool", "wrong_station_or_angle", "part_out_of_spec", "program_does_not_run", "other")
# Initial lists for damage analysis (Pareto); the technician can correct the cause when evaluating.
DAMAGE_CAUSES = (
    "collision", "normal_wear", "wrong_setup", "wrong_material",
    "handling", "lubrication", "supplier_defect", "unknown",
)
DAMAGE_MOMENTS = ("setup", "production", "inspection", "handling", "unknown")
OPERATOR_ROLES = ("PUNCHING_OPERATOR", "PUNCHING_TECHNICIAN")
NOTE_REQUIRED = {"damage", "performance"}
# Active requests; "replaced" means the machine already has its tool and only the old tool is still in service.
OPEN_STATES = ("open", "attending", "collected", "replaced")
# Requests that leave the machine position without a tool.
POSITION_STATES = ("open", "attending", "collected")
COLLECT_DESTINATIONS = {"rack", "togu", "maintenance", "workshop"}
COLLECT_TOOL_STATUS = {"sharpening": "afilado", "damage": "danado"}
INSTALL_SOURCES = {"rack", "cart", "togu", "maintenance"}
SERVICE_LOCATIONS = {"togu", "maintenance", "workshop"}
CAN_ATTEND_FOR_OTHERS = {"TOOLCRIB_SUPERVISOR", "SYSTEM_ADMIN"}
MAX_NOTE_LENGTH = 500
# A sharpening that removes more than this (inches) is abnormal and needs a justification.
ABNORMAL_REBAJE = Decimal("0.020")
MAX_REBAJE = Decimal("0.050")
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
    operador: Optional[str] = None
    causa: Optional[str] = None
    momento: Optional[str] = None
    cambio_piloto: bool = False


class IncidentRecord(BaseModel):
    operador: str
    causa: str
    momento: str
    hallazgo: str
    descripcion: Optional[str] = None


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


def _minutes(start, end):
    if not start or not end:
        return None
    return max(0, round((end - start).total_seconds() / 60))


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


def _incident_fields(db, operador, causa, momento, require_operator=True):
    """Validated who/why/when of an incident; the shift comes from the clock, not from the operator's profile."""
    cause = (causa or "").strip().lower()
    moment = (momento or "").strip().lower()
    shift = shifts.shift_for(workflow_api.utcnow())
    if cause not in DAMAGE_CAUSES:
        raise HTTPException(status_code=422, detail="Choose the cause of the damage from the list")
    if moment not in DAMAGE_MOMENTS:
        raise HTTPException(status_code=422, detail="Choose when it happened")
    username = (operador or "").strip()
    if not username and not require_operator:
        return {"operador": None, "turno": shift, "causa": cause, "momento": moment}
    person = db.query(Usuario).filter(
        func.lower(Usuario.username) == username.lower(), Usuario.activo.is_(True)
    ).first() if username else None
    if not person:
        raise HTTPException(status_code=422, detail="Choose the operator who was on the machine from the list")
    return {"operador": person.username, "turno": shift, "causa": cause, "momento": moment}


def _save_incident(db, row, tool, fields, actor, descripcion=None, hallazgo=None):
    """Creates the incident of a request, or updates it when it already exists."""
    now = workflow_api.utcnow()
    incident = db.query(IncidenteHerramienta).filter(IncidenteHerramienta.solicitud_id == row.id).first()
    if incident is None:
        incident = IncidenteHerramienta(
            herramienta_id=tool.id, solicitud_id=row.id, origen="machine", reportado_como=row.tipo,
            maquina_codigo=row.maquina_codigo, numero_parte=row.numero_parte, estacion=row.estacion,
            setup_id=row.setup_id, tn=row.tn, categoria=row.categoria, life=tool.life,
            creado_por=actor, creado_en=now,
        )
        db.add(incident)
    else:
        incident.actualizado_por = actor
        incident.actualizado_en = now
    incident.operador = fields["operador"]
    incident.turno = incident.turno or fields["turno"]
    incident.causa = fields["causa"]
    incident.momento = fields["momento"]
    if descripcion:
        incident.descripcion = descripcion
    if hallazgo:
        incident.hallazgo = hallazgo
    return incident


def _incident_read(incident):
    return {
        "id": incident.id,
        "tool_id": incident.herramienta_id,
        "request_id": incident.solicitud_id,
        "origin": incident.origen,
        "reported_as": incident.reportado_como,
        "machine": incident.maquina_codigo,
        "part_number": incident.numero_parte,
        "station": incident.estacion,
        "tn": incident.tn,
        "category": incident.categoria,
        "life": incident.life,
        "operator": incident.operador,
        "shift": incident.turno,
        "cause": incident.causa,
        "moment": incident.momento,
        "description": incident.descripcion,
        "finding": incident.hallazgo,
        "created_by": incident.creado_por,
        "created_at": _iso(incident.creado_en),
        "updated_by": incident.actualizado_por,
        "updated_at": _iso(incident.actualizado_en),
    }


def _read_many(db, rows):
    people = _people(db, rows)
    request_ids = [row.id for row in rows]
    services = {}
    incidents = {}
    tickets = {}
    retirements = {}
    if request_ids:
        for retirement in db.query(BajaHerramienta).filter(
            BajaHerramienta.solicitud_id.in_(request_ids)
        ).order_by(BajaHerramienta.id).all():
            retirements[retirement.solicitud_id] = retirement
        for ticket in db.query(TicketMantenimiento).filter(
            TicketMantenimiento.solicitud_id.in_(request_ids)
        ).order_by(TicketMantenimiento.id).all():
            tickets[ticket.solicitud_id] = ticket
        for event in db.query(ServicioHerramienta).filter(
            ServicioHerramienta.solicitud_id.in_(request_ids)
        ).order_by(ServicioHerramienta.id).all():
            services[event.solicitud_id] = event
        for incident in db.query(IncidenteHerramienta).filter(
            IncidenteHerramienta.solicitud_id.in_(request_ids)
        ).all():
            incidents[incident.solicitud_id] = incident
    tool_ids = {row.herramienta_id for row in rows if row.herramienta_id}
    tools = {
        tool.id: tool
        for tool in db.query(Herramienta).filter(Herramienta.id.in_(tool_ids)).all()
    } if tool_ids else {}
    demand_ids = {row.demanda_id for row in rows if row.demanda_id}
    demands = {
        demand.id: demand
        for demand in db.query(DemandaPlaneacion).filter(DemandaPlaneacion.id.in_(demand_ids)).all()
    } if demand_ids else {}

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
            "incident": _incident_read(incidents[row.id]) if row.id in incidents else None,
            "ticket": {
                "id": tickets[row.id].id,
                "folio": f"MT-{tickets[row.id].id:05d}",
                "state": tickets[row.id].estado,
                "result": tickets[row.id].resultado,
            } if row.id in tickets else None,
            "retirement": {
                "id": retirements[row.id].id,
                "folio": f"BJ-{retirements[row.id].id:05d}",
                "state": retirements[row.id].estado,
            } if row.id in retirements else None,
            "replacement_tool_id": row.herramienta_nueva_id,
            "pilot_change": bool(row.cambio_piloto),
            "automatic": bool(row.automatico),
            "strokes_at_request": row.golpes_al_crear,
            "installed_tn": row.tn_instalado,
            "tn_difference": workflow_api.describe_difference(row.tn, row.tn_instalado) if row.tn_instalado else None,
            "destination": row.destino_codigo,
            "requested_by": row.solicitado_por,
            "requested_by_name": name(row.solicitado_por),
            "demand_id": row.demanda_id,
            "work_order": demands[row.demanda_id].orden_trabajo if row.demanda_id in demands else None,
            "quantity": demands[row.demanda_id].cantidad if row.demanda_id in demands else None,
            "requested_by_shift": shifts.shift_for(row.solicitado_en),
            "requested_at": _iso(row.solicitado_en),
            "attended_by": row.atendido_por,
            "attended_by_name": name(row.atendido_por) if row.atendido_por else None,
            "attended_at": _iso(row.atendido_en),
            "received_at": _iso(row.recibida_en),
            "wait_min": _minutes(row.solicitado_en, row.atendido_en),
            "attention_min": _minutes(row.atendido_en, row.recibida_en),
            "downtime_min": _minutes(row.solicitado_en, row.recibida_en),
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
        workflow_api.require_machine_enabled(db, code)
        setup = tool_tracking_api._active_setup(db, code)
        if not setup:
            raise HTTPException(status_code=409, detail="The machine has no registered setup")
        requirement = _position(db, code, setup, item)
        if item.cambio_piloto:
            if tipo not in ("replacement", "tool_request"):
                raise HTTPException(status_code=422, detail="A pilot change only applies to a replacement or a tool request")
            if not is_round(requirement.tn):
                raise HTTPException(status_code=422, detail="A pilot change only applies to a round tool (TN 1xxxx)")
            if len(note) < 5:
                raise HTTPException(status_code=422, detail="Write which pilot size is needed and why")
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

        incident_fields = None
        if tipo == "damage":
            incident_fields = _incident_fields(db, item.operador or user.username, item.causa, item.momento)

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
            cambio_piloto=bool(item.cambio_piloto),
            herramienta_id=tool.id if tool else None,
            solicitado_por=user.username,
            solicitado_en=workflow_api.utcnow(),
        )
        db.add(row)
        db.flush()
        if incident_fields is not None and tool is not None:
            _save_incident(db, row, tool, incident_fields, user.username, descripcion=note)
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
        workflow_api.require_machine_enabled(db, code)
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
        if row.tipo not in ("program_problem", "part_request"):
            raise HTTPException(status_code=409, detail="Only program problems and part number requests are resolved this way")
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


@router.post("/service-requests/{request_id}/incident")
def record_tool_incident(request_id: int, item: IncidentRecord):
    """Tool Crib records what it found when it has the tool: who was on the machine, the cause and when it happened."""
    user = _user()
    _require_toolcrib(user)
    finding = item.hallazgo.strip()
    if len(finding) < 3:
        raise HTTPException(status_code=422, detail="Write what Tool Crib found on the tool")
    description = (item.descripcion or "").strip()
    if len(finding) > MAX_NOTE_LENGTH or len(description) > MAX_NOTE_LENGTH:
        raise HTTPException(status_code=422, detail=f"Notes are limited to {MAX_NOTE_LENGTH} characters")
    db = workflow_api.SessionLocal()
    try:
        row = _get(db, request_id)
        if row.estado not in ("collected", "replaced") or not row.herramienta_id \
                or row.tipo in ("tool_request", "program_problem"):
            raise HTTPException(status_code=409, detail="Record the incident once Tool Crib has the tool")
        tool = db.query(Herramienta).filter(Herramienta.id == row.herramienta_id).with_for_update().one()
        fields = _incident_fields(db, item.operador, item.causa, item.momento)
        _save_incident(db, row, tool, fields, user.username, descripcion=description or None, hallazgo=finding)
        db.commit()
        return _read_many(db, [row])[0]
    except HTTPException:
        db.rollback()
        raise
    finally:
        db.close()


@router.get("/tool-incidents")
def list_tool_incidents(tool_id: Optional[str] = None, limit: int = 200):
    """Incident history (damage and escalations) for analysis."""
    db = workflow_api.SessionLocal()
    try:
        query = db.query(IncidenteHerramienta)
        if tool_id:
            query = query.filter(IncidenteHerramienta.herramienta_id == scan_codes.tool_code(tool_id).upper())
        rows = query.order_by(IncidenteHerramienta.id.desc()).limit(max(1, min(limit, 1000))).all()
        return [_incident_read(row) for row in rows]
    finally:
        db.close()


@router.get("/operators")
def list_operators():
    """Active Manufacturing users, to say who was on the machine when something happened."""
    user = _user()
    if not ({"service.request", "service.view"} & set(user.permissions)):
        raise HTTPException(status_code=403, detail=f"Your role ({user.role}) is not allowed to do this")
    db = workflow_api.SessionLocal()
    try:
        rows = db.query(Usuario).filter(
            Usuario.activo.is_(True), Usuario.role.in_(OPERATOR_ROLES)
        ).order_by(Usuario.full_name, Usuario.username).all()
        return [
            {
                "username": person.username,
                "full_name": person.full_name or person.username,
                "employee_number": person.employee_number,
                "shift": person.shift,
            }
            for person in rows
        ]
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


class ServiceAttend(BaseModel):
    atendido_por: Optional[str] = None


TOOLCRIB_ROLES = ("TOOLCRIB_OPERATOR", "TOOLCRIB_SUPERVISOR")


@router.get("/toolcrib-users")
def list_toolcrib_users():
    """Active Tool Crib people, so a shared screen can say who is attending."""
    user = _user()
    if "service.attend" not in user.permissions:
        raise HTTPException(status_code=403, detail=f"Your role ({user.role}) is not allowed to do this")
    db = workflow_api.SessionLocal()
    try:
        rows = db.query(Usuario).filter(
            Usuario.activo.is_(True), Usuario.role.in_(TOOLCRIB_ROLES)
        ).order_by(Usuario.full_name, Usuario.username).all()
        return [
            {"username": person.username, "full_name": person.full_name or person.username, "shift": person.shift}
            for person in rows
        ]
    finally:
        db.close()


@router.post("/service-requests/{request_id}/attend")
def attend_service_request(request_id: int, item: Optional[ServiceAttend] = None):
    """Tool Crib takes a request. On a shared screen the attendant is picked from the Tool Crib list;
    a supervisor can hand an attended request to someone else."""
    user = _user()
    db = workflow_api.SessionLocal()
    try:
        row = _get(db, request_id)
        is_program = row.tipo == "program_problem"
        if not is_program and "tools.move" not in user.permissions:
            raise HTTPException(status_code=403, detail="Only Tool Crib attends tool requests")
        chosen = ((item.atendido_por if item else None) or "").strip()
        if is_program or not chosen:
            attendant = user.username
        else:
            person = db.query(Usuario).filter(
                func.lower(Usuario.username) == chosen.lower(), Usuario.activo.is_(True),
                Usuario.role.in_(TOOLCRIB_ROLES),
            ).first()
            if not person:
                raise HTTPException(status_code=422, detail="Choose who is attending from the Tool Crib list")
            attendant = person.username
        if row.estado == "attending":
            if user.role not in CAN_ATTEND_FOR_OTHERS or not chosen:
                raise HTTPException(status_code=409, detail=f"{row.atendido_por} is already attending this request")
        elif row.estado != "open":
            raise HTTPException(status_code=409, detail="This request is no longer open")
        row.estado = "attending"
        row.atendido_por = attendant
        row.atendido_en = row.atendido_en if row.atendido_en else workflow_api.utcnow()
        db.commit()
        return _read_many(db, [row])[0]
    except HTTPException:
        db.rollback()
        raise
    finally:
        db.close()


def _require_attendant(user, row):
    """Tool Crib screens are shared, so any Tool Crib account can record the steps of a tool request;
    program problems stay with whoever took them."""
    if row.tipo != "program_problem" and "tools.move" in user.permissions:
        return
    if user.username != row.atendido_por and user.role not in CAN_ATTEND_FOR_OTHERS:
        raise HTTPException(
            status_code=403,
            detail=f"{row.atendido_por} is attending this request; only that user or a supervisor can record it.",
        )


def _service_destination(db, row, tool, code):
    """Where a recovered tool can go: work needs a service location; only a return goes straight to a rack."""
    destination = workflow_api._location(db, scan_codes.location_code(code), COLLECT_DESTINATIONS)
    if row.tipo != "return" and destination.tipo not in SERVICE_LOCATIONS:
        raise HTTPException(
            status_code=409,
            detail=f"A tool sent for {row.tipo} goes to a service location (TOGU, Maintenance or Workshop), "
                   f"not to '{destination.codigo}'. It goes to a rack once the result is recorded.",
        )
    workflow_api._require_home_rack(db, tool, destination)
    if destination.tipo == "togu" and db.query(Herramienta.id).filter(
        Herramienta.ubicacion == destination.codigo,
        Herramienta.id != tool.id,
    ).first():
        raise HTTPException(status_code=409, detail=f"TOGU '{destination.codigo}' is occupied")
    return destination


@router.post("/service-requests/{request_id}/relocate")
def relocate_service_tool(request_id: int, item: ServiceCollect):
    """A recovered tool that is not in a service location yet (for example, left in a rack) goes to one so its result can be recorded."""
    user = _user()
    _require_toolcrib(user)
    db = workflow_api.SessionLocal()
    try:
        row = _get(db, request_id)
        if row.estado not in ("collected", "replaced") or not row.herramienta_id or row.tipo in ("tool_request", "return", "program_problem"):
            raise HTTPException(status_code=409, detail="This request has no tool in service")
        if db.query(ServicioHerramienta.id).filter(ServicioHerramienta.solicitud_id == row.id).first():
            raise HTTPException(status_code=409, detail="The result of this service is already recorded")
        if db.query(TicketMantenimiento.id).filter(TicketMantenimiento.solicitud_id == row.id).first():
            raise HTTPException(status_code=409, detail="This tool went to Maintenance; its work is recorded in the ticket")
        tool = db.query(Herramienta).filter(Herramienta.id == row.herramienta_id).with_for_update().one()
        current = db.query(Ubicacion).filter(Ubicacion.codigo == tool.ubicacion).first()
        if current and current.tipo in SERVICE_LOCATIONS:
            raise HTTPException(status_code=409, detail=f"Tool {tool.id} is already in {current.codigo}")
        destination = _service_destination(db, row, tool, item.destino_codigo)
        if tool.estado != "baja":
            tool.estado = COLLECT_TOOL_STATUS.get(row.tipo) or workflow_api.SERVICE_STATUS_BY_LOCATION[destination.tipo]
        detail = f"{_folio(row)}: moved to {destination.codigo} to record the {row.tipo} result"
        if item.nota and item.nota.strip():
            detail += f" - {item.nota.strip()}"
        workflow_api._append_movement(
            db, tool, tool.ubicacion, destination.codigo, f"service_relocate_{row.tipo}",
            user.username, details=detail, setup_id=row.setup_id,
        )
        row.destino_codigo = destination.codigo
        db.commit()
        return _read_many(db, [row])[0]
    except HTTPException:
        db.rollback()
        raise
    finally:
        db.close()


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
        destination = _service_destination(db, row, tool, item.destino_codigo)
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
        row.recogido_por = row.atendido_por or user.username
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


def _candidate(db, setup, tn, category, tool_id, sources, machine_code=None, observed=False, pilot_change=False):
    """Tool that can be put on a position: right TN/type, usable, not committed elsewhere.

    The TN may differ within the dimensional tolerance; a pilot change also accepts any other round tool.
    `observed` is for tools already mounted before MIRA tracked them: an unregistered or empty
    previous location is accepted, but a tool known to be in service, a cart or a machine is not.
    """
    tool = scan_codes.find_tool(db, tool_id, lock=True)
    if not tool:
        raise HTTPException(status_code=404, detail=f"Tool '{tool_id}' is not in the inventory")
    fits = workflow_api._tn_fits(db, tn, tool.tn) or (pilot_change and is_round(tn) and is_round(tool.tn))
    if not fits or workflow_api._category(tool) != category:
        raise HTTPException(
            status_code=409,
            detail=f"Tool {tool.id} does not match this position (needs TN {tn} within ±{workflow_api._tolerance(db):.3f} in., {category}).",
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
        tool, location = _candidate(
            db, setup, row.tn, row.categoria, tool_id, INSTALL_SOURCES, pilot_change=bool(row.cambio_piloto),
        )
        workflow_api._append_movement(
            db, tool, location.codigo, row.maquina_codigo, "service_install", user.username,
            details=f"{_folio(row)}: installed on station {row.estacion} ({row.categoria})"
                    + (f", replaces {row.herramienta_id}" if row.herramienta_id else ""),
            setup_id=setup.id, capture=scan_codes.capture_mode(item.captura),
        )
        _set_slot(requirement, row.categoria, tool.id)
        row.herramienta_nueva_id = tool.id
        row.tn_instalado = tool.tn
        row.recibida_en = workflow_api.utcnow()
        old_tool = db.query(Herramienta).filter(Herramienta.id == row.herramienta_id).first() if row.herramienta_id else None
        old_location = db.query(Ubicacion).filter(Ubicacion.codigo == old_tool.ubicacion).first() if old_tool else None
        old_tool_resolved = (
            old_tool is None
            or old_tool.id == tool.id
            or (old_tool.estado or "disponible") == "baja"
            or (old_location is not None and old_location.tipo == "rack")
            or db.query(TicketMantenimiento.id).filter(TicketMantenimiento.solicitud_id == row.id).first() is not None
            or db.query(BajaHerramienta.id).filter(
                BajaHerramienta.solicitud_id == row.id, BajaHerramienta.estado != "rejected",
            ).first() is not None
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
    ticket = db.query(TicketMantenimiento).filter(TicketMantenimiento.solicitud_id == row.id).first()
    if ticket:
        raise HTTPException(
            status_code=409,
            detail=f"Tool {tool.id} went to Maintenance (MT-{ticket.id:05d}); the work is recorded in that ticket.",
        )
    location = db.query(Ubicacion).filter(Ubicacion.codigo == tool.ubicacion).first()
    pending = db.query(BajaHerramienta).filter(
        BajaHerramienta.solicitud_id == row.id, BajaHerramienta.estado != "rejected",
    ).first()
    if pending:
        raise HTTPException(
            status_code=409,
            detail=f"Tool {tool.id} has the retirement BJ-{pending.id:05d}; the decision is recorded there.",
        )
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
    if result_kind == "retired":
        raise HTTPException(
            status_code=409,
            detail="A tool is retired through a retirement proposal (Retire…) that a supervisor or the technician approves.",
        )
    if result_kind != "ready":
        raise HTTPException(status_code=422, detail="Result must be ready")
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
            if rebaje > MAX_REBAJE:
                raise HTTPException(
                    status_code=422, detail=f"The amount ground off cannot be more than {MAX_REBAJE} in.",
                )
        abnormal = False
        if row.tipo == "sharpening" and rebaje is None:
            raise HTTPException(status_code=422, detail="Record how much was ground off (use 0 if nothing was removed)")
        if row.tipo in ("damage", "performance") and not note:
            raise HTTPException(status_code=422, detail="Describe what was found and repaired")
        abnormal = rebaje is not None and rebaje >= ABNORMAL_REBAJE
        if abnormal and len(justification) < MIN_JUSTIFICATION_LENGTH:
            raise HTTPException(
                status_code=422,
                detail=f"{ABNORMAL_REBAJE} in. or more was ground off, which is abnormal: explain what happened "
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
            if tool.altura_total not in (None, ""):
                try:
                    tool.altura_total = format(max(Decimal("0"), Decimal(str(tool.altura_total)) - rebaje), ".3f")
                except (InvalidOperation, TypeError):
                    pass
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
        tool.estado = "disponible"
        if row.tipo in ("sharpening", "damage", "performance"):
            import strokes_api
            strokes_api.reset_after_service(db, tool, user.username, f"{_folio(row)}: {row.tipo} finished")
        movement = "service_completed"
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
