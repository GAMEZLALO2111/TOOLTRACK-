"""Second phase of the demo database (called by make_demo_database.py): the history that makes the dashboard worth showing.

All of it is synthetic and written only to the demo copy:
- FGs with a BOM and a weekly forecast, and production that follows it by machine, shift and day (capacity decides
  what gets done, so some weeks fall short),
- machines disabled for maintenance or unavailable, with their history (one is disabled right now),
- strokes per physical tool, limits per TN, pre-alerts and automatic sharpening requests opened by the system,
- extra tools in stock so the Clear to build check shows ready, risky and not ready FGs,
- thousands of traceability movements (kits, service, Maintenance, retirements) with who, when and how it was captured.
"""
import json
import math
import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

import shifts
from models.inventory import (
    BajaHerramienta,
    DemandaPlaneacion,
    EstadoMaquina,
    FgParte,
    FgPlaneacion,
    ForecastSemana,
    GolpeHerramienta,
    Herramienta,
    LimiteGolpes,
    Maquina,
    MaquinaDisponibilidad,
    MovimientoHerramienta,
    ProgramaParte,
    RegistroProduccion,
    RequisitoSetup,
    ServicioHerramienta,
    Setup,
    SolicitudServicio,
    Surtido,
    TicketMantenimiento,
    Ubicacion,
)

SHIFT_SLOTS = ((1, 7, "Turno 1", 1.0), (2, 15, "Turno 2", 0.97), (3, 23, "Turno 3", 0.88))
SLOWNESS = {"Turno 1": 0.8, "Turno 2": 1.0, "Turno 3": 1.6}
NEW_FGS = (
    ("TYEH-1171174_01-FG", "Rack 5x - 2U server", "GOOGLE"), ("TYEH-1180221_00-FG", "Shelf assembly", "GOOGLE"),
    ("TYEH-1195630_02-FG", "Power shelf", "META"), ("TYEH-1203318_00-FG", "Switch chassis", "AMAZON"),
    ("TYEH-1214907_01-FG", "Storage tray", "MICROSOFT"),
)
SHARES = (0.24, 0.20, 0.17, 0.15, 0.13, 0.11)
UTILIZATION_TARGET = 0.72
SUPPLIERS = ("WILSON", "AMADA", "MATE", "TRUMPF", "EUROMAC")
DEMO_PLANNER = "demo_planner"


def kind(raw):
    text = (raw or "").upper().replace(" ", "")
    if "45" in text:
        return "45STA"
    if text.startswith("2"):
        return "2I"
    if text.startswith("4"):
        return "4I"
    return text


def hours_per_piece(program):
    if program.uph_editado:
        return 1 / program.uph_editado
    if not program.piezas_blank or not program.cycle_con_lead_s:
        return None
    return program.cycle_con_lead_s / (program.piezas_blank * 3600)


def to_utc(aware):
    return aware.astimezone(timezone.utc).replace(tzinfo=None)


def capture(rng):
    return rng.choices(("scan", "manual", "camera"), weights=(72, 20, 8))[0]


