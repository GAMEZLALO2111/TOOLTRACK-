"""Strokes per physical tool.

A program says how many strokes each station gives per blank (cycle). Every production record of a part
adds those strokes to the punch and the die mounted on those stations. Each tool number has a limit for
its first maintenance and another between sharpenings; at 90% the tool shows a pre-alert and at 100%
the system opens a sharpening request for Tool Crib. The tool stays on the machine until Tool Crib arrives.
"""
import json
import math
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import auth
import scan_codes
import tool_tracking_api
import workflow_api
from models.inventory import (
    EstadoMaquina,
    GolpeHerramienta,
    GolpesPrograma,
    Herramienta,
    LimiteGolpes,
    Maquina,
    NumeroHerramienta,
    ProgramaParte,
    RequisitoSetup,
    ServicioHerramienta,
    SolicitudServicio,
)

router = APIRouter(prefix="/workflow/strokes", tags=["strokes"])

DEFAULT_STROKES = 1.0
MAX_STROKES = 1000
MAX_LIMIT = 100_000_000
WARNING_RATIO = 0.9
SYSTEM_USER = "SYSTEM"
OPEN_STATES = ("open", "attending", "collected", "replaced")
CATEGORIES = ("punzon", "matriz")


def _user():
    user = auth.current_user()
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user


def _iso(value):
    return value.isoformat() if value else None


def _angle(value):
    return (value or "").strip()


# ---------------------------------------------------------------- limits and levels

def limit_for(limit_row, sharpenings):
    """Limit that applies now and the stage: before any sharpening it is the first maintenance (or the
    interval if that was left empty); afterwards it is the sharpening interval."""
    if limit_row is None:
        return None, "first" if not sharpenings else "interval"
    if not sharpenings:
        return limit_row.primer_mantenimiento or limit_row.intervalo_afilado, "first"
    return limit_row.intervalo_afilado, "interval"


def evaluate(tool, limit_row, sharpenings):
    limit, stage = limit_for(limit_row, sharpenings)
    used = float(tool.golpes_servicio or 0)
    if not limit:
        return {"used": round(used), "limit": None, "pct": None, "level": "none", "stage": stage}
    ratio = used / limit
    level = "due" if ratio >= 1 else "warning" if ratio >= WARNING_RATIO else "ok"
    return {"used": round(used), "limit": limit, "pct": round(ratio * 100, 1), "level": level, "stage": stage}


def _sharpenings(db, tool_ids):
    """Services already done on each tool (sharpenings and released maintenance): they end the first-maintenance stage."""
    if not tool_ids:
        return {}
    from sqlalchemy import func
    from models.inventory import TicketMantenimiento

    counts = {}
    for tool_id, count in db.query(ServicioHerramienta.herramienta_id, func.count(ServicioHerramienta.id)).filter(
        ServicioHerramienta.herramienta_id.in_(tool_ids), ServicioHerramienta.tipo == "sharpening",
    ).group_by(ServicioHerramienta.herramienta_id).all():
        counts[tool_id] = counts.get(tool_id, 0) + count
    for tool_id, count in db.query(TicketMantenimiento.herramienta_id, func.count(TicketMantenimiento.id)).filter(
        TicketMantenimiento.herramienta_id.in_(tool_ids), TicketMantenimiento.resultado == "released",
    ).group_by(TicketMantenimiento.herramienta_id).all():
        counts[tool_id] = counts.get(tool_id, 0) + count
    return counts


def _limits(db):
    return {(row.tn, row.categoria): row for row in db.query(LimiteGolpes).all()}


def tool_level(db, tool):
    limits = _limits(db)
    return evaluate(tool, limits.get((tool.tn, tool.categoria)), _sharpenings(db, [tool.id]).get(tool.id, 0))


# ---------------------------------------------------------------- accumulation

