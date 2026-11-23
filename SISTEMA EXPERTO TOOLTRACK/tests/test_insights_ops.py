import json
import unittest
from datetime import timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import insights_ops
import planning_api
import shifts
import workflow_api
from models.inventory import (
    Base, FgParte, FgPlaneacion, ForecastSemana, Herramienta, LimiteGolpes, Maquina, MaquinaDisponibilidad,
    MovimientoHerramienta, ProgramaParte, RegistroProduccion, SolicitudServicio, Ubicacion,
)


class OperationsTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine, expire_on_commit=False, autoflush=False)()
        self.now = workflow_api.utcnow()
        db = self.db
        for code in ("T-1", "T-2"):
            db.add(Ubicacion(codigo=code, nombre=code, tipo="machine", activa=True))
            db.add(Maquina(codigo=code, tipo_maquina="4 Index"))
        db.add(ProgramaParte(
            numero_parte="MAKE-A_00-SW", tipo_maquina="4I", tipo_programa="NPI", revision=1, thickness="0.075", contenido="x",
            contenido_hash="h", actualizado_por="e", actualizado_en=self.now, piezas_blank=1, cycle_time_s=36, cycle_con_lead_s=36,
            uph_teorico=100, requisitos_json=json.dumps([{"station": "101", "tn": "10100", "angle": None}])))
        fg = FgPlaneacion(numero_fg="FG-1", activa=True, creado_por="p", creado_en=self.now, actualizado_en=self.now)
        db.add(fg)
        db.flush()
        db.add(FgParte(fg_id=fg.id, numero_parte="MAKE-A_00-SW", piezas_por_fg=2))
        self.monday = planning_api.current_monday()
        db.add(ForecastSemana(fg_id=fg.id, semana=self.monday - timedelta(weeks=1), cantidad=100, actualizado_por="p", actualizado_en=self.now))
        db.add(Herramienta(id="P1", tn="10100", categoria="punzon", estado="disponible", ubicacion="T-1", golpes_servicio=95, golpes_total=95))
        db.add(LimiteGolpes(tn="10100", categoria="punzon", primer_mantenimiento=100, intervalo_afilado=100))
        db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def _week_day(self):
        return shifts._local(self.now).replace(hour=10, minute=0) - timedelta(days=shifts._local(self.now).weekday() + 5)

    def test_attainment_is_pieces_built_against_the_forecast_and_names_the_limiting_make_item(self):
        moment = self.now - timedelta(days=(shifts._local(self.now).date() - self._week_day().date()).days)
        self.db.add(RegistroProduccion(numero_parte="MAKE-A_00-SW", maquina_codigo="T-1", piezas=160, fecha_produccion=moment,
                                       turno="Turno 1", fuente="manual", capturado_por="op", capturado_en=moment))
        self.db.commit()
        result = insights_ops.build_operations(self.db, 30)
        # 100 FG x 2 pieces planned = 200; 160 built.
        self.assertEqual(result["production"]["attainment_pct"], 80.0)
        row = result["production"]["by_fg"][0]
        self.assertEqual((row["fg"], row["limiting_part"], row["sets_pct"]), ("FG-1", "MAKE-A_00-SW", 80.0))
        self.assertEqual(result["production"]["pieces"], 160)
        self.assertEqual(next(x["value"] for x in result["production"]["by_shift"] if x["key"] == "Turno 1"), 160)

    def test_disabled_hours_reduce_the_available_machine_time(self):
        start = self.now - timedelta(days=3)
        self.db.add(MaquinaDisponibilidad(maquina_codigo="T-2", accion="disabled", tipo="maintenance", motivo="PM", usuario="s", fecha=start))
        self.db.add(MaquinaDisponibilidad(maquina_codigo="T-2", accion="enabled", tipo="maintenance", usuario="s", fecha=start + timedelta(days=1)))
        self.db.commit()
        result = insights_ops.build_operations(self.db, 7)
        machine = next(m for m in result["utilization"]["machines"] if m["code"] == "T-2")
        # One calendar day off out of seven: a seventh of the weekly capacity.
        self.assertAlmostEqual(machine["disabled_h"], machine["capacity_h"] / 7, delta=0.2)
        self.assertEqual(result["utilization"]["disabled_events"], 1)

    def test_automatic_tickets_and_movements_are_counted_with_who_and_how(self):
        asked = self.now - timedelta(hours=5)
        self.db.add(SolicitudServicio(
            maquina_codigo="T-1", tipo="sharpening", estado="closed", herramienta_id="P1", solicitado_por="SYSTEM", solicitado_en=asked,
            atendido_por="tc", atendido_en=asked + timedelta(minutes=10), automatico=True))
        self.db.add(MovimientoHerramienta(herramienta_id="P1", origen_codigo="R-1", destino_codigo="T-1", tipo="rack_to_cart_for_kit",
                                          realizado_por="tc", creado_en=asked, captura="scan"))
        self.db.add(MovimientoHerramienta(herramienta_id="P1", origen_codigo="T-1", destino_codigo="T-1", tipo="service_requested",
                                          realizado_por="SYSTEM", creado_en=asked))
        self.db.commit()
        result = insights_ops.build_operations(self.db, 7)
        strokes, trace = result["strokes"], result["traceability"]
        self.assertEqual((strokes["auto_opened"], strokes["auto_closed"], strokes["auto_avg_response"]), (1, 1, 10.0))
        self.assertEqual(next(x["value"] for x in strokes["levels"] if x["key"] == "warning"), 1)  # 95 of 100 strokes
        self.assertEqual((trace["total"], trace["tools_touched"], trace["scan_share"]), (2, 1, 100.0))


if __name__ == "__main__":
    unittest.main()
