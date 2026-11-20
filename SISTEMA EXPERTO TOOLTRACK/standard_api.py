"""Standard turret designer API (menu Planning). It proposes the station of each tool number for the programmers
and never changes a machine, a setup or a program."""
import io
import json
from collections import Counter, defaultdict
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Response

import planning_api
import standard_turret
import tn_tolerance
import workflow_api
from auth import require_permission
from db import SessionLocal
from models.inventory import EstacionMaquina, Herramienta, Maquina, ProgramaParte

router = APIRouter(prefix="/planeacion/estandar", tags=["estandar"])

MAX_PARTS = 120
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _decimal(text):
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _sizes_by_tn(db):
    counts = defaultdict(Counter)
    for tn, size in db.query(Herramienta.tn, Herramienta.size).filter(
        Herramienta.tn.isnot(None), Herramienta.size.isnot(None)
    ).all():
        if size.strip():
            counts[tn][size.strip().upper()] += 1
    return {tn: counter.most_common(1)[0][0] for tn, counter in counts.items()}


def _part_list(text):
    seen, result = set(), []
    for raw in (text or "").replace("\n", ",").split(","):
        part = raw.strip().upper()
        if part and part not in seen:
            seen.add(part)
            result.append(part)
    return result


def build_design(db, machine_code, part_numbers):
    code = (machine_code or "").strip().upper()
    machine = workflow_api._location(db, code, {"machine"})
    profile = db.query(Maquina).filter(Maquina.codigo == machine.codigo).first()
    kind = workflow_api._machine_kind(profile.tipo_maquina) if profile else ""
    if kind not in {"4I", "2I", "45STA"}:
        raise HTTPException(status_code=422, detail=f"{code} has no machine type (4I, 2I or 45STA) in its profile")
    stations_all = db.query(EstacionMaquina).filter(EstacionMaquina.maquina_codigo == code).all()
    if not stations_all:
        raise HTTPException(status_code=422, detail=f"{code} has no stations registered in its profile")
    if not part_numbers:
        raise HTTPException(status_code=422, detail="Choose at least one part number")
    if len(part_numbers) > MAX_PARTS:
        raise HTTPException(status_code=422, detail=f"Up to {MAX_PARTS} part numbers at a time")

    stations = [
        {"number": row.numero, "size": (row.tamano or "").strip().upper() or None, "autoindex": bool(row.autoindex)}
        for row in stations_all if (row.estado or "disponible") == "disponible"
    ]
    programs = {
        program.numero_parte: program for program in db.query(ProgramaParte).filter(
            ProgramaParte.numero_parte.in_(part_numbers), ProgramaParte.tipo_maquina == kind,
        ).all()
    }
    parts, skipped, warnings = [], [], []
    low, high = _decimal(profile.espesor_min), _decimal(profile.espesor_max)
    for number in part_numbers:
        program = programs.get(number)
        if not program:
            skipped.append({"part": number, "reason": f"no {kind} program"})
            continue
        requirements = json.loads(program.requisitos_json)
        if not requirements:
            skipped.append({"part": number, "reason": "program without stations"})
            continue
        thickness = _decimal(program.thickness)
        if thickness is not None and ((low is not None and thickness < low) or (high is not None and thickness > high)):
            warnings.append(f"{number}: thickness {program.thickness} in is outside the range of {code}")
        parts.append({
            "part": number, "thickness": program.thickness,
            "reqs": [{"station": r["station"], "tn": r["tn"], "angle": r.get("angle")} for r in requirements],
        })
    if not parts:
        raise HTTPException(status_code=422, detail="None of those part numbers has a program to design with")

    sizes = _sizes_by_tn(db)
    tolerance = tn_tolerance.tolerance_for(db)
    result = standard_turret.design(stations, parts, sizes, tolerance)
    known = {station["number"] for station in stations}
    for part in parts:
        missing = sorted({r["station"] for r in part["reqs"] if standard_turret.base_station(r["station"]) not in known})
        if missing:
            warnings.append(f"{part['part']}: station {', '.join(missing)} is not an available station of {code}")
    no_size = sorted({row["tn"] for row in result["assignments"] if not row["size_known"]} | {
        row["tn"] for row in result["unplaced"] if not row["size"]
    })
    if no_size:
        sample = ", ".join(no_size[:12]) + (f" and {len(no_size) - 12} more" if len(no_size) > 12 else "")
        warnings.append(f"{len(no_size)} TN without a tool size in the inventory (any station accepted): {sample}")
    if result["unplaced"]:
        result["suggestions"] = standard_turret.suggest_removals(stations, parts, sizes, tolerance)
    else:
        result["suggestions"] = []
    result.update({
        "machine": code, "machine_type": kind, "skipped": skipped, "warnings": warnings,
        "tolerance": float(tolerance), "stations_total": len(stations_all),
        "stations_damaged": len(stations_all) - len(stations),
        "thickness_min": min((p["thickness"] for p in parts if p["thickness"]), default=None),
        "thickness_max": max((p["thickness"] for p in parts if p["thickness"]), default=None),
    })
    return result