def _add(db, tool, amount, origin, actor, motivo=None, production_id=None, machine=None, part=None, station=None):
    before = float(tool.golpes_servicio or 0)
    after = max(0.0, before + float(amount))
    applied = after - before
    tool.golpes_servicio = after
    tool.golpes_total = max(0.0, float(tool.golpes_total or 0) + applied)
    db.add(GolpeHerramienta(
        herramienta_id=tool.id, origen=origin, golpes=round(applied, 3), golpes_servicio=round(applied, 3),
        produccion_id=production_id, maquina_codigo=machine, numero_parte=part, estacion=station,
        motivo=motivo, usuario=actor, fecha=workflow_api.utcnow(),
    ))


def program_rows(db, program):
    """Stations of a program with the strokes per blank in force (the default where none was set)."""
    saved = {
        (row.estacion, row.tn): row
        for row in db.query(GolpesPrograma).filter(
            GolpesPrograma.numero_parte == program.numero_parte, GolpesPrograma.tipo_maquina == program.tipo_maquina,
        ).all()
    }
    result = []
    for requirement in json.loads(program.requisitos_json):
        row = saved.get((requirement["station"], requirement["tn"]))
        result.append({
            "station": requirement["station"], "tn": requirement["tn"], "angle": requirement.get("angle"),
            "strokes": row.golpes if row else DEFAULT_STROKES,
            "source": row.fuente if row else "default",
            "edited_by": row.editado_por if row else None,
            "edited_at": _iso(row.editado_en) if row else None,
        })
    return result


def apply_production(db, part_number, pieces, machine, actor, production_id=None):
    """Adds the strokes of a production record to the tools mounted on the machine. It matches the program
    stations to the active setup by station, TN and angle, so a standard turret file works the same way."""
    if not machine or not pieces:
        return []
    setup = tool_tracking_api._active_setup(db, machine)
    profile = db.query(Maquina).filter(Maquina.codigo == machine).first()
    machine_type = workflow_api._machine_kind(profile.tipo_maquina) if profile else ""
    if not setup or machine_type not in {"4I", "2I", "45STA"}:
        return []
    part = (part_number or "").strip().upper()
    program = db.query(ProgramaParte).filter(
        ProgramaParte.numero_parte == part, ProgramaParte.tipo_maquina == machine_type,
    ).first()
    if not program:
        return []
    blanks = pieces / (program.piezas_blank or 1)
    mounted = {}
    for requirement in db.query(RequisitoSetup).filter(RequisitoSetup.setup_id == setup.id).all():
        mounted.setdefault((requirement.station, requirement.tn, _angle(requirement.angle)), []).append(requirement)

    created = []
    tools_seen = set()
    for row in program_rows(db, program):
        for requirement in mounted.get((row["station"], row["tn"], _angle(row["angle"])), []):
            for category, tool_id in (("punzon", requirement.punch_tool_id), ("matriz", requirement.die_tool_id)):
                if not tool_id or (tool_id, row["station"]) in tools_seen:
                    continue
                tools_seen.add((tool_id, row["station"]))
                tool = db.query(Herramienta).filter(Herramienta.id == tool_id).first()
                if not tool:
                    continue
                _add(
                    db, tool, row["strokes"] * blanks, "produccion", actor, production_id=production_id,
                    machine=machine, part=part, station=row["station"],
                )
                ticket = check_tool(db, tool, setup, requirement, category)
                if ticket:
                    created.append(ticket)
    return created


