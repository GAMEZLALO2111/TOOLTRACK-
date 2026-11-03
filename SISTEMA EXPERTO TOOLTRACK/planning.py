"""Planning values derived from a stored .stp program.

sym = piezas por blank, RUN TIME = cycle time por blank (segundos).
"""
import re

LEAD_TIME_S = 180

_SYM_RE = re.compile(r"sym\s*=\s*(\d+)")
_RUN_RE = re.compile(r"RUN TIME\s+Sheet\s*:\s*(\d+)\s*secs")


def calcular_planeacion(contenido: str):
    """Return planning values for a program, or None if sym/RUN TIME are missing."""
    sym = _SYM_RE.search(contenido or "")
    run = _RUN_RE.search(contenido or "")
    if not sym or not run:
        return None
    piezas = int(sym.group(1))
    cycle = int(run.group(1))
    if piezas <= 0 or cycle <= 0:
        return None
    cycle_lead = cycle + LEAD_TIME_S
    return {
        "piezas_blank": piezas,
        "cycle_time_s": cycle,
        "cycle_con_lead_s": cycle_lead,
        "uph_teorico": round(3600 / cycle * piezas, 1),
    }
