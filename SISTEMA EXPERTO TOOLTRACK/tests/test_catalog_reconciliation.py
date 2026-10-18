import unittest

from catalog_reconciliation import preview_legacy_reconciliation


class CatalogReconciliationTests(unittest.TestCase):
    def test_preview_identifies_link_candidate_without_changing_input_records(self):
        tools = [{
            "id": "PHYS-1",
            "tn": "TN-100",
            "size": "a",
            "type": "round",
            "categoria": "punch",
            "clearance": None,
        }]
        result = preview_legacy_reconciliation(
            tools,
            ["TN-100"],
            [{"tn": "TN-100", "size": "A", "type": "ROUND"}],
            [{"id": 10, "tn": "TN-100", "size": "A", "categoria": "punzon", "clearance": None}],
            [],
        )

        self.assertTrue(result["read_only"])
        self.assertEqual(result["summary"]["ready_to_link"], 1)
        self.assertEqual(result["items"][0]["proposed_action"], "link_to_existing_variant")
        self.assertIsNone(tools[0].get("variante_id"))

    def test_preview_marks_missing_catalog_entries_as_proposed_only(self):
        result = preview_legacy_reconciliation(
            [{
                "id": "DIE-1",
                "tn": "TN-200",
                "size": "B",
                "type": "SQUARE",
                "categoria": "die",
                "clearance": "0.020",
            }],
            [],
            [],
            [],
            [],
        )

        item = result["items"][0]
        self.assertEqual(item["status"], "catalog_entry_required")
        self.assertEqual(
            item["proposed_action"],
            "create_missing_catalog_entries_then_link",
        )
        self.assertIn("TN", item["detail"])
        self.assertIn("tool variant", item["detail"])

    def test_type_conflicts_in_legacy_configuration_require_review(self):
        tools = [
            {
                "id": "PUNCH-1",
                "tn": "TN-300",
                "size": "A",
                "type": "ROUND",
                "categoria": "punzon",
            },
            {
                "id": "DIE-1",
                "tn": "TN-300",
                "size": "A",
                "type": "SQUARE",
                "categoria": "matriz",
                "clearance": "0.010",
            },
        ]
        result = preview_legacy_reconciliation(tools, [], [], [], [])

        self.assertEqual(result["summary"]["requires_review"], 2)
        self.assertTrue(all(item["proposed_action"] == "none" for item in result["items"]))
        self.assertTrue(all("disagree on TYPE" in item["detail"] for item in result["items"]))

    def test_existing_link_is_only_reported_as_already_linked_when_matching(self):
        result = preview_legacy_reconciliation(
            [{
                "id": "PUNCH-1",
                "tn": "TN-400",
                "size": "C",
                "type": "ROUND",
                "categoria": "punzon",
            }],
            ["TN-400"],
            [{"tn": "TN-400", "size": "C", "type": "ROUND"}],
            [{"id": 20, "tn": "TN-400", "size": "C", "categoria": "punzon", "clearance": None}],
            [{"herramienta_id": "PUNCH-1", "variante_id": 20}],
        )

        self.assertEqual(result["summary"]["already_linked"], 1)
        self.assertEqual(result["items"][0]["proposed_action"], "none")

    def test_non_tn_catalog_categories_are_excluded_and_invalid_die_clearance_requires_review(self):
        result = preview_legacy_reconciliation(
            [
                {"id": "HOLDER-1", "tn": "", "categoria": "holder"},
                {"id": "ACCESSORY-1", "tn": "", "categoria": "accessory"},
                {
                    "id": "DIE-1",
                    "tn": "TN-500",
                    "size": "A",
                    "type": "ROUND",
                    "categoria": "matriz",
                    "clearance": "not-a-number",
                },
            ],
            [],
            [],
            [],
            [],
        )

        self.assertEqual(result["summary"]["requires_review"], 1)
        self.assertEqual(result["summary"]["out_of_scope"], 2)
        self.assertIn("outside the TN", result["items"][0]["detail"])
        self.assertIn("outside the TN", result["items"][1]["detail"])
        self.assertIn("CLEARANCE", result["items"][2]["detail"])

    def test_unknown_legacy_category_requires_review(self):
        result = preview_legacy_reconciliation(
            [{"id": "UNKNOWN-1", "categoria": "other"}],
            [],
            [],
            [],
            [],
        )

        self.assertEqual(result["summary"]["requires_review"], 1)
        self.assertIn("not recognized", result["items"][0]["detail"])


if __name__ == "__main__":
    unittest.main()
