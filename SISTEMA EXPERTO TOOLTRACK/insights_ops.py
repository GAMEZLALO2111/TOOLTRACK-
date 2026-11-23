"""Operations dashboard: plan attainment, machine utilisation, tool strokes, Clear to build and traceability.

Read-only and derived from what MIRA already records (production records, forecast, machine availability history,
stroke ledger, movements). It extends the management dashboard (insights_api) with the sections that show the scope
of MIRA beyond Tool Crib.
"""
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException
from sqlalchemy import func

import auth
import ctb_api
import demandas_api
import planning_api
import records_api
import shifts
import strokes_api
import workflow_api
from insights_api import _kpi
from models.inventory import (
    FgPlaneacion,
    ForecastSemana,
    GolpeHerramienta,
    Herramienta,
    LimiteGolpes,
    Maquina,
    MaquinaDisponibilidad,
    MovimientoHerramienta,
    RegistroProduccion,
    SolicitudServicio,
    Usuario,
)

router = APIRouter(prefix="/insights", tags=["insights"])

TOP = 8
TARGET_MIN = 15
SHIFT_NAMES = ("Turno 1", "Turno 2", "Turno 3")
CAPTURE_LABEL = {"scan": "Scanner", "manual": "Typed by hand", "camera": "Camera", None: "Not a physical scan"}


def _local_date(moment):
    return shifts._local(moment).date()


def _monday(day):
    return day - timedelta(days=day.weekday())


def _datum(key, label, value, count=None):
    row = {"key": key, "label": label, "value": value}
    if count is not None:
        row["count"] = count
    return row


def _window(days):
    now = workflow_api.utcnow()
    return now, now - timedelta(days=days), now - timedelta(days=days * 2)


