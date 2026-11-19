"""Parámetros editables de herramientas: stripper por tamaño, vida mínima, altura mínima de matrices y tolerancia de TN."""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Dict, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from db import SessionLocal
from models.inventory import ParametroHerramienta

router = APIRouter(prefix="/inventario/parametros", tags=["tool parameters"])

SIZES = ("A", "B", "C", "D", "E")
DEFAULTS = {
    "stripper_A": "0.250",
    "stripper_B": "0.250",
    "stripper_C": "0.300",
    "stripper_D": "0.350",
    "stripper_E": "0.350",
    "punch_min_life": "0.200",
    "die_min_height": "1.050",
    "tn_tolerance": "0.003",
}
MAX_VALUE = {"tn_tolerance": Decimal("0.050")}
DEFAULT_MAX = Decimal("5")


def load(db) -> Dict[str, Decimal]:
    values = dict(DEFAULTS)
    for row in db.query(ParametroHerramienta).all():
        if row.clave in values:
            values[row.clave] = row.valor
    return {key: Decimal(value) for key, value in values.items()}


def stripper_for(params, size: Optional[str], override: Optional[str] = None) -> Optional[Decimal]:
    """Espesor del stripper plate: el propio de la herramienta o el de su tamaño."""
    if override not in (None, ""):
        return Decimal(str(override))
    return params.get(f"stripper_{(size or '').upper()}")


def life_alert(categoria: Optional[str], life, params) -> bool:
    """True when the tool reached or fell below its minimum (punch: tip beyond the stripper; die: height)."""
    if life in (None, ""):
        return False
    try:
        value = Decimal(str(life))
    except InvalidOperation:
        return False
    if categoria == "punzon":
        return value <= params["punch_min_life"]
    if categoria == "matriz":
        return value <= params["die_min_height"]
    return False


class ParametersUpdate(BaseModel):
    valores: Dict[str, str]
    realizado_por: Optional[str] = None


def _read(db):
    params = load(db)
    return {
        "valores": {key: f"{value:.3f}" for key, value in params.items()},
        "por_defecto": dict(DEFAULTS),
        "tamanos": list(SIZES),
    }


@router.get("")
def read_parameters():
    db = SessionLocal()
    try:
        return _read(db)
    finally:
        db.close()


@router.put("")
def update_parameters(item: ParametersUpdate):
    cleaned = {}
    for key, raw in item.valores.items():
        if key not in DEFAULTS:
            raise HTTPException(status_code=422, detail=f"Unknown parameter {key}")
        try:
            value = Decimal(str(raw).strip())
        except InvalidOperation:
            raise HTTPException(status_code=422, detail=f"{key} must be a number") from None
        if not value.is_finite() or value < 0 or value > MAX_VALUE.get(key, DEFAULT_MAX):
            raise HTTPException(status_code=422, detail=f"{key} is out of range")
        if value.normalize().as_tuple().exponent < -3:
            raise HTTPException(status_code=422, detail=f"{key} supports at most three decimal places")
        cleaned[key] = f"{value:.3f}"
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        for key, text in cleaned.items():
            row = db.query(ParametroHerramienta).filter(ParametroHerramienta.clave == key).first()
            if row is None:
                db.add(ParametroHerramienta(clave=key, valor=text, actualizado_por=item.realizado_por, actualizado_en=now))
            else:
                row.valor, row.actualizado_por, row.actualizado_en = text, item.realizado_por, now
        db.commit()
        return _read(db)
    finally:
        db.close()
