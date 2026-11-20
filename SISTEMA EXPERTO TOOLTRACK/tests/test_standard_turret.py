import unittest

import standard_turret as st


def stations(spec):
    return [{"number": number, "size": size, "autoindex": False} for number, size in spec]


def part(name, reqs, thickness="0.075"):
    return {"part": name, "thickness": thickness, "reqs": [{"station": s, "tn": tn, "angle": a} for s, tn, a in reqs]}


class StandardTurretTests(unittest.TestCase):
    def test_tools_stay_where_most_parts_already_have_them(self):
        result = st.design(
            stations([("101", "A"), ("102", "A"), ("103", "A")]),
            [part("P1", [("101", "10100", None), ("102", "10200", None)]),
             part("P2", [("101", "10100", None), ("103", "10200", None)]),
             part("P3", [("102", "10200", None)])],
            {"10100": "A", "10200": "A"},
        )
        by_tn = {row["tn"]: row["station"] for row in result["assignments"]}
        self.assertEqual(by_tn, {"10100": "101", "10200": "102"})
        # Only P2 has to move its 10200 from 103 to 102.
        self.assertEqual({row["part"]: row["changes"] for row in result["parts"]}, {"P1": 0, "P2": 1, "P3": 0})

    def test_tns_within_tolerance_share_one_station_and_report_the_difference(self):
        result = st.design(
            stations([("101", "A"), ("102", "A")]),
            [part("P1", [("101", "31000.25", None)]), part("P2", [("102", "31002.252", None)]),
             part("P3", [("101", "31000.25", None)])],
            {"31000.25": "A", "31002.252": "A"},
        )
        self.assertEqual((result["tools"], result["placed"], result["tool_merges"]), (1, 1, 1))
        row = next(r for p in result["parts"] if p["part"] == "P2" for r in p["rows"])
        self.assertEqual((row["substitute_tn"], row["new_station"]), ("31000.25", "101"))
        self.assertEqual(row["difference"], "-0.002 x -0.002")

    def test_sizes_must_match_and_a_size_a_tool_may_use_a_b_station_with_adapter(self):
        result = st.design(
            stations([("101", "B"), ("102", "C")]),
            [part("P1", [("101", "10100", None), ("102", "10300", None), ("103", "10500", None)])],
            {"10100": "A", "10300": "A", "10500": "C"},
        )
        placed = {row["tn"]: (row["station"], row["adapter"]) for row in result["assignments"]}
        self.assertEqual(placed["10100"], ("101", True))
        self.assertEqual(placed["10500"], ("102", False))
        self.assertEqual([row["tn"] for row in result["unplaced"]], ["10300"])
        self.assertEqual(result["parts"][0]["off_standard"], 1)

    def test_overflow_lists_what_does_not_fit_and_suggests_which_part_to_leave_out(self):
        machine = stations([("101", "A"), ("102", "A")])
        parts = [part("BIG", [("101", "10100", None), ("102", "10200", None), ("103", "10300", None)]),
                 part("SMALL", [("101", "10100", None)])]
        sizes = {"10100": "A", "10200": "A", "10300": "A"}
        result = st.design(machine, parts, sizes)
        self.assertEqual((result["tools"], result["placed"], len(result["unplaced"])), (3, 2, 1))
        steps = st.suggest_removals(machine, parts, sizes)
        self.assertEqual(steps[0]["remove"], "BIG")
        self.assertEqual(steps[-1]["unplaced_after"], 0)

    def test_the_same_tn_at_two_angles_needs_two_stations_and_the_s_suffix_is_kept(self):
        result = st.design(
            stations([("101", "A"), ("102", "A")]),
            [part("P1", [("101", "10100", "0"), ("102s", "10100", "90")])],
            {"10100": "A"},
        )
        self.assertEqual(result["tools"], 2)
        labels = sorted(row["new_station"] for row in result["parts"][0]["rows"])
        self.assertEqual(labels, ["101", "102s"])


class StandardApiTests(unittest.TestCase):
    def setUp(self):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        import workflow_api
        from models.inventory import Base, EstacionMaquina, Herramienta, Maquina, ProgramaParte, Ubicacion

        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine, expire_on_commit=False)()
        now = workflow_api.utcnow()
        self.db.add(Ubicacion(codigo="T-1", nombre="T-1", tipo="machine", activa=True))
        self.db.add(Maquina(codigo="T-1", tipo_maquina="4 Index", espesor_min="0.040", espesor_max="0.100"))
        for number, size, state in (("101", "A", "disponible"), ("102", "A", "danada"), ("103", "A", "disponible")):
            self.db.add(EstacionMaquina(maquina_codigo="T-1", numero=number, tamano=size, estado=state))
        self.db.add(Herramienta(id="P1", tn="10100", size="A", categoria="punzon"))
        for part, thickness, reqs in (
            ("MAKE-A_00-SW", "0.075", [("101", "10100"), ("103", "10200")]),
            ("MAKE-B_00-SW", "0.150", [("101s", "10100")]),
        ):
            self.db.add(ProgramaParte(
                numero_parte=part, tipo_maquina="4I", tipo_programa="NPI", revision=1, thickness=thickness, contenido="x",
                contenido_hash=part, actualizado_por="eng", actualizado_en=now,
                requisitos_json=__import__("json").dumps([{"station": s, "tn": t, "angle": None} for s, t in reqs]),
            ))
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_design_skips_damaged_stations_warns_about_thickness_and_unknown_parts(self):
        import standard_api

        result = standard_api.build_design(self.db, "t-1", ["MAKE-A_00-SW", "MAKE-B_00-SW", "NOPE"])
        self.assertEqual((result["stations_available"], result["stations_damaged"], result["placed"]), (2, 1, 2))
        self.assertEqual(result["skipped"], [{"part": "NOPE", "reason": "no 4I program"}])
        self.assertTrue(any("MAKE-B_00-SW" in text and "outside the range" in text for text in result["warnings"]))
        self.assertTrue(any("10200" in text for text in result["warnings"]))  # no tool size in the inventory
        rows = {row["tn"]: row["station"] for row in result["assignments"]}
        self.assertEqual(rows["10100"], "101")


if __name__ == "__main__":
    unittest.main()