def _production(db, now, start, prev_start, hpp, machine_type, days):
    rows = db.query(RegistroProduccion).filter(
        RegistroProduccion.fecha_produccion >= prev_start, RegistroProduccion.fuente != "saldo_inicial").all()
    current = [r for r in rows if r.fecha_produccion >= start]
    previous = [r for r in rows if r.fecha_produccion < start]
    pieces = sum(r.piezas for r in current)
    by_shift = Counter()
    by_part = Counter()
    daily = defaultdict(int)
    by_week_part = defaultdict(int)
    run_hours = defaultdict(float)
    for r in current:
        by_shift[r.turno or shifts.shift_for(r.fecha_produccion)] += r.piezas
        by_part[r.numero_parte] += r.piezas
        day = _local_date(r.fecha_produccion)
        daily[day] += r.piezas
        by_week_part[(_monday(day), r.numero_parte.upper())] += r.piezas
        value = hpp.get((r.numero_parte.upper(), machine_type.get(r.maquina_codigo)))
        if value:
            run_hours[r.maquina_codigo] += r.piezas * value
    first_day = _local_date(start)
    today = _local_date(now)
    series = [
        {"date": (first_day + timedelta(days=i)).isoformat(), "pieces": daily.get(first_day + timedelta(days=i), 0)}
        for i in range((today - first_day).days + 1)
    ]

    # Plan attainment: complete FG sets built against the forecast, per week and per FG (only the days of the period).
    fgs = db.query(FgPlaneacion).filter(FgPlaneacion.activa.is_(True)).order_by(FgPlaneacion.numero_fg).all()
    forecast = defaultdict(dict)
    for row in db.query(ForecastSemana).filter(ForecastSemana.semana >= _monday(first_day),
                                               ForecastSemana.semana <= _monday(today)):
        forecast[row.fg_id][row.semana] = row.cantidad
    weeks, by_fg = defaultdict(lambda: [0.0, 0.0]), []
    for fg in fgs:
        bom = planning_api._bom_rows(db, fg)
        if not bom:
            continue
        planned_total = produced_total = 0.0
        set_ratios, piece_planned, piece_made = [], 0.0, 0.0
        limiting = {}
        for week, qty in sorted(forecast[fg.id].items()):
            overlap = sum(1 for i in range(7) if first_day <= week + timedelta(days=i) <= today)
            if overlap < 4:  # a week with only a couple of days in the period says nothing about attainment
                continue
            planned = qty * overlap / 7
            if planned <= 0:
                continue
            made = {row.numero_parte.upper(): by_week_part.get((week, row.numero_parte.upper()), 0) for row in bom}
            ratios = {row.numero_parte.upper(): made[row.numero_parte.upper()] / row.piezas_por_fg for row in bom}
            produced = min(ratios.values())
            plan_pieces = sum(planned * row.piezas_por_fg for row in bom)
            done_pieces = sum(min(made[row.numero_parte.upper()], planned * row.piezas_por_fg) for row in bom)
            weeks[week][0] += plan_pieces
            weeks[week][1] += done_pieces
            piece_planned += plan_pieces
            piece_made += done_pieces
            planned_total += planned
            produced_total += min(produced, planned)
            set_ratios.append(min(produced, planned) / planned)
            worst = min(ratios, key=ratios.get)
            limiting[worst] = limiting.get(worst, 0) + 1
        if planned_total:
            by_fg.append({
                "fg": fg.numero_fg, "customer": fg.cliente, "planned": round(planned_total), "produced": round(produced_total),
                "pct": round(piece_made / piece_planned * 100, 1), "sets_pct": round(produced_total / planned_total * 100, 1),
                "limiting_part": max(limiting, key=limiting.get) if limiting else None,
            })
    week_rows = [
        {"week": week.isoformat(), "planned": round(planned), "produced": round(produced),
         "pct": round(produced / planned * 100, 1) if planned else None}
        for week, (planned, produced) in sorted(weeks.items())
    ]
    planned_all = sum(p for p, _ in weeks.values())
    produced_all = sum(q for _, q in weeks.values())
    attainment = round(produced_all / planned_all * 100, 1) if planned_all else None
    try:
        capacity = planning_api.build_capacity(db, 4)
        outlook = [{"week": week, "status": item["status"], "load_pct": round(item["utilization"] * 100, 1),
                    "capacity_h": item["capacity_h"], "load_h": item["load_h"], "late_pieces": item["late_pieces"]}
                   for week, item in zip(capacity["weeks"], capacity["summary"])]
    except Exception:  # the outlook is a nice-to-have; never break the dashboard for it
        outlook = []
    return {
        "pieces": pieces, "previous_pieces": sum(r.piezas for r in previous), "previous_n": len(previous),
        "by_shift": [_datum(name, name, by_shift.get(name, 0)) for name in SHIFT_NAMES],
        "daily": series, "top_parts": [_datum(p, p, v) for p, v in by_part.most_common(TOP)],
        "weeks": week_rows, "attainment_pct": attainment, "by_fg": sorted(by_fg, key=lambda r: r["pct"]),
        "outlook": outlook,
    }, run_hours


def _disabled_hours(db, start, now):
    """Hours each machine spent disabled inside the period, from the availability history."""
    events = defaultdict(list)
    for row in db.query(MaquinaDisponibilidad).order_by(MaquinaDisponibilidad.fecha).all():
        events[row.maquina_codigo].append(row)
    hours, reasons, spells = defaultdict(float), {}, defaultdict(int)
    for code, rows in events.items():
        opened = None
        for row in rows + [None]:
            if row is not None and row.accion == "disabled":
                opened, reasons[code] = row, row
                continue
            end = row.fecha if row is not None else now
            if opened is not None:
                begin, finish = max(opened.fecha, start), min(end, now)
                if finish > begin:
                    hours[code] += (finish - begin).total_seconds() / 3600
                    spells[code] += 1
                opened = None
    return hours, spells


