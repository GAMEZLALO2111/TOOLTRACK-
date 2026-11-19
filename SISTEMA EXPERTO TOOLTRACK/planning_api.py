"""Planeación de capacidad: FG y su BOM, forecast semanal, capacidad por máquina y el cálculo «¿cumplimos?»."""
import base64
import io
import re
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import demandas_api
import programs_api
import shifts
import workflow_api
from auth import require_permission
from capacity_plan import net_need, simulate
from db import SessionLocal
from models.inventory import (
    CapacidadMaquina,
    DemandaPlaneacion,
    FgParte,
    FgPlaneacion,
    ForecastSemana,
    Maquina,
    ParametroPlaneacion,
    ProgramaParte,
    RegistroProduccion,
    Ubicacion,
)

router = APIRouter(prefix="/planeacion", tags=["planeacion"])

# Valores iniciales editables; una máquina sin captura propia usa estos y se marca «por defecto».
PARAM_DEFAULTS = {
    "turnos": "3", "horas_turno": "8", "dias_semana": "5", "eficiencia_pct": "85",
    "setup_minutos": "60", "adelanto_semanas": "2", "horizonte_semanas": "12",
}
PARAM_RANGE = {
    "turnos": (1, 3), "horas_turno": (1, 12), "dias_semana": (1, 7), "eficiencia_pct": (10, 100),
    "setup_minutos": (0, 600), "adelanto_semanas": (0, 8), "horizonte_semanas": (1, 26),
}
MAX_FORECAST = 10_000_000
MAX_PIEZAS_FG = 10_000


def _now():
    return workflow_api.utcnow()


def monday_of(value: date) -> date:
    return value - timedelta(days=value.weekday())


def current_monday() -> date:
    return monday_of(shifts._local(_now()).date())


def _params(db):
    values = dict(PARAM_DEFAULTS)
    for row in db.query(ParametroPlaneacion).all():
        if row.clave in values:
            values[row.clave] = row.valor
    return {key: float(value) for key, value in values.items()}


def _check_range(name, value):
    low, high = PARAM_RANGE[name]
    if not (low <= value <= high):
        raise HTTPException(status_code=422, detail=f"{name} must be between {low} and {high}")
    return value


def _fg_key(value):
    text = (value or "").strip().upper()
    if not text or len(text) > 60:
        raise HTTPException(status_code=422, detail="Write the FG number (up to 60 characters)")
    return text


def _part_key(value):
    text = (value or "").strip().upper()
    if not text or len(text) > 60:
        raise HTTPException(status_code=422, detail="Write the make part number (up to 60 characters)")
    return text


# ---------------------------------------------------------------- FG y BOM

class FgPart(BaseModel):
    numero_parte: str
    piezas: int


class FgIn(BaseModel):
    numero_fg: str
    descripcion: Optional[str] = None
    cliente: Optional[str] = None
    bom_de: Optional[str] = None
    partes: List[FgPart] = []


def _bom_rows(db, fg):
    source = db.query(FgPlaneacion).filter(FgPlaneacion.id == fg.bom_de_id).first() if fg.bom_de_id else fg
    return db.query(FgParte).filter(FgParte.fg_id == source.id).order_by(FgParte.numero_parte).all() if source else []


def _fg_read(db, fg, programs=None):
    source = db.query(FgPlaneacion).filter(FgPlaneacion.id == fg.bom_de_id).first() if fg.bom_de_id else None
    kinds = {}
    for program in (programs if programs is not None else db.query(ProgramaParte).all()):
        kinds.setdefault(program.numero_parte.upper(), set()).add(program.tipo_maquina.upper())
    return {
        "id": fg.id, "numero_fg": fg.numero_fg, "descripcion": fg.descripcion, "cliente": fg.cliente,
        "active": bool(fg.activa), "bom_from": source.numero_fg if source else None,
        "parts": [
            {"numero_parte": row.numero_parte, "piezas": row.piezas_por_fg,
             "types": sorted(kinds.get(row.numero_parte, [])), "has_program": row.numero_parte in kinds}
            for row in _bom_rows(db, fg)
        ],
    }


