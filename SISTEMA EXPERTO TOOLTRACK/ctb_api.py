"""Clear to Build (CTB) of a FG: can we build N FG per week with the programs, machines and tools we have?

It answers three questions and says exactly what is missing for each:
- Programs: does every make of the BOM have a program (and UPH) for the machine types chosen?
- Machines: do the chosen machines (count per type) have the hours, and how many more would be needed?
- Tools: every machine runs one part at a time and reuses its tools between parts, so the peak need of a TN is the sum,
  over the machines, of the most that any of its parts uses at once. Only exact TNs count (no tolerance) because this
  list feeds purchases.
"""
import io
import json
import math
from collections import Counter, defaultdict
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Response

import demandas_api
import planning_api
import programs_api
import tn_tolerance
import tool_params
import workflow_api
from auth import require_permission
from capacity_plan import _hours_per_piece
from db import SessionLocal
from models.inventory import FgPlaneacion, Herramienta, ProgramaParte, Ubicacion

router = APIRouter(prefix="/planeacion/ctb", tags=["ctb"])

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
TIGHT_LOAD = 90.0
UPH_LOW, UPH_HIGH = 10.0, 2000.0
MAX_WEEKLY = 1_000_000
MAX_MACHINES = 40
SERVICE_RETURNING = ("afilado",)
SHAPE = {"1": "Round", "2": "Square", "3": "Rectangular", "4": "Oblong"}


def describe_tn(tn):
    parsed = tn_tolerance.parse_tn(tn)
    if not parsed:
        return "Special / form tool"
    series, dims = parsed
    sizes = " x ".join(f"{value:.3f}" for value in dims)
    return f"{SHAPE[series]} {sizes} in."


def _find_fg(db, ref):
    text = str(ref or "").strip()
    if not text:
        raise HTTPException(status_code=422, detail="Choose a FG")
    query = db.query(FgPlaneacion)
    fg = query.filter(FgPlaneacion.id == int(text)).first() if text.isdigit() else None
    fg = fg or query.filter(FgPlaneacion.numero_fg == text.upper()).first()
    if not fg:
        raise HTTPException(status_code=404, detail=f"FG {text} not found")
    return fg


def _board_hours(db, programs, machine_types):
    from models.inventory import DemandaPlaneacion

    hours = defaultdict(float)
    for row in db.query(DemandaPlaneacion).filter(DemandaPlaneacion.activa.is_(True)).all():
        pending = max(row.cantidad - row.piezas_producidas, 0)
        value = demandas_api._hr(programs.get((row.numero_parte.upper(), machine_types.get(row.maquina_codigo))), pending) if pending else 0
        hours[row.maquina_codigo] += value or 0
    return hours


def _tool_stock(db, tns, selected_codes):
    """Per (TN, category): usable tools, in sharpening, damaged, mounted on machines outside the scenario, at minimum life."""
    machine_codes = {code for (code,) in db.query(Ubicacion.codigo).filter(Ubicacion.tipo == "machine").all()}
    params = tool_params.load(db)
    stock = defaultdict(lambda: Counter())
    for tool in db.query(Herramienta).filter(Herramienta.tn.in_(list(tns) or [""])).all():
        key = (tool.tn, tool.categoria)
        state = tool.estado or "disponible"
        if state == "baja":
            continue
        if state != "disponible":
            stock[key]["returning" if state in SERVICE_RETURNING else "damaged"] += 1
        elif tool.ubicacion in machine_codes and tool.ubicacion not in selected_codes:
            stock[key]["elsewhere"] += 1
        else:
            stock[key]["free"] += 1
            if tool_params.life_alert(tool.categoria, tool.life, params):
                stock[key]["at_minimum"] += 1
    return stock


