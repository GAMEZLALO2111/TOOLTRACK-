"""Maintenance tickets: Tool Crib sends a tool to Maintenance and the technician evaluates, repairs and releases it.

A ticket is never opened automatically: Tool Crib opens it, either from a service request whose tool it
already has (Manufacturing may have reported only a sharpening) or by hand for any tool it holds.
The incident record carries the same who/where/why/when in both cases.

Flow: open -> evaluating (technician received the tool) -> repairing (repairable) -> closed (released),
      or closed as unrepairable (the tool stays in Maintenance, damaged, until it is retired).
"""
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import auth
import scan_codes
import service_requests_api as service
import workflow_api
from models.inventory import (
    EstadoMaquina,
    Herramienta,
    IncidenteHerramienta,
    PendienteRecoger,
    RequisitoSetup,
    ReservaHerramienta,
    ServicioHerramienta,
    TicketMantenimiento,
    Ubicacion,
    Usuario,
)

router = APIRouter(prefix="/workflow/maintenance", tags=["maintenance"])

ORIGINS = ("machine", "toolcrib")
TOOLCRIB_LOCATIONS = {"rack", "togu", "maintenance", "workshop"}
MAX_TEXT = 500


class TicketCreate(BaseModel):
    hallazgo: str
    causa: str
    momento: str
    operador: Optional[str] = None
    request_id: Optional[int] = None
    tool_id: Optional[str] = None
    origen: Optional[str] = None
    machine: Optional[str] = None
    part_number: Optional[str] = None
    station: Optional[str] = None
    destino_codigo: Optional[str] = None


class TicketReceive(BaseModel):
    tool_id: str
    captura: Optional[str] = None


class TicketEvaluate(BaseModel):
    diagnostico: str
    reparable: bool
    causa_tecnica: Optional[str] = None


class TicketRelease(BaseModel):
    trabajo: str


def _folio(ticket):
    return f"MT-{ticket.id:05d}"


def _iso(value):
    return value.isoformat() if value else None


def _text(value, label, minimum=3):
    text = (value or "").strip()
    if len(text) < minimum:
        raise HTTPException(status_code=422, detail=f"{label} is required")
    if len(text) > MAX_TEXT:
        raise HTTPException(status_code=422, detail=f"{label} is limited to {MAX_TEXT} characters")
    return text


def _require_toolcrib(user):
    if not {"tools.move", "service.attend"} <= set(user.permissions):
        raise HTTPException(status_code=403, detail="Only Tool Crib opens maintenance tickets")


def _require_technician(user):
    if "maintenance.operate" not in user.permissions:
        raise HTTPException(status_code=403, detail=f"Your role ({user.role}) is not allowed to work maintenance tickets")


def _read_many(db, tickets):
    tool_ids = {ticket.herramienta_id for ticket in tickets}
    tools = {tool.id: tool for tool in db.query(Herramienta).filter(Herramienta.id.in_(tool_ids)).all()} if tool_ids else {}
    incident_ids = {ticket.incidente_id for ticket in tickets}
    incidents = {
        incident.id: incident
        for incident in db.query(IncidenteHerramienta).filter(IncidenteHerramienta.id.in_(incident_ids)).all()
    } if incident_ids else {}
    names = set()
    for ticket in tickets:
        names.update(name for name in (ticket.creado_por, ticket.recibido_por, ticket.evaluado_por, ticket.cerrado_por) if name)
    people = {
        person.username: (person.full_name or person.username)
        for person in db.query(Usuario).filter(Usuario.username.in_(names)).all()
    } if names else {}

    def name(username):
        return people.get(username, username) if username else None

    result = []
    for ticket in tickets:
        tool = tools.get(ticket.herramienta_id)
        incident = incidents.get(ticket.incidente_id)
        result.append({
            "id": ticket.id,
            "folio": _folio(ticket),
            "state": ticket.estado,
            "tool_id": ticket.herramienta_id,
            "tn": tool.tn if tool else None,
            "category": workflow_api._category(tool) if tool else None,
            "tool_status": (tool.estado or "disponible") if tool else None,
            "tool_location": tool.ubicacion if tool else None,
            "request_id": ticket.solicitud_id,
            "request_folio": f"SR-{ticket.solicitud_id:05d}" if ticket.solicitud_id else None,
            "finding": ticket.motivo,
            "incident": service._incident_read(incident) if incident else None,
            "created_by": ticket.creado_por,
            "created_by_name": name(ticket.creado_por),
            "created_at": _iso(ticket.creado_en),
            "received_by": ticket.recibido_por,
            "received_by_name": name(ticket.recibido_por),
            "received_at": _iso(ticket.recibido_en),
            "diagnosis": ticket.diagnostico,
            "repairable": ticket.reparable,
            "technical_cause": ticket.causa_tecnica,
            "evaluated_by": ticket.evaluado_por,
            "evaluated_at": _iso(ticket.evaluado_en),
            "work": ticket.trabajo,
            "result": ticket.resultado,
            "closed_by": ticket.cerrado_por,
            "closed_by_name": name(ticket.cerrado_por),
            "closed_at": _iso(ticket.cerrado_en),
        })
    return result


