import unittest

from fastapi import HTTPException
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

import kitting_api
import workflow_api
from models.inventory import Base, Maquina, ProgramaParte
from pick_list import build_pick_items
from tests.test_workflow_api import setup_text
from workflow_ui import build_operator_pick_list

CURRENT = "TYEH-1000001_00-SW"
NEW = "TYEH-1000002_00-SW"
OTHER_TYPE_ONLY = "TYEH-1000003_00-SW"


class KittingApiTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")

        @event.listens_for(self.engine, "connect")
        def enable_foreign_keys(connection, _record):
            cursor = connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        Base.metadata.create_all(self.engine)
        factory = sessionmaker(bind=self.engine, expire_on_commit=False, autoflush=False)
        self.originals = [(m, m.SessionLocal) for m in (workflow_api, kitting_api)]
        for module, _ in self.originals:
            module.SessionLocal = factory
        self.factory = factory
        for code, kind in (("M1", "machine"), ("C1", "cart"), ("C2", "cart")):
            workflow_api.create_location(workflow_api.LocationCreate(codigo=code, nombre=code, tipo=kind))
        db = factory()
        db.add(Maquina(codigo="M1", tipo_maquina="4 Index"))
        self._program(db, CURRENT, "4I", "0.075", [("201", "100", "0"), ("202", "200", "90")])
        self._program(db, NEW, "4I", "0.119", [("301", "100", "0"), ("303", "300", "45")])
        self._program(db, OTHER_TYPE_ONLY, "2I", "0.075", [("201", "100", "0")])
        db.commit()
        db.close()

    def tearDown(self):
        for module, original in self.originals:
            module.SessionLocal = original
        self.engine.dispose()

    @staticmethod
    def _program(db, part, kind, thickness, requirements):
        db.add(ProgramaParte(
            numero_parte=part, tipo_maquina=kind, tipo_programa="SUSTAINING", revision=1,
            contenido=setup_text(part, thickness, requirements), contenido_hash=part + kind,
            requisitos_json="[]", actualizado_por="test", actualizado_en=workflow_api.utcnow(),
        ))

    def plan(self, **kwargs):
        body = {"maquina_codigo": "m1", "setup_nuevo_parte": NEW, "setup_anterior_parte": CURRENT}
        body.update(kwargs)
        return kitting_api.plan(kitting_api.PlanRequest(**body))

    def test_plan_lists_tools_to_supply_and_dies_to_verify(self):
        result = self.plan()
        self.assertEqual(result["tipo"], "4I")
        self.assertEqual(result["current_part"], CURRENT)
        self.assertFalse(result["needs_current"])
        self.assertEqual(result["suggested_cart"], "C1")
        self.assertEqual(result["comparison"]["thickness_current"], "0.075")
        self.assertEqual(result["comparison"]["thickness_new"], "0.119")
        self.assertTrue(result["comparison"]["thickness_changed"])
        pairs = [(row["tn"], row["category"], row["kind"]) for row in result["pick_list"]]
        self.assertIn(("300", "punch", "supply"), pairs)
        self.assertIn(("300", "die", "supply"), pairs)
        self.assertIn(("100", "die", "verify"), pairs)
        self.assertEqual(result["comparison"]["supply"], 2)
        self.assertEqual(result["comparison"]["verify"], 1)

    def test_plan_without_a_known_current_part_asks_for_it(self):
        result = self.plan(setup_anterior_parte=None)
        self.assertTrue(result["needs_current"])
        self.assertIsNone(result["comparison"])

    def test_plan_rejects_part_that_only_exists_for_another_machine_type(self):
        with self.assertRaises(HTTPException) as caught:
            self.plan(setup_nuevo_parte=OTHER_TYPE_ONLY)
        self.assertEqual(caught.exception.status_code, 422)
        self.assertIn("only exists for 2I", caught.exception.detail)

    def test_plan_rejects_unknown_part_and_unknown_machine(self):
        with self.assertRaises(HTTPException) as caught:
            self.plan(setup_nuevo_parte="TYEH-9999999_00-SW")
        self.assertIn("not in the system", caught.exception.detail)
        with self.assertRaises(HTTPException) as caught:
            self.plan(maquina_codigo="T-999")
        self.assertEqual(caught.exception.status_code, 404)

    def test_start_creates_kit_in_first_free_cart_and_blocks_a_second_one(self):
        kit = kitting_api.start(kitting_api.StartRequest(
            maquina_codigo="M1", setup_nuevo_parte=NEW, setup_anterior_parte=CURRENT, creado_por="op"))
        self.assertEqual((kit["machine"], kit["cart"], kit["state"]), ("M1", "C1", "preparing"))
        overview = kitting_api.overview()
        busy = next(c for c in overview["carts"] if c["codigo"] == "C1")
        self.assertEqual((busy["state"], busy["machine"]), ("preparing", "M1"))
        self.assertGreater(busy["total"], 0)
        self.assertEqual(next(c for c in overview["carts"] if c["codigo"] == "C2")["state"], "free")
        self.assertEqual(self.plan()["open_kit"]["id"], kit["id"])
        with self.assertRaises(HTTPException) as caught:
            kitting_api.start(kitting_api.StartRequest(
                maquina_codigo="M1", setup_nuevo_parte=NEW, setup_anterior_parte=CURRENT, creado_por="op"))
        self.assertEqual(caught.exception.status_code, 409)

    def test_start_without_free_carts_explains_what_to_do(self):
        for machine, cart in (("M1", "C1"),):
            kitting_api.start(kitting_api.StartRequest(
                maquina_codigo=machine, setup_nuevo_parte=NEW, setup_anterior_parte=CURRENT,
                carro_codigo=cart, creado_por="op"))
        workflow_api.create_location(workflow_api.LocationCreate(codigo="M2", nombre="M2", tipo="machine"))
        db = self.factory()
        db.add(Maquina(codigo="M2", tipo_maquina="4 Index"))
        db.commit()
        db.close()
        kitting_api.start(kitting_api.StartRequest(
            maquina_codigo="M2", setup_nuevo_parte=NEW, setup_anterior_parte=CURRENT, creado_por="op"))
        workflow_api.create_location(workflow_api.LocationCreate(codigo="M3", nombre="M3", tipo="machine"))
        db = self.factory()
        db.add(Maquina(codigo="M3", tipo_maquina="4 Index"))
        db.commit()
        db.close()
        with self.assertRaises(HTTPException) as caught:
            kitting_api.start(kitting_api.StartRequest(
                maquina_codigo="M3", setup_nuevo_parte=NEW, setup_anterior_parte=CURRENT, creado_por="op"))
        self.assertIn("No free carts", caught.exception.detail)

    def test_mismatch_between_chosen_and_registered_setup_is_reported_with_both_names(self):
        kit = kitting_api.start(kitting_api.StartRequest(
            maquina_codigo="M1", setup_nuevo_parte=NEW, setup_anterior_parte=CURRENT, creado_por="op"))
        workflow_api.cancel_kit(kit["id"], workflow_api.SurtidoAction(realizado_por="op", motivo="test"))
        with self.assertRaises(HTTPException) as caught:
            kitting_api.start(kitting_api.StartRequest(
                maquina_codigo="M1", setup_nuevo_parte=CURRENT, setup_anterior_parte=NEW, creado_por="op"))
        detail = caught.exception.detail
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual((detail["code"], detail["registered"], detail["chosen"]), ("SETUP_MISMATCH", CURRENT, NEW))
        corrected = kitting_api.start(kitting_api.StartRequest(
            maquina_codigo="M1", setup_nuevo_parte=CURRENT, setup_anterior_parte=NEW, creado_por="op",
            corregir_actual=True, motivo_correccion="Operator confirmed what is mounted"))
        self.assertEqual(corrected["state"], "preparing")

    def test_pick_list_codes_and_spanish_desktop_labels_stay_in_sync(self):
        comparison = workflow_api.compare_setup_files(workflow_api.SetupFilesComparisonRequest(
            setup_anterior_nombre="a.stp", setup_anterior_contenido=setup_text("A", "0.075", [("201", "100", "0")]),
            setup_nuevo_nombre="b.stp", setup_nuevo_contenido=setup_text("B", "0.119", [("301", "100", "0"), ("303", "300", "45")]),
        ))
        codes = build_pick_items(comparison)
        spanish = build_operator_pick_list(comparison)
        self.assertEqual(len(codes), len(spanish))
        self.assertEqual({row["category"] for row in spanish}, {"Punzón", "Matriz"})


if __name__ == "__main__":
    unittest.main()
