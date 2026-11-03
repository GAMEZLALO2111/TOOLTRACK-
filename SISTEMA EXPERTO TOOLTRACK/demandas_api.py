from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import func

from db import SessionLocal
from auth import require_permission
from models.inventory import (
    DemandaPlaneacion,
    EstadoMaquina,
    Maquina,
    ProgramaParte,
    Setup,
    Surtido,
    Ubicacion,
)
from workflow_api import OPEN_KIT_STATES, _machine_kind, _part_from_name, utcnow

router = APIRouter(prefix="/workflow/demandas", tags=["demandas"])

MARCAS = ("PARO PROGRAMADO", "URGENTE", "LÁSER", "CORTE DE BLANCK")
ESTADO_POR_KIT = {
    "preparing": "SURTIENDO",
    "paused": "FALTA HERRAMIENTAS",
    "ready": "EN MÁQUINA",
}


MAX_CANTIDAD = 1_000_000
CAMPOS_POR_PERMISO = {
    "cantidad": "demand.manage",
    "orden": "demand.manage",
    "piezas_producidas": "demand.progress",
    "marca": "demand.mark",
    "comentario": "demand.mark",
}


class DemandaCreate(BaseModel):
    maquina_codigo: str
    numero_parte: str
    cantidad: int
    orden: Optional[int] = None
    creado_por: str


class DemandaUpdate(BaseModel):
    cantidad: Optional[int] = None
    piezas_producidas: Optional[int] = None
    orden: Optional[int] = None
    marca: Optional[str] = None
    comentario: Optional[str] = None


def _program_index(db):
    return {
        (p.numero_parte.upper(), p.tipo_maquina.upper()): p
        for p in db.query(ProgramaParte).all()
    }


def _hr(program, pieces):
    """Horas de máquina para `pieces` piezas: pzas × cycle c/lead ÷ (pzas por blank × 3600)."""
    if not program or not program.piezas_blank or not program.cycle_con_lead_s:
        return None
    return round(pieces * program.cycle_con_lead_s / (program.piezas_blank * 3600), 2)


def _estado(pendiente, running_part, kit_state, numero_parte):
    if pendiente == 0:
        return "TERMINADO"
    if running_part == numero_parte:
        return "CORRIENDO"
    return ESTADO_POR_KIT.get(kit_state, "NO INICIADO")


UMBRAL_ROJO_HR = 2.0
UMBRAL_AMARILLO_HR = 4.0
NIVEL_RANK = {"ROJO": 0, "AMARILLO": 1, "VERDE": 2, "PARO": 3, "SIN_COLA": 4}
POR_SURTIR = ("NO INICIADO", "FALTA HERRAMIENTAS")


def _nivel_por_hr(hr):
    if hr < UMBRAL_ROJO_HR:
        return "ROJO"
    if hr < UMBRAL_AMARILLO_HR:
        return "AMARILLO"
    return "VERDE"


def _assign_estados(items, running, kit_states):
    """Asigna estado a cada demanda; CORRIENDO y el kit abierto solo aplican a la primera NP repetida."""
    running_used = False
    kits_used = set()
    for item in items:
        np = item["numero_parte"].upper()
        kit = None
        if item["pendiente"] > 0:
            if running == np and not running_used:
                running_used = True
                item["estado"] = "CORRIENDO"
                continue
            if np not in kits_used:
                kit = kit_states.get(np)
                if kit:
                    kits_used.add(np)
        item["estado"] = _estado(item["pendiente"], None, kit, np)


def _analizar(running, items):
    """Semáforo de la máquina según las HR que le quedan a la NP que corre."""
    result = {
        "nivel": "SIN_COLA", "motivo": "No demands entered", "hr_restante": None,
        "hr_cola": 0.0, "actual_id": None, "siguiente_id": None, "surtir_urgente": False,
    }
    if not items:
        return result
    pending = [i for i in items if i["pendiente"] > 0]
    result["hr_cola"] = round(sum(i["hr_pendiente"] or 0 for i in pending), 2)
    actual = next((i for i in pending if i["estado"] == "CORRIENDO"), None)
    siguiente = next((i for i in pending if i is not actual), None)
    result["actual_id"] = actual["id"] if actual else None
    result["siguiente_id"] = siguiente["id"] if siguiente else None

    if not pending:
        result.update(nivel="ROJO", motivo="Queue finished: more demand is needed")
        return result
    if actual:
        if actual["marca"] == "PARO PROGRAMADO":
            result.update(nivel="PARO", motivo="Scheduled stop")
            return result
        hr = actual["hr_pendiente"]
        result["hr_restante"] = hr
        if hr is None:
            result.update(nivel="AMARILLO", motivo="No program: time cannot be calculated")
        else:
            result.update(nivel=_nivel_por_hr(hr),
                          motivo="Current part is about to finish" if hr < UMBRAL_ROJO_HR else "Running")
    else:
        first = pending[0]
        if first["marca"] == "PARO PROGRAMADO":
            result.update(nivel="PARO", motivo="Scheduled stop")
            return result
        finished = running and any(
            i["pendiente"] == 0 and i["numero_parte"].upper() == running for i in items
        )
        if not running:
            result.update(nivel="ROJO", motivo=f"Machine empty: mount {first['numero_parte']}")
        elif finished:
            result.update(nivel="ROJO", motivo=f"Finished {running}: change over to {first['numero_parte']}")
        else:
            result.update(nivel="AMARILLO", motivo=f"Running {running}, which is not in the queue")
    if result["nivel"] in ("ROJO", "AMARILLO") and siguiente and siguiente["estado"] in POR_SURTIR:
        result["surtir_urgente"] = True
    return result


