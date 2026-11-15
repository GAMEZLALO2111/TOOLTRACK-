"""Builds a DEMO database with several weeks of realistic history, so the dashboards can be shown to management.

It never touches the real database: it copies SOURCE to TARGET and fills the copy.

    python make_demo_database.py --source tooltrack.db --target demo_mira.db
    set TOOLTRACK_DB_PATH=demo_mira.db   (then start the backend as usual, on another port)

Everything generated is synthetic (requests, damage, sharpening, Maintenance, retirements, kits, plan progress)
and follows plausible patterns: a couple of machines concentrate the downtime, collisions lead the damage causes,
the night shift answers slower, and Tool Crib response improves over the period.
"""
import argparse
import math
import os
import random
import sqlite3
import sys
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, func
from sqlalchemy.orm import sessionmaker

from models.inventory import (
    BajaHerramienta,
    DemandaPlaneacion,
    Herramienta,
    IncidenteHerramienta,
    RequisitoSetup,
    ServicioHerramienta,
    Setup,
    SolicitudServicio,
    Surtido,
    TicketMantenimiento,
    Ubicacion,
    Usuario,
)
from passwords import hash_password

CAUSES = [("collision", 38), ("wrong_setup", 18), ("normal_wear", 16), ("handling", 10), ("wrong_material", 8),
          ("lubrication", 5), ("supplier_defect", 3), ("unknown", 2)]
MOMENTS = [("production", 55), ("setup", 28), ("handling", 10), ("inspection", 5), ("unknown", 2)]
TYPES = [("sharpening", 40), ("damage", 25), ("replacement", 10), ("inspection", 8), ("performance", 7), ("tool_request", 10)]
SHIFT_HOURS = [(1, 7, 15, 45), (2, 15, 23, 35), (3, 23, 31, 20)]
SHIFT_SLOWNESS = {1: 0.8, 2: 1.0, 3: 1.6}


def pick(rng, weighted):
    return rng.choices([item for item, _ in weighted], weights=[weight for _, weight in weighted])[0]


def to_utc(local_naive):
    return local_naive.astimezone(timezone.utc).replace(tzinfo=None)