def _save_fg(db, item: FgIn, fg=None, username="system"):
    key = _fg_key(item.numero_fg)
    clash = db.query(FgPlaneacion).filter(FgPlaneacion.numero_fg == key)
    if fg is not None:
        clash = clash.filter(FgPlaneacion.id != fg.id)
    if clash.first():
        raise HTTPException(status_code=409, detail=f"FG {key} already exists")
    source = None
    if item.bom_de and item.bom_de.strip():
        source = db.query(FgPlaneacion).filter(FgPlaneacion.numero_fg == item.bom_de.strip().upper()).first()
        if source is None:
            raise HTTPException(status_code=404, detail=f"FG {item.bom_de.strip().upper()} to copy the BOM from does not exist")
        if source.bom_de_id or (fg is not None and source.id == fg.id):
            raise HTTPException(status_code=422, detail="Choose a FG that has its own BOM (not itself or one that copies another)")
        if item.partes:
            raise HTTPException(status_code=422, detail="A FG that shares a BOM has no parts of its own")
    seen = set()
    cleaned = []
    for part in item.partes:
        number = _part_key(part.numero_parte)
        if number in seen:
            raise HTTPException(status_code=422, detail=f"{number} is repeated in the BOM")
        if not 1 <= part.piezas <= MAX_PIEZAS_FG:
            raise HTTPException(status_code=422, detail=f"Pieces per FG of {number} must be between 1 and {MAX_PIEZAS_FG}")
        seen.add(number)
        cleaned.append((number, part.piezas))
    now = _now()
    if fg is None:
        fg = FgPlaneacion(numero_fg=key, creado_por=username, creado_en=now, actualizado_en=now, activa=True)
        db.add(fg)
    fg.numero_fg = key
    fg.descripcion = (item.descripcion or "").strip() or None
    fg.cliente = (item.cliente or "").strip() or None
    fg.bom_de_id = source.id if source else None
    fg.actualizado_en = now
    db.flush()
    if source is not None and db.query(FgPlaneacion).filter(FgPlaneacion.bom_de_id == fg.id).first():
        raise HTTPException(status_code=422, detail="Other FGs copy this BOM; they must keep pointing at a FG with its own parts")
    db.query(FgParte).filter(FgParte.fg_id == fg.id).delete()
    for number, pieces in cleaned:
        db.add(FgParte(fg_id=fg.id, numero_parte=number, piezas_por_fg=pieces))
    db.flush()
    return fg


@router.get("/fg")
def list_fg():
    require_permission("planning.view")
    db = SessionLocal()
    try:
        programs = db.query(ProgramaParte).all()
        return [
            _fg_read(db, fg, programs)
            for fg in db.query(FgPlaneacion).filter(FgPlaneacion.activa.is_(True)).order_by(FgPlaneacion.numero_fg).all()
        ]
    finally:
        db.close()


@router.post("/fg", status_code=201)
def create_fg(item: FgIn):
    user = require_permission("planning.manage")
    db = SessionLocal()
    try:
        fg = _save_fg(db, item, None, user.username)
        db.commit()
        return _fg_read(db, fg)
    except HTTPException:
        db.rollback()
        raise
    finally:
        db.close()


@router.put("/fg/{fg_id}")
def update_fg(fg_id: int, item: FgIn):
    user = require_permission("planning.manage")
    db = SessionLocal()
    try:
        fg = db.query(FgPlaneacion).filter(FgPlaneacion.id == fg_id, FgPlaneacion.activa.is_(True)).first()
        if fg is None:
            raise HTTPException(status_code=404, detail="FG not found")
        _save_fg(db, item, fg, user.username)
        db.commit()
        return _fg_read(db, fg)
    except HTTPException:
        db.rollback()
        raise
    finally:
        db.close()


@router.delete("/fg/{fg_id}")
def remove_fg(fg_id: int):
    require_permission("planning.manage")
    db = SessionLocal()
    try:
        fg = db.query(FgPlaneacion).filter(FgPlaneacion.id == fg_id, FgPlaneacion.activa.is_(True)).first()
        if fg is None:
            raise HTTPException(status_code=404, detail="FG not found")
        if db.query(FgPlaneacion).filter(FgPlaneacion.bom_de_id == fg.id, FgPlaneacion.activa.is_(True)).first():
            raise HTTPException(status_code=409, detail="Other FGs copy this BOM; change them first")
        fg.activa = False
        fg.actualizado_en = _now()
        db.commit()
        return {"id": fg.id, "ok": True}
    finally:
        db.close()