class Demo:
    def __init__(self, session, rng, days, people, baseline):
        self.baseline = baseline
        self.s, self.rng, self.days = session, rng, days
        self.operators, self.toolcrib, self.technicians, self.supervisors = people
        self.now = datetime.now(timezone.utc).replace(tzinfo=None)
        self.today = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
        self.first = self.today - timedelta(days=days)
        self.counts = defaultdict(int)

    def at(self, day, hour):
        return to_utc(day + timedelta(hours=hour))

    # ------------------------------------------------------------ setup
    def load(self):
        s = self.s
        rows = s.query(Ubicacion, Maquina).outerjoin(Maquina, Maquina.codigo == Ubicacion.codigo).filter(
            Ubicacion.tipo == "machine", Ubicacion.activa.is_(True)).order_by(Ubicacion.codigo).all()
        self.machines = [{"code": u.codigo, "type": kind(m.tipo_maquina) if m else ""} for u, m in rows
                         if m and kind(m.tipo_maquina) in ("4I", "2I", "45STA")]
        self.racks = [u.codigo for u in s.query(Ubicacion).filter(Ubicacion.tipo == "rack")]
        self.carts = [u.codigo for u in s.query(Ubicacion).filter(Ubicacion.tipo == "cart")]
        self.hpp = defaultdict(dict)
        self.programs = {}
        for program in s.query(ProgramaParte).all():
            value = hours_per_piece(program)
            if value:
                self.hpp[program.numero_parte.upper()][program.tipo_maquina.upper()] = value
                self.programs[(program.numero_parte.upper(), program.tipo_maquina.upper())] = program
        self.real_sr_max = self.baseline[0]
        self.real_kits = self.baseline[1]

    # ------------------------------------------------------------ machine availability
    def availability(self):
        rng, s = self.rng, self.s
        self.down = defaultdict(list)
        picks = rng.sample([m["code"] for m in self.machines], 4)
        plan = (
            (picks[0], "maintenance", "Preventive maintenance: turret lubrication and alignment", 0.62, 1.4),
            (picks[1], "unavailable", "Hydraulic leak: waiting for seal kit", 0.30, 4.2),
            (picks[2], "maintenance", "Encoder calibration", 0.82, 0.8),
        )
        boss = self.supervisors[0]
        for code, tipo, reason, start_fraction, length in plan:
            start = self.first + timedelta(days=self.days * start_fraction, hours=6)
            end = start + timedelta(days=length)
            self.down[code].append((to_utc(start), to_utc(end)))
            s.add(MaquinaDisponibilidad(maquina_codigo=code, accion="disabled", tipo=tipo, motivo=reason,
                                        regreso_estimado=end.date(), usuario=boss, fecha=to_utc(start)))
            s.add(MaquinaDisponibilidad(maquina_codigo=code, accion="enabled", tipo=tipo, motivo=reason,
                                        usuario=boss, fecha=to_utc(end)))
        code = picks[3]
        start = self.today - timedelta(days=2, hours=-6)
        self.down[code].append((to_utc(start), None))
        profile = s.query(Maquina).filter(Maquina.codigo == code).one()
        profile.deshabilitada, profile.deshab_tipo = True, "unavailable"
        profile.deshab_motivo = "Turret encoder fault: waiting for the spare part"
        profile.deshab_desde, profile.deshab_por = to_utc(start), boss
        profile.regreso_estimado = (self.today + timedelta(days=4)).date()
        s.add(MaquinaDisponibilidad(maquina_codigo=code, accion="disabled", tipo="unavailable", motivo=profile.deshab_motivo,
                                    regreso_estimado=profile.regreso_estimado, usuario=boss, fecha=to_utc(start)))
        self.counts["machines_disabled"] = 4

    def is_down(self, code, moment):
        return any(start <= moment and (end is None or moment < end) for start, end in self.down.get(code, ()))

    # ------------------------------------------------------------ FGs, forecast
    def fgs(self):
        rng, s = self.rng, self.s
        now = self.now
        existing = s.query(FgPlaneacion).filter(FgPlaneacion.activa.is_(True)).all()
        used = {row.numero_parte.upper() for row in s.query(FgParte).all()}
        pool = [part for part, types in self.hpp.items()
                if "4I" in types and part not in used and 30 <= 1 / types["4I"] <= 900]
        rng.shuffle(pool)
        self.fg_list = []
        for fg in existing:
            bom = [(row.numero_parte.upper(), row.piezas_por_fg) for row in s.query(FgParte).filter(FgParte.fg_id == fg.id)]
            self.fg_list.append((fg, bom))
        for name, description, customer in NEW_FGS:
            if s.query(FgPlaneacion).filter(FgPlaneacion.numero_fg == name).first():
                continue
            fg = FgPlaneacion(numero_fg=name, descripcion=description, cliente=customer, activa=True,
                              creado_por=DEMO_PLANNER, creado_en=now, actualizado_en=now)
            s.add(fg)
            s.flush()
            bom = []
            for _ in range(rng.randint(6, 9)):
                if not pool:
                    break
                part = pool.pop()
                per_fg = rng.choice((1, 1, 2, 2, 4, 5, 8))
                bom.append((part, per_fg))
                s.add(FgParte(fg_id=fg.id, numero_parte=part, piezas_por_fg=per_fg))
            self.fg_list.append((fg, bom))
        s.flush()
        capacity = len(self.machines) * 3 * 8 * 5 * 0.85
        self.forecast = {}
        monday0 = (self.first - timedelta(days=self.first.weekday()))
        weeks = [monday0 + timedelta(weeks=i) for i in range(self.days // 7 + 2 + 12)]
        for index, (fg, bom) in enumerate(self.fg_list):
            unit = sum(per * min(self.hpp[part].values()) for part, per in bom if part in self.hpp)
            if not unit:
                continue
            base = UTILIZATION_TARGET * capacity * SHARES[index % len(SHARES)] / unit
            this_monday = (self.today - timedelta(days=self.today.weekday())).date()
            for number, week in enumerate(weeks):
                qty = max(20, int(round(base * (1 + 0.1 * math.sin(number * 0.9 + index) + rng.uniform(-0.08, 0.08)) / 10) * 10))
                if week.date() > this_monday:  # the future is planned lighter, so the outlook is not all red
                    qty = max(20, int(qty * rng.uniform(0.55, 0.85) / 10) * 10)
                day = week.date()
                self.forecast[(fg.id, day)] = qty
                if not s.query(ForecastSemana).filter(ForecastSemana.fg_id == fg.id, ForecastSemana.semana == day).first():
                    s.add(ForecastSemana(fg_id=fg.id, semana=day, cantidad=qty, actualizado_por=DEMO_PLANNER, actualizado_en=now))
        self.counts["fgs"] = len(self.fg_list)

    # ------------------------------------------------------------ production by machine, shift and day
    def production(self):
        rng, s = self.rng, self.s
        self.by_machine_day = defaultdict(list)
        monday0 = self.first - timedelta(days=self.first.weekday())
        week = monday0
        while week <= self.today:
            jobs = []
            # The first week is only partly inside the period: plan only the days that are.
            share = min(1.0, (7 - max(0, (self.first - week).days)) / 7)
            for fg, bom in self.fg_list:
                qty = self.forecast.get((fg.id, week.date()), 0)
                for part, per_fg in bom:
                    if part in self.hpp and qty:
                        jobs.append([part, int(qty * per_fg * share)])
            load = defaultdict(float)
            queues = defaultdict(list)
            for part, pieces in sorted(jobs, key=lambda j: -j[1] * min(self.hpp[j[0]].values())):
                options = [m for m in self.machines if m["type"] in self.hpp[part]]
                pieces_left = pieces
                chunks = 2 if pieces * min(self.hpp[part].values()) > 40 else 1
                for chunk in range(chunks):
                    machine = min(options, key=lambda m: (load[m["code"]] + rng.random() * 3))
                    take = pieces_left // (chunks - chunk)
                    load[machine["code"]] += take * self.hpp[part][machine["type"]]
                    queues[machine["code"]].append([part, take, machine["type"]])
                    pieces_left -= take
            for machine in self.machines:
                self.run_machine(machine, week, queues.get(machine["code"], []))
            week += timedelta(weeks=1)

    def slots(self, machine, week):
        rng = self.rng
        result = []
        for offset in range(6):
            day = week + timedelta(days=offset)
            if day > self.today or day < self.first:
                continue
            if offset == 5 and rng.random() < 0.55:
                continue
            for number, start, label, factor in SHIFT_SLOTS:
                begin, end = self.at(day, start), self.at(day, start + 8)
                if begin >= self.now or self.is_down(machine["code"], begin):
                    continue
                hours = 7.2 * rng.uniform(0.72, 0.97) * factor
                if rng.random() < 0.04:
                    hours = 0
                if end > self.now:
                    hours *= max(0.0, (self.now - begin).total_seconds() / 28800)
                result.append({"day": day, "label": label, "begin": begin, "end": min(end, self.now), "hours": hours})
        return result

    def run_machine(self, machine, week, queue):
        rng, s = self.rng, self.s
        slots = self.slots(machine, week)
        if not slots:
            return
        planned_week = week.date()
        current = week + timedelta(days=7) > self.today
        cursor = 0
        for part, pieces, mtype in queue:
            hpp = self.hpp[part][mtype]
            demand = DemandaPlaneacion(
                maquina_codigo=machine["code"], numero_parte=part, cantidad=pieces, piezas_producidas=0, orden=self.counts["demands"],
                activa=False, orden_trabajo=f"WO-{rng.randint(100000, 999999)}", creado_por=DEMO_PLANNER,
                creado_en=self.at(week, -30), actualizado_en=self.at(week, -30))
            s.add(demand)
            s.flush()
            self.counts["demands"] += 1
            remaining, setup = pieces, rng.uniform(0.7, 1.5)
            produced = 0
            while remaining > 0 and cursor < len(slots):
                slot = slots[cursor]
                available = slot["hours"]
                if available <= 0.05:
                    cursor += 1
                    continue
                if setup > 0:
                    used = min(setup, available)
                    slot["hours"] -= used
                    setup -= used
                    continue
                make = min(remaining, int(available / hpp))
                if make < 1:
                    cursor += 1
                    continue
                slot["hours"] -= make * hpp
                remaining -= make
                produced += make
                moment = slot["end"] - timedelta(minutes=rng.uniform(0, 40))
                s.add(RegistroProduccion(
                    demanda_id=demand.id, numero_parte=part, maquina_codigo=machine["code"], piezas=make,
                    fecha_produccion=moment, turno=slot["label"], fuente="manual", capturado_por=rng.choice(self.operators),
                    capturado_en=moment + timedelta(minutes=3)))
                self.counts["production_rows"] += 1
                self.by_machine_day[(machine["code"], slot["day"].date())].append((part, make))
            demand.piezas_producidas = produced
            if current:
                demand.activa = produced < pieces
                demand.actualizado_en = self.now
            if not demand.activa:
                done = produced >= pieces * 0.98
                demand.estado_cierre = "terminada" if done else "cancelada"
                demand.motivo_cierre = "Completed" if done else "Not finished: capacity ran out, moved to the next week's plan"
                demand.cerrado_por = DEMO_PLANNER
                demand.cerrado_en = self.at(week, 24 * 6)
            if cursor >= len(slots):
                break

    # ------------------------------------------------------------ extra stock for Clear to build
    def stock(self):
        rng, s = self.rng, self.s
        existing = defaultdict(int)
        for tn, category in s.query(Herramienta.tn, Herramienta.categoria).filter(Herramienta.estado == "disponible"):
            existing[(tn, category)] += 1
        number = s.query(Herramienta).filter(Herramienta.id.like("DM%")).count()
        plans = {1: ("full", 12), 2: ("risk", 12), 3: ("almost", 12), 4: ("partial", 6)}

        def add(tn, category, low_life=False):
            nonlocal number
            number += 1
            s.add(Herramienta(
                id=f"DM{number:05d}", tn=tn, size="A", type="ROUND", categoria=category, estado="disponible",
                ubicacion=rng.choice(self.racks), proveedor=rng.choice(SUPPLIERS),
                life=("0.150" if low_life else "0.900") if category == "punzon" else None,
                clearance="0.008" if category == "matriz" else None, so=f"SO-{rng.randint(1000, 9999)}"))

        for index, (fg, bom) in enumerate(self.fg_list):
            mode, target = plans.get(index, ("none", 0))
            if mode == "none":
                continue
            tns = sorted({row["tn"] for part, _ in bom for kind_ in ("4I", "2I") if (part, kind_) in self.programs
                          for row in json.loads(self.programs[(part, kind_)].requisitos_json)})
            skipped = set(rng.sample(tns, min(2, len(tns)))) if mode == "almost" else set()
            low = set(rng.sample(tns, min(3, len(tns)))) if mode == "risk" else set()
            if mode == "full":
                # A FG that is really ready: healthy punches and trustworthy UPH for every make item.
                for tool in s.query(Herramienta).filter(Herramienta.tn.in_(tns), Herramienta.categoria.in_(("punzon", "matriz"))).all():
                    try:
                        value = float(tool.life)
                    except (TypeError, ValueError):
                        continue
                    if tool.categoria == "punzon" and value <= 0.2:
                        tool.life = "0.800"
                    elif tool.categoria == "matriz" and value <= 1.05:
                        tool.life = "1.300"
                for part, _ in bom:
                    for kind_ in ("4I", "2I"):
                        program = self.programs.get((part, kind_))
                        if program and not 10 <= 1 / hours_per_piece(program) <= 2000:
                            program.uph_editado, program.uph_motivo = 120.0, "Demo: verified with Engineering"
                            program.uph_editado_por, program.uph_editado_en = self.supervisors[0], self.now
            for tn in tns:
                if tn in skipped or (mode == "partial" and rng.random() < 0.3):
                    continue
                for category in ("punzon", "matriz"):
                    for _ in range(max(0, target - existing[(tn, category)])):
                        add(tn, category, low_life=tn in low and category == "punzon")
                        existing[(tn, category)] += 1
        self.counts["stock_added"] = number

    # ------------------------------------------------------------ strokes, limits and automatic tickets
    def mount_extra(self):
        s = self.s
        free = defaultdict(list)
        for tool in s.query(Herramienta).filter(Herramienta.estado == "disponible").all():
            if tool.ubicacion in self.racks:
                free[(tool.tn, tool.categoria)].append(tool)
        for state in s.query(EstadoMaquina).filter(EstadoMaquina.setup_activo_id.isnot(None)).all():
            for requirement in s.query(RequisitoSetup).filter(RequisitoSetup.setup_id == state.setup_activo_id).all():
                for category, attribute in (("punzon", "punch_tool_id"), ("matriz", "die_tool_id")):
                    if getattr(requirement, attribute) or not free[(requirement.tn, category)]:
                        continue
                    tool = free[(requirement.tn, category)].pop()
                    setattr(requirement, attribute, tool.id)
                    tool.ubicacion = state.maquina_codigo
        s.flush()

    def strokes(self):
        rng, s = self.rng, self.s
        mounted = []
        for state in s.query(EstadoMaquina).filter(EstadoMaquina.setup_activo_id.isnot(None)).all():
            setup = s.query(Setup).filter(Setup.id == state.setup_activo_id).one()
            for requirement in s.query(RequisitoSetup).filter(RequisitoSetup.setup_id == setup.id).all():
                for category, tool_id in (("punzon", requirement.punch_tool_id), ("matriz", requirement.die_tool_id)):
                    if tool_id:
                        mounted.append((state.maquina_codigo, setup, requirement, category, s.get(Herramienta, tool_id)))
        days = [self.first.date() + timedelta(days=i) for i in range(self.days + 1)]
        per_tool = {}
        for code, setup, requirement, category, tool in mounted:
            spp = rng.choice((1, 1, 2, 3))
            series = []
            for day in days:
                entries = self.by_machine_day.get((code, day), [])
                pieces = sum(p for _, p in entries)
                part = max(entries, key=lambda e: e[1])[0] if entries else None
                series.append((day, pieces / 15 * spp, part))
            per_tool[tool.id] = series
        groups = defaultdict(list)
        for entry in mounted:
            groups[(entry[2].tn, entry[3])].append(entry)
        scenario = {}
        for key, entries in groups.items():
            total = sum(sum(v for _, v, _ in per_tool[e[4].id]) for e in entries) / len(entries)
            if total < 40:
                continue
            roll = rng.random()
            if roll < 0.42:
                kind_, limit = "healthy", total / rng.uniform(0.35, 0.62)
            elif roll < 0.62:
                kind_, limit = "warning", total / rng.uniform(0.91, 0.99)
            elif roll < 0.74:
                kind_, limit = "due", total / rng.uniform(1.01, 1.1)
            else:
                kind_, limit = "cycled", total * rng.uniform(0.3, 0.45)
            scenario[key] = (kind_, limit)
            s.add(LimiteGolpes(tn=key[0], categoria=key[1], primer_mantenimiento=int(limit * (1.2 if kind_ == "cycled" else 1)),
                               intervalo_afilado=int(limit), editado_por=self.supervisors[0], editado_en=self.now))
        s.flush()
        for code, setup, requirement, category, tool in mounted:
            key = (requirement.tn, category)
            if key not in scenario:
                continue
            kind_, limit = scenario[key]
            first = limit * (1.2 if kind_ == "cycled" else 1)
            counter, total, sharpenings, open_request = 0.0, 0.0, 0, False
            for day, value, part in per_tool[tool.id]:
                if value <= 0:
                    continue
                moment = self.at(self.today + timedelta(days=(day - self.today.date()).days), rng.uniform(7.5, 22.5))
                if moment > self.now:
                    continue
                counter += value
                total += value
                s.add(GolpeHerramienta(herramienta_id=tool.id, origen="produccion", golpes=round(value, 1),
                                       golpes_servicio=round(value, 1), maquina_codigo=code, numero_parte=part,
                                       estacion=requirement.station, usuario=rng.choice(self.operators), fecha=moment))
                threshold = first if sharpenings == 0 else limit
                if counter >= threshold and not open_request:
                    open_request = self.auto_ticket(code, setup, requirement, category, tool, counter, threshold, moment,
                                                    keep_open=(kind_ == "due"))
                    if kind_ != "due":
                        open_request = False
                        s.add(GolpeHerramienta(herramienta_id=tool.id, origen="reinicio", golpes=0, golpes_servicio=-round(counter, 1),
                                               motivo="Automatic sharpening finished", usuario=self.toolcrib[0],
                                               fecha=moment + timedelta(hours=1)))
                        counter, sharpenings = 0.0, sharpenings + 1
            tool.golpes_servicio, tool.golpes_total = round(counter, 1), round(total, 1)
        self.counts["tools_with_strokes"] = len(per_tool)

    def auto_ticket(self, code, setup, requirement, category, tool, counter, threshold, moment, keep_open):
        rng, s = self.rng, self.s
        label = shifts.shift_for(moment)
        attendant = rng.choice(self.toolcrib)
        wait = max(1.0, rng.lognormvariate(math.log(9 * SLOWNESS[label]), 0.5))
        attended = moment + timedelta(minutes=wait)
        attention = rng.uniform(25, 70)
        received = attended + timedelta(minutes=attention)
        part = (setup.drawing_name or setup.nombre_archivo or "").upper() or None
        note = (f"Automatic: {round(counter)} strokes since the last service, limit {round(threshold)}. "
                f"Bring a replacement; the tool stays on the machine until then.")
        request = SolicitudServicio(
            maquina_codigo=code, setup_id=setup.id, requisito_id=requirement.id, numero_parte=part, estacion=requirement.station,
            categoria=category, tn=requirement.tn, tipo="sharpening", estado="open" if keep_open else "closed", nota=note,
            herramienta_id=tool.id, solicitado_por="SYSTEM", solicitado_en=moment, automatico=True, golpes_al_crear=round(counter, 1))
        if keep_open and rng.random() < 0.5:
            request.estado, request.atendido_por, request.atendido_en = "attending", attendant, attended
        if not keep_open:
            request.destino_codigo = rng.choice(self.service_places)
            request.atendido_por, request.atendido_en = attendant, attended
            request.recogido_por, request.recogido_en = attendant, attended + timedelta(minutes=attention * 0.4)
            request.recibida_en, request.herramienta_nueva_id = received, tool.id
            request.cerrado_por, request.cerrado_en = attendant, received
        s.add(request)
        s.flush()
        if not keep_open:
            s.add(ServicioHerramienta(
                herramienta_id=tool.id, solicitud_id=request.id, tipo="sharpening", resultado="ready", maquina_codigo=code,
                numero_parte=part, estacion=requirement.station, tn=requirement.tn, categoria=category,
                rebaje=round(rng.uniform(0.004, 0.014), 3), realizado_por=attendant, fecha=received - timedelta(minutes=attention * 0.3)))
        self.counts["auto_tickets"] += 1
        return keep_open

    # ------------------------------------------------------------ traceability
    def movements(self):
        rng, s = self.rng, self.s
        tools = {tool.id: tool for tool in s.query(Herramienta).filter(Herramienta.estado != "baja")}
        by_tn = defaultdict(list)
        for tool in tools.values():
            by_tn[tool.tn].append(tool.id)
        out = []

        def move(tool_id, origin, destination, tipo, who, when, kit=None, setup=None, detail=None, shot=True):
            if when > self.now or not tool_id:
                return
            out.append(MovimientoHerramienta(
                herramienta_id=tool_id, origen_codigo=origin, destino_codigo=destination, tipo=tipo, surtido_id=kit,
                setup_id=setup, realizado_por=who, creado_en=when, detalle=detail, captura=capture(rng) if shot else None))

        def rack_of(tool_id):
            tool = tools.get(tool_id)
            return tool.ubicacion if tool and tool.ubicacion in self.racks else rng.choice(self.racks)

        for kit in s.query(Surtido).filter(Surtido.creado_en >= to_utc(self.first)).all():
            if kit.id in self.real_kits:
                continue
            new_req = s.query(RequisitoSetup).filter(RequisitoSetup.setup_id == kit.setup_nuevo_id).all()
            old_req = s.query(RequisitoSetup).filter(RequisitoSetup.setup_id == kit.setup_anterior_id).all()
            picked = []
            for requirement in rng.sample(new_req, min(len(new_req), rng.randint(5, 10))):
                candidates = by_tn.get(requirement.tn)
                if candidates:
                    picked.append(rng.choice(candidates))
            who = kit.creado_por
            when = kit.creado_en
            for index, tool_id in enumerate(picked):
                at = when + timedelta(minutes=index * rng.uniform(0.6, 2.2))
                move(tool_id, rack_of(tool_id), kit.carro_codigo, "rack_to_cart_for_kit", who, at, kit.id, kit.setup_nuevo_id)
                if kit.estado == "delivered" and kit.entregado_en:
                    move(tool_id, kit.carro_codigo, kit.maquina_codigo, "cart_to_machine_delivery", who,
                         kit.entregado_en + timedelta(seconds=index * 20), kit.id, kit.setup_nuevo_id)
                else:
                    move(tool_id, kit.carro_codigo, rack_of(tool_id), "kit_cancelled_return_to_rack", who,
                         kit.actualizado_en + timedelta(seconds=index * 15), kit.id, kit.setup_nuevo_id)
            if kit.estado == "delivered" and kit.entregado_en:
                for requirement in rng.sample(old_req, min(len(old_req), rng.randint(3, 7))):
                    candidates = by_tn.get(requirement.tn)
                    if not candidates:
                        continue
                    tool_id = rng.choice(candidates)
                    move(tool_id, kit.maquina_codigo, kit.maquina_codigo, "removed_from_setup_pending_pickup", who,
                         kit.entregado_en + timedelta(minutes=rng.uniform(1, 12)), kit.id, kit.setup_anterior_id)
                    move(tool_id, kit.maquina_codigo, rack_of(tool_id), "pending_pickup_to_rack", rng.choice(self.toolcrib),
                         kit.entregado_en + timedelta(minutes=rng.uniform(25, 420)), kit.id, kit.setup_anterior_id)

        tickets = {row.solicitud_id: row for row in s.query(TicketMantenimiento).all() if row.solicitud_id}
        retirements = {row.solicitud_id: row for row in s.query(BajaHerramienta).all() if row.solicitud_id}
        services = {row.solicitud_id: row for row in s.query(ServicioHerramienta).all() if row.solicitud_id}
        for request in s.query(SolicitudServicio).filter(SolicitudServicio.id > self.real_sr_max).all():
            tool_id = request.herramienta_id
            folio = f"SR-{request.id:05d}"
            if tool_id:
                move(tool_id, request.maquina_codigo, request.maquina_codigo, "service_requested", request.solicitado_por,
                     request.solicitado_en, None, request.setup_id,
                     f"{folio}: {request.tipo} on station {request.estacion} ({request.categoria})", shot=False)
            if not request.recogido_en or not tool_id:
                continue
            tipo = request.tipo if request.tipo in ("sharpening", "inspection", "replacement", "damage", "performance") else "sharpening"
            move(tool_id, request.maquina_codigo, request.destino_codigo, f"service_collect_{tipo}", request.recogido_por,
                 request.recogido_en, None, request.setup_id, f"{folio}: {tipo} from {request.maquina_codigo}")
            service = services.get(request.id)
            if service:
                move(tool_id, request.destino_codigo, request.destino_codigo, "service_completed", service.realizado_por,
                     service.fecha, None, request.setup_id, f"{folio}: {tipo} finished", shot=False)
                move(tool_id, request.destino_codigo, rack_of(tool_id), "togu_to_rack", service.realizado_por,
                     service.fecha + timedelta(minutes=rng.uniform(10, 120)))
            ticket = tickets.get(request.id)
            if ticket:
                move(tool_id, request.destino_codigo, "MANTENIMIENTO", "maintenance_ticket_opened", ticket.creado_por, ticket.creado_en)
                if ticket.recibido_en:
                    move(tool_id, "MANTENIMIENTO", "MANTENIMIENTO", "maintenance_received", ticket.recibido_por, ticket.recibido_en, shot=False)
                if ticket.cerrado_en:
                    move(tool_id, "MANTENIMIENTO", "MANTENIMIENTO",
                         "maintenance_released" if ticket.resultado == "released" else "maintenance_unrepairable",
                         ticket.cerrado_por, ticket.cerrado_en, shot=False)
            retirement = retirements.get(request.id)
            if retirement:
                move(tool_id, request.destino_codigo, request.destino_codigo, "retirement_proposed", retirement.propuesto_por,
                     retirement.propuesto_en, shot=False)
                if retirement.resuelto_en:
                    move(tool_id, request.destino_codigo, request.destino_codigo, "retirement_approved", retirement.resuelto_por,
                         retirement.resuelto_en, shot=False)
            if request.recibida_en and request.herramienta_nueva_id:
                move(request.herramienta_nueva_id, rack_of(request.herramienta_nueva_id), request.maquina_codigo, "service_install",
                     request.cerrado_por or request.solicitado_por, request.recibida_en, None, request.setup_id,
                     f"{folio}: installed on station {request.estacion}")
        s.add_all(out)
        self.counts["movements"] = len(out)

    # ------------------------------------------------------------ run
    def run(self):
        self.load()
        self.service_places = [u.codigo for u in self.s.query(Ubicacion).filter(Ubicacion.tipo.in_(("togu", "maintenance")))] or ["TOGU-1"]
        self.availability()
        self.fgs()
        self.production()
        self.stock()
        self.mount_extra()
        self.strokes()
        self.s.flush()
        self.movements()
        self.s.commit()
        return dict(self.counts)


def baseline(session):
    """What exists before any synthetic row is added, so the traceability pass only covers the demo's own kits and requests."""
    last = session.query(SolicitudServicio.id).order_by(SolicitudServicio.id.desc()).limit(1).scalar() or 0
    return last, {row[0] for row in session.query(Surtido.id).all()}


def generate(session, rng, days, people, baseline_data):
    return Demo(session, rng, days, people, baseline_data).run()