def _utilization(db, now, start, days, run_hours, machines):
    disabled, spells = _disabled_hours(db, start, now)
    waits = defaultdict(float)
    for row in db.query(SolicitudServicio).filter(
            SolicitudServicio.solicitado_en >= start, SolicitudServicio.requisito_id.isnot(None),
            SolicitudServicio.recibida_en.isnot(None), SolicitudServicio.automatico.is_(False)).all():
        waits[row.maquina_codigo] += max(0.0, (row.recibida_en - row.solicitado_en).total_seconds() / 3600)
    rows = []
    for machine in machines:
        if not machine["type"] or not machine["included"]:
            continue
        code = machine["code"]
        capacity = machine["hours_week"] * days / 7
        off = min(capacity, disabled.get(code, 0) / 24 * (machine["hours_week"] / 7))
        wait = min(waits.get(code, 0), capacity - off)
        run = min(run_hours.get(code, 0), max(capacity - off - wait, 0))
        available = capacity - off
        rows.append({
            "code": code, "type": machine["type"], "capacity_h": round(capacity, 1), "run_h": round(run, 1),
            "tool_wait_h": round(wait, 1), "disabled_h": round(off, 1), "idle_h": round(max(available - wait - run, 0), 1),
            "util_pct": round(run / available * 100, 1) if available > 0 else None, "spells": spells.get(code, 0),
        })
    total = {k: round(sum(r[k] for r in rows), 1) for k in ("capacity_h", "run_h", "tool_wait_h", "disabled_h", "idle_h")}
    available = total["capacity_h"] - total["disabled_h"]
    now_off = []
    for profile in db.query(Maquina).filter(Maquina.deshabilitada.is_(True)).all():
        now_off.append({
            "code": profile.codigo, "kind": profile.deshab_tipo, "reason": profile.deshab_motivo,
            "since": profile.deshab_desde.isoformat() if profile.deshab_desde else None,
            "expected_back": profile.regreso_estimado.isoformat() if profile.regreso_estimado else None,
        })
    return {
        "fleet": {**total, "util_pct": round(total["run_h"] / available * 100, 1) if available > 0 else None},
        "split": [_datum("run", "Running", total["run_h"]), _datum("wait", "Waiting for a tool", total["tool_wait_h"]),
                  _datum("off", "Disabled", total["disabled_h"]), _datum("idle", "Idle", total["idle_h"])],
        "machines": sorted(rows, key=lambda r: -(r["util_pct"] or 0)), "disabled_now": now_off,
        "disabled_events": sum(spells.values()),
    }


def _strokes(db, now, start):
    rows = db.query(GolpeHerramienta).filter(GolpeHerramienta.fecha >= start, GolpeHerramienta.origen == "produccion").all()
    daily, by_tn = defaultdict(float), Counter()
    tool_tn = {t.id: t.tn for t in db.query(Herramienta.id, Herramienta.tn).filter(
        Herramienta.id.in_({r.herramienta_id for r in rows} or {""})).all()}
    for row in rows:
        daily[_local_date(row.fecha)] += row.golpes
        by_tn[tool_tn.get(row.herramienta_id) or "?"] += row.golpes
    first_day = _local_date(start)
    series = [{"date": (first_day + timedelta(days=i)).isoformat(), "strokes": round(daily.get(first_day + timedelta(days=i), 0))}
              for i in range((_local_date(now) - first_day).days + 1)]
    limits = {(r.tn, r.categoria): r for r in db.query(LimiteGolpes).all()}
    tools = db.query(Herramienta).filter(Herramienta.golpes_servicio > 0).all()
    sharpenings = strokes_api._sharpenings(db, [t.id for t in tools])
    levels = Counter()
    for tool in tools:
        levels[strokes_api.evaluate(tool, limits.get((tool.tn, tool.categoria)), sharpenings.get(tool.id, 0))["level"]] += 1
    auto = db.query(SolicitudServicio).filter(SolicitudServicio.automatico.is_(True), SolicitudServicio.solicitado_en >= start).all()
    waits = [max(0.0, (r.atendido_en - r.solicitado_en).total_seconds() / 60) for r in auto if r.atendido_en]
    by_shift = defaultdict(list)
    for r in auto:
        if r.atendido_en:
            by_shift[shifts.shift_for(r.solicitado_en)].append(max(0.0, (r.atendido_en - r.solicitado_en).total_seconds() / 60))
    return {
        "total": round(sum(daily.values())), "daily": series,
        "top_tn": [_datum(tn, tn, round(v)) for tn, v in by_tn.most_common(TOP)],
        "levels": [
            _datum("ok", "Healthy", levels.get("ok", 0)), _datum("warning", "Near limit", levels.get("warning", 0)),
            _datum("due", "Limit reached", levels.get("due", 0)), _datum("none", "No limit set", levels.get("none", 0)),
        ],
        "auto_opened": len(auto), "auto_closed": sum(1 for r in auto if r.estado == "closed"),
        "auto_open_now": sum(1 for r in auto if r.estado in ("open", "attending", "collected", "replaced")),
        "auto_avg_response": round(sum(waits) / len(waits), 1) if waits else None,
        "auto_on_target": round(sum(1 for w in waits if w <= TARGET_MIN) / len(waits) * 100, 1) if waits else None,
        "auto_by_shift": [_datum(n, n, round(sum(v) / len(v), 1), len(v)) for n, v in sorted(by_shift.items())],
        "tools_tracked": len(tools), "limits_set": len(limits),
    }


