import unittest
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import auth
import planning_api
import workflow_api
from models.inventory import (
    Base,
    DemandaPlaneacion,
    Maquina,
    ProgramaParte,
    RegistroProduccion,
    Ubicacion,
)


class PlanningApiTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(bind=self.engine, expire_on_commit=False, autoflush=False)
        self.original = planning_api.SessionLocal
        planning_api.SessionLocal = self.factory
        token = auth.acting_as("planner", "PLANNING")
        self.addCleanup(auth.CURRENT_USER.reset, token)
        db = self.factory()
        now = workflow_api.utcnow()
        for code, kind in (("T-1", "4 Index"), ("T-2", "4 Index")):
            db.add(Ubicacion(codigo=code, nombre=code, tipo="machine", activa=True))
            db.add(Maquina(codigo=code, tipo_maquina=kind, edificio="B1"))
        for part, cycle in (("MAKE-A_00-SW", 36), ("MAKE-B_00-SW", 72)):
            db.add(ProgramaParte(
                numero_parte=part, tipo_maquina="4I", tipo_programa="NPI", revision=1, thickness="0.075",
                contenido="x", contenido_hash=part, requisitos_json="[]", actualizado_por="eng", actualizado_en=now,
                piezas_blank=1, cycle_time_s=cycle, cycle_con_lead_s=cycle, uph_teorico=3600 / cycle,
            ))
        db.commit()
        db.close()
        self.week = planning_api.current_monday()

    def tearDown(self):
        planning_api.SessionLocal = self.original
        self.engine.dispose()

    def _fg(self, number="FG-1", parts=(("MAKE-A_00-SW", 5), ("MAKE-B_00-SW", 2)), **extra):
        return planning_api.create_fg(planning_api.FgIn(
            numero_fg=number, partes=[planning_api.FgPart(numero_parte=p, piezas=q) for p, q in parts], **extra))

    def _forecast(self, number, **weeks):
        planning_api.save_forecast(planning_api.ForecastIn(filas=[planning_api.ForecastRow(
            numero_fg=number, semanas={(self.week + timedelta(weeks=int(k[1:]))).isoformat(): v for k, v in weeks.items()})]))

    def _params(self, **values):
        planning_api.save_params(planning_api.ParamsIn(valores=values))

    def test_a_fg_keeps_its_bom_and_flags_parts_without_program(self):
        fg = self._fg(parts=(("make-a_00-sw", 5), ("MANUAL-MAKE", 3)), descripcion="Rack", cliente="PCR")
        self.assertEqual(fg["numero_fg"], "FG-1")
        flags = {row["numero_parte"]: row["has_program"] for row in fg["parts"]}
        self.assertEqual(flags, {"MAKE-A_00-SW": True, "MANUAL-MAKE": False})
        with self.assertRaises(HTTPException) as duplicate:
            self._fg()
        self.assertEqual(duplicate.exception.status_code, 409)
        with self.assertRaises(HTTPException) as repeated:
            self._fg("FG-2", parts=(("MAKE-A_00-SW", 1), ("make-a_00-sw", 2)))
        self.assertEqual(repeated.exception.status_code, 422)

    def test_a_fg_can_share_the_bom_of_another_one_but_not_chain_it(self):
        self._fg("FG-1")
        copy = self._fg("FG-1-SH", parts=(), bom_de="fg-1")
        self.assertEqual((copy["bom_from"], [p["piezas"] for p in copy["parts"]]), ("FG-1", [5, 2]))
        with self.assertRaises(HTTPException) as chained:
            self._fg("FG-3", parts=(), bom_de="FG-1-SH")
        self.assertEqual(chained.exception.status_code, 422)
        with self.assertRaises(HTTPException) as both:
            self._fg("FG-4", parts=(("MAKE-A_00-SW", 1),), bom_de="FG-1")
        self.assertEqual(both.exception.status_code, 422)
        first = next(row for row in planning_api.list_fg() if row["numero_fg"] == "FG-1")
        with self.assertRaises(HTTPException) as in_use:
            planning_api.remove_fg(first["id"])
        self.assertEqual(in_use.exception.status_code, 409)

    def test_forecast_is_saved_by_week_and_zero_removes_it(self):
        self._fg()
        self._forecast("FG-1", w0=100, w1=50)
        grid = planning_api.read_forecast(4)
        self.assertEqual(grid["rows"][0]["values"], [100, 50, 0, 0])
        self._forecast("FG-1", w1=0)
        self.assertEqual(planning_api.read_forecast(4)["rows"][0]["values"], [100, 0, 0, 0])
        with self.assertRaises(HTTPException) as unknown:
            self._forecast("NOPE", w0=1)
        self.assertEqual(unknown.exception.status_code, 404)

    def test_capacity_meets_a_small_demand_and_fails_a_large_one(self):
        self._params(horizonte_semanas=4, setup_minutos=0, adelanto_semanas=0)
        self._fg()
        # MAKE-A: 100 pcs/h, MAKE-B: 50 pcs/h. 100 FG per week = 500 A (5 h) + 200 B (4 h).
        self._forecast("FG-1", w0=100)
        ok = planning_api.build_capacity(self.factory(), 4)
        self.assertEqual(ok["summary"][0]["status"], "cumple")
        self.assertEqual(ok["summary"][0]["load_h"], 9.0)
        self.assertEqual(ok["fgs"][0]["weeks"][0]["status"], "cumple")
        # Two machines at 3 × 8 × 5 × 85% = 102 h each.
        self._forecast("FG-1", w0=30000)
        bad = planning_api.build_capacity(self.factory(), 4)
        self.assertEqual(bad["summary"][0]["status"], "no_cumple")
        self.assertEqual(bad["fgs"][0]["weeks"][0]["status"], "no_cumple")
        self.assertEqual(bad["machines"][0]["hours_week"], 102.0)

    def test_what_was_produced_and_what_is_on_the_board_is_not_planned_again(self):
        self._params(horizonte_semanas=2, setup_minutos=0, adelanto_semanas=0)
        self._fg(parts=(("MAKE-A_00-SW", 1),))
        self._forecast("FG-1", w0=1000, w1=1000)
        db = self.factory()
        now = workflow_api.utcnow()
        db.add(DemandaPlaneacion(maquina_codigo="T-1", numero_parte="MAKE-A_00-SW", cantidad=700, piezas_producidas=0,
                                 orden=1, activa=True, creado_por="p", creado_en=now, actualizado_en=now))
        db.add(RegistroProduccion(numero_parte="MAKE-A_00-SW", piezas=500, fecha_produccion=now, capturado_por="op",
                                  capturado_en=now, fuente="manual"))
        db.commit()
        db.close()
        result = planning_api.build_capacity(self.factory(), 2)
        part = result["parts"][0]
        self.assertEqual((part["produced"], part["board_pending"], part["forecast_total"]), (500, 700, 2000))
        self.assertEqual([row["need"] for row in part["weeks"]], [0, 800])
        # The 700 on the Board take 7 h of T-1 from the first week.
        machine = next(m for m in result["machines"] if m["code"] == "T-1")
        self.assertEqual(machine["committed_h"], 7.0)

    def test_a_part_without_program_marks_its_fg_as_no_program_and_not_as_short(self):
        self._params(horizonte_semanas=2, setup_minutos=0, adelanto_semanas=0)
        self._fg("FG-9", parts=(("MAKE-A_00-SW", 1), ("MANUAL-MAKE", 1)))
        self._forecast("FG-9", w0=100)
        result = planning_api.build_capacity(self.factory(), 2)
        self.assertEqual(result["fgs"][0]["weeks"][0]["status"], "sin_programa")
        self.assertTrue(any("MANUAL-MAKE" in text for text in result["warnings"]))

    def test_machine_capacity_is_editable_and_marked_as_configured(self):
        before = next(m for m in planning_api.read_machines()["machines"] if m["code"] == "T-1")
        self.assertFalse(before["configured"])
        planning_api.save_machine("t-1", planning_api.MachineCapacity(turnos=2, horas_turno=8, dias_semana=6, eficiencia_pct=80))
        after = next(m for m in planning_api.read_machines()["machines"] if m["code"] == "T-1")
        self.assertEqual((after["configured"], after["hours_week"]), (True, 76.8))
        with self.assertRaises(HTTPException):
            planning_api.save_machine("T-1", planning_api.MachineCapacity(turnos=5, horas_turno=8, dias_semana=6, eficiencia_pct=80))
        planning_api.save_machine("T-2", planning_api.MachineCapacity(turnos=3, horas_turno=8, dias_semana=5, eficiencia_pct=85, incluida=False))
        self.assertEqual([m["code"] for m in planning_api.build_capacity(self.factory(), 2)["machines"]], ["T-1"])

    def test_planning_can_edit_but_engineering_only_reads(self):
        engineering = auth.ROLE_PERMISSIONS["ENGINEERING"]
        self.assertIn("planning.view", engineering)
        self.assertNotIn("planning.manage", engineering)
        token = auth.acting_as("eng", "ENGINEERING")
        try:
            with self.assertRaises(HTTPException) as denied:
                self._fg()
            self.assertEqual(denied.exception.status_code, 403)
            self.assertEqual(planning_api.list_fg(), [])
        finally:
            auth.CURRENT_USER.reset(token)


if __name__ == "__main__":
    unittest.main()
