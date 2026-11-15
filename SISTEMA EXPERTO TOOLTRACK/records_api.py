"""Records: every kind of event the system keeps, as its own list with filters and an Excel download.

Each record kind is a separate list (movements are split into groups that do not make sense mixed).
The same rows feed the screen and the .xlsx files, so what is seen is what is downloaded.
Times are shown in the server's local time.
"""
import io
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from sqlalchemy import func, not_, or_

import auth
import workflow_api
from models.inventory import (
    Auditoria,
    BajaHerramienta,
    BitacoraPrograma,
    Herramienta,
    IncidenteHerramienta,
    MovimientoHerramienta,
    ServicioHerramienta,
    SolicitudServicio,
    TicketMantenimiento,
    Usuario,
)

router = APIRouter(prefix="/records", tags=["records"])

MAX_PAGE = 500
MAX_EXPORT = 50000
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

MOVEMENT_GROUPS = {
    "kitting_machine": "Kitting and machine",
    "service_maintenance": "Service and Maintenance",
    "storage_status": "Storage and status",
}
KITTING_PREFIXES = ("rack_to_cart", "kit_", "cart_to_machine", "machine_", "removed_from", "pending_pickup")
SERVICE_PREFIXES = ("service_", "maintenance_", "retirement_")

MOVEMENT_LABEL = {
    "service_requested": "Service requested from the machine",
    "service_cancelled": "Service request cancelled: back on the machine",
    "service_install": "Installed on the machine",
    "service_completed": "Service finished, ready for use",
    "service_retired": "Retired after service",
    "machine_observed": "Registered as mounted on the machine",
    "service_collect_sharpening": "Collected from the machine for sharpening",
    "service_collect_inspection": "Collected from the machine for inspection",
    "service_collect_replacement": "Collected from the machine to be replaced",
    "service_collect_damage": "Collected from the machine, reported damaged",
    "service_collect_performance": "Collected from the machine, performance issue",
    "service_collect_return": "Returned from the machine",
    "service_relocate_sharpening": "Moved to a service location to record the sharpening",
    "service_relocate_inspection": "Moved to a service location to record the inspection",
    "service_relocate_replacement": "Moved to a service location to record the replacement",
    "service_relocate_damage": "Moved to a service location to record the damage",
    "service_relocate_performance": "Moved to a service location to record the performance issue",
    "maintenance_ticket_opened": "Sent to Maintenance",
    "maintenance_received": "Received by Maintenance",
    "maintenance_released": "Repaired and released by Maintenance",
    "maintenance_unrepairable": "Maintenance: cannot be repaired",
    "retirement_proposed": "Retirement proposed",
    "retirement_approved": "Retirement approved: retired",
    "retirement_rejected": "Retirement rejected: back in use",
    "kit_refresh_returned": "Back to its rack: the kit was refreshed",
    "placed_in_rack": "Placed in a rack (first location)",
    "rack_to_cart_for_kit": "Taken from the rack to the cart",
    "kit_selection_reversed": "Cart selection undone",
    "kit_cancelled_return_to_rack": "Kit cancelled: back to its rack",
    "cart_to_machine_delivery": "Delivered to the machine",
    "machine_return": "Returned from the machine to its rack",
    "machine_return_sharpening": "Returned for sharpening",
    "machine_return_damage": "Returned damaged",
    "removed_from_setup_pending_pickup": "Removed from the setup (waiting to be picked up)",
    "pending_pickup_to_rack": "Picked up from the machine to its rack",
    "status_danado": "Marked as damaged",
    "status_baja": "Retired",
    "status_disponible": "Released (available)",
}
REQUEST_TYPE = {
    "sharpening": "Sharpening", "inspection": "Inspection", "replacement": "Replacement", "damage": "Damage report",
    "performance": "Performance issue", "return": "Return", "tool_request": "Tool request", "program_problem": "Program problem",
}
REQUEST_STATE = {
    "open": "Waiting for Tool Crib", "attending": "Tool Crib on the way", "collected": "Waiting for a tool",
    "replaced": "Replaced, old tool in service", "closed": "Closed", "cancelled": "Cancelled",
}
CAUSE = {
    "collision": "Collision", "normal_wear": "Normal wear", "wrong_setup": "Wrong setup", "wrong_material": "Wrong material",
    "handling": "Handling", "lubrication": "Lubrication", "supplier_defect": "Supplier defect", "unknown": "Unknown",
}
MOMENT = {
    "setup": "During setup", "production": "In production", "inspection": "During inspection",
    "handling": "While handling", "unknown": "Unknown",
}
TICKET_STATE = {"open": "Waiting for the technician", "evaluating": "Being evaluated", "repairing": "Being repaired", "closed": "Closed"}
TICKET_RESULT = {"released": "Released", "unrepairable": "Cannot be repaired"}
RETIREMENT_STATE = {"pending": "Waiting for approval", "approved": "Approved: retired", "rejected": "Rejected"}
SERVICE_RESULT = {"ready": "Ready for use", "retired": "Retired"}
ORIGIN = {"machine": "Machine", "toolcrib": "Found in Tool Crib"}
CATEGORY = {"punzon": "Punch", "matriz": "Die"}