def _ctb(db):
    today = _local_date(workflow_api.utcnow())
    week = _monday(today)
    machines = [m for m in planning_api._machine_rows(db) if m["type"] and m["included"] and not m["disabled"]]
    available = Counter(m["type"] for m in machines)
    rows = []
    for fg in db.query(FgPlaneacion).filter(FgPlaneacion.activa.is_(True)).order_by(FgPlaneacion.numero_fg).all():
        qty = db.query(ForecastSemana.cantidad).filter(ForecastSemana.fg_id == fg.id, ForecastSemana.semana >= week).order_by(
            ForecastSemana.semana).limit(1).scalar()
        if not qty:
            continue
        try:
            wide = ctb_api.build_ctb(db, fg.numero_fg, qty, dict(available), _alt=True)
            needed = {k: v for k, v in wide["pillars"]["machines"]["minimum"].items() if v and available.get(k)} or {
                k: 1 for k in list(available)[:1]}
            result = ctb_api.build_ctb(db, fg.numero_fg, qty, needed, _alt=True)
            # The minimum is an estimate; add machines until the hours really fit (never more than the enabled ones).
            for _ in range(4):
                extra = result["pillars"]["machines"]["extra"]
                if not extra:
                    break
                grown = {k: min(available.get(k, 0), needed.get(k, 0) + extra.get(k, 0)) for k in set(needed) | set(extra)}
                if grown == needed:
                    break
                needed = grown
                result = ctb_api.build_ctb(db, fg.numero_fg, qty, needed, _alt=True)
        except HTTPException:
            continue
        rows.append({
            "fg": fg.numero_fg, "customer": fg.cliente, "weekly": qty, "verdict": result["verdict"],
            "machines": needed, "programs": result["pillars"]["programs"], "max_load_pct": result["pillars"]["machines"]["max_load_pct"],
            "units_to_buy": result["pillars"]["tools"]["units_to_buy"], "distinct_tn": result["pillars"]["tools"]["distinct_tn"],
            "at_minimum": result["pillars"]["tools"]["at_minimum"],
        })
    order = {"not_ready": 0, "risk": 1, "ready": 2}
    rows.sort(key=lambda r: (order[r["verdict"]], r["fg"]))
    return {
        "rows": rows, "ready": sum(1 for r in rows if r["verdict"] == "ready"),
        "risk": sum(1 for r in rows if r["verdict"] == "risk"), "not_ready": sum(1 for r in rows if r["verdict"] == "not_ready"),
        "units_to_buy": sum(r["units_to_buy"] for r in rows), "distinct_tn": sum(r["distinct_tn"] for r in rows),
    }


