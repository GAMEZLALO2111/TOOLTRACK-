"""Surtido (kitting) para la web: compara programas guardados, arma la lista y abre el surtido en el servidor."""
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import programs_api
import workflow_api
from db import SessionLocal
from models.inventory import EstadoMaquina, Maquina, ProgramaParte, Setup, Ubicacion
from pick_list import build_pick_items

router = APIRouter(prefix="/workflow/kitting", tags=["kitting"])


class PlanRequest(BaseModel):
    maquina_codigo: str
    setup_nuevo_parte: str
    setup_anterior_parte: Optional[str] = None


class StartRequest(BaseModel):
    maquina_codigo: str
    setup_nuevo_parte: str
    setup_anterior_parte: Optional[str] = None
    carro_codigo: Optional[str] = None
    corregir_actual: bool = False
    motivo_correccion: Optional[str] = None
    creado_por: str


def _machine_kind(db, code):
    location = db.query(Ubicacion).filter(
        Ubicacion.codigo == code, Ubicacion.tipo == "machine", Ubicacion.activa.is_(True)
    ).first()
    if not location:
        raise HTTPException(status_code=404, detail=f"Machine {code} is not registered or is inactive")
    profile = db.query(Maquina).filter(Maquina.codigo == code).first()
    kind = workflow_api._machine_kind(profile.tipo_maquina) if profile else ""
    if kind not in programs_api.MACHINE_TYPES:
        raise HTTPException(
            status_code=422, detail=f"Machine {code} has no type (4I, 2I or 45STA). Set it in Machines."
        )
    return kind


def _mounted_name(db, code):
    state = db.query(EstadoMaquina).filter(EstadoMaquina.maquina_codigo == code).first()
    setup = (
        db.query(Setup).filter(Setup.id == state.setup_activo_id).first()
        if state and state.setup_activo_id else None
    )
    return workflow_api._part_from_name(setup.nombre_archivo).upper() if setup else None


def _stored_program(db, kind, part):
    part = (part or "").strip().upper()
    if not part:
        raise HTTPException(status_code=422, detail="Choose a part number")
    program = db.query(ProgramaParte).filter(
        ProgramaParte.tipo_maquina == kind, ProgramaParte.numero_parte == part
    ).first()
    if program:
        return program
    other_types = sorted({
        row.tipo_maquina for row in db.query(ProgramaParte).filter(ProgramaParte.numero_parte == part).all()
    })
    if other_types:
        raise HTTPException(
            status_code=422,
            detail=f"{part} has no {kind} program; it only exists for {', '.join(other_types)}. "
                   "It cannot be used on this machine.",
        )
    raise HTTPException(status_code=422, detail=f"{part} is not in the system")


def _default_current(db, kind, mounted):
    """El programa montado en la máquina es el setup actual natural (coincidencia exacta o contenida)."""
    if not mounted:
        return None
    programs = db.query(ProgramaParte).filter(ProgramaParte.tipo_maquina == kind).all()
    return next((p for p in programs if p.numero_parte.upper() == mounted), None) or next(
        (p for p in programs if p.numero_parte.upper() in mounted), None
    )


def _cart_rows(db, kits):
    by_cart = {kit["cart"]: kit for kit in kits}
    carts = db.query(Ubicacion).filter(
        Ubicacion.tipo == "cart", Ubicacion.activa.is_(True)
    ).order_by(Ubicacion.codigo).all()
    rows = []
    for cart in carts:
        kit = by_cart.get(cart.codigo)
        if not kit:
            rows.append({"codigo": cart.codigo, "state": "free", "kit_id": None, "machine": None,
                         "done": 0, "total": 0, "pending": [], "reason": None})
            continue
        needed = [c for c in kit["components"] if c["action"] != "keep"]
        missing = [c for c in needed if c["state"] == "missing"]
        rows.append({
            "codigo": cart.codigo, "state": kit["state"], "kit_id": kit["id"], "machine": kit["machine"],
            "done": len(needed) - len(missing), "total": len(needed),
            "pending": [{"tn": c["tn"], "category": c["category"]} for c in missing],
            "reason": kit.get("reason"),
        })
    return rows


@router.get("/overview")
def overview():
    """Estado de las máquinas y de los carros (para empezar o retomar un surtido)."""
    kits = workflow_api.list_open_kits()
    db = SessionLocal()
    try:
        return {"machines": workflow_api.machines_running(), "carts": _cart_rows(db, kits)}
    finally:
        db.close()