def movement_group(movement_type):
    if movement_type.startswith(SERVICE_PREFIXES):
        return "service_maintenance"
    if movement_type.startswith(KITTING_PREFIXES):
        return "kitting_machine"
    return "storage_status"


def movement_label(movement_type):
    if movement_type in MOVEMENT_LABEL:
        return MOVEMENT_LABEL[movement_type]
    if movement_type.endswith("_to_rack"):
        return "Stored in its rack"
    if movement_type.startswith("pending_pickup_to_"):
        return "Picked up from the machine"
    return movement_type.replace("_", " ")


@dataclass
class Filters:
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    tool: Optional[str] = None
    machine: Optional[str] = None
    user: Optional[str] = None
    group: Optional[str] = None
    exact_tool: bool = False


@dataclass
class Column:
    key: str
    label: str
    export_only: bool = False


@dataclass
class Kind:
    key: str
    label: str
    columns: list
    filters: tuple
    query: Callable
    row: Callable
    groups: dict = field(default_factory=dict)


def _local(value):
    if not value:
        return None
    return value.replace(tzinfo=timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")


def _day_bound(text, end=False):
    try:
        day = datetime.strptime(text, "%Y-%m-%d")
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="Dates must look like 2026-11-30")
    if end:
        day = day.replace(hour=23, minute=59, second=59)
    return day.astimezone(timezone.utc).replace(tzinfo=None)


def _between(query, column, f):
    if f.date_from:
        query = query.filter(column >= _day_bound(f.date_from))
    if f.date_to:
        query = query.filter(column <= _day_bound(f.date_to, end=True))
    return query


def _has(column, text):
    return func.upper(column).contains(text.strip().upper(), autoescape=True)


def _minutes(start, end):
    if not start or not end:
        return None
    return max(0, round((end - start).total_seconds() / 60))


def _yes(value):
    return "Yes" if value else "No"


def _tool_filter(query, f, *columns):
    if f.tool and f.tool.strip():
        text = f.tool.strip().upper()
        if f.exact_tool:
            query = query.filter(or_(*[func.upper(column) == text for column in columns]))
        else:
            query = query.filter(or_(*[_has(column, f.tool) for column in columns]))
    return query


def _user_filter(query, f, *columns):
    if f.user and f.user.strip():
        query = query.filter(or_(*[_has(column, f.user) for column in columns]))
    return query


def _machine_filter(query, f, *columns):
    if f.machine and f.machine.strip():
        query = query.filter(or_(*[_has(column, f.machine) for column in columns]))
    return query


# ---- movements -----------------------------------------------------------------------------