def check_tool(db, tool, setup, requirement, category):
    """Opens the automatic sharpening request when the tool reached its limit (once per tool)."""
    limit_row = db.query(LimiteGolpes).filter(
        LimiteGolpes.tn == tool.tn, LimiteGolpes.categoria == category
    ).first()
    sharpenings = _sharpenings(db, [tool.id]).get(tool.id, 0)
    status = evaluate(tool, limit_row, sharpenings)
    if status["level"] != "due":
        return None
    if db.query(SolicitudServicio.id).filter(
        SolicitudServicio.herramienta_id == tool.id, SolicitudServicio.estado.in_(OPEN_STATES),
    ).first() or db.query(SolicitudServicio.id).filter(
        SolicitudServicio.requisito_id == requirement.id, SolicitudServicio.categoria == category,
        SolicitudServicio.estado.in_(("open", "attending", "collected")),
    ).first():
        return None
    stage = "first maintenance" if status["stage"] == "first" else "sharpening interval"
    request = SolicitudServicio(
        maquina_codigo=setup.maquina_codigo, setup_id=setup.id, requisito_id=requirement.id,
        numero_parte=workflow_api._part_from_name(setup.nombre_archivo).upper() or None,
        estacion=requirement.station, categoria=category, tn=requirement.tn, tipo="sharpening", estado="open",
        nota=f"Automatic: {status['used']} strokes since the last service, limit {status['limit']} ({stage}). "
             f"Bring a replacement; the tool stays on the machine until then.",
        herramienta_id=tool.id, solicitado_por=SYSTEM_USER, solicitado_en=workflow_api.utcnow(),
        automatico=True, golpes_al_crear=float(tool.golpes_servicio or 0),
    )
    db.add(request)
    db.flush()
    workflow_api._append_movement(
        db, tool, tool.ubicacion, tool.ubicacion, "service_requested", SYSTEM_USER,
        details=f"SR-{request.id:05d}: automatic sharpening, {status['used']} of {status['limit']} strokes "
                f"on station {requirement.station} ({category})",
        setup_id=setup.id,
    )
    return request


def reset_after_service(db, tool, actor, motivo):
    """A finished sharpening starts a new service interval; the lifetime total is kept."""
    current = float(tool.golpes_servicio or 0)
    if current <= 0:
        return
    tool.golpes_servicio = 0.0
    db.add(GolpeHerramienta(
        herramienta_id=tool.id, origen="reinicio", golpes=0, golpes_servicio=-round(current, 3),
        motivo=motivo, usuario=actor, fecha=workflow_api.utcnow(),
    ))


# ---------------------------------------------------------------- program strokes

class StrokeRow(BaseModel):
    station: str
    tn: str
    strokes: Optional[float] = None


class ProgramStrokes(BaseModel):
    rows: List[StrokeRow]


def _program(db, machine_type, part_number):
    program = db.query(ProgramaParte).filter(
        ProgramaParte.numero_parte == part_number.strip().upper(),
        ProgramaParte.tipo_maquina == machine_type.strip().upper(),
    ).first()
    if not program:
        raise HTTPException(status_code=404, detail="That part number has no program for this machine type")
    return program


@router.get("/program/{tipo_maquina}/{numero_parte}")
def get_program_strokes(tipo_maquina: str, numero_parte: str):
    db = workflow_api.SessionLocal()
    try:
        return {"default": DEFAULT_STROKES, "rows": program_rows(db, _program(db, tipo_maquina, numero_parte))}
    finally:
        db.close()


@router.put("/program/{tipo_maquina}/{numero_parte}")
def save_program_strokes(tipo_maquina: str, numero_parte: str, item: ProgramStrokes):
    user = _user()
    db = workflow_api.SessionLocal()
    try:
        program = _program(db, tipo_maquina, numero_parte)
        valid = {(row["station"], row["tn"]) for row in json.loads(program.requisitos_json)}
        now = workflow_api.utcnow()
        for entry in item.rows:
            key = (entry.station.strip(), entry.tn.strip())
            if key not in valid:
                raise HTTPException(status_code=422, detail=f"Station {key[0]} ({key[1]}) is not in this program")
            row = db.query(GolpesPrograma).filter(
                GolpesPrograma.numero_parte == program.numero_parte, GolpesPrograma.tipo_maquina == program.tipo_maquina,
                GolpesPrograma.estacion == key[0], GolpesPrograma.tn == key[1],
            ).first()
            if entry.strokes is None:
                if row:
                    db.delete(row)
                continue
            if not math.isfinite(entry.strokes) or entry.strokes < 0 or entry.strokes > MAX_STROKES:
                raise HTTPException(status_code=422, detail=f"Strokes per blank must be between 0 and {MAX_STROKES}")
            value = round(entry.strokes, 3)
            if row is None:
                db.add(GolpesPrograma(
                    numero_parte=program.numero_parte, tipo_maquina=program.tipo_maquina, estacion=key[0], tn=key[1],
                    golpes=value, fuente="manual", editado_por=user.username, editado_en=now,
                ))
            elif row.golpes != value or row.fuente != "manual":
                row.golpes, row.fuente, row.editado_por, row.editado_en = value, "manual", user.username, now
        db.commit()
        return {"default": DEFAULT_STROKES, "rows": program_rows(db, program)}
    except HTTPException:
        db.rollback()
        raise
    finally:
        db.close()


