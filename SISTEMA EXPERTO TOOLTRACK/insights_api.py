"""Management dashboard: indicators computed from what the system already records.

Everything here is read-only and derived (no new data is captured): tool-related machine downtime, Tool Crib
response, damage analysis, tool health, Maintenance, kitting and the production plan. Each indicator is compared
with the previous period of the same length so a trend is visible at a glance.
"""
from collections import Counter, defaultdict
from datetime import timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException
from sqlalchemy import or_

import auth
import records_api
import shifts
import tool_params
import workflow_api
from models.inventory import (
    BajaHerramienta,
    DemandaPlaneacion,
    Herramienta,
    IncidenteHerramienta,
    Maquina,
    ServicioHerramienta,
    SolicitudServicio,
    Surtido,
    TicketMantenimiento,
    Ubicacion,
)

router = APIRouter(prefix="/insights", tags=["insights"])

RESPONSE_TARGET_MIN = 15
MIN_PREVIOUS_SAMPLE = 5
NON_TOOL_REQUESTS = ("program_problem", "part_request")
TOP = 8
LEVELS = (
    ("ROJO", "Critical (< 2 h)"), ("AMARILLO", "Watch (2-4 h)"), ("VERDE", "Healthy (> 4 h)"),
    ("PARO", "Scheduled stop"), ("SIN_COLA", "No demand"),
)
STATUS_LABEL = {"disponible": "Available", "afilado": "Being sharpened", "danado": "Damaged", "baja": "Retired"}


def _minutes(start, end):
    if not start or not end:
        return None
    return max(0.0, (end - start).total_seconds() / 60)


def _avg(values):
    values = [value for value in values if value is not None]
    return round(sum(values) / len(values), 1) if values else None


def _delta(current, previous):
    if current is None or previous in (None, 0):
        return None
    return round((current - previous) / previous * 100, 1)


def _ranked(counter, labels=None, limit=TOP):
    return [
        {"key": key, "label": (labels or {}).get(key, key) or "Unknown", "value": value}
        for key, value in counter.most_common(limit)
    ]


def _kpi(key, label, current, previous, unit="", good="down", note=None, digits=1, prev_n=None):
    """prev_n: how many observations the previous value rests on; a trend on a handful of cases is not shown."""
    if prev_n is None:
        prev_n = previous
    comparable = previous is not None and prev_n is not None and prev_n >= MIN_PREVIOUS_SAMPLE
    return {
        "key": key, "label": label, "unit": unit, "good": good, "note": note,
        "value": round(current, digits) if isinstance(current, float) else current,
        "previous": round(previous, digits) if isinstance(previous, float) else previous,
        "delta_pct": _delta(current, previous) if comparable else None,
    }


class _Window:
    def __init__(self, start, end):
        self.start, self.end = start, end

    def has(self, moment):
        return moment is not None and self.start <= moment < self.end


def _stats(requests, incidents, services, tickets, retirements, kits, window):
    """Indicators of one period."""
    tool = [row for row in requests if row.tipo not in NON_TOOL_REQUESTS and window.has(row.solicitado_en)]
    positions = [row for row in tool if row.requisito_id is not None]
    downtime = [_minutes(row.solicitado_en, row.recibida_en) for row in positions if row.recibida_en]
    waits = [_minutes(row.solicitado_en, row.atendido_en) for row in tool if row.atendido_en]
    waits = [value for value in waits if value is not None]
    closed_tickets = [row for row in tickets if window.has(row.cerrado_en)]
    delivered = [row for row in kits if row.estado == "delivered" and window.has(row.entregado_en)]
    cut = [row for row in services if window.has(row.fecha)]
    return {
        "tool_requests": len(tool),
        "downtime_h": round(sum(downtime) / 60, 1),
        "downtime_n": len(downtime),
        "avg_response": _avg(waits),
        "waits_n": len(waits),
        "on_target": round(sum(1 for value in waits if value <= RESPONSE_TARGET_MIN) / len(waits) * 100, 1) if waits else None,
        "incidents": sum(1 for row in incidents if window.has(row.creado_en)),
        "retired": sum(1 for row in retirements if row.estado == "approved" and window.has(row.resuelto_en)),
        "kits": len(delivered),
        "kit_minutes": _avg([_minutes(row.creado_en, row.entregado_en) for row in delivered]),
        "sharpenings": sum(1 for row in cut if row.tipo == "sharpening"),
        "ground_off": round(sum(row.rebaje or 0 for row in cut), 3),
        "abnormal": sum(1 for row in cut if row.anormal),
        "tickets_closed": len(closed_tickets),
        "ticket_days": _avg([_minutes(row.creado_en, row.cerrado_en) / 1440 for row in closed_tickets]),
    }