def _movements_query(db, f):
    group = f.group or "kitting_machine"
    if group != "all" and group not in MOVEMENT_GROUPS:
        raise HTTPException(status_code=422, detail="Choose a movement group from the list")
    tipo = MovimientoHerramienta.tipo
    kitting = or_(*[tipo.startswith(prefix, autoescape=True) for prefix in KITTING_PREFIXES])
    service = or_(*[tipo.startswith(prefix, autoescape=True) for prefix in SERVICE_PREFIXES])
    query = db.query(MovimientoHerramienta, Herramienta.tn, Herramienta.categoria).join(
        Herramienta, Herramienta.id == MovimientoHerramienta.herramienta_id,
    )
    if group == "service_maintenance":
        query = query.filter(service)
    elif group == "kitting_machine":
        query = query.filter(not_(service), kitting)
    elif group == "storage_status":
        query = query.filter(not_(service), not_(kitting))
    query = _between(query, MovimientoHerramienta.creado_en, f)
    query = _tool_filter(query, f, MovimientoHerramienta.herramienta_id)
    query = _machine_filter(query, f, MovimientoHerramienta.origen_codigo, MovimientoHerramienta.destino_codigo)
    query = _user_filter(query, f, MovimientoHerramienta.realizado_por)
    return query.order_by(MovimientoHerramienta.id.desc())


def _movements_row(item, names):
    movement, tn, category = item
    return {
        "when": _local(movement.creado_en),
        "tool": movement.herramienta_id,
        "tn": tn,
        "category": CATEGORY.get(category, category),
        "from": movement.origen_codigo,
        "to": movement.destino_codigo,
        "movement": movement_label(movement.tipo),
        "by": names.get(movement.realizado_por, movement.realizado_por),
        "entry": {"scan": "Scan", "manual": "Manual", "camera": "Camera"}.get(movement.captura or "", None),
        "kit": movement.surtido_id,
        "detail": movement.detalle,
        "group": MOVEMENT_GROUPS[movement_group(movement.tipo)],
    }


# ---- service requests ----------------------------------------------------------------------

def _requests_query(db, f):
    query = db.query(SolicitudServicio, IncidenteHerramienta, TicketMantenimiento).outerjoin(
        IncidenteHerramienta, IncidenteHerramienta.solicitud_id == SolicitudServicio.id,
    ).outerjoin(TicketMantenimiento, TicketMantenimiento.solicitud_id == SolicitudServicio.id)
    query = _between(query, SolicitudServicio.solicitado_en, f)
    query = _tool_filter(query, f, SolicitudServicio.herramienta_id, SolicitudServicio.herramienta_nueva_id)
    query = _machine_filter(query, f, SolicitudServicio.maquina_codigo)
    query = _user_filter(
        query, f, SolicitudServicio.solicitado_por, SolicitudServicio.atendido_por,
        SolicitudServicio.recogido_por, SolicitudServicio.cerrado_por,
    )
    return query.order_by(SolicitudServicio.id.desc())


def _requests_row(item, names):
    request, incident, ticket = item
    return {
        "folio": f"SR-{request.id:05d}",
        "requested_at": _local(request.solicitado_en),
        "type": REQUEST_TYPE.get(request.tipo, request.tipo),
        "state": REQUEST_STATE.get(request.estado, request.estado),
        "machine": request.maquina_codigo,
        "part_number": request.numero_parte,
        "station": request.estacion,
        "tn": request.tn,
        "category": CATEGORY.get(request.categoria, request.categoria),
        "tool": request.herramienta_id,
        "replacement": request.herramienta_nueva_id,
        "requested_by": names.get(request.solicitado_por, request.solicitado_por),
        "wait_min": _minutes(request.solicitado_en, request.atendido_en),
        "attention_min": _minutes(request.atendido_en, request.recibida_en),
        "total_min": _minutes(request.solicitado_en, request.cerrado_en),
        "downtime_min": _minutes(request.solicitado_en, request.recibida_en),
        "cause": CAUSE.get(incident.causa, incident.causa) if incident else None,
        "ticket": f"MT-{ticket.id:05d}" if ticket else None,
        "note": request.nota or request.problema,
        "destination": request.destino_codigo,
        "attended_by": names.get(request.atendido_por, request.atendido_por),
        "attended_at": _local(request.atendido_en),
        "collected_by": names.get(request.recogido_por, request.recogido_por),
        "collected_at": _local(request.recogido_en),
        "closed_by": names.get(request.cerrado_por, request.cerrado_por),
        "closed_at": _local(request.cerrado_en),
        "outcome": request.resolucion or request.motivo_cancelacion,
    }


# ---- incidents -----------------------------------------------------------------------------