@router.get("/partes")
def list_parts():
    """Make items already in the system (the ones with a saved program), to pick from when building a BOM."""
    require_permission("planning.view")
    db = SessionLocal()
    try:
        info = defaultdict(lambda: {"types": [], "family": None})
        for program in db.query(ProgramaParte).order_by(ProgramaParte.numero_parte).all():
            entry = info[program.numero_parte.upper()]
            entry["types"].append(program.tipo_maquina.upper())
            entry["family"] = entry["family"] or program.familia
        return [{"numero_parte": part, **data} for part, data in sorted(info.items())]
    finally:
        db.close()


class FgImport(BaseModel):
    contenido_base64: str


HEADERS = {
    "fg": ("fg", "numero fg", "fg pn", "finish good", "fg number"),
    "part": ("make", "make item", "parte", "numero de parte", "part", "part number", "make pn"),
    "qty": ("piezas", "pzas", "qty", "quantity", "cantidad", "piezas por fg", "pcs per fg", "per fg"),
    "client": ("cliente", "cm", "customer"),
}


def _header_map(row):
    found = {}
    for index, cell in enumerate(row):
        text = re.sub(r"\s+", " ", str(cell or "").strip().lower())
        for key, names in HEADERS.items():
            if text in names and key not in found:
                found[key] = index
    return found


@router.post("/fg/importar")
def import_fg(item: FgImport):
    """Builds or replaces the BOM of each FG listed in an Excel file with the columns FG, MAKE and PIEZAS."""
    user = require_permission("planning.manage")
    from openpyxl import load_workbook

    try:
        raw = base64.b64decode(item.contenido_base64, validate=True)
        workbook = load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    except Exception:
        raise HTTPException(status_code=422, detail="The file is not a readable Excel (.xlsx) file") from None
    rows, columns = [], None
    for sheet in workbook.worksheets:
        for values in sheet.iter_rows(values_only=True):
            if columns is None:
                mapped = _header_map(values)
                if {"fg", "part", "qty"} <= set(mapped):
                    columns = mapped
                continue
            if all(cell in (None, "") for cell in values):
                continue
            rows.append(values)
        if columns is not None:
            break
    if columns is None:
        raise HTTPException(status_code=422, detail="Could not find the columns FG, MAKE and PIEZAS in the first rows")
    grouped, problems = {}, []
    for number, values in enumerate(rows, start=1):
        fg, part, qty = (values[columns[k]] if columns[k] < len(values) else None for k in ("fg", "part", "qty"))
        if fg in (None, "") or part in (None, ""):
            continue
        try:
            pieces = int(float(qty))
        except (TypeError, ValueError):
            problems.append(f"Row {number}: pieces is not a number")
            continue
        client = values[columns["client"]] if "client" in columns and columns["client"] < len(values) else None
        entry = grouped.setdefault(str(fg).strip().upper(), {"cliente": client, "partes": {}})
        entry["partes"][str(part).strip().upper()] = pieces
    if not grouped:
        raise HTTPException(status_code=422, detail="The file has no FG rows" + (f" ({problems[0]})" if problems else ""))
    db = SessionLocal()
    try:
        created = updated = 0
        for number, data in grouped.items():
            existing = db.query(FgPlaneacion).filter(FgPlaneacion.numero_fg == number).first()
            if existing is not None and existing.bom_de_id:
                problems.append(f"{number} copies the BOM of another FG; skipped")
                continue
            _save_fg(
                db,
                FgIn(numero_fg=number, cliente=str(data["cliente"]) if data["cliente"] else (existing.cliente if existing else None),
                     descripcion=existing.descripcion if existing else None,
                     partes=[FgPart(numero_parte=p, piezas=q) for p, q in data["partes"].items()]),
                existing, user.username,
            )
            created, updated = (created + 1, updated) if existing is None else (created, updated + 1)
        db.commit()
        return {"created": created, "updated": updated, "problems": problems}
    except HTTPException:
        db.rollback()
        raise
    finally:
        db.close()


# ---------------------------------------------------------------- Forecast

class ForecastRow(BaseModel):
    numero_fg: str
    semanas: Dict[str, int]


class ForecastIn(BaseModel):
    filas: List[ForecastRow]


def _horizon(db, weeks=None):
    count = int(weeks or _params(db)["horizonte_semanas"])
    start = current_monday()
    return [start + timedelta(weeks=k) for k in range(max(1, min(count, 26)))]


