import os
import unittest
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

import main
from models.inventory import (
    Base,
    ConfiguracionTN,
    Herramienta,
    HerramientaCatalogo,
    MovimientoHerramienta,
    NumeroHerramienta,
    Ubicacion,
)


class InventoryEditTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")

        @event.listens_for(self.engine, "connect")
        def enable_foreign_keys(connection, _record):
            cursor = connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False)
        self.original_session_local = main.SessionLocal
        main.SessionLocal = self.session_factory
        session = self.session_factory()
        try:
            session.add_all([
                Ubicacion(codigo=code, nombre=code, tipo="rack", activa=True)
                for code in ("RACK-1", "RACK-2", "RACK-9")
            ])
            session.commit()
        finally:
            session.close()

    def tearDown(self):
        main.SessionLocal = self.original_session_local
        self.engine.dispose()

    def create_punch(self, tool_id="PHYS-1", tn="TN-100", type_="ROUND"):
        return main.registrar_herramienta_inventario(main.RegistroInventarioCreate(
            id=tool_id,
            categoria="punzon",
            tn=tn,
            size="A",
            type=type_,
            life="10",
            so="SO-1",
            ubicacion="RACK-1",
        ))

    def test_listing_is_paginated_filtered_and_shows_catalog_link(self):
        for number in range(5):
            self.create_punch(tool_id=f"PHYS-{number}", tn="TN-100")
        main.registrar_herramienta_inventario(main.RegistroInventarioCreate(
            id="HOLD-1", categoria="holder", type="HOLDER A", ubicacion="RACK-9"))

        first = main.listar_inventario(limit=4, offset=0)
        self.assertEqual((first["total"], len(first["items"])), (6, 4))
        self.assertEqual(first["summary"], {"total": 6, "linked": 5, "unlinked": 1})
        second = main.listar_inventario(limit=4, offset=4)
        self.assertEqual(len(second["items"]), 2)

        holders = main.listar_inventario(categoria="holder")
        self.assertEqual([row["id"] for row in holders["items"]], ["HOLD-1"])
        self.assertFalse(holders["items"][0]["catalog_linked"])
        self.assertEqual(main.listar_inventario(vinculo="unlinked")["total"], 1)
        self.assertEqual(main.listar_inventario(vinculo="linked")["total"], 5)
        self.assertEqual(main.listar_inventario(q="phys-3")["total"], 1)
        self.assertEqual(main.listar_inventario(ubicacion="RACK-9")["total"], 1)
        self.assertEqual(main.listar_inventario(estado="afilado")["total"], 0)
        self.assertEqual(main.listar_inventario(estado="disponible")["items"][0]["estado"], "disponible")
        self.assertEqual(len(main.listar_inventario(limit=9999)["items"]), 6)

    def test_options_list_the_controlled_types(self):
        options = main.opciones_inventario()
        self.assertIn("ROUND", options["tipos"]["punzon"])
        self.assertIn("HOLDER A", options["tipos"]["holder"])
        self.assertEqual(options["sizes"], list("ABCDE"))

    def test_edit_updates_fields_and_reassigns_catalog_variant(self):
        self.create_punch()

        updated = main.actualizar_herramienta_inventario(
            "PHYS-1",
            main.RegistroInventarioUpdate(
                id="PHYS-1",
                categoria="matriz",
                tn="TN-200",
                size="B",
                type="SQUARE",
                life="12.5",
                clearance="0.020",
                so="SO-2",
                ubicacion="RACK-2",
            ),
        )

        self.assertEqual(updated["tn"], "TN-200")
        self.assertEqual(updated["categoria"], "matriz")
        self.assertEqual(updated["clearance"], "0.020")
        self.assertTrue(updated["catalog_linked"])

    def test_edit_can_convert_catalogued_tool_to_holder(self):
        self.create_punch()

        updated = main.actualizar_herramienta_inventario(
            "PHYS-1",
            main.RegistroInventarioUpdate(
                id="PHYS-1",
                categoria="holder",
                type="HOLDER A",
                so="SO-1",
                ubicacion="RACK-2",
            ),
        )

        self.assertIsNone(updated["tn"])
        self.assertIsNone(updated["size"])
        self.assertIsNone(updated["clearance"])
        self.assertFalse(updated["catalog_linked"])
        session = self.session_factory()
        try:
            self.assertEqual(session.query(HerramientaCatalogo).count(), 0)
            self.assertEqual(session.query(Herramienta).filter_by(id="PHYS-1").one().type, "HOLDER A")
        finally:
            session.close()

    def test_edit_conflict_rolls_back_and_preserves_existing_tool(self):
        self.create_punch(tn="TN-100", type_="ROUND")
        self.create_punch(tool_id="PHYS-2", tn="TN-100", type_="ROUND")

        with self.assertRaises(HTTPException) as error:
            main.actualizar_herramienta_inventario(
                "PHYS-1",
                main.RegistroInventarioUpdate(
                    id="PHYS-1",
                    categoria="punzon",
                    tn="TN-100",
                    size="A",
                    type="SQUARE",
                    life="15",
                ),
            )

        self.assertEqual(error.exception.status_code, 409)
        session = self.session_factory()
        try:
            tool = session.query(Herramienta).filter_by(id="PHYS-1").one()
            self.assertEqual(tool.type, "ROUND")
            self.assertEqual(tool.life, "10.000")
        finally:
            session.close()

    def test_edit_rejects_physical_id_change(self):
        self.create_punch()
        with self.assertRaises(HTTPException) as error:
            main.actualizar_herramienta_inventario(
                "PHYS-1",
                main.RegistroInventarioUpdate(
                    id="PHYS-2",
                    categoria="holder",
                    type="HOLDER A",
                ),
            )
        self.assertEqual(error.exception.status_code, 409)

    def test_create_rejects_type_from_another_category(self):
        with self.assertRaises(HTTPException) as error:
            main.registrar_herramienta_inventario(main.RegistroInventarioCreate(
                id="HOLDER-1",
                categoria="holder",
                type="ROUND",
            ))

        self.assertEqual(error.exception.status_code, 422)
        session = self.session_factory()
        try:
            self.assertEqual(session.query(Herramienta).count(), 0)
        finally:
            session.close()

    def test_catalog_configuration_rejects_non_geometry_type(self):
        session = self.session_factory()
        try:
            session.add(NumeroHerramienta(tn="TN-100"))
            session.commit()
        finally:
            session.close()

        with self.assertRaises(HTTPException) as error:
            main.crear_configuracion_tn(main.ConfiguracionTNCreate(
                tn="TN-100",
                size="A",
                type="HOLDER A",
            ))

        self.assertEqual(error.exception.status_code, 422)
        session = self.session_factory()
        try:
            self.assertEqual(session.query(ConfiguracionTN).count(), 0)
        finally:
            session.close()

    def test_edit_allows_unchanged_unrecognized_legacy_type(self):
        session = self.session_factory()
        try:
            session.add(Herramienta(
                id="LEGACY-1",
                type="PUNZON PLANO A",
                categoria="punzon",
                tn="TN-LEGACY",
                size="A",
                life="8",
            ))
            session.commit()
        finally:
            session.close()

        updated = main.actualizar_herramienta_inventario(
            "LEGACY-1",
            main.RegistroInventarioUpdate(
                id="LEGACY-1",
                categoria="punzon",
                type="PUNZON PLANO A",
                tn="TN-LEGACY",
                size="A",
                life="8",
            ),
        )

        self.assertEqual(updated["type"], "PUNZON PLANO A")
        self.assertTrue(updated["catalog_linked"])

    def test_edit_rejects_legacy_type_when_category_changes(self):
        session = self.session_factory()
        try:
            session.add(Herramienta(
                id="LEGACY-1",
                type="ROUND",
                categoria="punzon",
                tn="TN-LEGACY",
                size="A",
                life="8",
            ))
            session.commit()
        finally:
            session.close()

        with self.assertRaises(HTTPException) as error:
            main.actualizar_herramienta_inventario(
                "LEGACY-1",
                main.RegistroInventarioUpdate(
                    id="LEGACY-1",
                    categoria="holder",
                    type="ROUND",
                ),
            )

        self.assertEqual(error.exception.status_code, 422)

    def test_workflow_movement_history_blocks_manual_location_edits_and_deletion(self):
        self.create_punch()
        session = self.session_factory()
        try:
            session.add_all([
                Ubicacion(codigo="M1", nombre="Machine 1", tipo="machine", activa=True),
            ])
            session.flush()
            session.add(MovimientoHerramienta(
                herramienta_id="PHYS-1",
                origen_codigo="RACK-1",
                destino_codigo="M1",
                tipo="rack_to_machine_delivery",
                realizado_por="operator",
                creado_en=datetime.now(timezone.utc).replace(tzinfo=None),
            ))
            session.commit()
        finally:
            session.close()

        with self.assertRaises(HTTPException) as edit_error:
            main.actualizar_herramienta_inventario(
                "PHYS-1",
                main.RegistroInventarioUpdate(
                    id="PHYS-1",
                    categoria="punzon",
                    tn="TN-100",
                    size="A",
                    type="ROUND",
                    life="10",
                    ubicacion="RACK-2",
                ),
            )
        self.assertEqual(edit_error.exception.status_code, 409)

        with self.assertRaises(HTTPException) as identity_error:
            main.actualizar_herramienta_inventario(
                "PHYS-1",
                main.RegistroInventarioUpdate(
                    id="PHYS-1",
                    categoria="punzon",
                    tn="TN-200",
                    size="A",
                    type="ROUND",
                    life="10",
                    ubicacion="RACK-1",
                ),
            )
        self.assertEqual(identity_error.exception.status_code, 409)

        updated = main.actualizar_herramienta_inventario(
            "PHYS-1",
            main.RegistroInventarioUpdate(
                id="PHYS-1",
                categoria="punzon",
                tn="TN-100",
                size="A",
                type="ROUND",
                life="11",
                ubicacion="RACK-1",
            ),
        )
        self.assertEqual(updated["life"], "11.000")

        with self.assertRaises(HTTPException) as delete_error:
            main.eliminar_herramienta("PHYS-1")
        self.assertEqual(delete_error.exception.status_code, 409)

    def test_location_must_be_a_registered_active_rack(self):
        self.create_punch()
        session = self.session_factory()
        try:
            session.add_all([
                Ubicacion(codigo="M1", nombre="Machine 1", tipo="machine", activa=True),
                Ubicacion(codigo="RACK-OLD", nombre="Old", tipo="rack", activa=False),
            ])
            session.commit()
        finally:
            session.close()

        def payload(place):
            return dict(id="PHYS-1", categoria="punzon", tn="TN-100", size="A", type="ROUND", life="10", ubicacion=place)

        for bad in ("RACK-TYPO", "M1", "RACK-OLD"):
            with self.assertRaises(HTTPException) as error:
                main.actualizar_herramienta_inventario("PHYS-1", main.RegistroInventarioUpdate(**payload(bad)))
            self.assertEqual(error.exception.status_code, 422, bad)
        with self.assertRaises(HTTPException) as new_error:
            main.registrar_herramienta_inventario(main.RegistroInventarioCreate(
                id="PHYS-2", categoria="punzon", tn="TN-100", size="A", type="ROUND", life="10", ubicacion="NOPE",
            ))
        self.assertEqual(new_error.exception.status_code, 422)

        moved = main.actualizar_herramienta_inventario("PHYS-1", main.RegistroInventarioUpdate(**payload("RACK-2")))
        self.assertEqual(moved["ubicacion"], "RACK-2")
        cleared = main.actualizar_herramienta_inventario("PHYS-1", main.RegistroInventarioUpdate(**payload("")))
        self.assertIsNone(cleared["ubicacion"])

    def test_edit_keeps_an_unchanged_legacy_location(self):
        self.create_punch()
        session = self.session_factory()
        try:
            session.query(Herramienta).filter_by(id="PHYS-1").one().ubicacion = "R-LEGACY"
            session.commit()
        finally:
            session.close()
        updated = main.actualizar_herramienta_inventario("PHYS-1", main.RegistroInventarioUpdate(
            id="PHYS-1", categoria="punzon", tn="TN-100", size="A", type="ROUND", life="12", ubicacion="R-LEGACY",
        ))
        self.assertEqual((updated["ubicacion"], updated["life"]), ("R-LEGACY", "12.000"))

    def test_punch_life_is_the_measured_height_minus_the_stripper_and_can_be_overridden(self):
        punch = main.registrar_herramienta_inventario(main.RegistroInventarioCreate(
            id="H-1", categoria="punzon", tn="TN-200", size="C", type="ROUND", altura="1.000",
            proveedor=" wilson ", ubicacion="RACK-1",
        ))
        self.assertEqual((punch["altura_total"], punch["stripper"], punch["life"]), ("1.000", "0.300", "0.700"))
        self.assertEqual((punch["proveedor"], punch["stripper_custom"], punch["life_alert"]), ("WILSON", False, False))

        custom = main.registrar_herramienta_inventario(main.RegistroInventarioCreate(
            id="H-2", categoria="punzon", tn="TN-200", size="C", type="ROUND", altura="1.000", stripper="0.250",
        ))
        self.assertEqual((custom["stripper"], custom["life"], custom["stripper_custom"]), ("0.250", "0.750", True))

        with self.assertRaises(HTTPException) as too_short:
            main.registrar_herramienta_inventario(main.RegistroInventarioCreate(
                id="H-3", categoria="punzon", tn="TN-200", size="D", type="ROUND", altura="0.350",
            ))
        self.assertEqual(too_short.exception.status_code, 422)
        with self.assertRaises(HTTPException) as missing:
            main.registrar_herramienta_inventario(main.RegistroInventarioCreate(
                id="H-4", categoria="punzon", tn="TN-200", size="A", type="ROUND",
            ))
        self.assertIn("TOTAL HEIGHT", missing.exception.detail)

        # The editable table of strippers changes the next calculation, and the minimum raises the alert.
        original_session = main.tool_params.SessionLocal
        main.tool_params.SessionLocal = self.session_factory
        try:
            main.tool_params.update_parameters(main.tool_params.ParametersUpdate(valores={"stripper_A": "0.280"}))
            with self.assertRaises(HTTPException) as bad_value:
                main.tool_params.update_parameters(main.tool_params.ParametersUpdate(valores={"stripper_Z": "1"}))
        finally:
            main.tool_params.SessionLocal = original_session
        self.assertEqual(bad_value.exception.status_code, 422)
        low = main.registrar_herramienta_inventario(main.RegistroInventarioCreate(
            id="H-5", categoria="punzon", tn="TN-300", size="A", type="ROUND", altura="0.480",
        ))
        self.assertEqual((low["stripper"], low["life"], low["life_alert"]), ("0.280", "0.200", True))

    def test_dies_alert_below_the_minimum_height_and_clearance_comes_from_a_list(self):
        def die(tool_id, clearance, life="1.200"):
            return main.registrar_herramienta_inventario(main.RegistroInventarioCreate(
                id=tool_id, categoria="matriz", tn="TN-400", size="A", type="ROUND", life=life, clearance=clearance,
            ))

        self.assertFalse(die("D-1", "0.016")["life_alert"])
        self.assertTrue(die("D-2", "0.016", life="1.050")["life_alert"])
        for wrong in ("0.003", "0.031", "0.0165"):
            with self.assertRaises(HTTPException) as caught:
                die("D-X", wrong)
            self.assertEqual(caught.exception.status_code, 422)
        self.assertEqual(die("D-3", "0.004")["clearance"], "0.004")
        self.assertEqual(die("D-4", "0.030")["clearance"], "0.030")

        # An old out-of-list value can stay while it is not changed.
        session = self.session_factory()
        try:
            session.query(Herramienta).filter_by(id="D-1").one().clearance = "0.002"
            session.commit()
        finally:
            session.close()
        kept = main.actualizar_herramienta_inventario("D-1", main.RegistroInventarioUpdate(
            id="D-1", categoria="matriz", tn="TN-400", size="A", type="ROUND", life="1.100", clearance="0.002",
        ))
        self.assertEqual(kept["clearance"], "0.002")

    def test_inventory_listing_filters_by_column_and_keeps_the_supplier_when_it_is_not_sent(self):
        main.registrar_herramienta_inventario(main.RegistroInventarioCreate(
            id="F-1", categoria="punzon", tn="TN-500", size="A", type="2 RADIUS", life="0.700", proveedor="WILSON"))
        main.registrar_herramienta_inventario(main.RegistroInventarioCreate(
            id="F-2", categoria="punzon", tn="TN-501", size="B", type="ROUND", life="0.200", proveedor="AMADA"))
        main.registrar_herramienta_inventario(main.RegistroInventarioCreate(
            id="F-3", categoria="matriz", tn="TN-501", size="B", type="ROUND", life="1.200", clearance="0.016"))

        def ids(**filters):
            return [row["id"] for row in main.listar_inventario(**filters)["items"]]

        self.assertEqual(ids(f_supplier="wilson"), ["F-1"])
        self.assertEqual(ids(f_type="2 radius"), ["F-1"])
        self.assertEqual(ids(f_tn="501"), ["F-2", "F-3"])
        self.assertEqual(ids(f_size="B", categoria="matriz"), ["F-3"])
        self.assertEqual(ids(f_clearance="0.016"), ["F-3"])
        self.assertEqual(ids(f_alert="low"), ["F-2"])
        self.assertEqual(ids(f_id="f-2"), ["F-2"])

        main.actualizar_herramienta_inventario("F-1", main.RegistroInventarioUpdate(
            id="F-1", categoria="punzon", tn="TN-500", size="A", type="2 RADIUS", life="0.650"))
        self.assertEqual(ids(f_supplier="WILSON"), ["F-1"])
        cleared = main.actualizar_herramienta_inventario("F-1", main.RegistroInventarioUpdate(
            id="F-1", categoria="punzon", tn="TN-500", size="A", type="2 RADIUS", life="0.650", proveedor=""))
        self.assertIsNone(cleared["proveedor"])
        options = main.opciones_inventario()
        self.assertEqual(options["proveedores"], ["AMADA"])
        self.assertEqual(options["claridades"][0], "0.004")
        self.assertEqual(options["claridades"][-1], "0.030")
        self.assertEqual(len(options["claridades"]), 27)
        self.assertIn("2 RADIUS", options["tipos"]["punzon"])

    def test_a_tool_shows_its_own_image_or_the_one_of_its_tn(self):
        import base64
        import tempfile

        import tool_images

        png = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"x" * 20).decode()
        jpeg = "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8\xff\xe0" + b"y" * 20).decode()
        self.create_punch(tool_id="IMG-1", tn="TN-IMG")
        self.create_punch(tool_id="IMG-2", tn="TN-IMG")
        original = (tool_images.SessionLocal, tool_images.IMAGE_DIR)
        tool_images.SessionLocal = self.session_factory
        with tempfile.TemporaryDirectory() as folder:
            tool_images.IMAGE_DIR = folder
            try:
                with self.assertRaises(HTTPException) as none_yet:
                    tool_images.get_tool_image("IMG-1")
                self.assertEqual(none_yet.exception.status_code, 404)
                with self.assertRaises(HTTPException) as not_image:
                    tool_images.put_tool_image("IMG-1", tool_images.ImageUpload(data=base64.b64encode(b"hello").decode()))
                self.assertEqual(not_image.exception.status_code, 422)
                with self.assertRaises(HTTPException) as too_big:
                    tool_images.put_tool_image("IMG-1", tool_images.ImageUpload(
                        data=base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"x" * (tool_images.MAX_IMAGE_BYTES + 1)).decode()))
                self.assertEqual(too_big.exception.status_code, 413)

                tool_images.put_tn_image("TN-IMG", tool_images.ImageUpload(data=png))
                shared = tool_images.get_tool_image("IMG-2")
                self.assertTrue(shared.path.endswith(".png"))
                tool_images.put_tool_image("IMG-1", tool_images.ImageUpload(data=jpeg))
                own = tool_images.get_tool_image("IMG-1")
                self.assertTrue(own.path.endswith(".jpg"))
                self.assertNotEqual(own.path, shared.path)
                self.assertEqual(tool_images.get_tool_image("IMG-2").path, shared.path)
                self.assertTrue(main.listar_inventario(f_id="IMG-1")["items"][0]["has_image"])

                tool_images.delete_tool_image("IMG-1")
                self.assertEqual(tool_images.get_tool_image("IMG-1").path, shared.path)
                self.assertEqual(len(os.listdir(folder)), 1)
            finally:
                tool_images.SessionLocal, tool_images.IMAGE_DIR = original

    def test_a_tool_is_never_deleted(self):
        self.create_punch()
        with self.assertRaises(HTTPException) as error:
            main.eliminar_herramienta("PHYS-1")
        self.assertEqual(error.exception.status_code, 409)
        with self.assertRaises(HTTPException) as missing:
            main.eliminar_herramienta("NOPE")
        self.assertEqual(missing.exception.status_code, 404)
        session = self.session_factory()
        try:
            self.assertIsNotNone(session.query(Herramienta).filter_by(id="PHYS-1").first())
            self.assertEqual(session.query(HerramientaCatalogo).count(), 1)
        finally:
            session.close()


if __name__ == "__main__":
    unittest.main()