def _traceability(db, now, start, prev_start):
    moves = db.query(MovimientoHerramienta).filter(MovimientoHerramienta.creado_en >= prev_start).all()
    current = [m for m in moves if m.creado_en >= start]
    names = {u.username: (u.full_name or u.username) for u in db.query(Usuario).all()}
    groups, capture, people, tools = Counter(), Counter(), Counter(), Counter()
    daily = defaultdict(int)
    for m in current:
        groups[records_api.MOVEMENT_GROUPS.get(records_api.movement_group(m.tipo), "Other")] += 1
        capture[m.captura] += 1
        people[m.realizado_por] += 1
        tools[m.herramienta_id] += 1
        daily[_local_date(m.creado_en)] += 1
    first_day = _local_date(start)
    series = [{"date": (first_day + timedelta(days=i)).isoformat(), "movements": daily.get(first_day + timedelta(days=i), 0)}
              for i in range((_local_date(now) - first_day).days + 1)]
    total_tools = db.query(func.count(Herramienta.id)).scalar() or 0
    scanned = sum(v for k, v in capture.items() if k is not None)
    return {
        "total": len(current), "previous": len(moves) - len(current), "daily": series,
        "by_group": [_datum(k, k, v) for k, v in groups.most_common()],
        "capture": [_datum(str(k), CAPTURE_LABEL.get(k, str(k)), v) for k, v in capture.most_common()],
        "scan_share": round(sum(v for k, v in capture.items() if k in ("scan", "camera")) / scanned * 100, 1) if scanned else None,
        "people": [_datum(u, names.get(u, u), v) for u, v in people.most_common(TOP)],
        "tools_touched": len(tools), "tools_total": total_tools,
        "touched_pct": round(len(tools) / total_tools * 100, 1) if total_tools else None,
        "per_tool": round(len(current) / len(tools), 1) if tools else None,
        "busiest": [_datum(t, t, v) for t, v in tools.most_common(5)],
    }


def build_operations(db, days):
    now, start, prev_start = _window(days)
    machines = planning_api._machine_rows(db)
    machine_type = {m["code"]: m["type"] for m in machines}
    programs = demandas_api._program_index(db)
    hpp = {}
    for key, program in programs.items():
        value = demandas_api._hr(program, 10000)
        if value:
            hpp[key] = value / 10000
    production, run_hours = _production(db, now, start, prev_start, hpp, machine_type, days)
    utilization = _utilization(db, now, start, days, run_hours, machines)
    strokes = _strokes(db, now, start)
    ctb = _ctb(db)
    trace = _traceability(db, now, start, prev_start)
    kpis = [
        _kpi("pieces", "Pieces produced", production["pieces"], production["previous_pieces"], "", "up",
             f"{len(production['daily'])} days of production", prev_n=production["previous_n"]),
        _kpi("attainment", "Plan attainment (pieces vs forecast)", production["attainment_pct"], None, "%", "up", "make items built vs planned"),
        _kpi("utilization", "Machine utilisation", utilization["fleet"]["util_pct"], None, "%", "up",
             f"{len(utilization['machines'])} machines, {utilization['fleet']['run_h']:.0f} h running"),
        _kpi("disabled", "Machines disabled now", len(utilization["disabled_now"]), None, "", "down",
             f"{utilization['disabled_events']} stops in the period"),
        _kpi("auto", "Automatic tickets by strokes", strokes["auto_opened"], None, "", None,
             f"{strokes['auto_open_now']} open now"),
        _kpi("ctb", "FGs clear to build", ctb["ready"], None, "", "up", f"of {len(ctb['rows'])} with demand"),
        _kpi("movements", "Traceable movements", trace["total"], trace["previous"], "", None,
             f"{trace['tools_touched']} tools touched", prev_n=trace["previous"]),
    ]
    return {
        "period": {"days": days}, "kpis": kpis, "production": production, "utilization": utilization,
        "strokes": strokes, "ctb": ctb, "traceability": trace,
    }


@router.get("/operations")
def operations(days: int = 30):
    if days not in (7, 14, 30, 60, 90, 180, 365):
        raise HTTPException(status_code=422, detail="Choose 7, 14, 30, 60, 90, 180 or 365 days")
    if auth.current_user() is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    db = workflow_api.SessionLocal()
    try:
        return build_operations(db, days)
    finally:
        db.close()