def _by_day(days, local_end, *series):
    """One row per local day with the count of each series (each is a list of datetimes)."""
    first = local_end.date() - timedelta(days=days - 1)
    rows = {first + timedelta(days=i): [0] * len(series) for i in range(days)}
    for index, moments in enumerate(series):
        for moment in moments:
            if moment is None:
                continue
            day = shifts._local(moment).date()
            if day in rows:
                rows[day][index] += 1
    return rows


def build_overview(db, days):
    now = workflow_api.utcnow()
    current = _Window(now - timedelta(days=days), now + timedelta(minutes=1))
    previous = _Window(now - timedelta(days=days * 2), now - timedelta(days=days))
    since = previous.start

    requests = db.query(SolicitudServicio).filter(SolicitudServicio.solicitado_en >= since).all()
    incidents = db.query(IncidenteHerramienta).filter(IncidenteHerramienta.creado_en >= since).all()
    services = db.query(ServicioHerramienta).filter(ServicioHerramienta.fecha >= since).all()
    tickets = db.query(TicketMantenimiento).filter(
        or_(TicketMantenimiento.creado_en >= since, TicketMantenimiento.cerrado_en >= since)
    ).all()
    retirements = db.query(BajaHerramienta).filter(
        or_(BajaHerramienta.propuesto_en >= since, BajaHerramienta.resuelto_en >= since)
    ).all()
    kits = db.query(Surtido).filter(or_(Surtido.creado_en >= since, Surtido.entregado_en >= since)).all()

    a = _stats(requests, incidents, services, tickets, retirements, kits, current)
    b = _stats(requests, incidents, services, tickets, retirements, kits, previous)

    tools = db.query(Herramienta).all()
    params = tool_params.load(db)
    watch = defaultdict(lambda: {"at_minimum": [], "healthy": 0})
    for tool in tools:
        if (tool.estado or "disponible") == "baja":
            continue
        entry = watch[(tool.tn, tool.categoria)]
        if tool_params.life_alert(tool.categoria, tool.life, params):
            entry["at_minimum"].append(tool.id)
        else:
            entry["healthy"] += 1
    purchase_watch = sorted(
        (
            {
                "tn": tn, "category": category, "at_minimum": len(entry["at_minimum"]),
                "healthy": entry["healthy"], "tool_ids": sorted(entry["at_minimum"])[:6],
            }
            for (tn, category), entry in watch.items() if entry["at_minimum"]
        ),
        key=lambda item: (item["healthy"], -item["at_minimum"], item["tn"] or ""),
    )
    locations = {row.codigo: row.tipo for row in db.query(Ubicacion).all()}
    status = Counter((tool.estado or "disponible") for tool in tools)
    in_service = Counter(
        {"togu": "TOGU (sharpening)", "maintenance": "Maintenance", "workshop": "Workshop", "machine": "On a machine"}[locations[tool.ubicacion]]
        for tool in tools if locations.get(tool.ubicacion) in ("togu", "maintenance", "workshop", "machine")
        and (tool.estado or "disponible") != "baja"
    )
    available_pct = round(status.get("disponible", 0) / len(tools) * 100, 1) if tools else None

    cur_requests = [row for row in requests if current.has(row.solicitado_en) and row.tipo not in NON_TOOL_REQUESTS]
    cur_incidents = [row for row in incidents if current.has(row.creado_en)]
    cur_services = [row for row in services if current.has(row.fecha)]
    cur_tickets = [row for row in tickets if current.has(row.creado_en)]
    cur_closed = [row for row in tickets if row.estado == "closed" and current.has(row.cerrado_en)]

    downtime_by_machine = defaultdict(lambda: [0.0, 0])
    for row in cur_requests:
        if row.requisito_id is not None and row.recibida_en:
            entry = downtime_by_machine[row.maquina_codigo]
            entry[0] += _minutes(row.solicitado_en, row.recibida_en) / 60
            entry[1] += 1
    downtime_rows = sorted(
        ({"key": code, "label": code, "value": round(hours, 1), "count": count} for code, (hours, count) in downtime_by_machine.items()),
        key=lambda item: -item["value"],
    )[:TOP]

    waits_by_shift = defaultdict(list)
    for row in cur_requests:
        if row.atendido_en:
            waits_by_shift[shifts.shift_for(row.solicitado_en)].append(_minutes(row.solicitado_en, row.atendido_en))
    response_by_shift = [
        {"key": name, "label": name, "value": _avg(waits_by_shift[name]), "count": len(waits_by_shift[name])}
        for name in ("Turno 1", "Turno 2", "Turno 3") if waits_by_shift.get(name)
    ]

    cause = Counter(row.causa or "unknown" for row in cur_incidents)
    pareto_total = sum(cause.values())
    running = 0
    damage_by_cause = []
    for item in _ranked(cause, records_api.CAUSE, limit=10):
        running += item["value"]
        item["cumulative"] = round(running / pareto_total * 100, 1) if pareto_total else 0
        damage_by_cause.append(item)

    shift_labels = {"Turno 1": "Turno 1", "Turno 2": "Turno 2", "Turno 3": "Turno 3"}
    damage_by_shift = _ranked(Counter(row.turno or "Unknown" for row in cur_incidents), shift_labels)

    per_tool = defaultdict(lambda: {"services": 0, "ground_off": 0.0, "abnormal": 0, "incidents": 0})
    for row in cur_services:
        entry = per_tool[row.herramienta_id]
        entry["services"] += 1
        entry["ground_off"] += row.rebaje or 0
        entry["abnormal"] += 1 if row.anormal else 0
    for row in cur_incidents:
        per_tool[row.herramienta_id]["incidents"] += 1
    by_id = {tool.id: tool for tool in tools}
    attention = sorted(per_tool.items(), key=lambda pair: (-(pair[1]["incidents"] * 2 + pair[1]["services"]), pair[0]))[:TOP]
    tools_attention = [
        {
            "tool_id": tool_id, "tn": by_id[tool_id].tn if tool_id in by_id else None,
            "category": by_id[tool_id].categoria if tool_id in by_id else None,
            "status": (by_id[tool_id].estado or "disponible") if tool_id in by_id else None,
            "life": by_id[tool_id].life if tool_id in by_id else None,
            "services": data["services"], "ground_off": round(data["ground_off"], 3),
            "abnormal": data["abnormal"], "incidents": data["incidents"],
        }
        for tool_id, data in attention
    ]

    open_tickets = db.query(TicketMantenimiento).filter(TicketMantenimiento.estado != "closed").all()
    maintenance = {
        "opened": len(cur_tickets),
        "closed": a["tickets_closed"],
        "open_now": len(open_tickets),
        "oldest_open_days": round(max((_minutes(row.creado_en, now) / 1440 for row in open_tickets), default=0), 1),
        "avg_days": a["ticket_days"],
        "results": _ranked(Counter(row.resultado or "pending" for row in cur_closed), {"released": "Repaired and released", "unrepairable": "Unrepairable", "pending": "Open"}),
    }

    delivered = [row for row in kits if row.estado == "delivered" and current.has(row.entregado_en)]
    kit_machines = Counter(row.maquina_codigo for row in delivered)
    kits_summary = {
        "delivered": len(delivered),
        "avg_minutes": a["kit_minutes"],
        "cancelled": sum(1 for row in kits if row.estado == "cancelled" and current.has(row.creado_en)),
        "open_now": db.query(Surtido).filter(Surtido.estado.in_(("preparing", "paused", "ready"))).count(),
        "by_machine": _ranked(kit_machines),
    }

    daily_rows = _by_day(
        days, shifts._local(now),
        [row.solicitado_en for row in cur_requests], [row.creado_en for row in cur_incidents],
        [row.entregado_en for row in delivered],
    )
    daily = [
        {"date": day.isoformat(), "requests": values[0], "incidents": values[1], "kits": values[2]}
        for day, values in daily_rows.items()
    ]

    plan = _plan(db)

    kpis = [
        _kpi("downtime", "Machine hours lost waiting for a tool", a["downtime_h"], b["downtime_h"], "h", "down", f"{a['downtime_n']} positions without a tool", prev_n=b["downtime_n"]),
        _kpi("response", "Tool Crib average response", a["avg_response"], b["avg_response"], "min", "down", "from request to a person on it", prev_n=b["waits_n"]),
        _kpi("on_target", f"Attended within {RESPONSE_TARGET_MIN} min", a["on_target"], b["on_target"], "%", "up", prev_n=b["waits_n"]),
        _kpi("requests", "Tool requests handled", a["tool_requests"], b["tool_requests"], "", None),
        _kpi("incidents", "Damage incidents", a["incidents"], b["incidents"], "", "down"),
        _kpi("retired", "Tools retired", a["retired"], b["retired"], "", None),
        _kpi("kits", "Kits delivered", a["kits"], b["kits"], "", "up", f"avg {a['kit_minutes']} min to deliver" if a["kit_minutes"] is not None else None),
        _kpi("availability", "Tools available now", available_pct, None, "%", "up", f"{status.get('disponible', 0)} of {len(tools)} tools"),
        _kpi("abnormal", "Abnormal grinding", a["abnormal"], b["abnormal"], "", "down", f"{a['sharpenings']} sharpenings, {a['ground_off']} in. removed", prev_n=b["sharpenings"]),
        _kpi("maintenance_days", "Maintenance turnaround", a["ticket_days"], b["ticket_days"], "days", "down", f"{a['tickets_closed']} tickets closed", prev_n=b["tickets_closed"]),
    ]

    machine_incidents = _ranked(Counter(row.maquina_codigo or "Tool Crib" for row in cur_incidents))
    result = {
        "period": {"days": days, "from": shifts._local(current.start).date().isoformat(), "to": shifts._local(now).date().isoformat()},
        "kpis": kpis,
        "daily": daily,
        "downtime_by_machine": downtime_rows,
        "response_by_shift": response_by_shift,
        "request_mix": _ranked(Counter(row.tipo for row in cur_requests), records_api.REQUEST_TYPE),
        "damage_by_cause": damage_by_cause,
        "damage_by_machine": machine_incidents,
        "damage_by_shift": damage_by_shift,
        "damage_by_moment": _ranked(Counter(row.momento or "unknown" for row in cur_incidents), records_api.MOMENT),
        "damage_by_part": _ranked(Counter(row.numero_parte for row in cur_incidents if row.numero_parte)),
        "damage_by_tn": _ranked(Counter(row.tn for row in cur_incidents if row.tn)),
        "inventory": {
            "total": len(tools),
            "status": [{"key": key, "label": STATUS_LABEL.get(key, key), "value": value} for key, value in status.most_common()],
            "in_service": [{"key": key, "label": key, "value": value} for key, value in in_service.most_common()],
        },
        "tools_attention": tools_attention,
        "purchase_watch": {
            "at_minimum": sum(item["at_minimum"] for item in purchase_watch),
            "without_stock": sum(1 for item in purchase_watch if item["healthy"] == 0),
            "limits": {"punch_min_life": float(params["punch_min_life"]), "die_min_height": float(params["die_min_height"])},
            "rows": purchase_watch[:TOP],
        },
        "maintenance": maintenance,
        "kits": kits_summary,
        "plan": plan,
    }
    result["insights"] = _insights(result, a, b)
    return result