def _incidents_query(db, f):
    query = db.query(IncidenteHerramienta)
    query = _between(query, IncidenteHerramienta.creado_en, f)
    query = _tool_filter(query, f, IncidenteHerramienta.herramienta_id)
    query = _machine_filter(query, f, IncidenteHerramienta.maquina_codigo)
    query = _user_filter(query, f, IncidenteHerramienta.operador, IncidenteHerramienta.creado_por)
    return query.order_by(IncidenteHerramienta.id.desc())


def _incidents_row(incident, names):
    return {
        "when": _local(incident.creado_en),
        "tool": incident.herramienta_id,
        "tn": incident.tn,
        "category": CATEGORY.get(incident.categoria, incident.categoria),
        "machine": incident.maquina_codigo,
        "part_number": incident.numero_parte,
        "station": incident.estacion,
        "operator": names.get(incident.operador, incident.operador),
        "shift": incident.turno,
        "cause": CAUSE.get(incident.causa, incident.causa),
        "moment": MOMENT.get(incident.momento, incident.momento),
        "reported_as": REQUEST_TYPE.get(incident.reportado_como, incident.reportado_como),
        "found": incident.hallazgo,
        "origin": ORIGIN.get(incident.origen, incident.origen),
        "request": f"SR-{incident.solicitud_id:05d}" if incident.solicitud_id else None,
        "ticket": f"MT-{incident.ticket_id:05d}" if incident.ticket_id else None,
        "description": incident.descripcion,
        "life": incident.life,
        "recorded_by": names.get(incident.creado_por, incident.creado_por),
    }


# ---- maintenance tickets -------------------------------------------------------------------

def _tickets_query(db, f):
    query = db.query(TicketMantenimiento, IncidenteHerramienta, Herramienta.tn, Herramienta.categoria).join(
        IncidenteHerramienta, IncidenteHerramienta.id == TicketMantenimiento.incidente_id,
    ).join(Herramienta, Herramienta.id == TicketMantenimiento.herramienta_id)
    query = _between(query, TicketMantenimiento.creado_en, f)
    query = _tool_filter(query, f, TicketMantenimiento.herramienta_id)
    query = _machine_filter(query, f, IncidenteHerramienta.maquina_codigo)
    query = _user_filter(
        query, f, TicketMantenimiento.creado_por, TicketMantenimiento.recibido_por,
        TicketMantenimiento.evaluado_por, TicketMantenimiento.cerrado_por,
    )
    return query.order_by(TicketMantenimiento.id.desc())


def _tickets_row(item, names):
    ticket, incident, tn, category = item
    return {
        "folio": f"MT-{ticket.id:05d}",
        "opened_at": _local(ticket.creado_en),
        "tool": ticket.herramienta_id,
        "tn": tn,
        "category": CATEGORY.get(category, category),
        "state": TICKET_STATE.get(ticket.estado, ticket.estado),
        "result": TICKET_RESULT.get(ticket.resultado, ticket.resultado),
        "machine": incident.maquina_codigo,
        "part_number": incident.numero_parte,
        "station": incident.estacion,
        "operator": names.get(incident.operador, incident.operador),
        "shift": incident.turno,
        "cause": CAUSE.get(incident.causa, incident.causa),
        "reason": ticket.motivo,
        "diagnosis": ticket.diagnostico,
        "work": ticket.trabajo,
        "hours_open": round((ticket.cerrado_en - ticket.creado_en).total_seconds() / 3600, 1) if ticket.cerrado_en else None,
        "request": f"SR-{ticket.solicitud_id:05d}" if ticket.solicitud_id else None,
        "moment": MOMENT.get(incident.momento, incident.momento),
        "reported_as": REQUEST_TYPE.get(incident.reportado_como, incident.reportado_como),
        "technical_cause": CAUSE.get(ticket.causa_tecnica, ticket.causa_tecnica),
        "repairable": None if ticket.reparable is None else _yes(ticket.reparable),
        "opened_by": names.get(ticket.creado_por, ticket.creado_por),
        "received_by": names.get(ticket.recibido_por, ticket.recibido_por),
        "received_at": _local(ticket.recibido_en),
        "evaluated_at": _local(ticket.evaluado_en),
        "closed_by": names.get(ticket.cerrado_por, ticket.cerrado_por),
        "closed_at": _local(ticket.cerrado_en),
    }