def ensure_users(session, rng):
    def names(*roles):
        return [row.username for row in session.query(Usuario).filter(Usuario.role.in_(roles), Usuario.activo.is_(True)).all()]

    wanted = (
        ("PUNCHING_OPERATOR", 6, "demo_oper"), ("TOOLCRIB_OPERATOR", 4, "demo_tc"),
        ("MAINTENANCE_TECHNICIAN", 2, "demo_mt"), ("TOOLCRIB_SUPERVISOR", 1, "demo_sup"),
    )
    for role, minimum, prefix in wanted:
        have = len(names(role))
        for index in range(max(0, minimum - have)):
            session.add(Usuario(
                username=f"{prefix}{index + 1}", password=hash_password("demo1234"), role=role, activo=True,
                full_name=f"{prefix.replace('demo_', '').upper()} {index + 1}", shift=f"Turno {index % 3 + 1}", area="Demo",
            ))
    session.commit()
    if not session.query(Usuario).filter(Usuario.username == "demo_manager").first():
        session.add(Usuario(
            username="demo_manager", password=hash_password("demo1234"), role="TOOLCRIB_SUPERVISOR", activo=True,
            full_name="Demo Manager", shift="Turno 1", area="Demo",
        ))
        session.commit()
    return (names("PUNCHING_OPERATOR", "PUNCHING_TECHNICIAN"), names("TOOLCRIB_OPERATOR", "TOOLCRIB_SUPERVISOR"),
            names("MAINTENANCE_TECHNICIAN"), names("TOOLCRIB_SUPERVISOR", "SYSTEM_ADMIN"))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", required=True, help="database to copy (it is only read)")
    parser.add_argument("--target", required=True, help="demo database to create")
    parser.add_argument("--days", type=int, default=180)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--force", action="store_true", help="overwrite the target if it exists")
    args = parser.parse_args()

    source, target = os.path.abspath(args.source), os.path.abspath(args.target)
    if source == target:
        sys.exit("The target must be a different file than the source.")
    if os.path.exists(target) and not args.force:
        sys.exit(f"{target} already exists (use --force to replace it).")
    if os.path.exists(target):
        os.remove(target)
    src, dst = sqlite3.connect(source), sqlite3.connect(target)
    src.backup(dst)
    src.close()
    dst.close()

    rng = random.Random(args.seed)
    engine = create_engine(f"sqlite:///{target}")
    session = sessionmaker(bind=engine)()
    operators, toolcrib, technicians, supervisors = ensure_users(session, rng)

    tools = session.query(Herramienta).all()
    by_tn = {}
    for tool in tools:
        by_tn.setdefault(tool.tn, []).append(tool.id)
    service_places = [row.codigo for row in session.query(Ubicacion).filter(Ubicacion.tipo.in_(("togu", "maintenance")))]
    carts = [row.codigo for row in session.query(Ubicacion).filter(Ubicacion.tipo == "cart")]

    positions = []
    for setup in session.query(Setup).filter(Setup.estado == "active").all():
        for requirement in session.query(RequisitoSetup).filter(RequisitoSetup.setup_id == setup.id).all():
            positions.append((setup, requirement))
    if not positions or not service_places:
        sys.exit("The source has no active setups or service locations to build a demo from.")
    machines = sorted({setup.maquina_codigo for setup, _ in positions})
    rng.shuffle(machines)
    weight = {code: (6 if index == 0 else 4 if index == 1 else 1) for index, code in enumerate(machines)}
    pos_weights = [weight[setup.maquina_codigo] for setup, _ in positions]

    today = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    counts = {"requests": 0, "incidents": 0, "services": 0, "tickets": 0, "retirements": 0, "kits": 0}
    retired = set()

    for offset in range(args.days, -1, -1):
        day = today - timedelta(days=offset)
        factor = 0.2 if day.weekday() == 6 else 0.45 if day.weekday() == 5 else 1.0
        progress = 1 - offset / max(args.days, 1)
        base_wait = 16 - 9 * progress  # Tool Crib gets faster over the period
        for _ in range(max(0, round(rng.gauss(4.2, 1.6) * factor))):
            shift, start, end, _w = rng.choices(SHIFT_HOURS, weights=[item[3] for item in SHIFT_HOURS])[0]
            moment = day + timedelta(hours=rng.uniform(start, end - 0.2), minutes=0)
            requested = to_utc(moment.replace(tzinfo=None).astimezone())
            setup, requirement = rng.choices(positions, weights=pos_weights)[0]
            tipo = pick(rng, TYPES)
            category = rng.choice(("punzon", "matriz"))
            candidates = by_tn.get(requirement.tn) or [tool.id for tool in tools[:50]]
            old_tool = None if tipo == "tool_request" else rng.choice(candidates)
            new_tool = rng.choice(candidates)
            wait = max(1.0, rng.lognormvariate(math.log(base_wait * SHIFT_SLOWNESS[shift]), 0.5))
            attention = rng.uniform(20, 70) if tipo != "tool_request" else rng.uniform(8, 30)
            attended = requested + timedelta(minutes=wait)
            received = attended + timedelta(minutes=attention)
            operator = rng.choice(operators)
            attendant = rng.choice(toolcrib)
            request = SolicitudServicio(
                maquina_codigo=setup.maquina_codigo, setup_id=setup.id, requisito_id=requirement.id,
                numero_parte=(setup.drawing_name or setup.nombre_archivo or "").upper() or None,
                estacion=requirement.station, categoria=category, tn=requirement.tn, tipo=tipo, estado="closed",
                nota="Demo data" if tipo in ("damage", "performance") else None,
                herramienta_id=old_tool, herramienta_nueva_id=new_tool, destino_codigo=rng.choice(service_places),
                solicitado_por=operator, solicitado_en=requested, atendido_por=attendant, atendido_en=attended,
                recogido_por=attendant, recogido_en=attended + timedelta(minutes=attention * 0.4), recibida_en=received,
                cerrado_por=operator, cerrado_en=received,
            )
            session.add(request)
            session.flush()
            counts["requests"] += 1
            if old_tool is None:
                continue

            outcome = "service"
            if tipo == "damage":
                incident = IncidenteHerramienta(
                    herramienta_id=old_tool, solicitud_id=request.id, origen="machine", reportado_como=tipo,
                    maquina_codigo=setup.maquina_codigo, numero_parte=request.numero_parte, estacion=requirement.station,
                    setup_id=setup.id, tn=requirement.tn, categoria=category, operador=operator, turno=f"Turno {shift}",
                    causa=pick(rng, CAUSES), momento=pick(rng, MOMENTS), hallazgo="Demo finding",
                    creado_por=operator, creado_en=requested,
                )
                session.add(incident)
                session.flush()
                counts["incidents"] += 1
                roll = rng.random()
                outcome = "service" if roll < 0.55 else "ticket" if roll < 0.87 else "retire"
                if outcome == "ticket":
                    unrepairable = rng.random() < 0.3
                    created = received + timedelta(minutes=rng.uniform(5, 40))
                    turnaround = max(0.4, rng.gauss(4.5 - 2.5 * progress, 1.0))
                    closed = created + timedelta(days=turnaround)
                    technician = rng.choice(technicians or toolcrib)
                    session.add(TicketMantenimiento(
                        herramienta_id=old_tool, solicitud_id=request.id, incidente_id=incident.id, estado="closed",
                        motivo="Demo: needs more than sharpening", creado_por=attendant, creado_en=created,
                        recibido_por=technician, recibido_en=created + timedelta(hours=rng.uniform(1, 6)),
                        diagnostico="Demo diagnosis", reparable=not unrepairable, evaluado_por=technician,
                        evaluado_en=created + timedelta(hours=rng.uniform(6, 20)), trabajo=None if unrepairable else "Demo repair",
                        resultado="unrepairable" if unrepairable else "released", cerrado_por=technician, cerrado_en=closed,
                    ))
                    counts["tickets"] += 1
                    if unrepairable:
                        outcome = "retire"
                if outcome == "retire" and old_tool not in retired:
                    approver = rng.choice(supervisors)
                    proposed = received + timedelta(hours=rng.uniform(0.5, 3))
                    session.add(BajaHerramienta(
                        herramienta_id=old_tool, solicitud_id=request.id, incidente_id=incident.id, estado="approved",
                        estado_previo="danado", motivo="Demo: beyond repair", propuesto_por=attendant, propuesto_en=proposed,
                        resuelto_por=approver, resuelto_en=proposed + timedelta(hours=rng.uniform(1, 30)),
                    ))
                    retired.add(old_tool)
                    counts["retirements"] += 1

            if outcome == "service" or tipo != "damage":
                ground = max(0.0, rng.gauss(0.008, 0.004)) if tipo == "sharpening" else None
                abnormal = tipo == "sharpening" and rng.random() < 0.05
                if abnormal:
                    ground = rng.uniform(0.021, 0.035)
                session.add(ServicioHerramienta(
                    herramienta_id=old_tool, solicitud_id=request.id, tipo=tipo, resultado="ready",
                    maquina_codigo=setup.maquina_codigo, numero_parte=request.numero_parte, estacion=requirement.station,
                    tn=requirement.tn, categoria=category, rebaje=round(ground, 3) if ground is not None else None,
                    anormal=abnormal, justificacion="Demo: chipped tip, extra material removed" if abnormal else None,
                    realizado_por=attendant, fecha=received - timedelta(minutes=attention * 0.3),
                ))
                counts["services"] += 1

        for _ in range(max(0, round(rng.gauss(1.8, 0.8) * factor))):
            code = rng.choices(machines, weights=[weight[m] for m in machines])[0]
            setups = session.query(Setup).filter(Setup.maquina_codigo == code).all()
            if len(setups) < 2 or not carts:
                continue
            before, after = rng.sample(setups, 2)
            created = to_utc((day + timedelta(hours=rng.uniform(7, 22))).replace(tzinfo=None).astimezone())
            lead = max(15.0, rng.gauss(75 - 35 * progress, 12))
            cancelled = rng.random() < 0.12
            session.add(Surtido(
                id=str(uuid.uuid4()), maquina_codigo=code, carro_codigo=rng.choice(carts), setup_anterior_id=before.id,
                setup_nuevo_id=after.id, estado="cancelled" if cancelled else "delivered", qr_token=uuid.uuid4().hex,
                creado_por=rng.choice(toolcrib), creado_en=created, actualizado_en=created + timedelta(minutes=lead),
                entregado_en=None if cancelled else created + timedelta(minutes=lead),
                motivo="Demo: plan changed" if cancelled else None,
            ))
            counts["kits"] += 1

    for tool_id in retired:
        tool = session.get(Herramienta, tool_id)
        tool.estado = "baja"
    for demand in session.query(DemandaPlaneacion).filter(DemandaPlaneacion.activa.is_(True)).all():
        demand.piezas_producidas = int(demand.cantidad * rng.choice((0, 0, 0.2, 0.45, 0.7, 0.9)))
        demand.orden_trabajo = demand.orden_trabajo or f"WO-{rng.randint(100000, 999999)}"
    session.commit()
    session.close()
    print(f"Demo database created: {target}")
    for name, value in counts.items():
        print(f"  {name}: {value}")
    print("Start the backend on another port with TOOLTRACK_DB_PATH pointing to it. Demo users use password demo1234.")


if __name__ == "__main__":
    main()