def _ticket(db, ticket_id):
    ticket = db.query(TicketMantenimiento).filter(TicketMantenimiento.id == ticket_id).with_for_update().first()
    if not ticket:
        raise HTTPException(status_code=404, detail="Maintenance ticket not found")
    return ticket


def _maintenance_location(db, code):
    if code and code.strip():
        return workflow_api._location(db, scan_codes.location_code(code), {"maintenance"})
    places = db.query(Ubicacion).filter(Ubicacion.tipo == "maintenance", Ubicacion.activa.is_(True)).all()
    if len(places) != 1:
        raise HTTPException(status_code=422, detail="Choose the Maintenance location")
    return places[0]


def _require_free(db, tool):
    """A tool sitting in Tool Crib that is not promised to a kit, a pickup or an active setup."""
    busy = (
        db.query(ReservaHerramienta.herramienta_id).filter(ReservaHerramienta.herramienta_id == tool.id).first()
        or db.query(PendienteRecoger.id).filter(
            PendienteRecoger.herramienta_id == tool.id, PendienteRecoger.estado == "pending",
        ).first()
        or db.query(RequisitoSetup.id).join(
            EstadoMaquina, EstadoMaquina.setup_activo_id == RequisitoSetup.setup_id,
        ).filter((RequisitoSetup.punch_tool_id == tool.id) | (RequisitoSetup.die_tool_id == tool.id)).first()
    )
    if busy:
        raise HTTPException(status_code=409, detail=f"Tool {tool.id} is reserved or assigned to a machine; it cannot go to Maintenance now")