# ---- retirements ---------------------------------------------------------------------------

def _retirements_query(db, f):
    query = db.query(BajaHerramienta, IncidenteHerramienta, Herramienta.tn, Herramienta.categoria).join(
        IncidenteHerramienta, IncidenteHerramienta.id == BajaHerramienta.incidente_id,
    ).join(Herramienta, Herramienta.id == BajaHerramienta.herramienta_id)
    query = _between(query, BajaHerramienta.propuesto_en, f)
    query = _tool_filter(query, f, BajaHerramienta.herramienta_id)
    query = _machine_filter(query, f, IncidenteHerramienta.maquina_codigo)
    query = _user_filter(query, f, BajaHerramienta.propuesto_por, BajaHerramienta.resuelto_por)
    return query.order_by(BajaHerramienta.id.desc())


def _retirements_row(item, names):
    retirement, incident, tn, category = item
    return {
        "folio": f"BJ-{retirement.id:05d}",
        "proposed_at": _local(retirement.propuesto_en),
        "tool": retirement.herramienta_id,
        "tn": tn,
        "category": CATEGORY.get(category, category),
        "state": RETIREMENT_STATE.get(retirement.estado, retirement.estado),
        "machine": incident.maquina_codigo,
        "part_number": incident.numero_parte,
        "station": incident.estacion,
        "operator": names.get(incident.operador, incident.operador),
        "shift": incident.turno,
        "cause": CAUSE.get(incident.causa, incident.causa),
        "reason": retirement.motivo,
        "proposed_by": names.get(retirement.propuesto_por, retirement.propuesto_por),
        "resolved_by": names.get(retirement.resuelto_por, retirement.resuelto_por),
        "resolved_at": _local(retirement.resuelto_en),
        "hours_to_decide": (
            round((retirement.resuelto_en - retirement.propuesto_en).total_seconds() / 3600, 1)
            if retirement.resuelto_en else None
        ),
        "decision_note": retirement.nota_resolucion,
        "moment": MOMENT.get(incident.momento, incident.momento),
        "reported_as": REQUEST_TYPE.get(incident.reportado_como, incident.reportado_como),
        "request": f"SR-{retirement.solicitud_id:05d}" if retirement.solicitud_id else None,
        "origin": ORIGIN.get(incident.origen, incident.origen),
    }


# ---- sharpening and repairs ----------------------------------------------------------------

def _services_query(db, f):
    query = db.query(ServicioHerramienta)
    query = _between(query, ServicioHerramienta.fecha, f)
    query = _tool_filter(query, f, ServicioHerramienta.herramienta_id)
    query = _machine_filter(query, f, ServicioHerramienta.maquina_codigo)
    query = _user_filter(query, f, ServicioHerramienta.realizado_por)
    return query.order_by(ServicioHerramienta.id.desc())


def _services_row(service, names):
    return {
        "when": _local(service.fecha),
        "tool": service.herramienta_id,
        "tn": service.tn,
        "category": CATEGORY.get(service.categoria, service.categoria),
        "service": REQUEST_TYPE.get(service.tipo, service.tipo),
        "result": SERVICE_RESULT.get(service.resultado, service.resultado),
        "ground_off": service.rebaje,
        "abnormal": _yes(service.anormal),
        "justification": service.justificacion,
        "machine": service.maquina_codigo,
        "part_number": service.numero_parte,
        "station": service.estacion,
        "by": names.get(service.realizado_por, service.realizado_por),
        "request": f"SR-{service.solicitud_id:05d}" if service.solicitud_id else None,
        "life_before": service.life_antes,
        "life_after": service.life_despues,
        "notes": service.nota,
    }


# ---- part number changes and system audit --------------------------------------------------

def _programs_query(db, f):
    query = db.query(BitacoraPrograma)
    query = _between(query, BitacoraPrograma.fecha, f)
    query = _user_filter(query, f, BitacoraPrograma.usuario)
    return query.order_by(BitacoraPrograma.id.desc())


def _programs_row(entry, names):
    return {
        "when": _local(entry.fecha),
        "part_number": entry.numero_parte,
        "machine_type": entry.tipo_maquina,
        "action": entry.accion,
        "revision": entry.revision,
        "reason": entry.motivo,
        "by": names.get(entry.usuario, entry.usuario),
        "detail": entry.detalle,
    }