def build_ctb(db, fg_ref, weekly, counts, shifts=None, hours_shift=None, days=None, efficiency=None,
              include_board=True, setup_minutes=None, _alt=False):
    fg = _find_fg(db, fg_ref)
    if weekly is None or not (0 < weekly <= MAX_WEEKLY):
        raise HTTPException(status_code=422, detail=f"FG per week must be between 1 and {MAX_WEEKLY}")
    counts = {kind: int(counts.get(kind) or 0) for kind in programs_api.MACHINE_TYPES}
    if any(value < 0 or value > MAX_MACHINES for value in counts.values()):
        raise HTTPException(status_code=422, detail=f"Machines per type must be between 0 and {MAX_MACHINES}")
    if not any(counts.values()):
        raise HTTPException(status_code=422, detail="Choose at least one machine")
    params = planning_api._params(db)
    for name, value in (("turnos", shifts), ("horas_turno", hours_shift), ("dias_semana", days),
                        ("eficiencia_pct", efficiency), ("setup_minutos", setup_minutes)):
        if value is not None:
            params[name] = planning_api._check_range(name, float(value))
    setup_h = params["setup_minutos"] / 60
    per_machine_h = params["turnos"] * params["horas_turno"] * params["dias_semana"] * params["eficiencia_pct"] / 100
    bom = planning_api._bom_rows(db, fg)
    if not bom:
        raise HTTPException(status_code=422, detail=f"{fg.numero_fg} has no BOM yet: add its make items in FG and BOM")

    programs = demandas_api._program_index(db)
    warnings = []
    parts = {}
    for row in bom:
        part = row.numero_parte.upper()
        hpp, uph, issues = {}, {}, []
        thickness = None
        for kind in programs_api.MACHINE_TYPES:
            program = programs.get((part, kind))
            if not program:
                continue
            value = demandas_api._hr(program, 10000)
            if value:
                hpp[kind] = value / 10000
                uph[kind] = round(1 / hpp[kind], 1)
                if not (UPH_LOW <= uph[kind] <= UPH_HIGH):
                    issues.append(f"UPH {uph[kind]} on {kind} looks unusual: please verify it")
            else:
                issues.append(f"Program for {kind} has no UPH")
            thickness = thickness if thickness is not None else planning_api._number(program.thickness)
        parts[part] = {
            "part": part, "per_fg": row.piezas_por_fg, "pieces": weekly * row.piezas_por_fg, "hpp": hpp, "uph": uph,
            "thickness": thickness, "thickness_text": next(
                (programs[(part, kind)].thickness for kind in programs_api.MACHINE_TYPES if (part, kind) in programs), None),
            "issues": issues, "types_with_program": sorted(kind for kind in programs_api.MACHINE_TYPES if (part, kind) in programs),
        }

    # ---- machines of the scenario: enabled ones with the fewest hours already committed on the Board
    rows = planning_api._machine_rows(db)
    machine_types = {row["code"]: row["type"] for row in rows}
    committed = _board_hours(db, programs, machine_types) if include_board else defaultdict(float)
    pool = defaultdict(list)
    for row in rows:
        if row["type"] and row["included"] and not row["disabled"]:
            pool[row["type"]].append(row)
    machines, shortage = [], {}
    for kind in programs_api.MACHINE_TYPES:
        wanted = counts[kind]
        if not wanted:
            continue
        ordered = sorted(pool[kind], key=lambda row: (committed[row["code"]], row["code"]))
        if len(ordered) < wanted:
            shortage[kind] = wanted - len(ordered)
            warnings.append(f"You asked for {wanted} machine(s) of type {kind} but only {len(ordered)} are enabled")
        for row in ordered[:wanted]:
            # The scenario sets shifts, hours and days for every machine, so its own weekly capacity is not used.
            cap = per_machine_h
            taken = committed[row["code"]]
            machines.append({
                "code": row["code"], "type": kind, "thickness_min": row["thickness_min"], "thickness_max": row["thickness_max"],
                "capacity_h": round(cap, 1), "committed_h": round(taken, 1), "free": max(cap - taken, 0.0),
                "used": 0.0, "items": [],
            })
    if sum(1 for m in machines if m["thickness_min"] is None or m["thickness_max"] is None):
        warnings.append("Some machines have no thickness range: any part is assumed to fit (not verified)")

    # ---- allocate pieces: parts with fewer machine options first, fastest machine type first
    def options(part_info):
        return [m for m in machines if _hours_per_piece(m, part_info)]

    unplaced = {}
    order = sorted(parts.values(), key=lambda p: (len(options(p)), -p["pieces"] * min(p["hpp"].values(), default=0)))
    for info in order:
        remaining = info["pieces"]
        info["machines"] = {}
        while remaining > 0:
            choices = []
            for m in options(info):
                first = m["code"] not in info["machines"]
                free = m["free"] - (setup_h if first else 0)
                hpp = _hours_per_piece(m, info)
                if free > 0 and int(free / hpp) >= 1:
                    choices.append((round(hpp, 6), -m["free"], m["code"], m, first, hpp, free))
            if not choices:
                break
            _h, _f, _c, m, first, hpp, free = min(choices, key=lambda c: c[:3])
            take = min(remaining, int(free / hpp))
            hours = take * hpp
            m["free"] -= hours + (setup_h if first else 0)
            m["used"] += hours + (setup_h if first else 0)
            m["items"].append({"part": info["part"], "pieces": take, "hours": round(hours, 1), "setup_h": round(setup_h if first else 0, 1)})
            info["machines"][m["code"]] = info["machines"].get(m["code"], 0) + take
            remaining -= take
        if remaining > 0:
            unplaced[info["part"]] = remaining

    # ---- machines needed (with as many machines as it takes) and what is short in this scenario
    def best_type(info):
        return min(info["hpp"], key=lambda kind: info["hpp"][kind]) if info["hpp"] else None

    need_hours = defaultdict(float)
    for info in parts.values():
        kind = best_type(info)
        if kind:
            need_hours[kind] += info["pieces"] * info["hpp"][kind] + setup_h
    minimum = {kind: math.ceil(hours / per_machine_h - 1e-9) for kind, hours in need_hours.items() if per_machine_h > 0}
    short_hours = defaultdict(float)
    for part, pieces in unplaced.items():
        info = parts[part]
        kind = best_type(info)
        if kind:
            short_hours[kind] += pieces * info["hpp"][kind] + setup_h
    extra = {kind: math.ceil(hours / per_machine_h - 1e-9) for kind, hours in short_hours.items() if per_machine_h > 0}

    # ---- tools: one part at a time per machine, tools reused between its parts
    program_rows = {}
    for part in parts:
        for kind in programs_api.MACHINE_TYPES:
            program = programs.get((part, kind))
            if program:
                program_rows[(part, kind)] = Counter(r["tn"] for r in json.loads(program.requisitos_json))
    need = Counter()
    users = defaultdict(set)
    for m in machines:
        peak = Counter()
        for item in m["items"]:
            for tn, qty in program_rows.get((item["part"], m["type"]), {}).items():
                peak[tn] = max(peak[tn], qty)
                users[tn].add(item["part"])
        need.update(peak)
    selected = {m["code"] for m in machines}
    all_tns = {tn for counter in program_rows.values() for tn in counter}
    stock = _tool_stock(db, all_tns, selected)

    buy, risky = [], []
    for tn in sorted(need, key=lambda t: (tn_tolerance.parse_tn(t) is None, t)):
        for category in ("punzon", "matriz"):
            have = stock[(tn, category)]
            gap = max(need[tn] - have["free"], 0)
            to_buy = max(gap - have["returning"], 0)
            entry = {
                "tn": tn, "category": category, "description": describe_tn(tn), "need": need[tn], "free": have["free"],
                "returning": have["returning"], "damaged": have["damaged"], "elsewhere": have["elsewhere"],
                "at_minimum": have["at_minimum"], "missing": gap, "to_buy": to_buy, "parts": sorted(users[tn]),
            }
            if gap:
                buy.append(entry)
            elif have["at_minimum"]:
                risky.append(entry)

    # ---- per make status
    part_rows = []
    for part, info in parts.items():
        issues = list(info["issues"])
        placed = info["pieces"] - unplaced.get(part, 0)
        missing_tools = sorted({(e["tn"], e["category"]) for e in buy if part in e["parts"]})
        if not info["hpp"]:
            status = "no_program"
            issues.append("No program with UPH for any machine type")
        elif not options(info) and counts:
            status = "no_machine"
            issues.append("None of the chosen machines can run it (type or thickness)")
        elif part in unplaced:
            status = "no_capacity"
        elif missing_tools:
            status = "no_tools"
        else:
            status = "ready"
        part_rows.append({
            "part": part, "per_fg": info["per_fg"], "pieces_week": info["pieces"], "placed": placed,
            "types": info["types_with_program"], "uph": info["uph"], "thickness": info["thickness_text"],
            "machines": info["machines"], "status": status, "issues": issues,
            "tools_missing": len(missing_tools),
        })

    # ---- verdict, pillars and the list of what is missing
    loads = [m["used"] / m["capacity_h"] * 100 if m["capacity_h"] else 0 for m in machines]
    no_program = [p["part"] for p in part_rows if p["status"] == "no_program"]
    blockers = []
    for part in no_program:
        blockers.append({"kind": "program", "text": f"{part} has no program with UPH: load it or fill in its UPH."})
    for p in part_rows:
        if p["status"] == "no_machine":
            blockers.append({"kind": "machine", "text": f"{p['part']} cannot run on the chosen machines (types with program: {', '.join(p['types']) or 'none'})."})
    for kind, value in sorted(extra.items()):
        blockers.append({"kind": "machine", "text": f"{value} more {kind} machine(s) are needed to fit the demand."})
    for kind, value in sorted(shortage.items()):
        blockers.append({"kind": "machine", "text": f"{value} of the {kind} machines you asked for are not enabled."})
    by_tn = defaultdict(list)
    for e in buy:
        by_tn[e["tn"]].append(e)
    for tn, entries in by_tn.items():
        same = len(entries) == 2 and (entries[0]["need"], entries[0]["free"], entries[0]["to_buy"], entries[0]["returning"]) == (
            entries[1]["need"], entries[1]["free"], entries[1]["to_buy"], entries[1]["returning"])
        groups = [(entries, "punch + die")] if same else [([e], "punch" if e["category"] == "punzon" else "die") for e in entries]
        for group, label in groups:
            e = group[0]
            text = f"TN {tn} ({e['description']}), {label}: need {e['need']}, have {e['free']} usable"
            text += f" → buy {e['to_buy']}" + (" of each" if len(group) > 1 else "") if e["to_buy"] else f" → {e['returning']} come back from sharpening"
            blockers.append({"kind": "tool", "text": text})
    programs_status = "blocked" if no_program else ("risk" if any(p["issues"] for p in part_rows) else "ok")
    machines_status = "blocked" if (unplaced or shortage or any(p["status"] == "no_machine" for p in part_rows)) else (
        "risk" if loads and max(loads) >= TIGHT_LOAD else "ok")
    tools_status = "blocked" if any(e["to_buy"] or e["missing"] for e in buy) else ("risk" if risky else "ok")
    statuses = (programs_status, machines_status, tools_status)
    verdict = "not_ready" if "blocked" in statuses else ("risk" if "risk" in statuses else "ready")
    if risky:
        warnings.append(f"{len({e['tn'] for e in risky})} TN have usable tools at minimum life: plan their replacement (listed in the tools table)")
    if include_board and any(m["committed_h"] for m in machines):
        warnings.append("Hours already committed on the Board are taken out of the machines' capacity")

    used_total = sum(m["used"] for m in machines)
    room_total = sum(max(m["capacity_h"] - m["committed_h"], 0) for m in machines)
    headroom = int(weekly * room_total / used_total) if used_total > 0 and not unplaced else None
    alternative = None
    if not _alt:
        smallest = {kind: min(counts[kind], minimum.get(kind, 0)) for kind in counts if counts[kind]}
        if sum(smallest.values()) and any(smallest[k] < counts[k] for k in smallest):
            try:
                other = build_ctb(db, fg_ref, weekly, smallest, shifts, hours_shift, days, efficiency, include_board,
                                  setup_minutes, _alt=True)
                alternative = {
                    "counts": smallest, "units_to_buy": other["pillars"]["tools"]["units_to_buy"],
                    "distinct_tn": other["pillars"]["tools"]["distinct_tn"], "machines_status": other["pillars"]["machines"]["status"],
                    "max_load_pct": other["pillars"]["machines"]["max_load_pct"],
                }
            except HTTPException:
                alternative = None

    return {
        "fg": {"id": fg.id, "numero_fg": fg.numero_fg, "description": fg.descripcion, "customer": fg.cliente},
        "weekly": weekly, "verdict": verdict, "headroom_fg": headroom, "alternative": alternative,
        "params": {**params, "capacity_per_machine_h": round(per_machine_h, 1)},
        "pillars": {
            "programs": {"status": programs_status, "ok": len(parts) - len(no_program), "total": len(parts)},
            "machines": {
                "status": machines_status, "chosen": counts, "found": sum(1 for _ in machines),
                "max_load_pct": round(max(loads), 1) if loads else 0,
                "avg_load_pct": round(sum(loads) / len(loads), 1) if loads else 0,
                "minimum": minimum, "extra": extra, "short_pieces": sum(unplaced.values()),
                "hours_needed": {kind: round(value, 1) for kind, value in need_hours.items()},
            },
            "tools": {
                "status": tools_status, "distinct_tn": len({e["tn"] for e in buy}),
                "units_to_buy": sum(e["to_buy"] for e in buy), "units_missing": sum(e["missing"] for e in buy),
                "at_minimum": len(risky), "units_needed": sum(need.values()) * 2,
            },
        },
        "parts": sorted(part_rows, key=lambda p: p["part"]),
        "machines": [
            {"code": m["code"], "type": m["type"], "capacity_h": m["capacity_h"], "committed_h": m["committed_h"],
             "used_h": round(m["used"], 1), "load_pct": round(m["used"] / m["capacity_h"] * 100, 1) if m["capacity_h"] else 0,
             "items": m["items"]}
            for m in machines
        ],
        "buy": buy, "at_minimum": risky, "blockers": blockers, "warnings": warnings,
    }