# ---------------------------------------------------------------- limits per TN

class LimitSave(BaseModel):
    tn: str
    categoria: str
    primer_mantenimiento: Optional[int] = None
    intervalo_afilado: Optional[int] = None


def _limit_read(row):
    return {
        "tn": row.tn, "category": row.categoria, "first_maintenance": row.primer_mantenimiento,
        "sharpening_interval": row.intervalo_afilado, "edited_by": row.editado_por, "edited_at": _iso(row.editado_en),
    }


@router.get("/limits")
def list_limits():
    db = workflow_api.SessionLocal()
    try:
        rows = db.query(LimiteGolpes).order_by(LimiteGolpes.tn, LimiteGolpes.categoria).all()
        return [_limit_read(row) for row in rows]
    finally:
        db.close()


@router.put("/limits")
def save_limit(item: LimitSave):
    user = _user()
    category = workflow_api.CATEGORY_ALIASES.get(item.categoria.strip().lower())
    if not category:
        raise HTTPException(status_code=422, detail="Choose punch or die")
    tn = item.tn.strip()
    for label, value in (("First maintenance", item.primer_mantenimiento), ("Sharpening interval", item.intervalo_afilado)):
        if value is not None and not (0 < value <= MAX_LIMIT):
            raise HTTPException(status_code=422, detail=f"{label} must be a whole number of strokes above 0")
    db = workflow_api.SessionLocal()
    try:
        known = db.query(NumeroHerramienta.tn).filter(NumeroHerramienta.tn == tn).first() \
            or db.query(Herramienta.id).filter(Herramienta.tn == tn).first()
        if not known:
            raise HTTPException(status_code=404, detail=f"TN {tn} is not in the catalog")
        row = db.query(LimiteGolpes).filter(LimiteGolpes.tn == tn, LimiteGolpes.categoria == category).first()
        if item.primer_mantenimiento is None and item.intervalo_afilado is None:
            if row:
                db.delete(row)
            db.commit()
            return {"tn": tn, "category": category, "first_maintenance": None, "sharpening_interval": None}
        now = workflow_api.utcnow()
        if row is None:
            row = LimiteGolpes(tn=tn, categoria=category)
            db.add(row)
        row.primer_mantenimiento = item.primer_mantenimiento
        row.intervalo_afilado = item.intervalo_afilado
        row.editado_por, row.editado_en = user.username, now
        db.commit()
        return _limit_read(row)
    except HTTPException:
        db.rollback()
        raise
    finally:
        db.close()


# ---------------------------------------------------------------- per tool and alerts

class StrokeSet(BaseModel):
    golpes: float
    motivo: str


