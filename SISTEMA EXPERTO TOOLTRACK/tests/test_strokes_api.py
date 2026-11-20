import json
import unittest

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import auth
import strokes_api
import workflow_api
from models.inventory import (
    Base,
    EstadoMaquina,
    Herramienta,
    LimiteGolpes,
    Maquina,
    NumeroHerramienta,
    ProgramaParte,
    RequisitoSetup,
    ServicioHerramienta,
    Setup,
    SolicitudServicio,
    Ubicacion,
)

PART = "MAKE-A_00-SW"


class StrokesTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(bind=self.engine, expire_on_commit=False, autoflush=False)
        self.original = workflow_api.SessionLocal
        workflow_api.SessionLocal = self.factory
        token = auth.acting_as("eng", "ENGINEERING")
        self.addCleanup(auth.CURRENT_USER.reset, token)
        db = self.factory()
        now = workflow_api.utcnow()
        db.add(Ubicacion(codigo="T-1", nombre="T-1", tipo="machine", activa=True))
        db.add(Maquina(codigo="T-1", tipo_maquina="4 Index"))
        db.add(NumeroHerramienta(tn="10100"))
        for tool_id, category in (("P1", "punzon"), ("D1", "matriz"), ("P2", "punzon")):
            db.add(Herramienta(id=tool_id, tn="10100", size="A", type="ROUND", categoria=category, ubicacion="T-1"))
        db.add(ProgramaParte(
            numero_parte=PART, tipo_maquina="4I", tipo_programa="NPI", revision=1, thickness="0.075", contenido="x",
            contenido_hash="h", actualizado_por="eng", actualizado_en=now, piezas_blank=2, cycle_time_s=10,
            cycle_con_lead_s=10, uph_teorico=360,
            requisitos_json=json.dumps([{"station": "201", "tn": "10100", "angle": "0"}]),
        ))
        db.add(Setup(
            id=1, maquina_codigo="T-1", nombre_archivo=f"{PART}.stp", thickness="0.075", contenido_hash="h",
            creado_por="eng", creado_en=now, estado="active",
        ))
        db.flush()
        db.add(EstadoMaquina(maquina_codigo="T-1", setup_activo_id=1))
        self.requirement = RequisitoSetup(setup_id=1, station="201", tn="10100", angle="0", punch_tool_id="P1", die_tool_id="D1")
        db.add(self.requirement)
        db.commit()
        db.close()

    def tearDown(self):
        workflow_api.SessionLocal = self.original
        self.engine.dispose()

    def _limit(self, first=None, interval=None):
        strokes_api.save_limit(strokes_api.LimitSave(
            tn="10100", categoria="punch", primer_mantenimiento=first, intervalo_afilado=interval))
        strokes_api.save_limit(strokes_api.LimitSave(
            tn="10100", categoria="die", primer_mantenimiento=first, intervalo_afilado=interval))

    def _produce(self, pieces):
        db = self.factory()
        created = strokes_api.apply_production(db, PART, pieces, "T-1", "op")
        db.commit()
        db.close()
        return created

    def _tool(self, tool_id):
        db = self.factory()
        try:
            return db.query(Herramienta).filter(Herramienta.id == tool_id).one()
        finally:
            db.close()

    def test_punch_and_die_both_accumulate_strokes_per_blank(self):
        strokes_api.save_program_strokes("4I", PART, strokes_api.ProgramStrokes(
            rows=[strokes_api.StrokeRow(station="201", tn="10100", strokes=3)]))
        self._produce(100)  # 2 pieces per blank = 50 blanks x 3 strokes
        self.assertEqual((self._tool("P1").golpes_servicio, self._tool("D1").golpes_total), (150, 150))
        self.assertEqual(self._tool("P2").golpes_servicio, 0)
        self._produce(-20)  # a production correction takes strokes back
        self.assertEqual(self._tool("P1").golpes_servicio, 120)

    def test_default_is_one_stroke_per_blank_and_a_row_can_be_restored(self):
        self._produce(10)
        self.assertEqual(self._tool("P1").golpes_servicio, 5)
        strokes_api.save_program_strokes("4I", PART, strokes_api.ProgramStrokes(
            rows=[strokes_api.StrokeRow(station="201", tn="10100", strokes=4)]))
        strokes_api.save_program_strokes("4I", PART, strokes_api.ProgramStrokes(
            rows=[strokes_api.StrokeRow(station="201", tn="10100", strokes=None)]))
        self.assertEqual(strokes_api.get_program_strokes("4I", PART)["rows"][0]["source"], "default")
        with self.assertRaises(HTTPException):
            strokes_api.save_program_strokes("4I", PART, strokes_api.ProgramStrokes(
                rows=[strokes_api.StrokeRow(station="999", tn="10100", strokes=1)]))

    def test_pre_alert_at_90_percent_and_one_automatic_ticket_at_the_limit(self):
        self._limit(first=100, interval=80)
        self.assertEqual(self._produce(180), [])  # 90 strokes: warning, no ticket
        alerts = strokes_api.stroke_alerts()
        self.assertEqual({row["tool_id"]: row["level"] for row in alerts}, {"P1": "warning", "D1": "warning"})
        created = self._produce(20)  # 100 strokes: due
        self.assertEqual(len(created), 2)
        self._produce(20)  # already has open requests: no duplicates
        db = self.factory()
        rows = db.query(SolicitudServicio).all()
        db.close()
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(r.automatico and r.solicitado_por == "SYSTEM" and r.estado == "open" for r in rows))
        self.assertEqual(self._tool("P1").ubicacion, "T-1")  # the tool stays until Tool Crib arrives
        self.assertIsNotNone(self.requirement.punch_tool_id)

    def test_after_a_sharpening_the_counter_restarts_and_the_interval_applies(self):
        self._limit(first=100, interval=40)
        self._produce(200)
        db = self.factory()
        tool = db.query(Herramienta).filter(Herramienta.id == "P1").one()
        db.add(ServicioHerramienta(
            herramienta_id="P1", tipo="sharpening", resultado="ready", realizado_por="tc", fecha=workflow_api.utcnow()))
        strokes_api.reset_after_service(db, tool, "tc", "sharpened")
        db.commit()
        db.close()
        level = strokes_api.get_tool_strokes("P1")
        self.assertEqual((level["used"], level["limit"], level["stage"], level["total"]), (0, 40, "interval", 100))

    def test_no_limit_means_no_alert_and_a_manual_set_needs_a_reason(self):
        self._produce(1000)
        self.assertEqual(strokes_api.get_tool_strokes("P1")["level"], "none")
        with self.assertRaises(HTTPException):
            strokes_api.set_tool_strokes("P1", strokes_api.StrokeSet(golpes=10, motivo=""))
        result = strokes_api.set_tool_strokes("P1", strokes_api.StrokeSet(golpes=10, motivo="Counted by hand"))
        self.assertEqual(result["used"], 10)
        with self.assertRaises(HTTPException):
            strokes_api.save_limit(strokes_api.LimitSave(tn="NOPE", categoria="punch", intervalo_afilado=5))


if __name__ == "__main__":
    unittest.main()
