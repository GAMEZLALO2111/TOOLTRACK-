import unittest
import json
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

import auth
import workflow_api
from models.inventory import (
    Base,
    EstadoMaquina,
    Herramienta,
    Maquina,
    MovimientoHerramienta,
    PendienteRecoger,
    ProgramaParte,
    ReservaHerramienta,
    RequisitoSetup,
    Setup,
    Surtido,
    Ubicacion,
    Usuario,
)


def setup_text(drawing, thickness, requirements):
    rows = "\n".join(
        f" {station} RECTANGULAR 1.000 x 0.250 in - {angle or '-'} 0.018 - {tn}"
        for station, tn, angle in requirements
    )
    return (
        f"DRAWING NAME : {drawing}\n"
        f"THICKNESS : {thickness} in\n\n"
        "TOOL LIST\n"
        "TOOL TYPE SIZE RADS ANGLE DIE/CL EHL Tool No.\n"
        f"{rows}\n\n"
        "EXTRA NOTES\n"
    )


class WorkflowApiTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")

        @event.listens_for(self.engine, "connect")
        def enable_foreign_keys(connection, _record):
            cursor = connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(
            bind=self.engine, expire_on_commit=False, autoflush=False
        )
        self.original_session_local = workflow_api.SessionLocal
        workflow_api.SessionLocal = self.session_factory
        self._create_location("M1", "machine")
        self._create_location("C1", "cart")
        self._create_location("R1", "rack")
        self._create_location("R2", "rack")
        self._create_location("TG1", "togu")
        self._create_location("MT1", "maintenance")

    def tearDown(self):
        workflow_api.SessionLocal = self.original_session_local
        self.engine.dispose()

    def _create_location(self, code, kind):
        return workflow_api.create_location(workflow_api.LocationCreate(
            codigo=code,
            nombre=code,
            tipo=kind,
        ))

    def _create_tool(self, tool_id, tn, category, clearance=None):
        session = self.session_factory()
        try:
            session.add(Herramienta(
                id=tool_id,
                tn=tn,
                size="A",
                type="ROUND",
                categoria=category,
                clearance=clearance,
                ubicacion="R1",
            ))
            session.commit()
        finally:
            session.close()

    def _baseline(self):
        current = setup_text(
            "CURRENT",
            "0.075",
            [("201", "100", "0"), ("202", "200", "90")],
        )
        assignments = [
            workflow_api.SetupAssignment(
                station="201", tn="100", angle="0", punch_id="P100", die_id="D100"
            ),
            workflow_api.SetupAssignment(
                station="202", tn="200", angle="90", punch_id="P200", die_id="D200"
            ),
        ]
        self._create_tool("P100", "100", "punzon")
        self._create_tool("D100", "100", "matriz", "0.016")
        self._create_tool("P200", "200", "punzon")
        self._create_tool("D200", "200", "matriz", "0.016")
        return workflow_api.create_machine_baseline(workflow_api.SetupBaselineCreate(
            maquina_codigo="M1",
            nombre_archivo="current.stp",
            contenido=current,
            realizado_por="operator",
            herramientas=assignments,
        ))

    def _create_new_setup_kit(self):
        self._baseline()
        self._create_tool("D100N", "100", "matriz", "0.020")
        self._create_tool("P300", "300", "punzon")
        self._create_tool("D300", "300", "matriz", "0.020")
        new_content = setup_text(
            "NEW",
            "0.119",
            [("301", "100", "0"), ("303", "300", "45")],
        )
        return new_content, workflow_api.create_kit(workflow_api.SurtidoCreate(
            maquina_codigo="M1",
            carro_codigo="C1",
            nombre_archivo="new.stp",
            contenido=new_content,
            creado_por="operator",
        ))

    def test_delivery_updates_setup_and_tracks_pending_pickups_and_togu_moves(self):
        new_content, kit = self._create_new_setup_kit()
        comparison = workflow_api.compare_with_machine_setup(
            workflow_api.SetupComparisonRequest(
                maquina_codigo="M1",
                nombre_archivo="new.stp",
                contenido=new_content,
            )
        )
        self.assertEqual(comparison["added_or_changed"][0]["die_action"], "replace")
        self.assertEqual(len(comparison["removed"]), 1)

        with self.assertRaises(HTTPException) as error:
            workflow_api.generate_kit_qr_token(kit["id"])
        self.assertEqual(error.exception.status_code, 409)

        components = {
            (item["tn"], item["category"]): item
            for item in kit["components"]
        }
        for key, physical_id in (
            (("100", "matriz"), "D100N"),
            (("300", "punzon"), "P300"),
            (("300", "matriz"), "D300"),
        ):
            workflow_api.select_kit_tool(
                kit["id"],
                components[key]["component_id"],
                workflow_api.SurtidoToolSelect(
                    herramienta_id=physical_id,
                    realizado_por="operator",
                ),
            )

        qr = workflow_api.generate_kit_qr_token(kit["id"])
        for wrong_scan, code in (("", 422), ("T-999", 409)):
            with self.assertRaises(HTTPException) as bad_machine:
                workflow_api.confirm_kit_delivery(workflow_api.DeliveryScan(
                    scan_value=qr["scan_value"], machine_scan=wrong_scan, realizado_por="operator",
                ))
            self.assertEqual(bad_machine.exception.status_code, code)
        result = workflow_api.confirm_kit_delivery(workflow_api.DeliveryScan(
            scan_value=qr["scan_value"],
            machine_scan="TOOLTRACK-MACHINE:M1",
            realizado_por="operator",
        ))
        self.assertEqual(result["pending_pickups_created"], 3)
        self.assertEqual(result["active_drawing"], "NEW")

        session = self.session_factory()
        try:
            state = session.query(EstadoMaquina).filter_by(maquina_codigo="M1").one()
            active_setup = session.query(Setup).filter_by(id=state.setup_activo_id).one()
            self.assertEqual(active_setup.drawing_name, "NEW")
            self.assertEqual(
                session.query(PendienteRecoger).filter_by(estado="pending").count(), 3
            )
            self.assertEqual(
                session.query(ReservaHerramienta).count(), 0
            )
            self.assertEqual(
                session.query(RequisitoSetup).filter_by(
                    setup_id=active_setup.id
                ).count(),
                2,
            )
            self.assertEqual(
                session.query(MovimientoHerramienta).filter_by(
                    tipo="cart_to_machine_delivery"
                ).count(),
                3,
            )
        finally:
            session.close()

        pending = workflow_api.list_pending_pickups()
        pending_by_tool = {row["tool_id"]: row for row in pending}
        self.assertEqual(set(pending_by_tool), {"D100", "P200", "D200"})
        self.assertTrue(all(not row["overdue"] for row in pending_by_tool.values()))
        session = self.session_factory()
        try:
            old_pending = session.query(PendienteRecoger).filter_by(
                id=pending_by_tool["D100"]["pending_id"]
            ).one()
            old_pending.creado_en = workflow_api.utcnow() - timedelta(days=3)
            session.commit()
        finally:
            session.close()
        overdue = {
            row["tool_id"]: row["overdue"]
            for row in workflow_api.list_pending_pickups()
        }
        self.assertTrue(overdue["D100"])
        workflow_api.set_machine_standard("M1", workflow_api.LocationStandard(estandar=True))
        standard_rows = {row["tool_id"]: row for row in workflow_api.list_pending_pickups()}
        self.assertTrue(standard_rows["D100"]["standard_machine"])
        self.assertFalse(standard_rows["D100"]["overdue"])
        workflow_api.set_machine_standard("M1", workflow_api.LocationStandard(estandar=False))
        self.assertTrue(
            {row["tool_id"]: row for row in workflow_api.list_pending_pickups()}["D100"]["overdue"]
        )
        workflow_api.move_pending_pickup(
            pending_by_tool["D100"]["pending_id"],
            workflow_api.PendingPickupMove(destino_codigo="TG1", realizado_por="operator"),
        )
        with self.assertRaises(HTTPException) as occupied:
            workflow_api.move_pending_pickup(
                pending_by_tool["P200"]["pending_id"],
                workflow_api.PendingPickupMove(destino_codigo="TG1", realizado_por="operator"),
            )
        self.assertEqual(occupied.exception.status_code, 409)

        movable = workflow_api.list_movable_tools()
        self.assertEqual([row["tool_id"] for row in movable], ["D100"])
        with self.assertRaises(HTTPException) as needs_result:
            workflow_api.move_available_tool(
                "D100",
                workflow_api.PendingPickupMove(destino_codigo="R2", realizado_por="operator"),
            )
        self.assertEqual(needs_result.exception.status_code, 409)
        import tool_tracking_api as tracking
        tracking.store_tool_in_rack(
            "R2", tracking.RackStore(tool_id="D100", realizado_por="operator", resultado="disponible")
        )
        session = self.session_factory()
        try:
            self.assertEqual(session.query(Herramienta).filter_by(id="D100").one().ubicacion, "R2")
            self.assertEqual(
                session.query(PendienteRecoger).filter_by(
                    herramienta_id="D100", estado="collected"
                ).count(),
                1,
            )
        finally:
            session.close()

    def test_default_machine_locations_are_seeded_idempotently(self):
        inserted = workflow_api.seed_default_locations()
        self.assertEqual(inserted, 20)
        self.assertEqual(workflow_api.seed_default_locations(), 0)
        session = self.session_factory()
        try:
            machines = session.query(workflow_api.Ubicacion).filter(
                workflow_api.Ubicacion.codigo.in_(workflow_api.DEFAULT_MACHINE_CODES)
            ).all()
            carts = session.query(workflow_api.Ubicacion).filter(
                workflow_api.Ubicacion.codigo.in_(workflow_api.DEFAULT_CART_CODES)
            ).all()
            self.assertEqual(len(machines), 17)
            self.assertEqual(len(carts), 3)
            self.assertTrue(all(location.tipo == "machine" for location in machines))
            self.assertTrue(all(location.activa for location in machines))
            self.assertTrue(all(location.tipo == "cart" for location in carts))
            self.assertTrue(all(location.activa for location in carts))
        finally:
            session.close()

    def test_two_setup_files_compare_without_machine_baseline_or_physical_ids(self):
        previous_content = setup_text(
            "OLD-DRAWING",
            "0.075",
            [("201", "100", "0"), ("202", "200", "90")],
        )
        new_content = setup_text(
            "NEW-DRAWING",
            "0.119",
            [("301", "100", "0"), ("303", "300", "45")],
        )

        result = workflow_api.compare_setup_files(
            workflow_api.SetupFilesComparisonRequest(
                setup_anterior_nombre="old.stp",
                setup_anterior_contenido=previous_content,
                setup_nuevo_nombre="new.stp",
                setup_nuevo_contenido=new_content,
            )
        )

        self.assertEqual(result["current_setup"]["drawing_name"], "OLD-DRAWING")
        self.assertEqual(result["new_setup"]["drawing_name"], "NEW-DRAWING")
        self.assertTrue(result["thickness_changed"])
        self.assertEqual(
            {(row["tn"], row["angle"]) for row in result["unchanged"]},
            {("100", "0")},
        )
        self.assertEqual(
            (result["unchanged"][0]["punch_action"], result["unchanged"][0]["die_action"]),
            ("keep", "verify_clearance"),
        )
        self.assertEqual({row["tn"] for row in result["added"]}, {"300"})
        self.assertEqual({row["tn"] for row in result["removed"]}, {"200"})

        session = self.session_factory()
        try:
            self.assertEqual(session.query(EstadoMaquina).count(), 0)
            self.assertEqual(session.query(Setup).count(), 0)
        finally:
            session.close()

    def test_tool_tracking_locate_and_machine_returns(self):
        import tool_tracking_api as tracking

        self._baseline()
        tools = tracking.list_machine_tools("M1")
        self.assertEqual(tools["drawing_name"], "CURRENT")
        self.assertEqual(len(tools["tools"]), 4)

        found = tracking.locate_tools(q="P100")
        self.assertEqual(found[0]["drawing_name"], "CURRENT")
        self.assertIn("M1", found[0]["status"])
        self.assertEqual(len(tracking.locate_tools(q="100")), 2)

        with self.assertRaises(HTTPException) as error:
            tracking.return_machine_tools("M1", tracking.MachineReturn(
                tool_ids=["P100", "D100"], destino_codigo="TG1", realizado_por="op"
            ))
        self.assertEqual(error.exception.status_code, 409)

        result = tracking.return_machine_tools("M1", tracking.MachineReturn(
            tool_ids=["P100"], motivo="afilado", destino_codigo="TG1", realizado_por="op"
        ))
        self.assertEqual(result["remaining"], 3)
        self.assertIn("TG1", tracking.locate_tools(q="P100")[0]["status"])

        result = tracking.return_machine_tools("M1", tracking.MachineReturn(
            tool_ids=["D100"], motivo="dano", destino_codigo="MT1", realizado_por="op"
        ))
        self.assertEqual(result["remaining"], 2)

        with self.assertRaises(HTTPException):
            tracking.return_machine_tools("M1", tracking.MachineReturn(
                tool_ids=["P100"], destino_codigo="R1", realizado_por="op"
            ))

        result = tracking.return_machine_tools("M1", tracking.MachineReturn(
            todas=True, destino_codigo="R2", realizado_por="op"
        ))
        self.assertEqual(sorted(result["returned"]), ["D200", "P200"])
        self.assertEqual(result["remaining"], 0)
        self.assertEqual(tracking.list_machine_tools("M1")["tools"], [])

    def test_manufacturing_workspace_reports_setup_coverage_and_pending_pickups(self):
        import tool_tracking_api as tracking

        self._baseline()
        session = self.session_factory()
        try:
            session.query(Herramienta).filter_by(id="P100").one().ubicacion = "R1"
            session.query(Herramienta).filter_by(id="P200").one().tn = "999"
            session.query(RequisitoSetup).filter_by(tn="200").one().die_tool_id = None
            session.add(Maquina(codigo="M1", tipo_maquina="4 Index", edificio="B1"))
            session.add(ProgramaParte(
                numero_parte="CURRENT",
                tipo_maquina="4I",
                familia="Family A",
                tipo_programa="NPI",
                revision=2,
                nombre_archivo="current.stp",
                drawing_name="CURRENT",
                material="Steel",
                thickness="0.075",
                contenido="program",
                contenido_hash="hash",
                requisitos_json=json.dumps([{"station": "201", "tn": "100", "angle": "0"}]),
                actualizado_por="engineer",
                actualizado_en=workflow_api.utcnow(),
                piezas_blank=2,
                cycle_time_s=30,
                cycle_con_lead_s=210,
                uph_teorico=34.3,
            ))
            session.commit()
        finally:
            session.close()

        workspace = tracking.manufacturing_machine_workspace("M1")
        self.assertEqual(workspace["part_number"], "CURRENT")
        self.assertEqual(workspace["building"], "B1")
        self.assertEqual(workspace["coverage"], {
            "installed": 1, "required": 4, "complete": False,
        })
        first_station = next(row for row in workspace["stations"] if row["station"] == "201")
        self.assertEqual(
            {row["category"]: row["state"] for row in first_station["components"]},
            {"punzon": "elsewhere", "matriz": "installed"},
        )
        second_station = next(row for row in workspace["stations"] if row["station"] == "202")
        self.assertEqual(
            {row["category"]: row["state"] for row in second_station["components"]},
            {"punzon": "incompatible", "matriz": "missing"},
        )
        self.assertTrue(first_station["components"][1]["clearance_valid"])
        self.assertEqual(workspace["program"]["drawing_name"], "CURRENT")
        self.assertEqual(workspace["program"]["material"], "Steel")
        self.assertEqual(workspace["program"]["updated_by"], "engineer")
        self.assertEqual(workspace["program"]["requirements"], [
            {"station": "201", "tn": "100", "angle": "0"},
        ])

    def test_manufacturing_workspace_returns_empty_context_without_active_setup(self):
        import tool_tracking_api as tracking

        workspace = tracking.manufacturing_machine_workspace("M1")
        self.assertIsNone(workspace["part_number"])
        self.assertIsNone(workspace["setup"])
        self.assertEqual(workspace["coverage"], {
            "installed": 0, "required": 0, "complete": False,
        })
        self.assertEqual(workspace["stations"], [])

    def test_manufacturing_workspace_reports_complete_compatible_coverage(self):
        import tool_tracking_api as tracking

        self._baseline()
        workspace = tracking.manufacturing_machine_workspace("M1")
        self.assertEqual(workspace["coverage"], {
            "installed": 4, "required": 4, "complete": True,
        })

    def test_manufacturing_workspace_shows_only_active_machine_kit_status(self):
        import tool_tracking_api as tracking

        self._baseline()
        session = self.session_factory()
        try:
            setup_id = session.query(EstadoMaquina).filter_by(maquina_codigo="M1").one().setup_activo_id
            now = workflow_api.utcnow()
            session.add(Surtido(
                id="KIT-M1",
                maquina_codigo="M1",
                carro_codigo="C1",
                setup_anterior_id=setup_id,
                setup_nuevo_id=setup_id,
                estado="ready",
                creado_por="operator",
                creado_en=now,
                actualizado_en=now,
            ))
            session.commit()
        finally:
            session.close()

        workspace = tracking.manufacturing_machine_workspace("M1")
        self.assertEqual(workspace["active_kit"]["id"], "KIT-M1")
        self.assertEqual(workspace["active_kit"]["state"], "ready")

    def test_manufacturing_workspace_flags_matrix_outside_required_clearance(self):
        import tool_tracking_api as tracking

        self._baseline()
        session = self.session_factory()
        try:
            session.query(Herramienta).filter_by(id="D100").one().clearance = "0.020"
            session.commit()
        finally:
            session.close()

        workspace = tracking.manufacturing_machine_workspace("M1")
        first_station = next(row for row in workspace["stations"] if row["station"] == "201")
        die = next(row for row in first_station["components"] if row["category"] == "matriz")
        self.assertEqual(die["state"], "incompatible")
        self.assertFalse(die["clearance_valid"])
        self.assertEqual(workspace["coverage"]["installed"], 3)

    def test_store_in_rack_requires_the_tools_own_rack(self):
        import tool_tracking_api as tracking

        self._baseline()
        session = self.session_factory()
        try:
            for tool_id in ("P100", "D100"):
                session.add(MovimientoHerramienta(
                    herramienta_id=tool_id, origen_codigo="R1", destino_codigo="M1",
                    tipo="rack_to_cart_for_kit", realizado_por="op",
                    creado_en=workflow_api.utcnow(),
                ))
            session.commit()
        finally:
            session.close()

        with self.assertRaises(HTTPException) as error:
            tracking.store_tool_in_rack("R2", tracking.RackStore(tool_id="P100", realizado_por="op"))
        self.assertEqual(error.exception.status_code, 409)
        self.assertIn("R1", error.exception.detail)
        self.assertEqual(len(tracking.list_machine_tools("M1")["tools"]), 4)

        result = tracking.store_tool_in_rack("R1", tracking.RackStore(tool_id="P100", realizado_por="op"))
        self.assertEqual(result["kind"], "machine_return")
        self.assertEqual(len(tracking.list_machine_tools("M1")["tools"]), 3)
        self.assertEqual(tracking.locate_tools(q="P100")[0]["location"], "R1")

        with self.assertRaises(HTTPException):
            tracking.store_tool_in_rack("R1", tracking.RackStore(tool_id="P100", realizado_por="op"))
        with self.assertRaises(HTTPException):
            tracking.return_machine_tools("M1", tracking.MachineReturn(
                tool_ids=["D100"], destino_codigo="R2", realizado_por="op"
            ))

    def test_tool_status_blocks_kits_until_inspected_result_is_recorded(self):
        import tool_tracking_api as tracking

        self._baseline()
        tracking.return_machine_tools("M1", tracking.MachineReturn(
            tool_ids=["P100"], motivo="afilado", destino_codigo="TG1", realizado_por="op"
        ))
        self.assertEqual(tracking.locate_tools(q="P100")[0]["estado"], "afilado")
        with self.assertRaises(HTTPException) as no_result:
            tracking.store_tool_in_rack("R1", tracking.RackStore(tool_id="P100", realizado_por="op"))
        self.assertEqual(no_result.exception.status_code, 409)
        with self.assertRaises(HTTPException):
            workflow_api.move_available_tool(
                "P100", workflow_api.PendingPickupMove(destino_codigo="R1", realizado_por="op")
            )

        stored = tracking.store_tool_in_rack(
            "R1", tracking.RackStore(tool_id="P100", realizado_por="op", resultado="disponible")
        )
        self.assertEqual(stored["to"], "R1")
        self.assertEqual(tracking.locate_tools(q="P100")[0]["estado"], "disponible")

        tracking.change_tool_status("P100", tracking.ToolStatusChange(estado="danado", realizado_por="op"))
        with self.assertRaises(HTTPException):
            session = self.session_factory()
            try:
                tool = session.query(Herramienta).filter_by(id="P100").one()
                workflow_api._require_available(tool)
            finally:
                session.close()
        tracking.change_tool_status("P100", tracking.ToolStatusChange(estado="baja", realizado_por="op"))
        with self.assertRaises(HTTPException):
            tracking.change_tool_status("P100", tracking.ToolStatusChange(estado="disponible", realizado_por="op"))

        tracking.return_machine_tools("M1", tracking.MachineReturn(
            tool_ids=["D100"], motivo="dano", destino_codigo="MT1", realizado_por="op"
        ))
        result = tracking.store_tool_in_rack(
            "R1", tracking.RackStore(tool_id="D100", realizado_por="op", resultado="baja")
        )
        self.assertEqual(result["kind"], "baja")
        self.assertEqual(tracking.locate_tools(q="D100")[0]["location"], "MT1")

    def test_guided_current_new_comparison_starts_and_delivers_kit_without_baseline_ids(self):
        current_content = setup_text(
            "CURRENT-PART",
            "0.075",
            [("201", "100", "0")],
        )
        new_content = setup_text(
            "NEW-PART",
            "0.075",
            [("201", "100", "0"), ("303", "300", "45")],
        )
        self._create_tool("P300", "300", "punzon")
        self._create_tool("D300", "300", "matriz", "0.016")
        self._create_tool("P100", "100", "punzon")
        self._create_tool("D100", "100", "matriz", "0.016")

        comparison = workflow_api.compare_setup_files(
            workflow_api.SetupFilesComparisonRequest(
                setup_anterior_nombre="CURRENT-PART.stp",
                setup_anterior_contenido=current_content,
                setup_nuevo_nombre="NEW-PART.stp",
                setup_nuevo_contenido=new_content,
            )
        )

        self.assertEqual(comparison["current_setup"]["drawing_name"], "CURRENT-PART")
        self.assertEqual(comparison["new_setup"]["drawing_name"], "NEW-PART")
        self.assertEqual({row["tn"] for row in comparison["added"]}, {"300"})
        self.assertEqual(comparison["removed"], [])

        kit = workflow_api.create_kit(workflow_api.SurtidoCreate(
            maquina_codigo="M1",
            carro_codigo="C1",
            nombre_archivo="NEW-PART.stp",
            contenido=new_content,
            creado_por="operator",
            setup_anterior_nombre="CURRENT-PART.stp",
            setup_anterior_contenido=current_content,
        ))

        self.assertEqual(kit["state"], "preparing")
        self.assertTrue(all(
            component["action"] == "new"
            and component["state"] == "missing"
            and component["tool_id"] is None
            for component in kit["components"]
            if component["tn"] == "100"
        ))
        with self.assertRaises(HTTPException) as blocked:
            workflow_api.generate_kit_qr_token(kit["id"])
        self.assertEqual(blocked.exception.status_code, 409)
        added_components = [
            component for component in kit["components"]
            if component["tn"] == "300"
        ]
        self.assertEqual(len(added_components), 2)
        self.assertTrue(all(component["state"] == "missing" for component in added_components))
        session = self.session_factory()
        try:
            machine_state = session.query(EstadoMaquina).filter_by(
                maquina_codigo="M1"
            ).one()
            current_setup = session.query(Setup).filter_by(
                id=machine_state.setup_activo_id
            ).one()
            self.assertEqual(current_setup.drawing_name, "CURRENT-PART")
            self.assertEqual(current_setup.estado, "active")
        finally:
            session.close()

        tool_ids = {
            ("300", "punzon"): "P300", ("300", "matriz"): "D300",
            ("100", "punzon"): "P100", ("100", "matriz"): "D100",
        }
        for component in kit["components"]:
            workflow_api.select_kit_tool(
                kit["id"],
                component["component_id"],
                workflow_api.SurtidoToolSelect(
                    herramienta_id=tool_ids[(component["tn"], component["category"])],
                    realizado_por="operator",
                ),
            )
        qr = workflow_api.generate_kit_qr_token(kit["id"])
        delivered = workflow_api.confirm_kit_delivery(workflow_api.DeliveryScan(
            scan_value=qr["scan_value"],
            machine_scan="M1",
            realizado_por="operator",
        ))
        self.assertEqual(delivered["machine"], "M1")
        self.assertEqual(delivered["delivered_tools"], 4)

    def test_full_cycle_two_consecutive_kits_with_return_sharpening_and_reuse(self):
        import tool_tracking_api as tracking

        new_content, kit = self._create_new_setup_kit()
        for scan in ("P300", "D300", "D100N"):
            workflow_api.scan_kit_tool(
                kit["id"],
                workflow_api.SurtidoToolSelect(herramienta_id=scan, realizado_por="op"),
            )
        qr = workflow_api.generate_kit_qr_token(kit["id"])
        workflow_api.confirm_kit_delivery(workflow_api.DeliveryScan(
            scan_value=qr["scan_value"], machine_scan="M1", realizado_por="op",
        ))
        mounted = {row["tool_id"] for row in tracking.list_machine_tools("M1")["tools"]}
        self.assertEqual(mounted, {"P100", "D100N", "P300", "D300"})
        for row in tracking.locate_tools(q="300"):
            self.assertEqual(row["location"], "M1")

        # Replaced and removed tools go back to rack from pending pickups.
        for pending in workflow_api.list_pending_pickups():
            tracking.store_tool_in_rack(
                "R1", tracking.RackStore(tool_id=pending["tool_id"], realizado_por="op")
            )
        self.assertEqual(workflow_api.list_pending_pickups(), [])

        # A tool leaves for sharpening, can't be kitted, then returns ready.
        tracking.return_machine_tools("M1", tracking.MachineReturn(
            tool_ids=["P300"], motivo="afilado", destino_codigo="TG1", realizado_por="op",
        ))
        self.assertEqual(tracking.locate_tools(q="P300")[0]["estado"], "afilado")
        tracking.store_tool_in_rack(
            "R1", tracking.RackStore(tool_id="P300", realizado_por="op", resultado="disponible")
        )
        self.assertEqual(tracking.locate_tools(q="P300")[0]["estado"], "disponible")

        # Second kit goes back to the first part number using returned tools.
        old_content = setup_text(
            "CURRENT", "0.075", [("201", "100", "0"), ("202", "200", "90")],
        )
        kit2 = workflow_api.create_kit(workflow_api.SurtidoCreate(
            maquina_codigo="M1",
            carro_codigo="C1",
            nombre_archivo="current.stp",
            contenido=old_content,
            creado_por="op",
        ))
        missing = [c for c in kit2["components"] if c["state"] != "ready"]
        self.assertTrue(missing)
        with self.assertRaises(HTTPException):
            workflow_api.generate_kit_qr_token(kit2["id"])
        for tool_id in ("P200", "D200", "D100"):
            try:
                workflow_api.scan_kit_tool(
                    kit2["id"],
                    workflow_api.SurtidoToolSelect(herramienta_id=tool_id, realizado_por="op"),
                )
            except HTTPException as error:
                self.assertEqual(error.status_code, 409, tool_id)
        qr2 = workflow_api.generate_kit_qr_token(kit2["id"])
        result = workflow_api.confirm_kit_delivery(workflow_api.DeliveryScan(
            scan_value=qr2["scan_value"], machine_scan="M1", realizado_por="op",
        ))
        self.assertEqual(result["active_drawing"], "CURRENT")
        final = {row["tool_id"] for row in tracking.list_machine_tools("M1")["tools"]}
        self.assertEqual(len(final), 4)

    def test_part_programs_format_family_replace_with_reason_and_log(self):
        import programs_api

        content = setup_text("TYEH-1193217_01-SW", "0.119", [("201", "100", "0"), ("202", "200", "90")])
        part = "TYEH-1193217_01-SW"
        with self.assertRaises(HTTPException):
            programs_api.save_program("4I", "ABC123", programs_api.ProgramSave(
                tipo_programa="NPI", contenido=content, usuario="prog1",
            ))
        with self.assertRaises(HTTPException):
            programs_api.save_program("4I", part, programs_api.ProgramSave(
                familia="NOEXISTE", tipo_programa="NPI", contenido=content, usuario="prog1",
            ))
        with self.assertRaises(HTTPException):
            programs_api.create_family(programs_api.FamilyCreate(nombre="Familia X", cliente="NOCLIENTE"))
        programs_api.create_client(programs_api.ClientCreate(nombre="Ciena"))
        with self.assertRaises(HTTPException):
            programs_api.create_client(programs_api.ClientCreate(nombre="ciena"))
        programs_api.create_family(programs_api.FamilyCreate(nombre="Familia X", cliente="ciena"))
        with self.assertRaises(HTTPException):
            programs_api.create_family(programs_api.FamilyCreate(nombre="familia x", cliente="ciena"))
        with self.assertRaises(HTTPException):
            programs_api.save_program("4I", part, programs_api.ProgramSave(
                tipo_programa="NPI", contenido=content, usuario="prog1",
            ))
        created = programs_api.save_program("4I", part, programs_api.ProgramSave(
            familia="familia x", tipo_programa="npi", contenido=content, usuario="prog1",
        ))
        self.assertEqual(created["revision"], 1)
        self.assertEqual(created["familia"], "FAMILIA X")
        self.assertEqual(created["cliente"], "CIENA")
        self.assertEqual(programs_api.list_families(cliente="ciena")[0]["cliente"], "CIENA")

        changed = setup_text("TYEH-1193217_01-SW", "0.119", [("201", "100", "0"), ("203", "300", "0")])
        with self.assertRaises(HTTPException) as no_reason:
            programs_api.save_program("4I", part, programs_api.ProgramSave(
                familia="FAMILIA X", tipo_programa="NPI", contenido=changed, usuario="prog2",
            ))
        self.assertEqual(no_reason.exception.status_code, 422)
        updated = programs_api.save_program("4I", part, programs_api.ProgramSave(
            familia="FAMILIA X", tipo_programa="SUSTAINING", contenido=changed, usuario="prog2", motivo="Cambio de herramienta",
        ))
        self.assertEqual(updated["revision"], 2)

        # the same part number is independent per machine type
        other = programs_api.save_program("2I", part, programs_api.ProgramSave(
            familia="FAMILIA X", tipo_programa="NPI", contenido=content, usuario="prog1",
        ))
        self.assertEqual(other["revision"], 1)
        self.assertEqual(len(programs_api.list_programs(tipo_maquina="4I")), 1)
        self.assertEqual(programs_api.get_program("4I", part)["contenido"], changed)

        log = programs_api.list_program_log(numero_parte="1193217", tipo_maquina="4I")
        self.assertEqual([row["accion"] for row in log], ["actualizado", "alta"])
        self.assertEqual(log[0]["motivo"], "Cambio de herramienta")
        self.assertIn("202", log[0]["detalle"])
        self.assertIn("203", log[0]["detalle"])

        preview = programs_api.preview_program("4I", part, programs_api.ProgramPreview(contenido=content))
        self.assertTrue(preview["exists"])
        self.assertEqual((preview["revision"], preview["next_revision"]), (2, 3))
        self.assertIn("203", preview["diff"])
        self.assertEqual(len(preview["requisitos"]), 2)
        fresh = programs_api.preview_program("45STA", part, programs_api.ProgramPreview(contenido=content))
        self.assertFalse(fresh["exists"])
        self.assertEqual(fresh["next_revision"], 1)
        self.assertIsNone(fresh["diff"])
        with self.assertRaises(HTTPException):
            programs_api.preview_program("4I", part, programs_api.ProgramPreview(contenido="not a setup file"))
        self.assertEqual(programs_api.get_program("4I", part)["revision"], 2)

    def test_machine_transfer_moves_part_and_tools_and_lists_running(self):
        import machines_api

        self._baseline()
        self._create_location("M2", "machine")
        for code in ("M1", "M2"):
            machines_api.save_machine_profile(code, machines_api.MachineProfile(tipo_maquina="4 Index"))
        for tool_id in ("P100", "D100", "P200", "D200"):
            session = self.session_factory()
            session.query(Herramienta).filter(Herramienta.id == tool_id).update({"ubicacion": "M1"})
            session.commit()
            session.close()
        running = {row["machine"]: row for row in workflow_api.machines_running()}
        self.assertEqual(running["M1"]["tipo"], "4I")
        self.assertEqual(running["M1"]["numero_parte"], "current")
        self.assertIsNone(running["M2"]["numero_parte"])

        with self.assertRaises(HTTPException) as no_reason:
            workflow_api.transfer_running_part(workflow_api.MachineTransfer(
                origen="M1", destino="M2", motivo=" ", realizado_por="op"))
        self.assertEqual(no_reason.exception.status_code, 422)

        result = workflow_api.transfer_running_part(workflow_api.MachineTransfer(
            origen="M1", destino="M2", motivo="Falla de M1", realizado_por="op"))
        self.assertEqual(result["herramientas_movidas"], 4)
        running = {row["machine"]: row for row in workflow_api.machines_running()}
        self.assertIsNone(running["M1"]["numero_parte"])
        self.assertEqual(running["M2"]["numero_parte"], "current")
        session = self.session_factory()
        try:
            self.assertEqual(session.query(Herramienta).filter(Herramienta.id == "P100").one().ubicacion, "M2")
        finally:
            session.close()
        with self.assertRaises(HTTPException) as empty_origin:
            workflow_api.transfer_running_part(workflow_api.MachineTransfer(
                origen="M1", destino="M2", motivo="x", realizado_por="op"))
        self.assertEqual(empty_origin.exception.status_code, 409)

    def test_machine_profile_stations_and_setup_warnings(self):
        import machines_api

        machines_api.save_machine_profile("M1", machines_api.MachineProfile(
            tipo_maquina="Torreta", linea="L1", capacidad="30 ton", espesor_max="0.100",
        ))
        for number, size, state in (("201", "A", "disponible"), ("202", "B", "danada")):
            machines_api.add_station("M1", machines_api.StationCreate(
                numero=number, tamano=size, estado=state, nota="golpeada" if state == "danada" else None,
            ))
        with self.assertRaises(HTTPException) as duplicate:
            machines_api.add_station("M1", machines_api.StationCreate(numero="201", tamano="A"))
        self.assertEqual(duplicate.exception.status_code, 409)
        with self.assertRaises(HTTPException):
            machines_api.add_station("M1", machines_api.StationCreate(numero="203", estado="rota"))

        profile = machines_api.get_machine_profile("M1")
        self.assertEqual(profile["estaciones_total"], 2)
        self.assertEqual(profile["estaciones_danadas"], 1)

        self._baseline()
        content = setup_text(
            "NEWW", "0.119", [("201", "100", "0"), ("202", "200", "90"), ("350", "300", "0")],
        )
        self._create_tool("PB", "100", "punzon")
        session = self.session_factory()
        try:
            session.query(Herramienta).filter_by(id="PB").one().size = "C"
            session.commit()
        finally:
            session.close()
        comparison = workflow_api.compare_with_machine_setup(workflow_api.SetupComparisonRequest(
            maquina_codigo="M1", nombre_archivo="n.stp", contenido=content,
        ))
        text = " | ".join(comparison["warnings"])
        self.assertIn("espesor", text)
        self.assertIn("350", text)
        self.assertIn("dañada", text)

        machines_api.update_station("M1", "202", machines_api.StationUpdate(estado="disponible"))
        self.assertEqual(machines_api.get_machine_profile("M1")["estaciones_danadas"], 0)
        comparison = workflow_api.compare_with_machine_setup(workflow_api.SetupComparisonRequest(
            maquina_codigo="M1", nombre_archivo="n.stp", contenido=content,
        ))
        self.assertIn("tamaño B", " | ".join(comparison["warnings"]))

        machines_api.save_machine_profile("M1", machines_api.MachineProfile(
            edificio="B1", espesor_min="0.200", espesor_max="0.300",
        ))
        self.assertEqual(machines_api.get_machine_profile("M1")["edificio"], "B1")
        comparison = workflow_api.compare_with_machine_setup(workflow_api.SetupComparisonRequest(
            maquina_codigo="M1", nombre_archivo="n.stp", contenido=content,
        ))
        self.assertIn("mínimo", " | ".join(comparison["warnings"]))
        with self.assertRaises(HTTPException):
            machines_api.save_machine_profile("M1", machines_api.MachineProfile(
                espesor_min="0.5", espesor_max="0.3",
            ))
        with self.assertRaises(HTTPException) as referenced_station:
            machines_api.delete_station("M1", "202")
        self.assertEqual(referenced_station.exception.status_code, 409)
        machines_api.add_station("M1", machines_api.StationCreate(numero="999"))
        machines_api.delete_station("M1", "999")
        self.assertEqual(machines_api.get_machine_profile("M1")["estaciones_total"], 2)

    def test_scanning_physical_ids_matches_tn_and_category_and_assigns_tools(self):
        _content, kit = self._create_new_setup_kit()

        punch_scan = workflow_api.scan_kit_tool(
            kit["id"],
            workflow_api.SurtidoToolSelect(
                herramienta_id="P300",
                realizado_por="operator",
            ),
        )
        die_scan = workflow_api.scan_kit_tool(
            kit["id"],
            workflow_api.SurtidoToolSelect(
                herramienta_id="D300",
                realizado_por="operator",
            ),
        )

        assigned = {
            (item["tn"], item["category"]): (item["tool_id"], item["state"])
            for item in die_scan["components"]
            if item["tn"] == "300"
        }
        self.assertEqual(assigned[("300", "punzon")], ("P300", "ready"))
        self.assertEqual(assigned[("300", "matriz")], ("D300", "ready"))
        self.assertEqual(die_scan["state"], "preparing")
        self.assertEqual(len(punch_scan["components"]), len(die_scan["components"]))

    def test_scanning_tool_that_does_not_match_a_pending_component_changes_nothing(self):
        _content, kit = self._create_new_setup_kit()
        session = self.session_factory()
        try:
            movement_count = session.query(MovimientoHerramienta).count()
        finally:
            session.close()

        with self.assertRaises(HTTPException) as mismatch:
            workflow_api.scan_kit_tool(
                kit["id"],
                workflow_api.SurtidoToolSelect(
                    herramienta_id="P100",
                    realizado_por="operator",
                ),
            )
        self.assertEqual(mismatch.exception.status_code, 409)

        session = self.session_factory()
        try:
            self.assertEqual(session.query(MovimientoHerramienta).count(), movement_count)
            self.assertEqual(session.query(Herramienta).filter_by(id="P100").one().ubicacion, "M1")
            self.assertEqual(session.query(ReservaHerramienta).count(), 0)
        finally:
            session.close()

    def test_repeated_tn_scans_assign_to_next_pending_station(self):
        current_content = setup_text("CURRENT", "0.075", [("201", "100", "0")])
        new_content = setup_text(
            "NEW",
            "0.075",
            [("301", "300", "45"), ("302", "300", "90")],
        )
        self._create_tool("P300A", "300", "punzon")
        self._create_tool("P300B", "300", "punzon")
        kit = workflow_api.create_kit(workflow_api.SurtidoCreate(
            maquina_codigo="M1",
            carro_codigo="C1",
            nombre_archivo="NEW.stp",
            contenido=new_content,
            creado_por="operator",
            setup_anterior_nombre="CURRENT.stp",
            setup_anterior_contenido=current_content,
        ))

        first_scan = workflow_api.scan_kit_tool(
            kit["id"],
            workflow_api.SurtidoToolSelect(
                herramienta_id="P300B",
                realizado_por="operator",
            ),
        )
        second_scan = workflow_api.scan_kit_tool(
            kit["id"],
            workflow_api.SurtidoToolSelect(
                herramienta_id="P300A",
                realizado_por="operator",
            ),
        )

        assigned_stations = {
            item["tool_id"]: item["station"]
            for item in second_scan["components"]
            if item["tool_id"] in {"P300A", "P300B"}
        }
        self.assertEqual(assigned_stations, {"P300B": "301", "P300A": "302"})
        self.assertEqual(
            sum(item["tool_id"] == "P300B" for item in first_scan["components"]),
            1,
        )

    def test_pause_and_cancel_returns_reserved_tool_to_its_rack(self):
        _content, kit = self._create_new_setup_kit()
        die_component = next(
            item for item in kit["components"]
            if item["tn"] == "100" and item["category"] == "matriz"
        )
        workflow_api.select_kit_tool(
            kit["id"],
            die_component["component_id"],
            workflow_api.SurtidoToolSelect(
                herramienta_id="D100N",
                realizado_por="operator",
            ),
        )
        paused = workflow_api.pause_kit(
            kit["id"], workflow_api.SurtidoAction(realizado_por="operator")
        )
        self.assertEqual(paused["state"], "paused")
        resumed = workflow_api.resume_kit(
            kit["id"], workflow_api.SurtidoAction(realizado_por="operator")
        )
        self.assertEqual(resumed["state"], "preparing")
        for component in kit["components"]:
            if component["category"] == "punzon" and component["tn"] == "300":
                physical_id = "P300"
            elif component["category"] == "matriz" and component["tn"] == "300":
                physical_id = "D300"
            else:
                continue
            workflow_api.select_kit_tool(
                kit["id"],
                component["component_id"],
                workflow_api.SurtidoToolSelect(
                    herramienta_id=physical_id,
                    realizado_por="operator",
                ),
            )
        qr = workflow_api.generate_kit_qr_token(kit["id"])
        self.assertEqual(
            workflow_api.get_kit(kit["id"])["state"],
            "ready",
        )
        workflow_api.cancel_kit(
            kit["id"], workflow_api.SurtidoAction(realizado_por="operator")
        )
        with self.assertRaises(HTTPException) as invalidated_qr:
            workflow_api.confirm_kit_delivery(workflow_api.DeliveryScan(
                scan_value=qr["scan_value"],
                realizado_por="operator",
            ))
        self.assertEqual(invalidated_qr.exception.status_code, 404)
        session = self.session_factory()
        try:
            self.assertEqual(session.query(Herramienta).filter_by(id="D100N").one().ubicacion, "R1")
            self.assertEqual(session.query(ReservaHerramienta).count(), 0)
            self.assertEqual(
                session.query(MovimientoHerramienta).filter_by(
                    tipo="kit_cancelled_return_to_rack"
                ).count(),
                3,
            )
        finally:
            session.close()

    # ---------- service requests (Manufacturing -> Tool Crib) ----------
    def _as(self, username, role):
        token = auth.acting_as(username, role)
        self.addCleanup(auth.CURRENT_USER.reset, token)

    def _requirement_id(self, station):
        import tool_tracking_api as tracking

        workspace = tracking.manufacturing_machine_workspace("M1")
        return next(row["requirement_id"] for row in workspace["stations"] if row["station"] == station)

    def _ensure_user(self, username, role="PUNCHING_OPERATOR", shift="Turno 1", full_name=None, active=True):
        session = self.session_factory()
        try:
            session.merge(Usuario(
                username=username, password="x", role=role, activo=active,
                full_name=full_name or username.upper(), shift=shift, area="Punzonado",
            ))
            session.commit()
        finally:
            session.close()

    def _open_request(self, tipo="sharpening", station="201", category="punzon", tool_id="P100", note=None,
                      causa=None, momento=None, operador=None):
        import service_requests_api as service

        self._ensure_user("op1")
        self._as("op1", "PUNCHING_OPERATOR")
        if tipo == "damage":
            causa = causa or "collision"
            momento = momento or "production"
        return service.create_service_request(service.ServiceRequestCreate(
            machine="M1", requirement_id=self._requirement_id(station),
            category=category, tipo=tipo, tool_id=tool_id, nota=note,
            causa=causa, momento=momento, operador=operador,
        ))

    def _slot_state(self, station, category):
        import tool_tracking_api as tracking

        workspace = tracking.manufacturing_machine_workspace("M1")
        row = next(row for row in workspace["stations"] if row["station"] == station)
        return next(component for component in row["components"] if component["category"] == category)

    def test_service_request_full_cycle_replaces_tool_with_same_tn(self):
        import service_requests_api as service
        import tool_tracking_api as tracking

        self._baseline()
        self._create_tool("P100B", "100", "punzon")
        self._create_tool("P300", "300", "punzon")
        request = self._open_request()
        self.assertEqual(request["state"], "open")
        self.assertEqual(request["part_number"], "CURRENT")
        self.assertEqual(request["requested_by"], "op1")

        slot = self._slot_state("201", "punzon")
        self.assertEqual(slot["state"], "missing")
        self.assertEqual(slot["open_request"]["id"], request["id"])
        self.assertEqual(tracking.manufacturing_machine_workspace("M1")["coverage"]["installed"], 3)
        with self.assertRaises(HTTPException) as duplicate:
            self._open_request()
        self.assertEqual(duplicate.exception.status_code, 409)

        self._as("tc1", "TOOLCRIB_OPERATOR")
        attending = service.attend_service_request(request["id"])
        self.assertEqual(attending["state"], "attending")
        self.assertEqual(attending["attended_by"], "tc1")
        self._as("tc2", "TOOLCRIB_OPERATOR")
        with self.assertRaises(HTTPException) as taken:
            service.attend_service_request(request["id"])
        self.assertEqual(taken.exception.status_code, 409)
        with self.assertRaises(HTTPException) as other_user:
            service.collect_service_tool(request["id"], service.ServiceCollect(destino_codigo="TG1"))
        self.assertEqual(other_user.exception.status_code, 403)

        self._as("op1", "PUNCHING_OPERATOR")
        with self.assertRaises(HTTPException) as too_early:
            service.install_service_tool(request["id"], service.ServiceInstall(tool_id="P100B"))
        self.assertEqual(too_early.exception.status_code, 409)

        self._as("tc1", "TOOLCRIB_OPERATOR")
        collected = service.collect_service_tool(request["id"], service.ServiceCollect(destino_codigo="TG1"))
        self.assertEqual(collected["state"], "collected")
        self.assertEqual(collected["destination"], "TG1")
        session = self.session_factory()
        try:
            old = session.query(Herramienta).filter_by(id="P100").one()
            self.assertEqual((old.ubicacion, old.estado), ("TG1", "afilado"))
        finally:
            session.close()

        self._as("op1", "PUNCHING_OPERATOR")
        for wrong, status in (("P300", 409), ("P100", 409), ("NOPE", 404)):
            with self.assertRaises(HTTPException) as rejected:
                service.install_service_tool(request["id"], service.ServiceInstall(tool_id=wrong))
            self.assertEqual(rejected.exception.status_code, status, wrong)

        replaced = service.install_service_tool(request["id"], service.ServiceInstall(tool_id="P100B"))
        self.assertEqual(replaced["state"], "replaced")
        self.assertFalse(replaced["same_tool"])
        self.assertEqual(replaced["tool_status"], "afilado")
        self.assertEqual(self._slot_state("201", "punzon")["tool_id"], "P100B")
        self.assertEqual(tracking.manufacturing_machine_workspace("M1")["coverage"]["complete"], True)
        self.assertIsNone(self._slot_state("201", "punzon")["open_request"])

        self._as("tc1", "TOOLCRIB_OPERATOR")
        with self.assertRaises(HTTPException) as early_store:
            service.store_serviced_tool(request["id"], service.ServiceStore(rack_code="R1", tool_id="P100"))
        self.assertEqual(early_store.exception.status_code, 409)
        service.finish_tool_service(request["id"], service.ServiceFinish(rebaje=0.015))
        closed = service.store_serviced_tool(request["id"], service.ServiceStore(rack_code="R1", tool_id="P100"))
        self.assertEqual(closed["state"], "closed")
        session = self.session_factory()
        try:
            self.assertEqual(session.query(Herramienta).filter_by(id="P100B").one().ubicacion, "M1")
            old = session.query(Herramienta).filter_by(id="P100").one()
            self.assertEqual((old.ubicacion, old.estado), ("R1", "disponible"))
            types = [
                row.tipo for row in session.query(MovimientoHerramienta).filter_by(herramienta_id="P100").all()
            ]
            self.assertIn("service_requested", types)
            self.assertIn("service_collect_sharpening", types)
            self.assertIn("service_completed", types)
        finally:
            session.close()

    def test_sharpening_rebaje_over_the_limit_needs_a_justification_and_is_logged(self):
        import service_requests_api as service

        self._baseline()
        session = self.session_factory()
        try:
            session.query(Herramienta).filter_by(id="P100").one().life = "1.200"
            session.commit()
        finally:
            session.close()
        request = self._open_request()
        self._as("tc1", "TOOLCRIB_OPERATOR")
        with self.assertRaises(HTTPException) as too_early:
            service.finish_tool_service(request["id"], service.ServiceFinish(rebaje=0.01))
        self.assertEqual(too_early.exception.status_code, 409)
        service.attend_service_request(request["id"])
        service.collect_service_tool(request["id"], service.ServiceCollect(destino_codigo="TG1"))

        with self.assertRaises(HTTPException) as missing:
            service.finish_tool_service(request["id"], service.ServiceFinish())
        self.assertEqual(missing.exception.status_code, 422)
        with self.assertRaises(HTTPException) as negative:
            service.finish_tool_service(request["id"], service.ServiceFinish(rebaje=-0.001))
        self.assertEqual(negative.exception.status_code, 422)
        for text in (None, "short"):
            with self.assertRaises(HTTPException) as abnormal:
                service.finish_tool_service(request["id"], service.ServiceFinish(rebaje=0.021, justificacion=text))
            self.assertEqual(abnormal.exception.status_code, 422)
        self._as("eng1", "ENGINEERING")
        with self.assertRaises(HTTPException) as engineering:
            service.finish_tool_service(request["id"], service.ServiceFinish(rebaje=0.01))
        self.assertEqual(engineering.exception.status_code, 403)

        self._as("tc1", "TOOLCRIB_OPERATOR")
        done = service.finish_tool_service(request["id"], service.ServiceFinish(
            rebaje=0.025, justificacion="Chipped tip, had to remove extra material",
        ))
        self.assertTrue(done["service"]["abnormal"])
        self.assertEqual(done["tool_status"], "disponible")
        with self.assertRaises(HTTPException) as again:
            service.finish_tool_service(request["id"], service.ServiceFinish(rebaje=0.01))
        self.assertEqual(again.exception.status_code, 409)

        log = service.list_tool_service_log(tool_id="P100", limit=10)
        self.assertEqual(len(log), 1)
        self.assertEqual((log[0]["rebaje"], log[0]["life_before"], log[0]["life_after"]), (0.025, "1.200", "1.175"))
        self.assertEqual((log[0]["part_number"], log[0]["station"], log[0]["machine"]), ("CURRENT", "201", "M1"))
        self.assertTrue(log[0]["abnormal"])

    def test_sharpened_tool_returns_directly_to_the_machine_from_togu(self):
        import service_requests_api as service

        self._baseline()
        request = self._open_request()
        self._as("tc1", "TOOLCRIB_OPERATOR")
        service.attend_service_request(request["id"])
        service.collect_service_tool(request["id"], service.ServiceCollect(destino_codigo="TG1"))
        service.finish_tool_service(request["id"], service.ServiceFinish(rebaje=0.02))
        session = self.session_factory()
        try:
            tool = session.query(Herramienta).filter_by(id="P100").one()
            self.assertEqual((tool.ubicacion, tool.estado), ("TG1", "disponible"))
        finally:
            session.close()
        self._as("op1", "PUNCHING_OPERATOR")
        closed = service.install_service_tool(request["id"], service.ServiceInstall(tool_id="P100"))
        self.assertEqual(closed["state"], "closed")
        self.assertTrue(closed["same_tool"])
        self.assertEqual(self._slot_state("201", "punzon")["tool_id"], "P100")

    def test_retiring_a_tool_keeps_the_position_waiting_until_a_replacement_arrives(self):
        import service_requests_api as service

        self._baseline()
        self._create_tool("P100B", "100", "punzon")
        request = self._open_request(tipo="damage", note="Broken tip")
        self._as("tc1", "TOOLCRIB_OPERATOR")
        service.attend_service_request(request["id"])
        service.collect_service_tool(request["id"], service.ServiceCollect(destino_codigo="MT1"))
        with self.assertRaises(HTTPException) as no_reason:
            service.finish_tool_service(request["id"], service.ServiceFinish(resultado="retired"))
        self.assertEqual(no_reason.exception.status_code, 422)
        waiting = service.finish_tool_service(request["id"], service.ServiceFinish(resultado="retired", nota="Beyond repair"))
        self.assertEqual((waiting["state"], waiting["tool_status"]), ("collected", "baja"))
        self._as("op1", "PUNCHING_OPERATOR")
        with self.assertRaises(HTTPException) as retired_tool:
            service.install_service_tool(request["id"], service.ServiceInstall(tool_id="P100"))
        self.assertEqual(retired_tool.exception.status_code, 409)
        closed = service.install_service_tool(request["id"], service.ServiceInstall(tool_id="P100B"))
        self.assertEqual(closed["state"], "closed")

    def test_retiring_the_old_tool_after_a_replacement_closes_the_request(self):
        import service_requests_api as service

        self._baseline()
        self._create_tool("P100B", "100", "punzon")
        request = self._open_request(tipo="damage", note="Cracked")
        self._as("tc1", "TOOLCRIB_OPERATOR")
        service.attend_service_request(request["id"])
        service.collect_service_tool(request["id"], service.ServiceCollect(destino_codigo="MT1"))
        self._as("op1", "PUNCHING_OPERATOR")
        self.assertEqual(
            service.install_service_tool(request["id"], service.ServiceInstall(tool_id="P100B"))["state"], "replaced",
        )
        self._as("tc1", "TOOLCRIB_OPERATOR")
        closed = service.finish_tool_service(request["id"], service.ServiceFinish(resultado="retired", nota="Cracked, discarded"))
        self.assertEqual(closed["state"], "closed")
        self.assertEqual(closed["tool_status"], "baja")

    def test_service_request_can_install_the_same_tool_after_sharpening(self):
        import service_requests_api as service
        import tool_tracking_api as tracking

        self._baseline()
        request = self._open_request()
        self._as("tc1", "TOOLCRIB_OPERATOR")
        service.attend_service_request(request["id"])
        service.collect_service_tool(request["id"], service.ServiceCollect(destino_codigo="TG1"))
        self._as("op1", "PUNCHING_OPERATOR")
        with self.assertRaises(HTTPException):
            service.install_service_tool(request["id"], service.ServiceInstall(tool_id="P100"))

        tracking.store_tool_in_rack("R1", tracking.RackStore(
            tool_id="P100", realizado_por="tc1", resultado="disponible",
        ))
        closed = service.install_service_tool(request["id"], service.ServiceInstall(tool_id="P100"))
        self.assertTrue(closed["same_tool"])
        self.assertEqual(self._slot_state("201", "punzon")["tool_id"], "P100")

    def test_service_flow_accepts_scanned_labels_typed_ids_and_records_how_they_were_entered(self):
        import service_requests_api as service

        self._baseline()
        self._create_tool("M00001", "100", "punzon")
        request = self._open_request(tool_id="TOOLTRACK-TOOL:p100")
        self.assertEqual(request["tool_id"], "P100")
        self._as("tc1", "TOOLCRIB_OPERATOR")
        service.attend_service_request(request["id"])
        collected = service.collect_service_tool(
            request["id"], service.ServiceCollect(destino_codigo="TOOLTRACK-LOCATION:tg1\r\n"),
        )
        self.assertEqual(collected["destination"], "TG1")
        service.finish_tool_service(request["id"], service.ServiceFinish(rebaje=0.01))

        self._as("op1", "PUNCHING_OPERATOR")
        with self.assertRaises(HTTPException) as wrong_kind:
            service.install_service_tool(request["id"], service.ServiceInstall(tool_id="TOOLTRACK-LOCATION:R1", captura="scan"))
        self.assertEqual(wrong_kind.exception.status_code, 422)
        replaced = service.install_service_tool(
            request["id"], service.ServiceInstall(tool_id="TOOLTRACK-TOOL:m00001\r", captura="scan"),
        )
        self.assertEqual(replaced["replacement_tool_id"], "M00001")
        self.assertEqual(replaced["state"], "replaced")

        self._as("tc1", "TOOLCRIB_OPERATOR")
        with self.assertRaises(HTTPException) as other_tool:
            service.store_serviced_tool(request["id"], service.ServiceStore(rack_code="R1", tool_id="M00001"))
        self.assertEqual(other_tool.exception.status_code, 409)
        closed = service.store_serviced_tool(
            request["id"], service.ServiceStore(rack_code="tooltrack-location:r1", tool_id="p100", captura="manual"),
        )
        self.assertEqual(closed["state"], "closed")

        session = self.session_factory()
        try:
            def capture(tool_id, movement_type):
                return session.query(MovimientoHerramienta).filter_by(
                    herramienta_id=tool_id, tipo=movement_type
                ).one().captura

            self.assertEqual(capture("M00001", "service_install"), "scan")
            self.assertEqual(capture("P100", "togu_to_rack"), "manual")
            self.assertIsNone(capture("P100", "service_requested"))
        finally:
            session.close()
        movements = workflow_api.list_movements(tool_id="M00001", machine_code=None)
        self.assertEqual(movements[0]["capture"], "scan")

    def test_kit_scan_accepts_lowercase_and_prefixed_tool_codes(self):
        _content, kit = self._create_new_setup_kit()
        components = {(item["tn"], item["category"]): item for item in kit["components"]}
        for raw in ("tooltrack-tool:d100n", "P300\r\n"):
            result = workflow_api.scan_kit_tool(kit["id"], workflow_api.SurtidoToolSelect(
                herramienta_id=raw, realizado_por="operator", captura="scan",
            ))
            self.assertTrue(result)
        with self.assertRaises(HTTPException) as wrong_kind:
            workflow_api.scan_kit_tool(kit["id"], workflow_api.SurtidoToolSelect(
                herramienta_id="TOOLTRACK-LOCATION:R1", realizado_por="operator",
            ))
        self.assertEqual(wrong_kind.exception.status_code, 422)
        session = self.session_factory()
        try:
            self.assertEqual(
                session.query(MovimientoHerramienta).filter_by(herramienta_id="D100N", tipo="rack_to_cart_for_kit").one().captura,
                "scan",
            )
        finally:
            session.close()
        self.assertIn(("100", "matriz"), components)

    def test_damage_report_captures_who_why_and_when_with_the_context_already_known(self):
        import service_requests_api as service

        self._baseline()
        self._ensure_user("op1")
        self._ensure_user("op2", shift="Turno 2", full_name="Second Operator")
        self._ensure_user("gone", shift="Turno 3", active=False)
        self._ensure_user("tc1", role="TOOLCRIB_OPERATOR", shift="Turno 2")
        self._as("op1", "PUNCHING_OPERATOR")
        base = dict(machine="M1", requirement_id=self._requirement_id("201"), category="punzon",
                    tipo="damage", tool_id="P100", nota="Tip chipped on the first hit")
        for bad in (
            dict(causa=None, momento="production"),
            dict(causa="witchcraft", momento="production"),
            dict(causa="collision", momento="lunch"),
            dict(causa="collision", momento="production", operador="nobody"),
            dict(causa="collision", momento="production", operador="gone"),
        ):
            with self.assertRaises(HTTPException, msg=str(bad)) as error:
                service.create_service_request(service.ServiceRequestCreate(**base, **bad))
            self.assertEqual(error.exception.status_code, 422, bad)

        request = service.create_service_request(service.ServiceRequestCreate(
            **base, causa="collision", momento="setup", operador="op2",
        ))
        incident = request["incident"]
        self.assertEqual(
            (incident["operator"], incident["shift"], incident["cause"], incident["moment"]),
            ("op2", "Turno 2", "collision", "setup"),
        )
        self.assertEqual(
            (incident["machine"], incident["part_number"], incident["station"], incident["tn"], incident["tool_id"]),
            ("M1", "CURRENT", "201", "100", "P100"),
        )
        self.assertEqual((incident["origin"], incident["reported_as"], incident["description"]),
                         ("machine", "damage", "Tip chipped on the first hit"))
        self.assertEqual(incident["created_by"], "op1")

        # Without naming anyone, the operator involved is the person reporting.
        self._as("tc1", "TOOLCRIB_OPERATOR")
        service.attend_service_request(request["id"])
        service.collect_service_tool(request["id"], service.ServiceCollect(destino_codigo="MT1"))
        self._as("op1", "PUNCHING_OPERATOR")
        second = service.create_service_request(service.ServiceRequestCreate(
            machine="M1", requirement_id=self._requirement_id("202"), category="punzon", tipo="damage",
            tool_id="P200", nota="Worn out", causa="normal_wear", momento="production",
        ))
        self.assertEqual((second["incident"]["operator"], second["incident"]["shift"]), ("op1", "Turno 1"))
        listed = service.list_tool_incidents(tool_id="p100", limit=10)
        self.assertEqual([row["tool_id"] for row in listed], ["P100"])

    def test_toolcrib_records_the_finding_when_a_sharpening_needs_more(self):
        import service_requests_api as service

        self._baseline()
        self._ensure_user("op2", shift="Turno 2")
        self._ensure_user("tc1", role="TOOLCRIB_OPERATOR", shift="Turno 2")
        request = self._open_request()  # reported as sharpening
        self.assertIsNone(request["incident"])
        self._as("tc1", "TOOLCRIB_OPERATOR")
        payload = dict(operador="op2", causa="wrong_setup", momento="setup", hallazgo="Cracked shoulder, not just dull")
        with self.assertRaises(HTTPException) as too_early:
            service.record_tool_incident(request["id"], service.IncidentRecord(**payload))
        self.assertEqual(too_early.exception.status_code, 409)
        service.attend_service_request(request["id"])
        service.collect_service_tool(request["id"], service.ServiceCollect(destino_codigo="TG1"))

        for wrong in (dict(hallazgo="x"), dict(causa="nope"), dict(operador="ghost")):
            with self.assertRaises(HTTPException) as invalid:
                service.record_tool_incident(request["id"], service.IncidentRecord(**{**payload, **wrong}))
            self.assertEqual(invalid.exception.status_code, 422, wrong)
        self._as("eng1", "ENGINEERING")
        with self.assertRaises(HTTPException) as engineering:
            service.record_tool_incident(request["id"], service.IncidentRecord(**payload))
        self.assertEqual(engineering.exception.status_code, 403)

        self._as("tc1", "TOOLCRIB_OPERATOR")
        recorded = service.record_tool_incident(request["id"], service.IncidentRecord(**payload))["incident"]
        self.assertEqual((recorded["reported_as"], recorded["finding"], recorded["origin"]),
                         ("sharpening", "Cracked shoulder, not just dull", "machine"))
        self.assertEqual((recorded["operator"], recorded["shift"], recorded["cause"]), ("op2", "Turno 2", "wrong_setup"))
        self.assertEqual((recorded["machine"], recorded["part_number"], recorded["station"]), ("M1", "CURRENT", "201"))

        corrected = service.record_tool_incident(
            request["id"], service.IncidentRecord(**{**payload, "causa": "collision", "hallazgo": "Collision confirmed"}),
        )["incident"]
        self.assertEqual((corrected["id"], corrected["cause"], corrected["updated_by"]), (recorded["id"], "collision", "tc1"))
        self.assertEqual(len(service.list_tool_incidents(tool_id=None, limit=50)), 1)

    def test_operators_list_only_shows_active_manufacturing_users_to_allowed_roles(self):
        import service_requests_api as service

        self._ensure_user("op1", full_name="First Operator")
        self._ensure_user("tech1", role="PUNCHING_TECHNICIAN", full_name="A Technician")
        self._ensure_user("old", active=False)
        self._ensure_user("tc1", role="TOOLCRIB_OPERATOR")
        self._as("tc1", "TOOLCRIB_OPERATOR")
        self.assertEqual([row["username"] for row in service.list_operators()], ["tech1", "op1"])
        self._as("op1", "PUNCHING_OPERATOR")
        self.assertEqual(len(service.list_operators()), 2)
        self._as("plan1", "PLANNING")
        with self.assertRaises(HTTPException) as denied:
            service.list_operators()
        self.assertEqual(denied.exception.status_code, 403)

    def test_board_flags_machines_whose_position_is_waiting_for_a_tool(self):
        import demandas_api

        self._baseline()
        session = self.session_factory()
        try:
            session.add(Maquina(codigo="M1", tipo_maquina="4 Index", edificio="B1"))
            session.commit()
        finally:
            session.close()
        original = demandas_api.SessionLocal
        demandas_api.SessionLocal = self.session_factory
        self.addCleanup(setattr, demandas_api, "SessionLocal", original)

        def waiting():
            machine = next(m for m in demandas_api.demandas_board("B1")["maquinas"] if m["maquina"] == "M1")
            return machine["herramientas_esperando"]

        self.assertEqual(waiting(), [])
        request = self._open_request()
        flagged = waiting()
        self.assertEqual([(w["station"], w["category"], w["state"]) for w in flagged], [("201", "punzon", "open")])

        import service_requests_api as service

        self._as("tc1", "TOOLCRIB_OPERATOR")
        service.attend_service_request(request["id"])
        service.collect_service_tool(request["id"], service.ServiceCollect(destino_codigo="TG1"))
        self._create_tool("P100B", "100", "punzon")
        self._as("op1", "PUNCHING_OPERATOR")
        service.install_service_tool(request["id"], service.ServiceInstall(tool_id="P100B"))
        self.assertEqual(waiting(), [])

    def test_service_request_is_allowed_with_an_open_kit_but_blocks_its_delivery(self):
        import service_requests_api as service

        _content, kit = self._create_new_setup_kit()
        self.assertTrue(kit["id"])
        request = self._open_request()
        self.assertEqual(request["state"], "open")

        def blockers():
            session = self.session_factory()
            try:
                components = session.query(workflow_api.ComponenteSurtido).filter_by(surtido_id=kit["id"]).all()
                kit_row = session.query(Surtido).filter_by(id=kit["id"]).one()
                return workflow_api._kit_blockers(session, kit_row, components)
            finally:
                session.close()

        self.assertTrue(any("SR-" in text and "still open" in text for text in blockers()))
        self._as("op1", "PUNCHING_OPERATOR")
        service.cancel_service_request(request["id"], service.ServiceCancel(motivo="not needed"))
        self.assertFalse(any("SR-" in text for text in blockers()))

    def test_service_request_validation_and_permissions(self):
        import service_requests_api as service

        self._baseline()
        self._as("op1", "PUNCHING_OPERATOR")
        for tipo in ("damage", "performance"):
            with self.assertRaises(HTTPException) as missing_note:
                self._open_request(tipo=tipo)
            self.assertEqual(missing_note.exception.status_code, 422)
        with self.assertRaises(HTTPException) as bad_type:
            self._open_request(tipo="paint")
        self.assertEqual(bad_type.exception.status_code, 422)
        with self.assertRaises(HTTPException) as stale_tool:
            self._open_request(tool_id="P200")
        self.assertEqual(stale_tool.exception.status_code, 409)
        with self.assertRaises(HTTPException) as has_tool:
            self._open_request(tipo="tool_request")
        self.assertEqual(has_tool.exception.status_code, 409)
        with self.assertRaises(HTTPException) as other_machine_position:
            service.create_service_request(service.ServiceRequestCreate(
                machine="M1", requirement_id=99999, category="punzon", tipo="inspection",
            ))
        self.assertEqual(other_machine_position.exception.status_code, 404)

        damage = self._open_request(tipo="damage", note="Punch tip chipped")
        self.assertEqual(damage["note"], "Punch tip chipped")
        self._as("tc1", "TOOLCRIB_OPERATOR")
        service.attend_service_request(damage["id"])
        service.collect_service_tool(damage["id"], service.ServiceCollect(destino_codigo="MT1"))
        session = self.session_factory()
        try:
            self.assertEqual(session.query(Herramienta).filter_by(id="P100").one().estado, "danado")
        finally:
            session.close()

        listed = service.list_service_requests(state="active", machine=None, limit=100)
        self.assertEqual([row["id"] for row in listed], [damage["id"]])
        self.assertEqual(service.list_service_requests(state="closed", machine=None, limit=100), [])

    def test_service_request_cancel_restores_tool_before_pickup(self):
        import service_requests_api as service

        self._baseline()
        request = self._open_request(tipo="inspection")
        self.assertEqual(self._slot_state("201", "punzon")["state"], "missing")

        self._as("op2", "PUNCHING_OPERATOR")
        with self.assertRaises(HTTPException) as not_owner:
            service.cancel_service_request(request["id"], service.ServiceCancel(motivo="mistake"))
        self.assertEqual(not_owner.exception.status_code, 403)
        self._as("op1", "PUNCHING_OPERATOR")
        with self.assertRaises(HTTPException) as no_reason:
            service.cancel_service_request(request["id"], service.ServiceCancel(motivo=" "))
        self.assertEqual(no_reason.exception.status_code, 422)
        cancelled = service.cancel_service_request(request["id"], service.ServiceCancel(motivo="Opened by mistake"))
        self.assertEqual(cancelled["state"], "cancelled")
        self.assertTrue(cancelled["restored"])
        self.assertEqual(self._slot_state("201", "punzon")["state"], "installed")

        again = self._open_request(tipo="inspection")
        self._as("tc1", "TOOLCRIB_OPERATOR")
        service.attend_service_request(again["id"])
        self._as("op1", "PUNCHING_OPERATOR")
        with self.assertRaises(HTTPException) as owner_too_late:
            service.cancel_service_request(again["id"], service.ServiceCancel(motivo="changed my mind"))
        self.assertEqual(owner_too_late.exception.status_code, 403)

    def test_service_return_closes_on_pickup_and_tool_request_fills_empty_position(self):
        import service_requests_api as service

        self._baseline()
        self._create_tool("D100B", "100", "matriz", "0.016")
        returned = self._open_request(tipo="return", station="201", category="matriz", tool_id="D100")
        self._as("tc1", "TOOLCRIB_OPERATOR")
        service.attend_service_request(returned["id"])
        closed = service.collect_service_tool(returned["id"], service.ServiceCollect(destino_codigo="R1"))
        self.assertEqual(closed["state"], "closed")
        self.assertEqual(self._slot_state("201", "matriz")["state"], "missing")

        self._as("op1", "PUNCHING_OPERATOR")
        wanted = service.create_service_request(service.ServiceRequestCreate(
            machine="M1", requirement_id=self._requirement_id("201"), category="matriz", tipo="tool_request",
        ))
        self.assertIsNone(wanted["tool_id"])
        self._as("tc1", "TOOLCRIB_OPERATOR")
        service.attend_service_request(wanted["id"])
        with self.assertRaises(HTTPException) as nothing_to_collect:
            service.collect_service_tool(wanted["id"], service.ServiceCollect(destino_codigo="R1"))
        self.assertEqual(nothing_to_collect.exception.status_code, 409)
        self._as("op1", "PUNCHING_OPERATOR")
        done = service.install_service_tool(wanted["id"], service.ServiceInstall(tool_id="D100B"))
        self.assertEqual(done["state"], "closed")
        self.assertEqual(self._slot_state("201", "matriz")["tool_id"], "D100B")

    def test_work_on_a_tool_cannot_be_collected_into_a_rack_and_a_stuck_tool_can_be_moved(self):
        import service_requests_api as service

        self._baseline()
        request = self._open_request()
        self._as("tc1", "TOOLCRIB_OPERATOR")
        service.attend_service_request(request["id"])
        with self.assertRaises(HTTPException) as rack:
            service.collect_service_tool(request["id"], service.ServiceCollect(destino_codigo="R1"))
        self.assertEqual(rack.exception.status_code, 409)
        self.assertIn("service location", rack.exception.detail)
        service.collect_service_tool(request["id"], service.ServiceCollect(destino_codigo="TG1"))

        # A tool already in a service location has nothing to move.
        with self.assertRaises(HTTPException) as in_service:
            service.relocate_service_tool(request["id"], service.ServiceCollect(destino_codigo="MT1"))
        self.assertEqual(in_service.exception.status_code, 409)

        # Simulate a tool left in a rack by an older version, then move it to a service location.
        session = self.session_factory()
        try:
            session.query(Herramienta).filter_by(id="P100").one().ubicacion = "R1"
            session.commit()
        finally:
            session.close()
        with self.assertRaises(HTTPException) as no_result:
            service.finish_tool_service(request["id"], service.ServiceFinish(resultado="ready", rebaje=0.002))
        self.assertEqual(no_result.exception.status_code, 409)
        with self.assertRaises(HTTPException) as to_rack:
            service.relocate_service_tool(request["id"], service.ServiceCollect(destino_codigo="R1"))
        self.assertEqual(to_rack.exception.status_code, 409)
        moved = service.relocate_service_tool(request["id"], service.ServiceCollect(destino_codigo="TG1"))
        self.assertEqual((moved["tool_location"], moved["tool_status"]), ("TG1", "afilado"))
        finished = service.finish_tool_service(request["id"], service.ServiceFinish(resultado="ready", rebaje=0.002))
        self.assertEqual(finished["tool_status"], "disponible")

    def test_maintenance_ticket_from_a_sharpening_request_follows_the_tool_through_repair(self):
        import maintenance_api as maint
        import service_requests_api as service
        import tool_tracking_api as tracking

        self._baseline()
        self._create_tool("P100B", "100", "punzon")
        self._ensure_user("tc1", role="TOOLCRIB_OPERATOR")
        self._ensure_user("mt1", role="MAINTENANCE_TECHNICIAN")
        request = self._open_request()  # Manufacturing only asked for a sharpening
        self._as("tc1", "TOOLCRIB_OPERATOR")
        service.attend_service_request(request["id"])
        with self.assertRaises(HTTPException) as too_early:
            maint.create_ticket(maint.TicketCreate(
                request_id=request["id"], hallazgo="Cracked", causa="collision", momento="setup", operador="op1",
            ))
        self.assertEqual(too_early.exception.status_code, 409)
        service.collect_service_tool(request["id"], service.ServiceCollect(destino_codigo="TG1"))

        for bad in (dict(hallazgo="x"), dict(causa="nope"), dict(operador="ghost"), dict(operador="")):
            payload = dict(request_id=request["id"], hallazgo="Cracked shoulder", causa="collision", momento="setup", operador="op1")
            payload.update(bad)
            with self.assertRaises(HTTPException, msg=str(bad)) as invalid:
                maint.create_ticket(maint.TicketCreate(**payload))
            self.assertEqual(invalid.exception.status_code, 422, bad)

        ticket = maint.create_ticket(maint.TicketCreate(
            request_id=request["id"], hallazgo="Cracked shoulder, needs more than sharpening",
            causa="collision", momento="setup", operador="op1",
        ))
        self.assertEqual((ticket["folio"], ticket["state"], ticket["tool_id"]), ("MT-00001", "open", "P100"))
        self.assertEqual((ticket["tool_location"], ticket["tool_status"]), ("MT1", "danado"))
        self.assertEqual((ticket["incident"]["reported_as"], ticket["incident"]["machine"], ticket["incident"]["station"]),
                         ("sharpening", "M1", "201"))
        with self.assertRaises(HTTPException) as twice:
            maint.create_ticket(maint.TicketCreate(
                request_id=request["id"], hallazgo="Again", causa="collision", momento="setup", operador="op1",
            ))
        self.assertEqual(twice.exception.status_code, 409)
        with self.assertRaises(HTTPException) as blocked_result:
            service.finish_tool_service(request["id"], service.ServiceFinish(resultado="ready", rebaje=0.001))
        self.assertEqual(blocked_result.exception.status_code, 409)
        with self.assertRaises(HTTPException) as blocked_store:
            tracking.store_tool_in_rack("R1", tracking.RackStore(tool_id="P100", realizado_por="tc1", resultado="baja"))
        self.assertIn("MT-00001", blocked_store.exception.detail)

        # The machine gets its replacement and the request closes: the old tool is now in the ticket.
        self._as("op1", "PUNCHING_OPERATOR")
        installed = service.install_service_tool(request["id"], service.ServiceInstall(tool_id="P100B"))
        self.assertEqual((installed["state"], installed["ticket"]["folio"]), ("closed", "MT-00001"))

        with self.assertRaises(HTTPException) as not_technician:
            maint.receive_ticket(ticket["id"], maint.TicketReceive(tool_id="P100"))
        self.assertEqual(not_technician.exception.status_code, 403)
        self._as("mt1", "MAINTENANCE_TECHNICIAN")
        with self.assertRaises(HTTPException) as wrong_tool:
            maint.receive_ticket(ticket["id"], maint.TicketReceive(tool_id="P200"))
        self.assertEqual(wrong_tool.exception.status_code, 409)
        with self.assertRaises(HTTPException) as not_received:
            maint.evaluate_ticket(ticket["id"], maint.TicketEvaluate(diagnostico="Weld the tip", reparable=True))
        self.assertEqual(not_received.exception.status_code, 409)
        received = maint.receive_ticket(ticket["id"], maint.TicketReceive(tool_id="tooltrack-tool:p100"))
        self.assertEqual((received["state"], received["received_by"]), ("evaluating", "mt1"))
        with self.assertRaises(HTTPException) as no_release:
            maint.release_ticket(ticket["id"], maint.TicketRelease(trabajo="Reground"))
        self.assertEqual(no_release.exception.status_code, 409)
        evaluated = maint.evaluate_ticket(ticket["id"], maint.TicketEvaluate(
            diagnostico="Shoulder crack is superficial", reparable=True, causa_tecnica="wrong_setup",
        ))
        self.assertEqual((evaluated["state"], evaluated["technical_cause"]), ("repairing", "wrong_setup"))
        released = maint.release_ticket(ticket["id"], maint.TicketRelease(trabajo="Reground the shoulder and polished"))
        self.assertEqual((released["state"], released["result"], released["tool_status"], released["tool_location"]),
                         ("closed", "released", "disponible", "MT1"))
        self.assertEqual(maint.list_tickets(state="active", tool_id=None, limit=10), [])
        self.assertEqual([row["folio"] for row in maint.list_tickets(state="closed", tool_id="p100", limit=10)], ["MT-00001"])

        # Tool Crib picks it up from Maintenance and stores it; it is a normal tool again.
        self._as("tc1", "TOOLCRIB_OPERATOR")
        stored = tracking.store_tool_in_rack("R1", tracking.RackStore(tool_id="P100", realizado_por="tc1"))
        self.assertEqual(stored["to"], "R1")
        session = self.session_factory()
        try:
            types = [row.tipo for row in session.query(MovimientoHerramienta).filter_by(herramienta_id="P100").all()]
        finally:
            session.close()
        for expected in ("maintenance_ticket_opened", "maintenance_received", "maintenance_released", "maintenance_to_rack"):
            self.assertIn(expected, types)

    def test_ticket_opened_after_the_machine_already_has_its_replacement_closes_the_request(self):
        import maintenance_api as maint
        import service_requests_api as service

        self._baseline()
        self._create_tool("P100B", "100", "punzon")
        self._ensure_user("tc1", role="TOOLCRIB_OPERATOR")
        request = self._open_request()
        self._as("tc1", "TOOLCRIB_OPERATOR")
        service.attend_service_request(request["id"])
        service.collect_service_tool(request["id"], service.ServiceCollect(destino_codigo="TG1"))
        self._as("op1", "PUNCHING_OPERATOR")
        self.assertEqual(service.install_service_tool(request["id"], service.ServiceInstall(tool_id="P100B"))["state"], "replaced")
        self._as("tc1", "TOOLCRIB_OPERATOR")
        maint.create_ticket(maint.TicketCreate(
            request_id=request["id"], hallazgo="Not just dull: chipped", causa="handling", momento="handling", operador="op1",
        ))
        rows = service.list_service_requests(state="all", machine=None, limit=10)
        self.assertEqual((rows[0]["state"], rows[0]["ticket"]["state"]), ("closed", "open"))

    def test_toolcrib_opens_a_manual_ticket_for_a_tool_it_holds_and_unrepairable_tools_stay_damaged(self):
        import maintenance_api as maint

        self._baseline()
        self._create_tool("P300", "300", "punzon")
        self._ensure_user("op1")
        self._ensure_user("tc1", role="TOOLCRIB_OPERATOR")
        self._ensure_user("mt1", role="MAINTENANCE_TECHNICIAN")
        base = dict(hallazgo="Chipped edge found at the rack", causa="unknown", momento="unknown", tool_id="P300")

        self._as("op1", "PUNCHING_OPERATOR")
        with self.assertRaises(HTTPException) as operator:
            maint.create_ticket(maint.TicketCreate(**base, origen="toolcrib"))
        self.assertEqual(operator.exception.status_code, 403)
        self._as("mt1", "MAINTENANCE_TECHNICIAN")
        with self.assertRaises(HTTPException) as technician:
            maint.create_ticket(maint.TicketCreate(**base, origen="toolcrib"))
        self.assertEqual(technician.exception.status_code, 403)

        self._as("tc1", "TOOLCRIB_OPERATOR")
        with self.assertRaises(HTTPException) as no_origin:
            maint.create_ticket(maint.TicketCreate(**base))
        self.assertEqual(no_origin.exception.status_code, 422)
        with self.assertRaises(HTTPException) as no_machine:
            maint.create_ticket(maint.TicketCreate(**base, origen="machine", operador="op1"))
        self.assertEqual(no_machine.exception.status_code, 422)
        with self.assertRaises(HTTPException) as no_operator:
            maint.create_ticket(maint.TicketCreate(**base, origen="machine", machine="M1"))
        self.assertEqual(no_operator.exception.status_code, 422)
        with self.assertRaises(HTTPException) as mounted:
            maint.create_ticket(maint.TicketCreate(**{**base, "tool_id": "P200"}, origen="toolcrib"))
        self.assertEqual(mounted.exception.status_code, 409)

        ticket = maint.create_ticket(maint.TicketCreate(**base, origen="toolcrib"))
        self.assertEqual((ticket["state"], ticket["tool_location"], ticket["tool_status"]), ("open", "MT1", "danado"))
        self.assertEqual((ticket["incident"]["origin"], ticket["incident"]["operator"], ticket["incident"]["tn"]),
                         ("toolcrib", None, "300"))
        with self.assertRaises(HTTPException) as duplicate:
            maint.create_ticket(maint.TicketCreate(**base, origen="toolcrib"))
        self.assertEqual(duplicate.exception.status_code, 409)

        self._as("mt1", "MAINTENANCE_TECHNICIAN")
        maint.receive_ticket(ticket["id"], maint.TicketReceive(tool_id="P300"))
        closed = maint.evaluate_ticket(ticket["id"], maint.TicketEvaluate(diagnostico="Body cracked through", reparable=False))
        self.assertEqual((closed["state"], closed["result"], closed["tool_status"], closed["tool_location"]),
                         ("closed", "unrepairable", "danado", "MT1"))

        self._as("tc1", "TOOLCRIB_OPERATOR")
        self._create_tool("P301", "301", "punzon")
        machine_ticket = maint.create_ticket(maint.TicketCreate(
            hallazgo="Came back from the machine bent", causa="collision", momento="production", tool_id="p301",
            origen="machine", machine="M1", operador="op1", part_number="ABC-1", station="201",
        ))
        incident = machine_ticket["incident"]
        self.assertEqual(
            (incident["origin"], incident["machine"], incident["part_number"], incident["station"], incident["operator"], incident["shift"]),
            ("machine", "M1", "ABC-1", "201", "op1", "Turno 1"),
        )
        self.assertEqual(len(maint.list_tickets(state="all", tool_id=None, limit=10)), 2)
        with self.assertRaises(HTTPException) as bad_state:
            maint.list_tickets(state="weird", tool_id=None, limit=10)
        self.assertEqual(bad_state.exception.status_code, 422)

    def test_records_list_each_kind_apart_filter_and_download_to_excel(self):
        import io

        import maintenance_api as maint
        import records_api as records
        import service_requests_api as service
        from openpyxl import load_workbook

        self._baseline()
        self._ensure_user("tc1", role="TOOLCRIB_OPERATOR")
        self._ensure_user("sup1", role="TOOLCRIB_SUPERVISOR", full_name="Super Visor")
        request = self._open_request("damage", note="=1+1 chipped tip")
        self._as("tc1", "TOOLCRIB_OPERATOR")
        service.attend_service_request(request["id"])
        service.collect_service_tool(request["id"], service.ServiceCollect(destino_codigo="TG1"))
        maint.create_ticket(maint.TicketCreate(
            request_id=request["id"], hallazgo="+cracked at the shoulder", causa="collision", momento="setup", operador="op1",
        ))

        self._as("op1", "PUNCHING_OPERATOR")
        with self.assertRaises(HTTPException) as denied:
            records.list_records()
        self.assertEqual(denied.exception.status_code, 403)
        self._as("tc1", "TOOLCRIB_OPERATOR")
        with self.assertRaises(HTTPException) as operator:
            records.get_records("requests")
        self.assertEqual(operator.exception.status_code, 403)

        self._as("sup1", "TOOLCRIB_SUPERVISOR")
        kinds = {kind["key"]: kind for kind in records.list_records()}
        self.assertEqual(
            list(kinds), ["movements", "requests", "incidents", "tickets", "services", "programs", "audit"],
        )
        self.assertEqual([group["value"] for group in kinds["movements"]["groups"]],
                         ["kitting_machine", "service_maintenance", "storage_status"])

        def fetch(key, **filters):
            params = dict(date_from=None, date_to=None, tool=None, machine=None, user=None, group=None, limit=100, offset=0)
            params.update(filters)
            return records.get_records(key, **params)

        service_moves = fetch("movements", group="service_maintenance")
        self.assertEqual([row["movement"] for row in service_moves["rows"]], [
            "Sent to Maintenance", "Collected from the machine, reported damaged", "Service requested from the machine",
        ])
        kitting = fetch("movements", group="kitting_machine")
        storage = fetch("movements", group="storage_status")
        for group_rows in (kitting["rows"], storage["rows"]):
            self.assertFalse([row for row in group_rows if "Maintenance" in row["movement"] or "Service" in row["movement"]])
        self.assertEqual(fetch("movements")["group"], "kitting_machine")
        session = self.session_factory()
        try:
            all_movements = session.query(MovimientoHerramienta).count()
        finally:
            session.close()
        self.assertEqual(
            sum(fetch("movements", group=group)["total"] for group in ("kitting_machine", "service_maintenance", "storage_status")),
            all_movements,
        )
        self.assertEqual(fetch("movements", group="service_maintenance", tool="p10")["total"], 3)
        self.assertEqual(fetch("movements", group="service_maintenance", machine="zz")["total"], 0)
        self.assertEqual(fetch("movements", group="service_maintenance", user="tc1")["total"], 2)
        self.assertEqual(fetch("movements", group="service_maintenance", limit=2)["rows"].__len__(), 2)
        with self.assertRaises(HTTPException) as group:
            fetch("movements", group="mixed")
        self.assertEqual(group.exception.status_code, 422)
        with self.assertRaises(HTTPException) as unknown:
            fetch("nothing")
        self.assertEqual(unknown.exception.status_code, 404)
        with self.assertRaises(HTTPException) as date:
            fetch("requests", date_from="30/11/2026")
        self.assertEqual(date.exception.status_code, 422)
        self.assertEqual(fetch("requests", date_from="2999-01-01")["total"], 0)
        self.assertEqual(fetch("requests", date_from="2000-01-01", date_to="2999-01-01")["total"], 1)

        row = fetch("requests", machine="m1", user="op1")["rows"][0]
        self.assertEqual(
            (row["folio"], row["type"], row["state"], row["tool"], row["cause"], row["ticket"], row["requested_by"]),
            ("SR-00001", "Damage report", "Waiting for a tool", "P100", "Collision", "MT-00001", "OP1"),
        )
        incident = fetch("incidents", tool="P100")["rows"][0]
        self.assertEqual((incident["operator"], incident["shift"], incident["cause"], incident["moment"], incident["request"], incident["ticket"]),
                         ("OP1", "Turno 1", "Collision", "During setup", "SR-00001", "MT-00001"))
        ticket = fetch("tickets")["rows"][0]
        self.assertEqual((ticket["folio"], ticket["state"], ticket["tool"], ticket["machine"], ticket["part_number"]),
                         ("MT-00001", "Waiting for the technician", "P100", "M1", "CURRENT"))
        self.assertEqual(fetch("services")["total"], 0)
        self.assertGreaterEqual(fetch("audit")["total"], 0)

        sheet_response = records.download_records("incidents", date_from=None, date_to=None, tool=None, machine=None, user=None, group=None)
        self.assertEqual(sheet_response.media_type, records.XLSX)
        self.assertIn("attachment; filename=\"MIRA-incidents-", sheet_response.headers["content-disposition"])
        book = load_workbook(io.BytesIO(sheet_response.body))
        self.assertEqual(book.sheetnames, ["Damage incidents", "Info"])
        header = [cell.value for cell in book["Damage incidents"][1]]
        values = dict(zip(header, [cell.value for cell in book["Damage incidents"][2]]))
        self.assertEqual((values["Tool"], values["Cause"], values["Found by Tool Crib"]), ("P100", "Collision", "'+cracked at the shoulder"))
        self.assertEqual(book["Info"]["B3"].value, "sup1")

        requests_book = load_workbook(io.BytesIO(records.download_records(
            "requests", date_from=None, date_to=None, tool=None, machine=None, user=None, group=None,
        ).body))
        request_header = [cell.value for cell in requests_book["Service requests"][1]]
        request_values = dict(zip(request_header, [cell.value for cell in requests_book["Service requests"][2]]))
        self.assertEqual(request_values["Note"], "'=1+1 chipped tip")
        self.assertIn("Resolution or cancel reason", request_header)

        workbook = load_workbook(io.BytesIO(records.download_workbook(date_from=None, date_to=None).body))
        self.assertEqual(workbook.sheetnames, [
            "Mov - Kitting and machine", "Mov - Service and Maintenance", "Mov - Storage and status",
            "Service requests", "Damage incidents", "Maintenance tickets", "Sharpening and repairs",
            "Part number changes", "System audit", "Info",
        ])
        self.assertEqual(workbook["Mov - Service and Maintenance"].max_row, 4)

    def test_tool_file_gathers_one_tool_and_hides_service_data_from_roles_without_access(self):
        import maintenance_api as maint
        import service_requests_api as service
        import tool_file_api as file_api

        self._baseline()
        self._create_tool("P1000", "100", "punzon")
        self._ensure_user("tc1", role="TOOLCRIB_OPERATOR")
        request = self._open_request("damage", note="Tip chipped")
        self._as("tc1", "TOOLCRIB_OPERATOR")
        service.attend_service_request(request["id"])
        service.collect_service_tool(request["id"], service.ServiceCollect(destino_codigo="TG1"))
        maint.create_ticket(maint.TicketCreate(
            request_id=request["id"], hallazgo="Cracked shoulder", causa="collision", momento="setup", operador="op1",
        ))

        full = file_api.get_tool_file("p100")
        self.assertEqual(
            (full["tool"]["id"], full["tool"]["tn"], full["tool"]["estado"], full["where"]["location"]),
            ("P100", "100", "danado", "MT1"),
        )
        self.assertEqual(list(full["sections"]), ["movements", "requests", "incidents", "tickets", "services"])
        self.assertEqual(full["summary"]["open_ticket"], "MT-00001")
        self.assertEqual((full["summary"]["incidents"], full["summary"]["tickets"], full["summary"]["requests"]), (1, 1, 1))
        self.assertEqual(full["summary"]["movements"], full["sections"]["movements"]["total"])
        self.assertTrue(all(row["tool"] == "P100" for row in full["sections"]["movements"]["rows"]))
        self.assertIn("Service and Maintenance", {row["group"] for row in full["sections"]["movements"]["rows"]})
        self.assertEqual(full["sections"]["tickets"]["rows"][0]["folio"], "MT-00001")

        # A longer ID that merely contains P100 never leaks into this tool's file.
        other = file_api.get_tool_file("P1000")
        self.assertEqual(other["sections"]["movements"]["total"], 0)
        self.assertEqual(other["summary"]["tickets"], 0)

        self._ensure_user("plan1", role="PLANNING")
        self._as("plan1", "PLANNING")
        limited = file_api.get_tool_file("P100")
        self.assertEqual(list(limited["sections"]), ["movements"])
        self.assertNotIn("tickets", limited["summary"])

        with self.assertRaises(HTTPException) as missing:
            file_api.get_tool_file("NOPE")
        self.assertEqual(missing.exception.status_code, 404)

    def test_a_tool_with_no_recorded_location_gets_its_first_rack_by_scanning_and_then_can_be_kitted(self):
        import tool_tracking_api as tracking

        self._baseline()
        self._create_tool("P100B", "100", "punzon")
        session = self.session_factory()
        try:
            tool = session.query(Herramienta).filter_by(id="P100B").one()
            tool.ubicacion = None
            session.commit()
        finally:
            session.close()

        placed = tracking.store_tool_in_rack("R1", tracking.RackStore(tool_id="p100b", realizado_por="op", captura="scan"))
        self.assertEqual((placed["kind"], placed["from"], placed["to"]), ("placement", None, "R1"))
        session = self.session_factory()
        try:
            self.assertEqual(session.query(Herramienta).filter_by(id="P100B").one().ubicacion, "R1")
            movement = session.query(MovimientoHerramienta).filter_by(herramienta_id="P100B").one()
            self.assertEqual((movement.tipo, movement.origen_codigo, movement.destino_codigo, movement.captura), ("placed_in_rack", None, "R1", "scan"))
        finally:
            session.close()
        # Once it has a rack it is an ordinary stored tool: there is nothing more to store.
        with self.assertRaises(HTTPException) as again:
            tracking.store_tool_in_rack("R1", tracking.RackStore(tool_id="P100B", realizado_por="op"))
        self.assertEqual(again.exception.status_code, 409)

    def test_a_damaged_tool_with_no_location_cannot_be_placed_and_kitting_explains_the_missing_rack(self):
        import tool_tracking_api as tracking

        new_content, kit = self._create_new_setup_kit()
        self._create_tool("P300B", "300", "punzon")
        self._create_tool("P300X", "300", "punzon")
        session = self.session_factory()
        try:
            for tool_id, state in (("P300B", "danado"), ("P300X", "disponible")):
                tool = session.query(Herramienta).filter_by(id=tool_id).one()
                tool.ubicacion = None
                tool.estado = state
            session.commit()
        finally:
            session.close()
        with self.assertRaises(HTTPException) as damaged:
            tracking.store_tool_in_rack("R1", tracking.RackStore(tool_id="P300B", realizado_por="op"))
        self.assertEqual(damaged.exception.status_code, 409)
        self.assertIn("no location", damaged.exception.detail)

        with self.assertRaises(HTTPException) as unplaced:
            workflow_api.scan_kit_tool(kit["id"], workflow_api.SurtidoToolSelect(herramienta_id="P300X", realizado_por="op"))
        self.assertEqual(unplaced.exception.status_code, 409)
        self.assertIn("no rack recorded", unplaced.exception.detail)

        # After its first placement the same scan works.
        tracking.store_tool_in_rack("R1", tracking.RackStore(tool_id="P300X", realizado_por="op"))
        scanned = workflow_api.scan_kit_tool(kit["id"], workflow_api.SurtidoToolSelect(herramienta_id="P300X", realizado_por="op"))
        self.assertTrue(any(item.get("tool_id") == "P300X" or item.get("herramienta_nueva_id") == "P300X" for item in scanned["components"]))

    def test_service_request_install_requires_the_setup_it_was_opened_on(self):
        import service_requests_api as service

        self._baseline()
        self._create_tool("P100B", "100", "punzon")
        request = self._open_request()
        self._as("tc1", "TOOLCRIB_OPERATOR")
        service.attend_service_request(request["id"])
        service.collect_service_tool(request["id"], service.ServiceCollect(destino_codigo="TG1"))
        session = self.session_factory()
        try:
            session.query(EstadoMaquina).filter_by(maquina_codigo="M1").one().setup_activo_id = None
            session.commit()
        finally:
            session.close()
        self._as("op1", "PUNCHING_OPERATOR")
        with self.assertRaises(HTTPException) as changed:
            service.install_service_tool(request["id"], service.ServiceInstall(tool_id="P100B"))
        self.assertEqual(changed.exception.status_code, 409)
    def test_workspace_uses_program_requirements_and_operator_registers_mounted_tools(self):
        import service_requests_api as service
        import tool_tracking_api as tracking

        session = self.session_factory()
        try:
            setup = Setup(
                maquina_codigo="M1", nombre_archivo="PN100.stp", drawing_name="PN100", thickness="0.075",
                contenido_hash="hash", creado_por="engineer", creado_en=workflow_api.utcnow(), estado="active",
            )
            session.add(setup)
            session.flush()
            session.add(EstadoMaquina(maquina_codigo="M1", setup_activo_id=setup.id))
            session.add(Maquina(codigo="M1", tipo_maquina="4 Index", edificio="B1"))
            session.add(ProgramaParte(
                numero_parte="PN100", tipo_maquina="4I", tipo_programa="NPI", revision=1,
                contenido="program", contenido_hash="hash",
                requisitos_json=json.dumps([
                    {"station": "201", "tn": "100", "angle": "0"},
                    {"station": "202", "tn": "200", "angle": None},
                ]),
                actualizado_por="engineer", actualizado_en=workflow_api.utcnow(),
            ))
            session.commit()
        finally:
            session.close()
        self._create_tool("P100", "100", "punzon")
        self._create_tool("D100", "100", "matriz", "0.016")
        self._create_tool("P200", "200", "punzon")

        workspace = tracking.manufacturing_machine_workspace("M1")
        self.assertEqual(workspace["requirements_source"], "program")
        self.assertEqual([row["station"] for row in workspace["stations"]], ["201", "202"])
        self.assertTrue(all(row["requirement_id"] is None for row in workspace["stations"]))
        self.assertEqual(workspace["coverage"], {"installed": 0, "required": 4, "complete": False})
        session = self.session_factory()
        try:
            self.assertEqual(session.query(RequisitoSetup).count(), 0)
        finally:
            session.close()

        self._as("op1", "PUNCHING_OPERATOR")
        position = dict(station="201", tn="100", angle="0")
        with self.assertRaises(HTTPException) as wrong_tn:
            service.register_mounted_tool("M1", service.MountedRegister(category="punzon", tool_id="P200", **position))
        self.assertEqual(wrong_tn.exception.status_code, 409)
        with self.assertRaises(HTTPException) as unknown:
            service.register_mounted_tool("M1", service.MountedRegister(category="punzon", tool_id="NOPE", **position))
        self.assertEqual(unknown.exception.status_code, 404)

        registered = service.register_mounted_tool(
            "M1", service.MountedRegister(category="punzon", tool_id="P100", **position)
        )
        self.assertEqual(registered["tool_id"], "P100")
        workspace = tracking.manufacturing_machine_workspace("M1")
        self.assertEqual(workspace["requirements_source"], "setup")
        self.assertEqual(workspace["coverage"]["installed"], 1)
        self.assertEqual(self._slot_state("201", "punzon")["tool_id"], "P100")
        session = self.session_factory()
        try:
            tool = session.query(Herramienta).filter_by(id="P100").one()
            self.assertEqual(tool.ubicacion, "M1")
            movement = session.query(MovimientoHerramienta).filter_by(herramienta_id="P100").one()
            self.assertEqual((movement.origen_codigo, movement.tipo), ("R1", "machine_observed"))
        finally:
            session.close()
        with self.assertRaises(HTTPException) as twice:
            service.register_mounted_tool("M1", service.MountedRegister(category="punzon", tool_id="P100", **position))
        self.assertEqual(twice.exception.status_code, 409)
        with self.assertRaises(HTTPException) as same_tool_elsewhere:
            service.register_mounted_tool(
                "M1", service.MountedRegister(category="punzon", tool_id="P100", station="202", tn="200")
            )
        self.assertEqual(same_tool_elsewhere.exception.status_code, 409)

        request = service.create_service_request(service.ServiceRequestCreate(
            machine="M1", category="punzon", tipo="inspection", station="201", tn="100", angle="0", tool_id="P100",
        ))
        self.assertEqual(request["tool_id"], "P100")
        self.assertEqual(request["part_number"], "PN100")
        empty = service.create_service_request(service.ServiceRequestCreate(
            machine="M1", category="matriz", tipo="tool_request", station="201", tn="100", angle="0",
        ))
        self.assertIsNone(empty["tool_id"])
        with self.assertRaises(HTTPException) as no_position:
            service.register_mounted_tool("M1", service.MountedRegister(category="punzon", tool_id="P200"))
        self.assertEqual(no_position.exception.status_code, 422)

    def test_registering_mounted_tool_accepts_unregistered_rack_but_not_service_locations(self):
        import service_requests_api as service

        self._baseline()
        session = self.session_factory()
        try:
            session.query(RequisitoSetup).filter_by(tn="100").one().punch_tool_id = None
            session.query(Herramienta).filter_by(id="P100").one().ubicacion = "R-LEGACY"
            session.commit()
        finally:
            session.close()
        self._as("op1", "PUNCHING_OPERATOR")
        service.register_mounted_tool(
            "M1", service.MountedRegister(category="punzon", tool_id="P100", requirement_id=self._requirement_id("201"))
        )
        session = self.session_factory()
        try:
            movement = session.query(MovimientoHerramienta).filter_by(
                herramienta_id="P100", tipo="machine_observed"
            ).one()
            self.assertIsNone(movement.origen_codigo)
            self.assertEqual(session.query(Herramienta).filter_by(id="P100").one().ubicacion, "M1")
            session.query(RequisitoSetup).filter_by(tn="100").one().punch_tool_id = None
            session.query(Herramienta).filter_by(id="P100").one().ubicacion = "TG1"
            session.commit()
        finally:
            session.close()
        with self.assertRaises(HTTPException) as in_togu:
            service.register_mounted_tool(
                "M1", service.MountedRegister(category="punzon", tool_id="P100", requirement_id=self._requirement_id("201"))
            )
        self.assertEqual(in_togu.exception.status_code, 409)

    def test_program_problem_reaches_the_queue_and_engineering_resolves_it(self):
        import service_requests_api as service

        self._baseline()
        self._as("op1", "PUNCHING_OPERATOR")
        with self.assertRaises(HTTPException) as bad:
            service.create_program_problem(service.ProgramProblemCreate(machine="M1", problema="paint"))
        self.assertEqual(bad.exception.status_code, 422)
        with self.assertRaises(HTTPException) as needs_note:
            service.create_program_problem(service.ProgramProblemCreate(machine="M1", problema="other"))
        self.assertEqual(needs_note.exception.status_code, 422)

        request = service.create_program_problem(service.ProgramProblemCreate(
            machine="M1", problema="wrong_tool", station="201", nota="Station 201 uses a different tool",
        ))
        self.assertEqual(request["type"], "program_problem")
        self.assertEqual(request["part_number"], "CURRENT")
        self.assertIsNone(request["tool_id"])
        with self.assertRaises(HTTPException) as duplicate:
            service.create_program_problem(service.ProgramProblemCreate(machine="M1", problema="wrong_tool"))
        self.assertEqual(duplicate.exception.status_code, 409)

        self._as("eng1", "ENGINEERING")
        listed = service.list_service_requests(state="active", machine=None, limit=100)
        self.assertEqual([row["id"] for row in listed], [request["id"]])
        attending = service.attend_service_request(request["id"])
        self.assertEqual(attending["attended_by"], "eng1")
        with self.assertRaises(HTTPException) as collect:
            service.collect_service_tool(request["id"], service.ServiceCollect(destino_codigo="TG1"))
        self.assertEqual(collect.exception.status_code, 409)
        self._as("tc1", "TOOLCRIB_OPERATOR")
        with self.assertRaises(HTTPException) as not_attendant:
            service.resolve_program_problem(request["id"], service.ServiceResolve(resolucion="Program corrected"))
        self.assertEqual(not_attendant.exception.status_code, 403)
        self._as("eng1", "ENGINEERING")
        with self.assertRaises(HTTPException) as short:
            service.resolve_program_problem(request["id"], service.ServiceResolve(resolucion="x"))
        self.assertEqual(short.exception.status_code, 422)
        closed = service.resolve_program_problem(request["id"], service.ServiceResolve(resolucion="Program corrected, rev 2"))
        self.assertEqual((closed["state"], closed["resolution"]), ("closed", "Program corrected, rev 2"))

        import tool_tracking_api as tracking

        workspace = tracking.manufacturing_machine_workspace("M1")
        self.assertEqual([row["id"] for row in workspace["service_requests"]], [request["id"]])
        self.assertTrue(all(c["open_request"] is None for s in workspace["stations"] for c in s["components"]))

    def test_engineering_cannot_attend_tool_requests(self):
        import service_requests_api as service

        self._baseline()
        request = self._open_request()
        self._as("eng1", "ENGINEERING")
        with self.assertRaises(HTTPException) as denied:
            service.attend_service_request(request["id"])
        self.assertEqual(denied.exception.status_code, 403)
        self._as("tc1", "TOOLCRIB_OPERATOR")
        with self.assertRaises(HTTPException) as not_program:
            service.resolve_program_problem(request["id"], service.ServiceResolve(resolucion="done"))
        self.assertEqual(not_program.exception.status_code, 409)

    def test_service_request_table_migration_keeps_rows_and_allows_program_problems(self):
        import os
        import sqlite3
        import tempfile

        import main

        handle, path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        try:
            conn = sqlite3.connect(path)
            conn.execute(
                "CREATE TABLE solicitudes_servicio (id INTEGER PRIMARY KEY, maquina_codigo TEXT NOT NULL, "
                "setup_id INTEGER NOT NULL, requisito_id INTEGER NOT NULL, numero_parte TEXT, estacion TEXT NOT NULL, "
                "categoria TEXT NOT NULL, tn TEXT NOT NULL, tipo TEXT NOT NULL, estado TEXT NOT NULL, nota TEXT, "
                "herramienta_id TEXT, herramienta_nueva_id TEXT, destino_codigo TEXT, solicitado_por TEXT NOT NULL, "
                "solicitado_en DATETIME NOT NULL, atendido_por TEXT, atendido_en DATETIME, recogido_por TEXT, "
                "recogido_en DATETIME, cerrado_por TEXT, cerrado_en DATETIME, motivo_cancelacion TEXT)"
            )
            conn.execute(
                "INSERT INTO solicitudes_servicio (id, maquina_codigo, setup_id, requisito_id, estacion, categoria, tn, "
                "tipo, estado, solicitado_por, solicitado_en) VALUES (7, 'M1', 1, 1, '201', 'punzon', '100', "
                "'inspection', 'closed', 'op1', '2026-01-01 00:00:00')"
            )
            conn.commit()
            conn.close()
            main.migrate_service_requests_table(path)
            conn = sqlite3.connect(path)
            columns = {row[1]: row for row in conn.execute("PRAGMA table_info(solicitudes_servicio)")}
            self.assertEqual(columns["requisito_id"][3], 0)
            self.assertIn("problema", columns)
            self.assertIn("resolucion", columns)
            self.assertEqual(conn.execute("SELECT id, estacion, estado FROM solicitudes_servicio").fetchall(), [(7, "201", "closed")])
            conn.close()
        finally:
            os.remove(path)

    def test_registering_mounted_tool_rejects_tools_being_serviced(self):
        import service_requests_api as service

        self._baseline()
        self._create_tool("P100B", "100", "punzon")
        request = self._open_request()
        self._as("tc1", "TOOLCRIB_OPERATOR")
        service.attend_service_request(request["id"])
        service.collect_service_tool(request["id"], service.ServiceCollect(destino_codigo="TG1"))
        self._as("op1", "PUNCHING_OPERATOR")
        with self.assertRaises(HTTPException) as busy_slot:
            service.register_mounted_tool(
                "M1", service.MountedRegister(category="punzon", tool_id="P100B", requirement_id=self._requirement_id("201"))
            )
        self.assertEqual(busy_slot.exception.status_code, 409)
        with self.assertRaises(HTTPException) as in_service:
            service.register_mounted_tool(
                "M1", service.MountedRegister(category="punzon", tool_id="P100", requirement_id=self._requirement_id("201"))
            )
        self.assertEqual(in_service.exception.status_code, 409)


if __name__ == "__main__":
    unittest.main()
