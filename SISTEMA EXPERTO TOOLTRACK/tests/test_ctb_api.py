import json
import unittest

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import auth
import ctb_api
import workflow_api
from models.inventory import (
    Base, DemandaPlaneacion, FgParte, FgPlaneacion, Herramienta, Maquina, ProgramaParte, Ubicacion,
)


def program(part, kind, uph, rows, thickness="0.075"):
    return ProgramaParte(
        numero_parte=part, tipo_maquina=kind, tipo_programa="NPI", revision=1, thickness=thickness, contenido="x",
        contenido_hash=f"{part}{kind}", actualizado_por="eng", actualizado_en=workflow_api.utcnow(),
        piezas_blank=1, cycle_time_s=int(3600 / uph), cycle_con_lead_s=int(3600 / uph), uph_teorico=uph,
        requisitos_json=json.dumps([{"station": s, "tn": t, "angle": None} for s, t in rows]),
    )


class CtbTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine, expire_on_commit=False, autoflush=False)()
        now = workflow_api.utcnow()
        for index in range(1, 5):
            code = f"T-{index}"
            self.db.add(Ubicacion(codigo=code, nombre=code, tipo="machine", activa=True))
            self.db.add(Maquina(codigo=code, tipo_maquina="4 Index" if index < 4 else "2 Index"))
        self.db.add(Ubicacion(codigo="R-1", nombre="R-1", tipo="rack", activa=True))
        self.db.add(FgPlaneacion(numero_fg="FG-1", descripcion="Rack", cliente="C", creado_por="p", creado_en=now, actualizado_en=now))
        self.db.flush()
        fg = self.db.query(FgPlaneacion).one()
        for part, qty in (("MAKE-A_00-SW", 5), ("MAKE-B_00-SW", 2), ("MAKE-C_00-SW", 1)):
            self.db.add(FgParte(fg_id=fg.id, numero_parte=part, piezas_por_fg=qty))
        self.db.add(program("MAKE-A_00-SW", "4I", 100, [("101", "10100"), ("102", "10200")]))
        self.db.add(program("MAKE-B_00-SW", "4I", 50, [("101", "10100")]))
        self.db.add(program("MAKE-B_00-SW", "2I", 40, [("101", "10100")]))
        self.db.commit()
        self.fg = fg.id

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def _tools(self, tn, free=1, state="disponible", where="R-1"):
        for category in ("punzon", "matriz"):
            for index in range(free):
                self.db.add(Herramienta(id=f"{tn}{category[0]}{state[0]}{where}{index}", tn=tn, categoria=category, estado=state, ubicacion=where))
        self.db.commit()

    def _run(self, counts, weekly=100, **extra):
        return ctb_api.build_ctb(self.db, "FG-1", weekly, counts, shifts=1, hours_shift=8, days=5, efficiency=100, setup_minutes=0, **extra)

    def test_a_make_without_program_blocks_the_verdict_and_is_named(self):
        self._tools("10100", 5), self._tools("10200", 5)
        result = self._run({"4I": 2})
        self.assertEqual(result["verdict"], "not_ready")
        self.assertEqual(result["pillars"]["programs"], {"status": "blocked", "ok": 2, "total": 3})
        self.assertTrue(any("MAKE-C_00-SW" in b["text"] for b in result["blockers"]))

    def test_machines_that_do_not_have_the_hours_say_how_many_more_are_needed(self):
        self.db.add(program("MAKE-C_00-SW", "4I", 100, [("101", "10100")]))
        self.db.commit()
        self._tools("10100", 9), self._tools("10200", 9)
        # 100 FG: 500 A (5 h) + 200 B (4 h) + 100 C (1 h) = 10 h; one machine has 40 h, so 1 machine is enough but not 0.
        enough = self._run({"4I": 1})
        self.assertEqual((enough["pillars"]["machines"]["status"], enough["pillars"]["machines"]["minimum"]), ("ok", {"4I": 1}))
        short = ctb_api.build_ctb(self.db, "FG-1", 1000, {"4I": 1}, shifts=1, hours_shift=8, days=5, efficiency=100, setup_minutes=0)
        self.assertEqual(short["pillars"]["machines"]["status"], "blocked")
        self.assertEqual(short["pillars"]["machines"]["extra"], {"4I": 2})
        self.assertTrue(any("2 more 4I" in b["text"] for b in short["blockers"]))

    def test_tools_are_counted_per_machine_reusing_them_between_parts_and_the_missing_ones_are_listed(self):
        self.db.add(program("MAKE-C_00-SW", "4I", 100, [("101", "10100")]))
        self.db.commit()
        self._tools("10100", 1), self._tools("10200", 2)
        # A uses 10100+10200, B and C use 10100: each machine needs one 10100 (reused between its parts).
        one = self._run({"4I": 1})
        self.assertEqual(one["pillars"]["tools"]["units_to_buy"], 0)
        three = self._run({"4I": 3})
        buy = {(e["tn"], e["category"]): e for e in three["buy"]}
        self.assertEqual(buy[("10100", "punzon")]["need"], 3)
        self.assertEqual(buy[("10100", "punzon")]["to_buy"], 2)
        self.assertNotIn(("10200", "punzon"), buy)
        self.assertEqual(three["verdict"], "not_ready")
        self.assertEqual(three["alternative"]["units_to_buy"], 0)  # fewer machines, fewer tool sets

    def test_only_exact_usable_tools_count_and_returning_or_elsewhere_tools_are_shown_apart(self):
        self.db.add(program("MAKE-C_00-SW", "4I", 100, [("101", "10100")]))
        self.db.commit()
        self._tools("10200", 1)
        self._tools("10100", 1, state="afilado", where="MANT")
        self._tools("10100", 1, where="T-4")  # mounted on a machine outside the scenario
        result = self._run({"4I": 1})
        entry = next(e for e in result["buy"] if e["tn"] == "10100" and e["category"] == "punzon")
        self.assertEqual((entry["free"], entry["returning"], entry["elsewhere"], entry["missing"], entry["to_buy"]), (0, 1, 1, 1, 0))
        self.assertEqual(entry["description"], "Round 0.100 in.")

    def test_the_board_takes_hours_off_the_machines_and_unknown_fg_is_rejected(self):
        self.db.add(program("MAKE-C_00-SW", "4I", 100, [("101", "10100")]))
        now = workflow_api.utcnow()
        self.db.add(DemandaPlaneacion(maquina_codigo="T-1", numero_parte="MAKE-A_00-SW", cantidad=3500, piezas_producidas=0,
                                      orden=1, activa=True, creado_por="p", creado_en=now, actualizado_en=now))
        self.db.commit()
        self._tools("10100", 9), self._tools("10200", 9)
        one = self._run({"4I": 1})
        self.assertNotEqual(one["machines"][0]["code"], "T-1")  # the free machine is chosen first
        all_three = self._run({"4I": 3})
        without = self._run({"4I": 3}, include_board=False)
        self.assertEqual({m["code"]: m["committed_h"] for m in all_three["machines"]}["T-1"], 35.0)
        self.assertEqual({m["code"]: m["committed_h"] for m in without["machines"]}["T-1"], 0)
        with self.assertRaises(HTTPException):
            ctb_api.build_ctb(self.db, "NOPE", 100, {"4I": 1})


if __name__ == "__main__":
    unittest.main()