@router.get("/forecast")
def read_forecast(semanas: Optional[int] = None):
    require_permission("planning.view")
    db = SessionLocal()
    try:
        weeks = _horizon(db, semanas)
        values = {(row.fg_id, row.semana): row.cantidad for row in db.query(ForecastSemana).filter(
            ForecastSemana.semana >= weeks[0], ForecastSemana.semana <= weeks[-1]).all()}
        fgs = db.query(FgPlaneacion).filter(FgPlaneacion.activa.is_(True)).order_by(FgPlaneacion.numero_fg).all()
        return {
            "weeks": [w.isoformat() for w in weeks],
            "rows": [
                {"fg_id": fg.id, "numero_fg": fg.numero_fg, "cliente": fg.cliente,
                 "values": [values.get((fg.id, w), 0) for w in weeks]}
                for fg in fgs
            ],
        }
    finally:
        db.close()


@router.put("/forecast")
def save_forecast(item: ForecastIn):
    user = require_permission("planning.manage")
    db = SessionLocal()
    try:
        saved = 0
        now = _now()
        for row in item.filas:
            fg = db.query(FgPlaneacion).filter(
                FgPlaneacion.numero_fg == _fg_key(row.numero_fg), FgPlaneacion.activa.is_(True)).first()
            if fg is None:
                raise HTTPException(status_code=404, detail=f"FG {row.numero_fg.strip().upper()} does not exist")
            for text, quantity in row.semanas.items():
                try:
                    week = monday_of(date.fromisoformat(text))
                except ValueError:
                    raise HTTPException(status_code=422, detail=f"{text} is not a date (YYYY-MM-DD)") from None
                if not 0 <= quantity <= MAX_FORECAST:
                    raise HTTPException(status_code=422, detail=f"Quantity must be between 0 and {MAX_FORECAST:,}")
                existing = db.query(ForecastSemana).filter(
                    ForecastSemana.fg_id == fg.id, ForecastSemana.semana == week).first()
                if existing is None and quantity > 0:
                    db.add(ForecastSemana(fg_id=fg.id, semana=week, cantidad=quantity,
                                          actualizado_por=user.username, actualizado_en=now))
                elif existing is not None and quantity == 0:
                    db.delete(existing)
                elif existing is not None and existing.cantidad != quantity:
                    existing.cantidad, existing.actualizado_por, existing.actualizado_en = quantity, user.username, now
                saved += 1
        db.commit()
        return {"saved": saved}
    except HTTPException:
        db.rollback()
        raise
    finally:
        db.close()


# ---------------------------------------------------------------- Capacidad y parámetros

def _machine_rows(db):
    params = _params(db)
    custom = {row.maquina_codigo: row for row in db.query(CapacidadMaquina).all()}
    rows = []
    query = (
        db.query(Ubicacion, Maquina)
        .outerjoin(Maquina, Maquina.codigo == Ubicacion.codigo)
        .filter(Ubicacion.tipo == "machine", Ubicacion.activa.is_(True))
        .order_by(Ubicacion.codigo)
    )
    for location, profile in query.all():
        kind = workflow_api._machine_kind(profile.tipo_maquina) if profile else ""
        row = custom.get(location.codigo)
        shifts_n = row.turnos if row else params["turnos"]
        hours = row.horas_turno if row else params["horas_turno"]
        days = row.dias_semana if row else params["dias_semana"]
        efficiency = row.eficiencia_pct if row else params["eficiencia_pct"]
        rows.append({
            "code": location.codigo, "type": kind if kind in programs_api.MACHINE_TYPES else None,
            "configured": row is not None, "included": bool(row.incluida) if row else True,
            "shifts": shifts_n, "hours_per_shift": hours, "days_per_week": days, "efficiency_pct": efficiency,
            "hours_week": round(shifts_n * hours * days * efficiency / 100, 1),
            "thickness_min": _number(profile.espesor_min) if profile else None,
            "thickness_max": _number(profile.espesor_max) if profile else None,
        })
    return rows


def _number(text):
    try:
        return float(str(text).strip()) if text not in (None, "") else None
    except ValueError:
        return None


@router.get("/maquinas")
def read_machines():
    require_permission("planning.view")
    db = SessionLocal()
    try:
        return {"machines": _machine_rows(db), "params": _params(db), "defaults": {k: float(v) for k, v in PARAM_DEFAULTS.items()}}
    finally:
        db.close()