def _plan(db):
    """Production plan state: Board levels and progress of the planned demand."""
    import demandas_api

    levels = Counter()
    machines = []
    hours = 0.0
    for (edificio,) in db.query(Maquina.edificio).distinct().all():
        board = demandas_api.demandas_board(edificio or "B1")
        for machine in board["maquinas"]:
            levels[machine["nivel"]] += 1
            hours += machine["hr_cola"] or 0
            machines.append({"key": machine["maquina"], "label": machine["maquina"], "value": machine["hr_cola"] or 0})
    demands = db.query(DemandaPlaneacion).filter(DemandaPlaneacion.activa.is_(True)).all()
    planned = sum(row.cantidad for row in demands)
    produced = sum(min(row.piezas_producidas, row.cantidad) for row in demands)
    machines.sort(key=lambda item: -item["value"])
    return {
        "levels": [{"key": key, "label": label, "value": levels.get(key, 0)} for key, label in LEVELS],
        "machines_total": sum(levels.values()),
        "queue_hours": round(hours, 1),
        "completion_pct": round(produced / planned * 100, 1) if planned else None,
        "planned_pieces": planned,
        "produced_pieces": produced,
        "demands": len(demands),
        "queue_by_machine": [{**item, "value": round(item["value"], 1)} for item in machines[:TOP]],
    }