def _audit_query(db, f):
    query = db.query(Auditoria)
    query = _between(query, Auditoria.fecha, f)
    query = _user_filter(query, f, Auditoria.usuario)
    return query.order_by(Auditoria.id.desc())


def _audit_row(entry, names):
    return {
        "when": _local(entry.fecha),
        "by": names.get(entry.usuario, entry.usuario),
        "role": entry.rol,
        "action": entry.accion,
        "entity": entry.entidad,
        "entity_id": entry.entidad_id,
        "before": entry.antes,
        "after": entry.despues,
    }


def _cols(*pairs):
    return [Column(key, label, export_only) for key, label, export_only in pairs]


KINDS = {kind.key: kind for kind in (
    Kind("movements", "Tool movements", _cols(
        ("when", "When", False), ("tool", "Tool", False), ("tn", "TN", False), ("category", "Type", False),
        ("from", "From", False), ("to", "To", False), ("movement", "Movement", False), ("by", "By", False),
        ("entry", "Entry", False), ("kit", "Kit", True), ("detail", "Detail", True), ("group", "Group", True),
    ), ("date", "tool", "machine", "user", "group"), _movements_query, _movements_row, MOVEMENT_GROUPS),
    Kind("requests", "Service requests", _cols(
        ("folio", "Request", False), ("requested_at", "Requested", False), ("type", "Type", False), ("state", "Status", False),
        ("machine", "Machine", False), ("part_number", "Part number", False), ("station", "Station", False),
        ("tn", "TN", False), ("category", "Punch / die", False), ("tool", "Tool", False), ("replacement", "Replacement", False),
        ("requested_by", "Requested by", False), ("wait_min", "Minutes waiting for Tool Crib", False),
        ("attended_by", "Attended by", False), ("attention_min", "Minutes of Tool Crib attention", False),
        ("downtime_min", "Minutes without tool", False), ("total_min", "Minutes in total", True),
        ("cause", "Cause", False), ("ticket", "Maintenance", False), ("note", "Note", True), ("destination", "Taken to", True),
        ("attended_at", "Attended", True), ("collected_by", "Collected by", True),
        ("collected_at", "Collected", True), ("received_at", "Machine received", True), ("closed_by", "Closed by", True), ("closed_at", "Closed", True),
        ("outcome", "Resolution or cancel reason", True),
    ), ("date", "tool", "machine", "user"), _requests_query, _requests_row),
    Kind("incidents", "Damage incidents", _cols(
        ("when", "When", False), ("tool", "Tool", False), ("tn", "TN", False), ("category", "Punch / die", False),
        ("machine", "Machine", False), ("part_number", "Part number", False), ("station", "Station", False),
        ("operator", "Operator", False), ("shift", "Shift", False), ("cause", "Cause", False), ("moment", "Moment", False),
        ("reported_as", "Reported as", False), ("found", "Found by Tool Crib", False), ("origin", "Origin", True),
        ("request", "Request", True), ("ticket", "Maintenance", True), ("description", "Description", True),
        ("life", "Tool life", True), ("recorded_by", "Recorded by", True),
    ), ("date", "tool", "machine", "user"), _incidents_query, _incidents_row),
    Kind("tickets", "Maintenance tickets", _cols(
        ("folio", "Ticket", False), ("opened_at", "Opened", False), ("tool", "Tool", False), ("tn", "TN", False),
        ("category", "Punch / die", False), ("state", "Status", False), ("result", "Result", False),
        ("machine", "Machine", False), ("part_number", "Part number", False), ("station", "Station", False),
        ("operator", "Operator", False), ("shift", "Shift", False), ("cause", "Cause", False), ("reason", "Reason", False),
        ("diagnosis", "Diagnosis", False), ("work", "Work done", False), ("hours_open", "Hours open", False),
        ("request", "Request", True), ("moment", "Moment", True), ("reported_as", "Reported as", True),
        ("technical_cause", "Cause per Maintenance", True), ("repairable", "Repairable", True), ("opened_by", "Opened by", True),
        ("received_by", "Received by", True), ("received_at", "Received", True), ("evaluated_at", "Evaluated", True),
        ("closed_by", "Closed by", True), ("closed_at", "Closed", True),
    ), ("date", "tool", "machine", "user"), _tickets_query, _tickets_row),
    Kind("retirements", "Retirements", _cols(
        ("folio", "Retirement", False), ("proposed_at", "Proposed", False), ("tool", "Tool", False), ("tn", "TN", False),
        ("category", "Punch / die", False), ("state", "Status", False), ("machine", "Machine", False),
        ("part_number", "Part number", False), ("station", "Station", False), ("operator", "Operator", False),
        ("shift", "Shift", False), ("cause", "Cause", False), ("reason", "Reason", False),
        ("proposed_by", "Proposed by", False), ("resolved_by", "Decided by", False), ("resolved_at", "Decided", False),
        ("hours_to_decide", "Hours to decide", False), ("decision_note", "Decision note", True),
        ("moment", "Moment", True), ("reported_as", "Reported as", True), ("request", "Request", True),
        ("origin", "Origin", True),
    ), ("date", "tool", "machine", "user"), _retirements_query, _retirements_row),
    Kind("services", "Sharpening and repairs", _cols(
        ("when", "When", False), ("tool", "Tool", False), ("tn", "TN", False), ("category", "Punch / die", False),
        ("service", "Service", False), ("result", "Result", False), ("ground_off", "Ground off (in)", False),
        ("abnormal", "Abnormal", False), ("justification", "Justification", False), ("machine", "Machine", False),
        ("part_number", "Part number", False), ("station", "Station", False), ("by", "By", False),
        ("request", "Request", True), ("life_before", "Life before", True), ("life_after", "Life after", True),
        ("notes", "Notes", True),
    ), ("date", "tool", "machine", "user"), _services_query, _services_row),
    Kind("programs", "Part number changes", _cols(
        ("when", "When", False), ("part_number", "Part number", False), ("machine_type", "Machine type", False),
        ("action", "Action", False), ("revision", "Revision", False), ("reason", "Reason", False), ("by", "By", False),
        ("detail", "Detail", True),
    ), ("date", "user"), _programs_query, _programs_row),
    Kind("audit", "System audit", _cols(
        ("when", "When", False), ("by", "User", False), ("role", "Role", False), ("action", "Action", False),
        ("entity", "Entity", False), ("entity_id", "Entity id", False), ("before", "Before", True), ("after", "After", True),
    ), ("date", "user"), _audit_query, _audit_row),
)}


