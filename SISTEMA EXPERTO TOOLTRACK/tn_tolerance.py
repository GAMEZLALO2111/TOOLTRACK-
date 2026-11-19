"""Tolerancia dimensional de los TN de punzonado.

Los TN de las series 1 a 4 llevan su medida en el número, así que dos TN se pueden comparar sin capturar
medidas: 1xxxx redondo (diámetro), 2xxxx cuadrado (lado), 3xxxx rectangular y 4xxxx oblongo (largo.ancho).
Los demás TN (formados y especiales) no traen medida y solo coinciden de forma exacta.
"""
import re
from decimal import Decimal
from typing import Optional, Tuple

DEFAULT_TOLERANCE = Decimal("0.003")
_TN_PATTERN = re.compile(r"^([1-4])(\d{4})(?:\.(\d{1,4}))?$")


def parse_tn(tn: Optional[str]) -> Optional[Tuple[str, Tuple[Decimal, ...]]]:
    match = _TN_PATTERN.match((tn or "").strip())
    if not match:
        return None
    series, body, fraction = match.groups()
    if series in ("1", "2"):
        return (series, (Decimal(body) / 1000,)) if fraction is None else None
    if fraction is None:
        return None
    return series, (Decimal(body) / 1000, Decimal("0." + fraction))


def is_round(tn: Optional[str]) -> bool:
    """Round tools: TN starting with 1 (10230, also longer forms like 101666). They are the ones a pilot change may swap."""
    return bool(re.match(r"^1\d+$", (tn or "").strip()))


def tn_difference(required: Optional[str], actual: Optional[str]) -> Optional[Tuple[Decimal, ...]]:
    """Signed difference (actual - required) per dimension, or None when the TNs cannot be compared."""
    if required == actual:
        return ()
    left, right = parse_tn(required), parse_tn(actual)
    if not left or not right or left[0] != right[0]:
        return None
    return tuple(b - a for a, b in zip(left[1], right[1]))


def tn_compatible(required: Optional[str], actual: Optional[str], tolerance: Decimal = DEFAULT_TOLERANCE) -> bool:
    difference = tn_difference(required, actual)
    return difference is not None and all(abs(value) <= tolerance for value in difference)


def describe_difference(required: Optional[str], actual: Optional[str]) -> Optional[str]:
    difference = tn_difference(required, actual)
    if not difference:
        return None
    return " x ".join(f"{value:+.3f}" for value in difference)


def tolerance_for(db) -> Decimal:
    import tool_params

    return tool_params.load(db)["tn_tolerance"]


def compatible_tns(db, required: str, tolerance: Optional[Decimal] = None):
    """The required TN first, then every inventory TN within tolerance of it."""
    from models.inventory import Herramienta

    tolerance = tolerance_for(db) if tolerance is None else tolerance
    found = [required]
    if parse_tn(required):
        for (tn,) in db.query(Herramienta.tn).distinct().all():
            if tn != required and tn_compatible(required, tn, tolerance):
                found.append(tn)
    return found
