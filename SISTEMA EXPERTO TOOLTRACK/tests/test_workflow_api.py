import unittest
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

import workflow_api
from models.inventory import (
    Base,
    EstadoMaquina,
    Herramienta,
    MovimientoHerramienta,
    PendienteRecoger,
    ReservaHerramienta,
    RequisitoSetup,
    Setup,
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
        machines_api.delete_station("M1", "202")
        self.assertEqual(machines_api.get_machine_profile("M1")["estaciones_total"], 1)

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


if __name__ == "__main__":
    unittest.main()