def _user():
    user = auth.current_user()
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    if "records.view" not in user.permissions:
        raise HTTPException(status_code=403, detail=f"Your role ({user.role}) is not allowed to see the records")
    return user


def _kind(key):
    kind = KINDS.get(key)
    if not kind:
        raise HTTPException(status_code=404, detail="Unknown record")
    return kind


def _names(db):
    return {person.username: (person.full_name or person.username) for person in db.query(Usuario).all()}


def _filters(date_from, date_to, tool, machine, user, group, exact_tool=False):
    return Filters(date_from or None, date_to or None, tool, machine, user, group or None, bool(exact_tool))


def _rows(db, kind, f, limit, offset=0):
    query = kind.query(db, f)
    total = query.count()
    names = _names(db)
    page = query.offset(offset).limit(limit).all()
    return total, [kind.row(item, names) for item in page]


@router.get("")
def list_records():
    _user()
    return [
        {
            "key": kind.key,
            "label": kind.label,
            "filters": list(kind.filters),
            "groups": [{"value": key, "label": label} for key, label in kind.groups.items()],
        }
        for kind in KINDS.values()
    ]


@router.get("/workbook")
def download_workbook(date_from: Optional[str] = None, date_to: Optional[str] = None):
    """One workbook with a sheet per record (movements split by group)."""
    user = _user()
    f = _filters(date_from, date_to, None, None, None, None)
    db = workflow_api.SessionLocal()
    try:
        sheets = []
        for kind in KINDS.values():
            if kind.groups:
                for group in kind.groups:
                    total, rows = _rows(db, kind, Filters(f.date_from, f.date_to, group=group), MAX_EXPORT)
                    sheets.append((_sheet_title(kind, group), kind, rows, total))
            else:
                total, rows = _rows(db, kind, f, MAX_EXPORT)
                sheets.append((_sheet_title(kind), kind, rows, total))
        return _xlsx_response(sheets, "MIRA-records", user, f)
    finally:
        db.close()


