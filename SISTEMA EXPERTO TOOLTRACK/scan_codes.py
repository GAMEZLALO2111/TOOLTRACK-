"""Reading of scanned or typed codes: one place that decides what a string means.

Labels carry an identifying code, never the data itself (life, status and location change; the
system returns the live data after the scan):

    TOOLTRACK-TOOL:<id>          physical tool
    TOOLTRACK-LOCATION:<code>    rack, TOGU, maintenance, workshop or any other location
    TOOLTRACK-MACHINE:<code>     machine
    TOOLTRACK-CART:<code>        kit cart
    TOOLTRACK-KIT:<token>        disposable kit label

A bare value (no prefix) is accepted everywhere, so typing by hand always works when a scanner or a
label fails. The old Code128 labels ("ID|TN|SIZE|...") are read as a tool.
"""
import re
from typing import NamedTuple, Optional

from fastapi import HTTPException
from sqlalchemy import func

from models.inventory import Herramienta

PREFIXES = {
    "TOOLTRACK-TOOL:": "tool",
    "TOOLTRACK-LOCATION:": "location",
    "TOOLTRACK-MACHINE:": "machine",
    "TOOLTRACK-CART:": "cart",
    "TOOLTRACK-KIT:": "kit",
}
KIND_LABEL = {
    "tool": "a tool",
    "location": "a location",
    "machine": "a machine",
    "cart": "a cart",
    "kit": "a kit",
}
# Scanners append CR, LF, TAB or GS; none of them belongs to the code.
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
CAPTURE_MODES = ("scan", "manual", "camera")


class Scan(NamedTuple):
    kind: Optional[str]
    code: str
    raw: str


def parse_scan(value) -> Scan:
    raw = "" if value is None else str(value)
    text = _CONTROL.sub("", raw).strip()
    upper = text.upper()
    for prefix, kind in PREFIXES.items():
        if upper.startswith(prefix):
            return Scan(kind, text[len(prefix):].strip(), raw)
    if "|" in text:  # legacy Code128 tool label: ID|TN|SIZE|TYPE|...
        return Scan("tool", text.split("|", 1)[0].strip(), raw)
    return Scan(None, text, raw)


def _expect(value, allowed, noun):
    scan = parse_scan(value)
    if scan.kind is not None and scan.kind not in allowed:
        raise HTTPException(
            status_code=422,
            detail=f"That code belongs to {KIND_LABEL.get(scan.kind, scan.kind)}, not {noun}.",
        )
    return scan.code


def tool_code(value) -> str:
    """Tool ID from a scan or typed value; rejects labels of another kind."""
    return _expect(value, {"tool"}, "a tool")


def location_code(value) -> str:
    """Location code (uppercase) from a scan or typed value; machines and carts are locations too."""
    return _expect(value, {"location", "machine", "cart"}, "a location").upper()


def machine_code(value) -> str:
    return _expect(value, {"machine", "location"}, "a machine").upper()


def capture_mode(value) -> Optional[str]:
    """How the code was entered, as reported by the client: scan or manual (None when unknown)."""
    mode = (value or "").strip().lower() if isinstance(value, str) else None
    return mode if mode in CAPTURE_MODES else None


def find_tool(db, value, lock=False):
    """Tool by scanned or typed ID: exact match first, then ignoring case (IDs are unique ignoring case)."""
    code = tool_code(value)
    if not code:
        return None
    for query in (
        db.query(Herramienta).filter(Herramienta.id == code),
        db.query(Herramienta).filter(func.upper(Herramienta.id) == code.upper()),
    ):
        if lock:
            query = query.with_for_update()
        tool = query.first()
        if tool:
            return tool
    return None