@router.post("/tickets", status_code=201)
def create_ticket(item: TicketCreate):
    """Tool Crib sends a tool to Maintenance, with the same who/where/why/when it records when it recovers a tool."""
    user = _user()
    _require_toolcrib(user)
    finding = _text(item.hallazgo, "What Tool Crib found")
    db = workflow_api.SessionLocal()
    try:
        request = None
        if item.request_id:
            request = service._get(db, item.request_id)
            if (
                request.estado not in ("collected", "replaced")
                or not request.herramienta_id
                or request.tipo in ("tool_request", "return", "program_problem")
            ):
                raise HTTPException(status_code=409, detail="Open the ticket once Tool Crib has the tool of this request")
            if db.query(TicketMantenimiento.id).filter(TicketMantenimiento.solicitud_id == request.id).first():
                raise HTTPException(status_code=409, detail="This request already has a maintenance ticket")
            if db.query(ServicioHerramienta.id).filter(ServicioHerramienta.solicitud_id == request.id).first():
                raise HTTPException(status_code=409, detail="The result of this service is already recorded")
            tool = db.query(Herramienta).filter(Herramienta.id == request.herramienta_id).with_for_update().one()
            known = db.query(IncidenteHerramienta).filter(IncidenteHerramienta.solicitud_id == request.id).first()
            fields = service._incident_fields(db, item.operador or (known.operador if known else None), item.causa, item.momento)
        else:
            tool = scan_codes.find_tool(db, item.tool_id or "", lock=True)
            if not tool:
                raise HTTPException(status_code=404, detail=f"Tool '{item.tool_id}' is not in the inventory")
            origin = (item.origen or "").strip().lower()
            if origin not in ORIGINS:
                raise HTTPException(status_code=422, detail="Say where the tool comes from: a machine or Tool Crib")
            current = db.query(Ubicacion).filter(Ubicacion.codigo == tool.ubicacion).first()
            if not current or current.tipo not in TOOLCRIB_LOCATIONS:
                raise HTTPException(
                    status_code=409,
                    detail=f"Tool {tool.id} is in {tool.ubicacion or 'an unknown location'}; Tool Crib has to hold it before it goes to Maintenance.",
                )
            _require_free(db, tool)
            machine = None
            if origin == "machine":
                code = scan_codes.machine_code(item.machine or "")
                if not code:
                    raise HTTPException(status_code=422, detail="Choose the machine the tool came from")
                machine = workflow_api._location(db, code, {"machine"}).codigo
            fields = service._incident_fields(db, item.operador, item.causa, item.momento, require_operator=origin == "machine")

        if (tool.estado or "disponible") == "baja":
            raise HTTPException(status_code=409, detail=f"Tool {tool.id} is retired")
        if db.query(TicketMantenimiento.id).filter(
            TicketMantenimiento.herramienta_id == tool.id, TicketMantenimiento.estado != "closed",
        ).first():
            raise HTTPException(status_code=409, detail=f"Tool {tool.id} already has an open maintenance ticket")
        destination = _maintenance_location(db, item.destino_codigo)

        now = workflow_api.utcnow()
        if request is not None:
            incident = service._save_incident(db, request, tool, fields, user.username, hallazgo=finding)
        else:
            incident = IncidenteHerramienta(
                herramienta_id=tool.id, origen=origin, reportado_como=None,
                maquina_codigo=machine, numero_parte=(item.part_number or "").strip() or None,
                estacion=(item.station or "").strip() or None, tn=tool.tn, categoria=workflow_api._category(tool),
                life=tool.life, operador=fields["operador"], turno=fields["turno"], causa=fields["causa"],
                momento=fields["momento"], hallazgo=finding, creado_por=user.username, creado_en=now,
            )
            db.add(incident)
        db.flush()
        ticket = TicketMantenimiento(
            herramienta_id=tool.id, solicitud_id=request.id if request else None, incidente_id=incident.id,
            estado="open", motivo=finding, creado_por=user.username, creado_en=now,
        )
        db.add(ticket)
        db.flush()
        incident.ticket_id = ticket.id

        detail = f"{_folio(ticket)}: {finding}"
        workflow_api._append_movement(
            db, tool, tool.ubicacion, destination.codigo, "maintenance_ticket_opened", user.username,
            details=detail, setup_id=request.setup_id if request else None,
        )
        tool.estado = "danado"
        if request is not None and request.estado == "replaced":
            # The machine already has its tool; the old one now lives in the ticket.
            request.estado = "closed"
            request.cerrado_por = user.username
            request.cerrado_en = now
        db.commit()
        return _read_many(db, [ticket])[0]
    except HTTPException:
        db.rollback()
        raise
    finally:
        db.close()


@router.get("/tickets")
def list_tickets(state: str = "active", tool_id: Optional[str] = None, limit: int = 200):
    if state not in {"active", "closed", "all"}:
        raise HTTPException(status_code=422, detail="State must be active, closed or all")
    db = workflow_api.SessionLocal()
    try:
        query = db.query(TicketMantenimiento)
        if state == "active":
            query = query.filter(TicketMantenimiento.estado != "closed")
        elif state == "closed":
            query = query.filter(TicketMantenimiento.estado == "closed")
        if tool_id:
            tool = scan_codes.find_tool(db, tool_id)
            query = query.filter(TicketMantenimiento.herramienta_id == (tool.id if tool else tool_id))
        rows = query.order_by(TicketMantenimiento.id.desc()).limit(max(1, min(limit, 500))).all()
        return _read_many(db, rows)
    finally:
        db.close()