class MachineCapacity(BaseModel):
    turnos: int
    horas_turno: float
    dias_semana: float
    eficiencia_pct: float
    incluida: bool = True


@router.put("/maquinas/{code}")
def save_machine(code: str, item: MachineCapacity):
    user = require_permission("planning.manage")
    _check_range("turnos", item.turnos)
    _check_range("horas_turno", item.horas_turno)
    _check_range("dias_semana", item.dias_semana)
    _check_range("eficiencia_pct", item.eficiencia_pct)
    db = SessionLocal()
    try:
        key = code.strip().upper()
        if not db.query(Ubicacion).filter(Ubicacion.codigo == key, Ubicacion.tipo == "machine").first():
            raise HTTPException(status_code=404, detail=f"Machine {key} not found")
        row = db.query(CapacidadMaquina).filter(CapacidadMaquina.maquina_codigo == key).first()
        if row is None:
            row = CapacidadMaquina(maquina_codigo=key, turnos=item.turnos, horas_turno=item.horas_turno,
                                   dias_semana=item.dias_semana, eficiencia_pct=item.eficiencia_pct)
            db.add(row)
        row.turnos, row.horas_turno, row.dias_semana = item.turnos, item.horas_turno, item.dias_semana
        row.eficiencia_pct, row.incluida = item.eficiencia_pct, item.incluida
        row.actualizado_por, row.actualizado_en = user.username, _now()
        db.commit()
        return {"ok": True}
    finally:
        db.close()


class ParamsIn(BaseModel):
    valores: Dict[str, float]


@router.put("/parametros")
def save_params(item: ParamsIn):
    user = require_permission("planning.manage")
    db = SessionLocal()
    try:
        for key, value in item.valores.items():
            if key not in PARAM_DEFAULTS:
                raise HTTPException(status_code=422, detail=f"Unknown parameter {key}")
            _check_range(key, value)
            row = db.query(ParametroPlaneacion).filter(ParametroPlaneacion.clave == key).first()
            text = f"{value:g}"
            if row is None:
                db.add(ParametroPlaneacion(clave=key, valor=text, actualizado_por=user.username, actualizado_en=_now()))
            else:
                row.valor, row.actualizado_por, row.actualizado_en = text, user.username, _now()
        db.commit()
        return {"params": _params(db)}
    except HTTPException:
        db.rollback()
        raise
    finally:
        db.close()


# ---------------------------------------------------------------- Cálculo