@router.post("/plan")
def plan(item: PlanRequest):
    """Compara el setup montado (o el elegido) contra el nuevo y devuelve qué surtir."""
    code = item.maquina_codigo.strip().upper()
    db = SessionLocal()
    try:
        kind = _machine_kind(db, code)
        new_program = _stored_program(db, kind, item.setup_nuevo_parte)
        mounted = _mounted_name(db, code)
        current_program = (
            _stored_program(db, kind, item.setup_anterior_parte)
            if (item.setup_anterior_parte or "").strip() else _default_current(db, kind, mounted)
        )
        kits = workflow_api.list_open_kits()
        carts = _cart_rows(db, kits)
        result = {
            "machine": code,
            "tipo": kind,
            "mounted_part": mounted,
            "current_part": current_program.numero_parte if current_program else None,
            "new_part": new_program.numero_parte,
            "needs_current": current_program is None,
            "comparison": None,
            "pick_list": [],
            "open_kit": next((kit for kit in kits if kit["machine"] == code), None),
            "carts": carts,
            "suggested_cart": next((c["codigo"] for c in carts if c["state"] == "free"), None),
        }
        if current_program is None:
            return result
        comparison = workflow_api.compare_setup_files(workflow_api.SetupFilesComparisonRequest(
            setup_anterior_nombre=f"{current_program.numero_parte}.stp",
            setup_anterior_contenido=current_program.contenido,
            setup_nuevo_nombre=f"{new_program.numero_parte}.stp",
            setup_nuevo_contenido=new_program.contenido,
        ))
        pick_list = build_pick_items(comparison)
        current_setup, new_setup = comparison["current_setup"], comparison["new_setup"]
        result["comparison"] = {
            "thickness_current": current_setup["thickness"],
            "thickness_new": new_setup["thickness"],
            "thickness_changed": comparison["thickness_changed"],
            "clearance_min": new_setup["clearance_min"],
            "clearance_max": new_setup["clearance_max"],
            "thickness_out_of_range": new_setup["thickness_out_of_range"],
            "supply": sum(1 for row in pick_list if row["kind"] == "supply"),
            "verify": sum(1 for row in pick_list if row["kind"] == "verify"),
        }
        result["pick_list"] = pick_list
        return result
    finally:
        db.close()


@router.post("/start", status_code=201)
def start(item: StartRequest):
    code = item.maquina_codigo.strip().upper()
    db = SessionLocal()
    try:
        kind = _machine_kind(db, code)
        new_program = _stored_program(db, kind, item.setup_nuevo_parte)
        mounted = _mounted_name(db, code)
        current_program = (
            _stored_program(db, kind, item.setup_anterior_parte)
            if (item.setup_anterior_parte or "").strip() else _default_current(db, kind, mounted)
        )
        if current_program is None:
            raise HTTPException(status_code=422, detail=f"Choose the part currently mounted on {code}")
        cart = (item.carro_codigo or "").strip().upper()
        if not cart:
            cart = next((c["codigo"] for c in _cart_rows(db, workflow_api.list_open_kits()) if c["state"] == "free"), "")
            if not cart:
                raise HTTPException(
                    status_code=409, detail="No free carts. Finish or cancel an open kit first."
                )
        payload = workflow_api.SurtidoCreate(
            maquina_codigo=code,
            carro_codigo=cart,
            nombre_archivo=f"{new_program.numero_parte}.stp",
            contenido=new_program.contenido,
            creado_por=item.creado_por,
            setup_anterior_nombre=f"{current_program.numero_parte}.stp",
            setup_anterior_contenido=current_program.contenido,
            corregir_actual=item.corregir_actual,
            motivo_correccion=item.motivo_correccion,
        )
        chosen_current = current_program.numero_parte
    finally:
        db.close()

    try:
        return workflow_api.create_kit(payload)
    except HTTPException as error:
        if error.status_code == 409 and str(error.detail).startswith("SETUP_NO_COINCIDE"):
            raise HTTPException(status_code=409, detail={
                "code": "SETUP_MISMATCH",
                "message": f"The system has {mounted or 'no part'} registered on {code}, "
                           f"but you chose {chosen_current} as the mounted part.",
                "registered": mounted,
                "chosen": chosen_current,
            }) from error
        raise