@router.get("/{kind_key}")
def get_records(
    kind_key: str,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    tool: Optional[str] = None,
    machine: Optional[str] = None,
    user: Optional[str] = None,
    group: Optional[str] = None,
    exact_tool: bool = False,
    limit: int = 100,
    offset: int = 0,
):
    _user()
    kind = _kind(kind_key)
    f = _filters(date_from, date_to, tool, machine, user, group, exact_tool)
    if kind.groups and not f.group:
        f.group = next(iter(kind.groups))
    db = workflow_api.SessionLocal()
    try:
        total, rows = _rows(db, kind, f, max(1, min(limit, MAX_PAGE)), max(0, offset))
        return {
            "key": kind.key,
            "label": kind.label,
            "group": f.group,
            "columns": [{"key": c.key, "label": c.label, "export_only": c.export_only} for c in kind.columns],
            "rows": rows,
            "total": total,
            "limit": max(1, min(limit, MAX_PAGE)),
            "offset": max(0, offset),
        }
    finally:
        db.close()


@router.get("/{kind_key}/download")
def download_records(
    kind_key: str,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    tool: Optional[str] = None,
    machine: Optional[str] = None,
    user: Optional[str] = None,
    group: Optional[str] = None,
    exact_tool: bool = False,
):
    who = _user()
    kind = _kind(kind_key)
    f = _filters(date_from, date_to, tool, machine, user, group, exact_tool)
    if kind.groups and not f.group:
        f.group = next(iter(kind.groups))
    db = workflow_api.SessionLocal()
    try:
        total, rows = _rows(db, kind, f, MAX_EXPORT)
        return _xlsx_response([(_sheet_title(kind, f.group), kind, rows, total)], f"MIRA-{kind.key}", who, f)
    finally:
        db.close()


def _cell(value):
    """Values typed by people never become formulas in Excel."""
    if isinstance(value, str):
        from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE

        value = ILLEGAL_CHARACTERS_RE.sub("", value)
        if value[:1] in ("=", "+", "-", "@", "\t", "\r"):
            return "'" + value
    return value


def _sheet_title(kind, group=None):
    return f"Mov - {kind.groups.get(group, 'All')}" if kind.groups else kind.label


def _xlsx_response(sheets, base_name, who, f):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter

    workbook = Workbook()
    workbook.remove(workbook.active)
    for title, kind, rows, total in sheets:
        sheet = workbook.create_sheet(title)
        sheet.append([column.label for column in kind.columns])
        for cell in sheet[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="1F3864")
        for row in rows:
            sheet.append([_cell(row.get(column.key)) for column in kind.columns])
        sheet.freeze_panes = "A2"
        if rows:
            sheet.auto_filter.ref = sheet.dimensions
        for index, column in enumerate(kind.columns, start=1):
            longest = max([len(str(column.label))] + [len(str(row.get(column.key) or "")) for row in rows[:300]])
            sheet.column_dimensions[get_column_letter(index)].width = min(max(longest + 2, 8), 50)
    info = workbook.create_sheet("Info")
    stamp = datetime.now().astimezone()
    info.append(["Exported", stamp.strftime("%Y-%m-%d %H:%M")])
    info.append(["Time zone", stamp.tzname()])
    info.append(["Exported by", who.username])
    info.append(["From", f.date_from or "start"])
    info.append(["To", f.date_to or "today"])
    for key, value in (("Tool", f.tool), ("Machine", f.machine), ("User", f.user)):
        if value:
            info.append([key, value])
    for title, _kind_, rows, total in sheets:
        info.append([f"{title} rows", len(rows) if total <= len(rows) else f"{len(rows)} of {total}"])
    info.column_dimensions["A"].width = 28
    info.column_dimensions["B"].width = 34
    buffer = io.BytesIO()
    workbook.save(buffer)
    name = f"{base_name}-{stamp.strftime('%Y%m%d-%H%M')}.xlsx"
    return Response(
        content=buffer.getvalue(),
        media_type=XLSX,
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )
