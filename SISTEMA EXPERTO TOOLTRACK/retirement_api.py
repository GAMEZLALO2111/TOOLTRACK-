"""Retirements (baja): Tool Crib proposes, a supervisor or the maintenance technician decides.

The proposal carries the same incident data as a damage report or a Maintenance ticket (who was on the
machine, shift, part number, station, cause, moment) plus the reason. Nobody approves their own proposal.
While a retirement waits, the tool is locked and flagged as damaged; rejecting restores its previous state.
"""
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import maintenance_api as maintenance
import service_requests_api as service
import workflow_api
from models.inventory import BajaHerramienta, Herramienta, IncidenteHerramienta, TicketMantenimiento, Usuario

router = APIRouter(prefix="/workflow/retirements", tags=["retirements"])


class RetirementCreate(BaseModel):
    hallazgo: str
    causa: Optional[str] = None
    momento: Optional[str] = None
    operador: Optional[str] = None
    request_id: Optional[int] = None
    ticket_id: Optional[int] = None
    tool_id: Optional[str] = None
    origen: Optional[str] = None
    machine: Optional[str] = None
    part_number: Optional[str] = None
    station: Optional[str] = None


class RetirementDecision(BaseModel):
    nota: Optional[str] = None


def _folio(row):
    return f"BJ-{row.id:05d}"


def _iso(value):
    return value.isoformat() if value else None


def _read_many(db, rows):
    tool_ids = {row.herramienta_id for row in rows}
    tools = {tool.id: tool for tool in db.query(Herramienta).filter(Herramienta.id.in_(tool_ids)).all()} if tool_ids else {}
    incident_ids = {row.incidente_id for row in rows}
    incidents = {
        item.id: item for item in db.query(IncidenteHerramienta).filter(IncidenteHerramienta.id.in_(incident_ids)).all()
    } if incident_ids else {}
    names = {name for row in rows for name in (row.propuesto_por, row.resuelto_por) if name}
    people = {
        person.username: (person.full_name or person.username)
        for person in db.query(Usuario).filter(Usuario.username.in_(names)).all()
    } if names else {}

    def name(username):
        return people.get(username, username) if username else None

    result = []
    for row in rows:
        tool = tools.get(row.herramienta_id)
        incident = incidents.get(row.incidente_id)
        result.append({
            "id": row.id,
            "folio": _folio(row),
            "state": row.estado,
            "tool_id": row.herramienta_id,
            "tn": tool.tn if tool else None,
            "category": workflow_api._category(tool) if tool else None,
            "tool_status": (tool.estado or "disponible") if tool else None,
            "tool_location": tool.ubicacion if tool else None,
            "request_id": row.solicitud_id,
            "request_folio": f"SR-{row.solicitud_id:05d}" if row.solicitud_id else None,
            "reason": row.motivo,
            "incident": service._incident_read(incident) if incident else None,
            "proposed_by": row.propuesto_por,
            "proposed_by_name": name(row.propuesto_por),
            "proposed_at": _iso(row.propuesto_en),
            "resolved_by": row.resuelto_por,
            "resolved_by_name": name(row.resuelto_por),
            "resolved_at": _iso(row.resuelto_en),
            "resolution_note": row.nota_resolucion,
        })
    return result


def _user():
    return maintenance._user()


def _get(db, retirement_id):
    row = db.query(BajaHerramienta).filter(BajaHerramienta.id == retirement_id).with_for_update().first()
    if not row:
        raise HTTPException(status_code=404, detail="Retirement not found")
    return row