def _validate_marca(marca, comentario):
    if marca is None:
        return
    if marca not in MARCAS:
        raise HTTPException(status_code=400, detail=f"Marca no válida: {marca}")
    if not (comentario or "").strip():
        raise HTTPException(status_code=400, detail=f"La marca {marca} requiere comentario")


def _validate_cantidad(cantidad):
    if cantidad <= 0:
        raise HTTPException(status_code=400, detail="La cantidad debe ser mayor a cero")
    if cantidad > MAX_CANTIDAD:
        raise HTTPException(status_code=400, detail=f"La cantidad máxima es {MAX_CANTIDAD:,} piezas")


@router.get("/board")
def demandas_board(edificio: str = "B1"):
    db = SessionLocal()
    try:
        edificio = edificio.strip().upper()
        machines = (
            db.query(Ubicacion, Maquina)
            .join(Maquina, Maquina.codigo == Ubicacion.codigo)
            .filter(
                Ubicacion.tipo == "machine",
                Ubicacion.activa.is_(True),
                func.upper(Maquina.edificio) == edificio,
            )
            .order_by(Ubicacion.codigo)
            .all()
        )
        codes = [loc.codigo for loc, _ in machines]

        running = {}
        for state in db.query(EstadoMaquina).filter(EstadoMaquina.maquina_codigo.in_(codes)).all():
            if state.setup_activo_id:
                setup = db.query(Setup).filter(Setup.id == state.setup_activo_id).first()
                if setup:
                    running[state.maquina_codigo] = _part_from_name(setup.nombre_archivo).upper()

        kit_states = {}
        for kit in db.query(Surtido).filter(
            Surtido.estado.in_(OPEN_KIT_STATES), Surtido.maquina_codigo.in_(codes)
        ).all():
            setup = db.query(Setup).filter(Setup.id == kit.setup_nuevo_id).first()
            if setup:
                part = _part_from_name(setup.nombre_archivo).upper()
                kit_states[(kit.maquina_codigo, part)] = kit.estado

        programs = _program_index(db)
        demands = db.query(DemandaPlaneacion).filter(
            DemandaPlaneacion.activa.is_(True),
            DemandaPlaneacion.maquina_codigo.in_(codes),
        ).order_by(DemandaPlaneacion.orden, DemandaPlaneacion.id).all()

        kinds = {loc.codigo: _machine_kind(profile.tipo_maquina) for loc, profile in machines}
        by_machine = {code: [] for code in codes}
        for demand in demands:
            np = demand.numero_parte.upper()
            machine = demand.maquina_codigo
            kind = kinds[machine]
            program = programs.get((np, kind))
            pendiente = max(demand.cantidad - demand.piezas_producidas, 0)
            by_machine[machine].append({
                "id": demand.id,
                "numero_parte": demand.numero_parte,
                "cantidad": demand.cantidad,
                "piezas_producidas": demand.piezas_producidas,
                "pendiente": pendiente,
                "hr": _hr(program, demand.cantidad),
                "hr_pendiente": _hr(program, pendiente),
                "sin_programa": program is None,
                "estado": None,
                "marca": demand.marca,
                "comentario": demand.comentario,
                "orden": demand.orden,
            })

        result = []
        for loc, profile in machines:
            code = loc.codigo
            items = by_machine.get(code, [])
            machine_kits = {np: state for (m, np), state in kit_states.items() if m == code}
            _assign_estados(items, running.get(code), machine_kits)
            analysis = _analizar(running.get(code), items)
            for item in items:
                item["es_actual"] = item["id"] == analysis["actual_id"]
                item["es_siguiente"] = item["id"] == analysis["siguiente_id"]
            hrs = [i["hr"] for i in items if i["hr"] is not None]
            cubierto = [i["hr"] for i in items if i["estado"] == "TERMINADO" and i["hr"] is not None]
            result.append({
                "maquina": code,
                "tipo": kinds[code],
                "corriendo": running.get(code),
                "tie_total": round(sum(hrs), 2),
                "tie_cubierto": round(sum(cubierto), 2),
                "demandas": items,
                **analysis,
            })
        result.sort(key=lambda m: (
            NIVEL_RANK[m["nivel"]],
            -1 if m["hr_restante"] is None else m["hr_restante"],
            m["maquina"],
        ))
        resumen = {nivel: sum(1 for m in result if m["nivel"] == nivel) for nivel in NIVEL_RANK}
        return {"edificio": edificio, "maquinas": result, "marcas": list(MARCAS), "resumen": resumen}
    finally:
        db.close()