def _share(items):
    total = sum(item["value"] for item in items)
    return (items[0], round(items[0]["value"] / total * 100)) if items and total else (None, 0)


def _insights(data, a, b):
    """Plain-language findings for the top of the dashboard."""
    notes = []
    if a["downtime_n"]:
        avg = round(a["downtime_h"] * 60 / a["downtime_n"])
        trend = ""
        change = _delta(a["downtime_h"], b["downtime_h"]) if b["downtime_n"] >= MIN_PREVIOUS_SAMPLE else None
        if change is not None:
            trend = f" ({'+' if change > 0 else ''}{change}% vs the previous {data['period']['days']} days)"
        notes.append(f"Machines lost {a['downtime_h']} h waiting for tools across {a['downtime_n']} positions, {avg} min each on average{trend}.")
    top, share = _share(data["downtime_by_machine"])
    if top and len(data["downtime_by_machine"]) > 1 and share >= 25:
        notes.append(f"{top['label']} concentrates {share}% of that downtime.")
    if a["on_target"] is not None:
        notes.append(f"{a['on_target']}% of requests were taken within {RESPONSE_TARGET_MIN} minutes (average {a['avg_response']} min).")
    shifts_with_data = [item for item in data["response_by_shift"] if item["value"] is not None]
    if len(shifts_with_data) > 1:
        slow = max(shifts_with_data, key=lambda item: item["value"])
        fast = min(shifts_with_data, key=lambda item: item["value"])
        if fast["value"] and slow["value"] >= fast["value"] * 1.3 and slow["value"] - fast["value"] >= 2:
            notes.append(f"{slow['label']} answers in {slow['value']} min on average versus {fast['value']} min in {fast['label']}.")
    top, share = _share(data["damage_by_cause"])
    if top:
        notes.append(f"{top['label']} is the leading cause of damage ({share}% of {a['incidents']} incidents).")
    top, share = _share(data["damage_by_machine"])
    if top and len(data["damage_by_machine"]) > 1 and share >= 30:
        notes.append(f"{top['label']} has {share}% of the damage reports.")
    if a["abnormal"]:
        notes.append(f"{a['abnormal']} sharpening(s) removed more than the normal amount of material and were justified one by one.")
    watch = data["purchase_watch"]
    if watch["at_minimum"]:
        notes.append(
            f"{watch['at_minimum']} tool(s) reached their minimum life or height; {watch['without_stock']} TN(s) have no healthy stock left: plan the next purchase."
        )
    if data["maintenance"]["open_now"]:
        notes.append(f"{data['maintenance']['open_now']} tool(s) are in Maintenance now; the oldest has waited {data['maintenance']['oldest_open_days']} days.")
    plan = data["plan"]
    critical = next((item["value"] for item in plan["levels"] if item["key"] == "ROJO"), 0)
    if plan["machines_total"]:
        notes.append(f"Critical machines on the production plan: {critical} of {plan['machines_total']}; {plan['queue_hours']} machine-hours are queued.")
    return notes


@router.get("/overview")
def overview(days: int = 30):
    if days not in (7, 14, 30, 60, 90, 180, 365):
        raise HTTPException(status_code=422, detail="Choose 7, 14, 30, 60, 90, 180 or 365 days")
    if auth.current_user() is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    db = workflow_api.SessionLocal()
    try:
        return build_overview(db, days)
    finally:
        db.close()