@router.post("", status_code=201)
def propose_retirement(item: RetirementCreate):
    """Tool Crib proposes retiring a tool it holds, with the incident that justifies it."""
    user = _user()
    maintenance._require_toolcrib(user)
    reason = maintenance._text(item.hallazgo, "The reason for the retirement")
    db = workflow_api.SessionLocal()
    try:
        if item.ticket_id:
            case = None
            ticket = db.query(TicketMantenimiento).filter(TicketMantenimiento.id == item.ticket_id).with_for_update().first()
            if not ticket:
                raise HTTPException(status_code=404, detail="Maintenance ticket not found")
            if ticket.estado != "closed" or ticket.resultado != "unrepairable":
                raise HTTPException(status_code=409, detail="Only a tool Maintenance found unrepairable can be retired from its ticket")
            tool = db.query(Herramienta).filter(Herramienta.id == ticket.herramienta_id).with_for_update().one()
            request, incident_id = None, ticket.incidente_id
        else:
            case = maintenance.open_case(db, item, "Propose the retirement")
            tool, request, incident_id = case.tool, case.request, None
        if (tool.estado or "disponible") == "baja":
            raise HTTPException(status_code=409, detail=f"Tool {tool.id} is already retired")
        workflow_api._require_no_open_ticket(db, tool)
        now = workflow_api.utcnow()
        incident = None
        if case is not None:
            incident = maintenance.save_case_incident(db, case, item, reason, user.username, now)
        db.flush()
        if incident is not None:
            incident_id = incident.id
        row = BajaHerramienta(
            herramienta_id=tool.id, solicitud_id=request.id if request else None, incidente_id=incident_id,
            estado="pending", estado_previo=tool.estado or "disponible", motivo=reason,
            propuesto_por=user.username, propuesto_en=now,
        )
        db.add(row)
        db.flush()
        workflow_api._append_movement(
            db, tool, tool.ubicacion, tool.ubicacion, "retirement_proposed", user.username,
            details=f"{_folio(row)}: {reason}", setup_id=request.setup_id if request else None,
        )
        tool.estado = "danado"
        if request is not None and request.estado == "replaced":
            request.estado = "closed"
            request.cerrado_por = user.username
            request.cerrado_en = now
        db.commit()
        return _read_many(db, [row])[0]
    except HTTPException:
        db.rollback()
        raise
    finally:
        db.close()


@router.get("")
def list_retirements(state: str = "pending", tool_id: Optional[str] = None, limit: int = 200):
    if state not in {"pending", "closed", "all"}:
        raise HTTPException(status_code=422, detail="State must be pending, closed or all")
    db = workflow_api.SessionLocal()
    try:
        query = db.query(BajaHerramienta)
        if state == "pending":
            query = query.filter(BajaHerramienta.estado == "pending")
        elif state == "closed":
            query = query.filter(BajaHerramienta.estado != "pending")
        if tool_id:
            query = query.filter(BajaHerramienta.herramienta_id == tool_id.strip())
        rows = query.order_by(BajaHerramienta.id.desc()).limit(max(1, min(limit, 500))).all()
        return _read_many(db, rows)
    finally:
        db.close()


def _require_approver(user, row):
    if "retirements.approve" not in user.permissions:
        raise HTTPException(status_code=403, detail=f"Your role ({user.role}) cannot approve retirements")
    if user.username == row.propuesto_por:
        raise HTTPException(status_code=403, detail="You proposed this retirement; someone else has to decide it")


@router.post("/{retirement_id}/approve")
def approve_retirement(retirement_id: int, item: RetirementDecision):
    user = _user()
    db = workflow_api.SessionLocal()
    try:
        row = _get(db, retirement_id)
        _require_approver(user, row)
        if row.estado != "pending":
            raise HTTPException(status_code=409, detail="This retirement was already decided")
        tool = db.query(Herramienta).filter(Herramienta.id == row.herramienta_id).with_for_update().one()
        note = (item.nota or "").strip() or None
        workflow_api._append_movement(
            db, tool, tool.ubicacion, tool.ubicacion, "retirement_approved", user.username,
            details=f"{_folio(row)} approved" + (f" - {note}" if note else ""),
        )
        tool.estado = "baja"
        row.estado = "approved"
        row.resuelto_por = user.username
        row.resuelto_en = workflow_api.utcnow()
        row.nota_resolucion = note
        db.commit()
        return _read_many(db, [row])[0]
    except HTTPException:
        db.rollback()
        raise
    finally:
        db.close()


@router.post("/{retirement_id}/reject")
def reject_retirement(retirement_id: int, item: RetirementDecision):
    user = _user()
    db = workflow_api.SessionLocal()
    try:
        row = _get(db, retirement_id)
        _require_approver(user, row)
        if row.estado != "pending":
            raise HTTPException(status_code=409, detail="This retirement was already decided")
        note = maintenance._text(item.nota, "Why the retirement is rejected")
        tool = db.query(Herramienta).filter(Herramienta.id == row.herramienta_id).with_for_update().one()
        workflow_api._append_movement(
            db, tool, tool.ubicacion, tool.ubicacion, "retirement_rejected", user.username,
            details=f"{_folio(row)} rejected - {note}",
        )
        tool.estado = row.estado_previo or "disponible"
        row.estado = "rejected"
        row.resuelto_por = user.username
        row.resuelto_en = workflow_api.utcnow()
        row.nota_resolucion = note
        db.commit()
        return _read_many(db, [row])[0]
    except HTTPException:
        db.rollback()
        raise
    finally:
        db.close()