@router.post("", status_code=201)
def crear_demanda(item: DemandaCreate):
    db = SessionLocal()
    try:
        code = item.maquina_codigo.strip().upper()
        np = item.numero_parte.strip().upper()
        _validate_cantidad(item.cantidad)
        profile = db.query(Maquina).filter(Maquina.codigo == code).first()
        location = db.query(Ubicacion).filter(Ubicacion.codigo == code).first()
        if not profile or not location:
            raise HTTPException(status_code=404, detail=f"Máquina no encontrada: {code}")
        if not location.activa:
            raise HTTPException(status_code=400, detail=f"La máquina {code} está desactivada")
        kind = _machine_kind(profile.tipo_maquina)
        if (np.upper(), kind) not in _program_index(db):
            raise HTTPException(
                status_code=400,
                detail=f"No hay programa guardado de {np} para {kind or 'este tipo de máquina'}",
            )
        orden = item.orden
        if orden is None:
            last = db.query(func.max(DemandaPlaneacion.orden)).filter(
                DemandaPlaneacion.maquina_codigo == code,
                DemandaPlaneacion.activa.is_(True),
            ).scalar()
            orden = (last or 0) + 1
        now = utcnow()
        demand = DemandaPlaneacion(
            maquina_codigo=code, numero_parte=np, cantidad=item.cantidad,
            piezas_producidas=0, orden=orden, activa=True,
            creado_por=item.creado_por, creado_en=now, actualizado_en=now,
        )
        db.add(demand)
        db.commit()
        return {"id": demand.id, "maquina": code, "numero_parte": np}
    finally:
        db.close()


@router.patch("/{demand_id}")
def actualizar_demanda(demand_id: int, item: DemandaUpdate):
    db = SessionLocal()
    try:
        demand = db.query(DemandaPlaneacion).filter(
            DemandaPlaneacion.id == demand_id, DemandaPlaneacion.activa.is_(True)
        ).first()
        if not demand:
            raise HTTPException(status_code=404, detail="Demanda no encontrada")
        changes = item.dict(exclude_unset=True)
        for field in changes:
            require_permission(CAMPOS_POR_PERMISO[field])
        if "cantidad" in changes:
            _validate_cantidad(changes["cantidad"] or 0)
        if "piezas_producidas" in changes and (changes["piezas_producidas"] or 0) < 0:
            raise HTTPException(status_code=400, detail="Las piezas producidas no pueden ser negativas")
        if "orden" in changes and (changes["orden"] or 0) < 0:
            raise HTTPException(status_code=400, detail="El orden no puede ser negativo")
        final_cantidad = changes.get("cantidad", demand.cantidad)
        final_producido = changes.get("piezas_producidas", demand.piezas_producidas)
        if final_producido > final_cantidad:
            raise HTTPException(status_code=400, detail="Las piezas producidas no pueden superar la cantidad")
        marca = changes.get("marca", demand.marca)
        comentario = changes.get("comentario", demand.comentario)
        if marca == "":
            marca = None
        _validate_marca(marca, comentario)
        for field, value in changes.items():
            setattr(demand, field, value)
        demand.marca = marca
        demand.actualizado_en = utcnow()
        db.commit()
        return {"id": demand.id, "ok": True}
    finally:
        db.close()


@router.delete("/{demand_id}")
def desactivar_demanda(demand_id: int):
    db = SessionLocal()
    try:
        demand = db.query(DemandaPlaneacion).filter(
            DemandaPlaneacion.id == demand_id, DemandaPlaneacion.activa.is_(True)
        ).first()
        if not demand:
            raise HTTPException(status_code=404, detail="Demanda no encontrada")
        demand.activa = False
        demand.actualizado_en = utcnow()
        db.commit()
        return {"id": demand.id, "ok": True}
    finally:
        db.close()