@router.get("/tool/{tool_id}")
def get_tool_strokes(tool_id: str):
    db = workflow_api.SessionLocal()
    try:
        tool = scan_codes.find_tool(db, tool_id)
        if not tool:
            raise HTTPException(status_code=404, detail=f"Tool '{tool_id}' is not in the inventory")
        history = db.query(GolpeHerramienta).filter(GolpeHerramienta.herramienta_id == tool.id).order_by(
            GolpeHerramienta.id.desc()
        ).limit(100).all()
        return {
            "tool_id": tool.id, "total": round(float(tool.golpes_total or 0)),
            **tool_level(db, tool),
            "history": [
                {
                    "id": row.id, "origin": row.origen, "strokes": row.golpes, "service_change": row.golpes_servicio,
                    "machine": row.maquina_codigo, "part_number": row.numero_parte, "station": row.estacion,
                    "reason": row.motivo, "by": row.usuario, "at": _iso(row.fecha),
                }
                for row in history
            ],
        }
    finally:
        db.close()


@router.post("/tool/{tool_id}/set")
def set_tool_strokes(tool_id: str, item: StrokeSet):
    """Manual correction of the counter since the last service (0 starts a new interval); it needs a reason."""
    user = _user()
    reason = item.motivo.strip()
    if len(reason) < 3:
        raise HTTPException(status_code=422, detail="Write the reason for the change")
    if not math.isfinite(item.golpes) or item.golpes < 0 or item.golpes > MAX_LIMIT:
        raise HTTPException(status_code=422, detail="Strokes must be zero or more")
    db = workflow_api.SessionLocal()
    try:
        tool = scan_codes.find_tool(db, tool_id)
        if not tool:
            raise HTTPException(status_code=404, detail=f"Tool '{tool_id}' is not in the inventory")
        delta = item.golpes - float(tool.golpes_servicio or 0)
        if item.golpes == 0:
            reset_after_service(db, tool, user.username, reason)
        elif delta:
            _add(db, tool, delta, "ajuste", user.username, motivo=reason)
        db.commit()
        return {"tool_id": tool.id, **tool_level(db, tool)}
    except HTTPException:
        db.rollback()
        raise
    finally:
        db.close()


def mounted_positions(db):
    """Tool id -> (machine, station, part number) for the tools mounted in the active setup of each machine."""
    positions = {}
    setups = {
        state.setup_activo_id: state.maquina_codigo
        for state in db.query(EstadoMaquina).filter(EstadoMaquina.setup_activo_id.isnot(None)).all()
    }
    if not setups:
        return positions
    for requirement in db.query(RequisitoSetup).filter(RequisitoSetup.setup_id.in_(list(setups))).all():
        for tool_id in (requirement.punch_tool_id, requirement.die_tool_id):
            if tool_id:
                positions[tool_id] = (setups[requirement.setup_id], requirement.station)
    return positions


@router.get("/alerts")
def stroke_alerts():
    """Tools at 90% of their stroke limit or beyond, mounted ones first."""
    db = workflow_api.SessionLocal()
    try:
        limits = _limits(db)
        tools = db.query(Herramienta).filter(Herramienta.golpes_servicio > 0).all()
        sharpenings = _sharpenings(db, [tool.id for tool in tools])
        positions = mounted_positions(db)
        open_requests = {
            row.herramienta_id: row for row in db.query(SolicitudServicio).filter(
                SolicitudServicio.herramienta_id.in_([tool.id for tool in tools] or [""]),
                SolicitudServicio.estado.in_(OPEN_STATES),
            ).all()
        }
        result = []
        for tool in tools:
            status = evaluate(tool, limits.get((tool.tn, tool.categoria)), sharpenings.get(tool.id, 0))
            if status["level"] not in ("warning", "due"):
                continue
            position = positions.get(tool.id)
            request = open_requests.get(tool.id)
            result.append({
                "tool_id": tool.id, "tn": tool.tn, "category": tool.categoria,
                "machine": position[0] if position else None, "station": position[1] if position else None,
                "location": tool.ubicacion, "request": f"SR-{request.id:05d}" if request else None,
                "request_state": request.estado if request else None, **status,
            })
        result.sort(key=lambda row: (row["machine"] is None, -(row["pct"] or 0)))
        return result
    finally:
        db.close()