def build_capacity(db, weeks=None):
    params = _params(db)
    horizon = _horizon(db, weeks)
    count = len(horizon)
    warnings = []
    programs = demandas_api._program_index(db)

    fgs = db.query(FgPlaneacion).filter(FgPlaneacion.activa.is_(True)).order_by(FgPlaneacion.numero_fg).all()
    forecast = defaultdict(dict)
    for row in db.query(ForecastSemana).filter(ForecastSemana.semana >= horizon[0], ForecastSemana.semana <= horizon[-1]):
        forecast[row.fg_id][row.semana] = row.cantidad
    demand = defaultdict(lambda: [0.0] * count)
    fg_parts = {}
    for fg in fgs:
        rows = _bom_rows(db, fg)
        fg_parts[fg.id] = rows
        if not rows:
            warnings.append(f"{fg.numero_fg} has no BOM yet")
        for index, week in enumerate(horizon):
            quantity = forecast[fg.id].get(week, 0)
            for part in rows:
                demand[part.numero_parte][index] += quantity * part.piezas_por_fg

    cut = datetime.combine(horizon[0], time.min).astimezone(timezone.utc).replace(tzinfo=None)
    produced = defaultdict(int)
    for part, total in db.query(RegistroProduccion.numero_parte, RegistroProduccion.piezas).filter(
            RegistroProduccion.fecha_produccion >= cut).all():
        produced[part.upper()] += total
    board = defaultdict(int)
    board_by_machine = defaultdict(list)
    for demand_row in db.query(DemandaPlaneacion).filter(DemandaPlaneacion.activa.is_(True)).order_by(
            DemandaPlaneacion.maquina_codigo, DemandaPlaneacion.orden, DemandaPlaneacion.id):
        pending = max(demand_row.cantidad - demand_row.piezas_producidas, 0)
        if pending:
            board[demand_row.numero_parte.upper()] += pending
            board_by_machine[demand_row.maquina_codigo].append((demand_row.numero_parte.upper(), pending))

    machines, defaults_used, no_range, no_type = [], 0, 0, []
    for row in _machine_rows(db):
        if not row["included"]:
            continue
        if not row["type"]:
            no_type.append(row["code"])
            continue
        defaults_used += 0 if row["configured"] else 1
        no_range += 1 if row["thickness_min"] is None or row["thickness_max"] is None else 0
        capacity = [row["hours_week"]] * count
        committed = 0.0
        for part, pending in board_by_machine.get(row["code"], []):
            hours = demandas_api._hr(programs.get((part, row["type"])), pending)
            if hours is None:
                warnings.append(f"Board demand of {part} on {row['code']} has no UPH: its hours are not counted")
                continue
            committed += hours
        remaining_commit = committed
        for index in range(count):
            taken = min(capacity[index], remaining_commit)
            capacity[index] -= taken
            remaining_commit -= taken
        machines.append({**row, "cap": capacity, "committed_h": round(committed, 1)})
    if no_type:
        warnings.append(f"Machines without a type are left out: {', '.join(no_type)}")
    if defaults_used:
        warnings.append(f"{defaults_used} machine(s) use the default shifts, days and efficiency: review them in Capacity settings")
    if no_range:
        warnings.append(f"{no_range} machine(s) have no thickness range: any part is assumed to fit (not verified)")

    part_info, need, part_meta = {}, {}, {}
    for part, weekly in demand.items():
        hpp, thickness = {}, None
        for kind in programs_api.MACHINE_TYPES:
            program = programs.get((part, kind))
            hours = demandas_api._hr(program, 10000) if program else None
            if hours:
                hpp[kind] = hours / 10000
            if program is not None and thickness is None:
                thickness = _number(program.thickness)
        part_info[part] = {"hpp": hpp, "thickness": thickness}
        offset = produced.get(part, 0) + board.get(part, 0)
        need[part] = net_need(weekly, offset)
        part_meta[part] = {
            "forecast_total": round(sum(weekly)), "produced": produced.get(part, 0), "board_pending": board.get(part, 0),
            "has_program": bool(hpp), "types": sorted(hpp),
        }
        if sum(weekly) and board.get(part, 0) + produced.get(part, 0) >= sum(weekly):
            warnings.append(f"{part}: the forecast is already covered by what was produced and what is on the Board")
        if sum(weekly) and not hpp:
            warnings.append(f"{part} has no program with UPH: its hours cannot be counted")

    result = simulate(count, machines, part_info, need, params["setup_minutos"] / 60, int(params["adelanto_semanas"]))
    fg_rows = []
    for fg in fgs:
        parts = [row.numero_parte for row in fg_parts[fg.id]]
        weekly = []
        for index, week in enumerate(horizon):
            quantity = forecast[fg.id].get(week, 0)
            late = any(
                result["parts"][p][index]["late"] > 0 for p in parts if p in part_meta and part_meta[p]["has_program"]
            )
            missing = quantity > 0 and any(not part_meta[p]["has_program"] for p in parts if p in part_meta)
            if quantity == 0 and not late:
                status = "sin_demanda"
            elif late:
                status = "no_cumple"
            elif missing:
                status = "sin_programa"
            else:
                status = result["weeks"][index]["status"] if any(
                    result["parts"][p][index]["assigned"] > 0 for p in parts if p in result["parts"]) else "cumple"
            weekly.append({"forecast": quantity, "status": status})
        fg_rows.append({"id": fg.id, "numero_fg": fg.numero_fg, "cliente": fg.cliente, "parts": parts, "weeks": weekly})
    return {
        "weeks": [w.isoformat() for w in horizon], "params": params,
        "summary": result["weeks"], "fgs": fg_rows,
        "parts": [
            {"numero_parte": part, **part_meta[part], "weeks": result["parts"][part]}
            for part in sorted(part_meta)
        ],
        "machines": [
            {"code": m["code"], "type": m["type"], "hours_week": m["hours_week"], "configured": m["configured"],
             "committed_h": m["committed_h"], "loads": result["machine_loads"][m["code"]]}
            for m in machines
        ],
        "warnings": warnings,
    }


@router.get("/capacidad")
def capacity(semanas: Optional[int] = None):
    require_permission("planning.view")
    db = SessionLocal()
    try:
        return build_capacity(db, semanas)
    finally:
        db.close()