@router.get("")
def read_design(maquina: str, partes: str):
    require_permission("planning.view")
    db = SessionLocal()
    try:
        return build_design(db, maquina, _part_list(partes))
    finally:
        db.close()


@router.get("/propuesta")
def parts_from_proposal(maquina: str, semanas: Optional[int] = None):
    """Part numbers that the planning proposal sends to this machine, to design its standard from them."""
    require_permission("planning.view")
    db = SessionLocal()
    try:
        code = maquina.strip().upper()
        result = planning_api.build_capacity(db, semanas, with_plan=True)
        pieces = Counter()
        for week in result["proposal"]["weeks"]:
            for entry in week["machines"]:
                if entry["code"] == code:
                    for item in entry["items"]:
                        pieces[item["numero_parte"]] += item["piezas"]
        return [{"part": part, "pieces": total} for part, total in pieces.most_common()]
    finally:
        db.close()


@router.get("/excel")
def design_excel(maquina: str, partes: str):
    user = require_permission("planning.view")
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter

    db = SessionLocal()
    try:
        data = build_design(db, maquina, _part_list(partes))
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
            longest = max([len(str(label))] + [len(str(row[index - 1] if index - 1 < len(row) else "")) for row in rows[:300]])
            ws.column_dimensions[get_column_letter(index)].width = min(max(longest + 2, 8), 60)
        if rows:
            ws.auto_filter.ref = ws.dimensions

    sheet("Standard", ["Station", "Station size", "Autoindex", "TN", "Also covers", "Tool size", "Adapter", "Parts using it", "Kept as in .stp", "Parts"], [
        [r["station"], r["station_size"], "yes" if r["autoindex"] else "", r["tn"], ", ".join(r["also"]), r["tool_size"] or "?",
         "A in B" if r["adapter"] else "", r["of"], r["kept_by"], ", ".join(r["parts"])]
        for r in data["assignments"]
    ])
    rows = []
    for part in data["parts"]:
        for r in part["rows"]:
            rows.append([
                part["part"], part["thickness"], r["current_station"], r["new_station"] or "OFF STANDARD", r["tn"], r["angle"] or "",
                "yes" if r["changed"] else "", r["substitute_tn"] or "", r["difference"] or "",
            ])
    sheet("Parts", ["Part", "Thickness", "Station in .stp", "Station in standard", "TN in .stp", "Angle", "Change the .stp", "Mount TN", "Difference (in.)"], rows)
    sheet("Not placed", ["TN", "Also covers", "Size", "Reason", "Parts"], [
        [r["tn"], ", ".join(r["also"]), r["size"] or "?", r["reason"], ", ".join(r["parts"])] for r in data["unplaced"]
    ])
    sheet("Summary", ["Item", "Value"], [
        ["Machine", f"{data['machine']} ({data['machine_type']})"],
        ["Stations available", data["stations_available"]], ["Damaged stations", data["stations_damaged"]],
        ["Tools needed", data["tools"]], ["Tools placed", data["placed"]], ["Tools not placed", len(data["unplaced"])],
        ["TNs merged by tolerance", data["tool_merges"]], ["Tolerance (in.)", data["tolerance"]],
        [".stp lines to change", f"{data['total_changes']} of {data['total_rows']}"],
        ["Free stations by size", ", ".join(f"{k}: {v}" for k, v in data["free_stations"].items())],
        *[["Warning", text] for text in data["warnings"]],
        *[["Skipped", f"{s['part']}: {s['reason']}"] for s in data["skipped"]],
        *[["To fit, leave out", f"{s['remove']} (not placed after: {s['unplaced_after']})"] for s in data["suggestions"]],
        ["Exported by", user.username], ["Exported", datetime.now().astimezone().strftime("%Y-%m-%d %H:%M")],
    ])
    buffer = io.BytesIO()
    workbook.save(buffer)
    name = f"standard-{data['machine']}-{datetime.now().strftime('%Y%m%d-%H%M')}.xlsx"
    return Response(content=buffer.getvalue(), media_type=XLSX, headers={"Content-Disposition": f'attachment; filename="{name}"'})
