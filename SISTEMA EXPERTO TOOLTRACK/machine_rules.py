from decimal import Decimal, InvalidOperation

from models.inventory import EstacionMaquina, Herramienta, Maquina

STATION_STATES = ("disponible", "danada")


def _station_lookup(stations, number):
    station = stations.get(number)
    if station is None and number.lower().endswith("s"):
        station = stations.get(number[:-1])
    return station


def setup_warnings(db, machine_code, parsed):
    """Soft checks of a parsed setup against the machine profile. Never blocks."""
    warnings = []
    stations = {
        row.numero: row
        for row in db.query(EstacionMaquina).filter(
            EstacionMaquina.maquina_codigo == machine_code
        ).all()
    }
    profile = db.query(Maquina).filter(Maquina.codigo == machine_code).first()

    if profile and profile.espesor_max:
        try:
            if parsed.thickness > Decimal(profile.espesor_max):
                warnings.append(
                    f"El espesor {parsed.thickness} in supera el máximo de {machine_code} ({profile.espesor_max} in)."
                )
        except InvalidOperation:
            pass

    if profile and profile.espesor_min:
        try:
            if parsed.thickness < Decimal(profile.espesor_min):
                warnings.append(
                    f"El espesor {parsed.thickness} in es menor al mínimo de {machine_code} ({profile.espesor_min} in)."
                )
        except InvalidOperation:
            pass

    if not stations:
        return warnings

    sizes_by_tn = {}
    for requirement in parsed.requirements:
        if requirement.tn not in sizes_by_tn:
            rows = db.query(Herramienta.size).filter(
                Herramienta.tn == requirement.tn, Herramienta.size.isnot(None)
            ).distinct().all()
            sizes_by_tn[requirement.tn] = {row[0].strip().upper() for row in rows if row[0]}

    for requirement in parsed.requirements:
        station = _station_lookup(stations, requirement.station)
        if station is None:
            warnings.append(
                f"La estación {requirement.station} (TN {requirement.tn}) no está registrada en {machine_code}."
            )
            continue
        if station.estado != "disponible":
            note = f": {station.nota}" if station.nota else ""
            warnings.append(
                f"La estación {requirement.station} (TN {requirement.tn}) está dañada{note}."
            )
        known_sizes = sizes_by_tn.get(requirement.tn) or set()
        if station.tamano and known_sizes and station.tamano.strip().upper() not in known_sizes:
            warnings.append(
                f"La estación {requirement.station} es tamaño {station.tamano}, "
                f"pero el TN {requirement.tn} es tamaño {', '.join(sorted(known_sizes))}."
            )
    return warnings