@router.post("/tickets/{ticket_id}/receive")
def receive_ticket(ticket_id: int, item: TicketReceive):
    """The technician scans the tool to confirm it is physically in Maintenance and takes the ticket."""
    user = _user()
    _require_technician(user)
    db = workflow_api.SessionLocal()
    try:
        ticket = _ticket(db, ticket_id)
        if ticket.estado != "open":
            raise HTTPException(status_code=409, detail="This ticket was already received")
        tool = db.query(Herramienta).filter(Herramienta.id == ticket.herramienta_id).with_for_update().one()
        if scan_codes.tool_code(item.tool_id).upper() != tool.id.upper():
            raise HTTPException(status_code=409, detail=f"That is not the tool of this ticket: scan {tool.id}.")
        workflow_api._append_movement(
            db, tool, tool.ubicacion, tool.ubicacion, "maintenance_received", user.username,
            details=f"{_folio(ticket)}: received by Maintenance", capture=scan_codes.capture_mode(item.captura),
        )
        ticket.estado = "evaluating"
        ticket.recibido_por = user.username
        ticket.recibido_en = workflow_api.utcnow()
        db.commit()
        return _read_many(db, [ticket])[0]
    except HTTPException:
        db.rollback()
        raise
    finally:
        db.close()


@router.post("/tickets/{ticket_id}/evaluate")
def evaluate_ticket(ticket_id: int, item: TicketEvaluate):
    """The technician decides whether the tool can be repaired."""
    user = _user()
    _require_technician(user)
    diagnosis = _text(item.diagnostico, "The diagnosis")
    cause = (item.causa_tecnica or "").strip().lower() or None
    if cause and cause not in service.DAMAGE_CAUSES:
        raise HTTPException(status_code=422, detail="Choose the cause from the list")
    db = workflow_api.SessionLocal()
    try:
        ticket = _ticket(db, ticket_id)
        if ticket.estado != "evaluating":
            raise HTTPException(status_code=409, detail="Receive the tool before evaluating it")
        tool = db.query(Herramienta).filter(Herramienta.id == ticket.herramienta_id).with_for_update().one()
        now = workflow_api.utcnow()
        ticket.diagnostico = diagnosis
        ticket.reparable = item.reparable
        ticket.causa_tecnica = cause
        ticket.evaluado_por = user.username
        ticket.evaluado_en = now
        if item.reparable:
            ticket.estado = "repairing"
        else:
            ticket.estado = "closed"
            ticket.resultado = "unrepairable"
            ticket.cerrado_por = user.username
            ticket.cerrado_en = now
            workflow_api._append_movement(
                db, tool, tool.ubicacion, tool.ubicacion, "maintenance_unrepairable", user.username,
                details=f"{_folio(ticket)}: cannot be repaired - {diagnosis}",
            )
        db.commit()
        return _read_many(db, [ticket])[0]
    except HTTPException:
        db.rollback()
        raise
    finally:
        db.close()


@router.post("/tickets/{ticket_id}/release")
def release_ticket(ticket_id: int, item: TicketRelease):
    """The technician finished the repair: the tool is released and Tool Crib picks it up."""
    user = _user()
    _require_technician(user)
    work = _text(item.trabajo, "The work done")
    db = workflow_api.SessionLocal()
    try:
        ticket = _ticket(db, ticket_id)
        if ticket.estado != "repairing":
            raise HTTPException(status_code=409, detail="Only a tool being repaired can be released")
        tool = db.query(Herramienta).filter(Herramienta.id == ticket.herramienta_id).with_for_update().one()
        workflow_api._append_movement(
            db, tool, tool.ubicacion, tool.ubicacion, "maintenance_released", user.username,
            details=f"{_folio(ticket)}: repaired and released - {work}",
        )
        tool.estado = "disponible"
        ticket.estado = "closed"
        ticket.resultado = "released"
        ticket.trabajo = work
        ticket.cerrado_por = user.username
        ticket.cerrado_en = workflow_api.utcnow()
        db.commit()
        return _read_many(db, [ticket])[0]
    except HTTPException:
        db.rollback()
        raise
    finally:
        db.close()


def _user():
    user = auth.current_user()
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user
