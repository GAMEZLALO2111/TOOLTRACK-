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
                Ubicacion(codigo="RACK-1", nombre="Rack 1", tipo="rack", activa=True),
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

    def test_delete_cleans_catalog_link(self):
        self.create_punch()
        result = main.eliminar_herramienta("PHYS-1")
        self.assertEqual(result, {"ok": True})
        session = self.session_factory()
        try:
            self.assertIsNone(session.query(Herramienta).filter_by(id="PHYS-1").first())
            self.assertEqual(session.query(HerramientaCatalogo).count(), 0)
        finally:
            session.close()


if __name__ == "__main__":
    unittest.main()