def _counts(c4i, c2i, c45):
    return {"4I": c4i, "2I": c2i, "45STA": c45}


@router.get("")
def read_ctb(fg: str, cantidad: int, c4i: int = 0, c2i: int = 0, c45: int = 0, turnos: Optional[float] = None,
             horas: Optional[float] = None, dias: Optional[float] = None, eficiencia: Optional[float] = None,
             tablero: int = 1, setup: Optional[float] = None):
    require_permission("planning.view")
    db = SessionLocal()
    try:
        return build_ctb(db, fg, cantidad, _counts(c4i, c2i, c45), turnos, horas, dias, eficiencia, bool(tablero), setup)
    finally:
        db.close()


@router.get("/excel")
def ctb_excel(fg: str, cantidad: int, c4i: int = 0, c2i: int = 0, c45: int = 0, turnos: Optional[float] = None,
              horas: Optional[float] = None, dias: Optional[float] = None, eficiencia: Optional[float] = None,
              tablero: int = 1, setup: Optional[float] = None):
    user = require_permission("planning.view")
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter

    db = SessionLocal()
    try:
        data = build_ctb(db, fg, cantidad, _counts(c4i, c2i, c45), turnos, horas, dias, eficiencia, bool(tablero), setup)
    finally:
        db.close()
    workbook = Workbook()
    workbook.remove(workbook.active)

    def sheet(title, header, rows):
        ws = workbook.create_sheet(title)
        ws.append(header)
        for cell in ws[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="1F3864")
        for row in rows:
            ws.append(row)
        ws.freeze_panes = "A2"
        for index, label in enumerate(header, start=1):
            longest = max([len(str(label))] + [len(str(row[index - 1])) for row in rows[:300]])
            ws.column_dimensions[get_column_letter(index)].width = min(max(longest + 2, 8), 70)
        if rows:
            ws.auto_filter.ref = ws.dimensions

    label = {"ready": "CLEAR TO BUILD", "risk": "CLEAR TO BUILD WITH RISKS", "not_ready": "NOT CLEAR TO BUILD"}[data["verdict"]]
    sheet("Summary", ["Item", "Value"], [
        ["FG", data["fg"]["numero_fg"]], ["FG per week", data["weekly"]], ["Verdict", label],
        ["Machines chosen", ", ".join(f"{k}: {v}" for k, v in data["pillars"]["machines"]["chosen"].items() if v)],
        ["Hours per machine per week", data["params"]["capacity_per_machine_h"]],
        ["Minimum machines needed", ", ".join(f"{k}: {v}" for k, v in data["pillars"]["machines"]["minimum"].items())],
        ["Programs ready", f"{data['pillars']['programs']['ok']} of {data['pillars']['programs']['total']}"],
        ["Tool units to buy", data["pillars"]["tools"]["units_to_buy"]],
        *[["Missing", b["text"]] for b in data["blockers"]], *[["Warning", w] for w in data["warnings"]],
        ["Exported by", user.username], ["Exported", datetime.now().astimezone().strftime("%Y-%m-%d %H:%M")],
    ])
    sheet("Tools to buy", ["TN", "Type", "Description", "Needed", "Usable now", "In sharpening", "Damaged", "On other machines", "To buy", "Make items"], [
        [e["tn"], "Punch" if e["category"] == "punzon" else "Die", e["description"], e["need"], e["free"], e["returning"],
         e["damaged"], e["elsewhere"], e["to_buy"], ", ".join(e["parts"])] for e in data["buy"]
    ])
    sheet("Make items", ["Make item", "Per FG", "Pieces/week", "Placed", "Status", "Programs", "UPH", "Machines", "Tools missing", "Notes"], [
        [p["part"], p["per_fg"], p["pieces_week"], p["placed"], p["status"], ", ".join(p["types"]),
         ", ".join(f"{k}: {v}" for k, v in p["uph"].items()), ", ".join(f"{k} ({v})" for k, v in p["machines"].items()),
         p["tools_missing"], "; ".join(p["issues"])] for p in data["parts"]
    ])
    sheet("Machines", ["Machine", "Type", "Capacity h", "Board h", "Used h", "Load %", "Runs"], [
        [m["code"], m["type"], m["capacity_h"], m["committed_h"], m["used_h"], m["load_pct"],
         ", ".join(f"{i['part']} ({i['pieces']})" for i in m["items"])] for m in data["machines"]
    ])
    buffer = io.BytesIO()
    workbook.save(buffer)
    name = f"ctb-{data['fg']['numero_fg']}-{datetime.now().strftime('%Y%m%d-%H%M')}.xlsx"
    return Response(content=buffer.getvalue(), media_type=XLSX, headers={"Content-Disposition": f'attachment; filename="{name}"'})
