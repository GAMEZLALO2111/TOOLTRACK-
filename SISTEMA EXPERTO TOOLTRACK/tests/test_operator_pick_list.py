import unittest

from workflow_ui import (
    build_operator_pick_list,
    find_open_kit_for_location,
)


class OperatorPickListTests(unittest.TestCase):
    def test_open_kit_is_found_by_selected_cart_even_for_another_machine(self):
        kits = [
            {"id": "kit-1", "machine": "T-104", "cart": "C-02"},
            {"id": "kit-2", "machine": "T-101", "cart": "C-01"},
        ]

        kit = find_open_kit_for_location(kits, "T-101", "C-02")

        self.assertEqual(kit["id"], "kit-1")
        self.assertIsNone(find_open_kit_for_location(kits, "T-110", "C-03"))

    def test_list_contains_only_items_to_supply_and_matrices_to_verify(self):
        comparison = {
            "new_setup": {
                "clearance_min": "0.018",
                "clearance_max": "0.022",
            },
            "added": [
                {"station": "301", "tn": "31000.25", "angle": "45"},
            ],
            "unchanged": [
                {
                    "station": "101",
                    "tn": "30236.116",
                    "angle": "90",
                    "die_action": "verify_clearance",
                },
                {
                    "station": "102",
                    "tn": "20500",
                    "angle": None,
                    "die_action": "keep",
                },
            ],
            "removed": [
                {"station": "201", "tn": "31750.25", "angle": None},
            ],
        }

        items = build_operator_pick_list(comparison)

        self.assertEqual(
            [(item["tn"], item["category"], item["kind"]) for item in items],
            [
                ("30236.116", "Matriz", "verify"),
                ("31000.25", "Matriz", "supply"),
                ("31000.25", "Punzón", "supply"),
            ],
        )
        self.assertTrue(all(item["tn"] != "31750.25" for item in items))
        self.assertTrue(all(item["tn"] != "20500" for item in items))
        die_to_supply = next(
            item for item in items
            if item["tn"] == "31000.25" and item["category"] == "Matriz"
        )
        self.assertEqual(die_to_supply["specification"], "0.018–0.022 in")
        self.assertEqual(die_to_supply["action"], "Surtir al carro")

    def test_out_of_range_thickness_requires_manual_matrix_validation(self):
        comparison = {
            "new_setup": {
                "clearance_min": None,
                "clearance_max": None,
            },
            "added": [],
            "unchanged": [
                {
                    "station": "101",
                    "tn": "30236.116",
                    "angle": "90",
                    "die_action": "manual_die_selection",
                },
            ],
            "removed": [],
        }

        items = build_operator_pick_list(comparison)

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["specification"], "Revisión manual requerida")
        self.assertEqual(items[0]["action"], "Confirmar matriz con ingeniería")


if __name__ == "__main__":
    unittest.main()
