import unittest
from decimal import Decimal

from setup_workflow import (
    SetupParseError,
    SetupReviewRequired,
    clearance_range_for_thickness,
    compare_setup_requirements,
    parse_setup_text,
    validate_setup_requirements,
)


CURRENT_SETUP = """\
SAMPLE PRESS - SETTING INFORMATION

DRAWING NAME      : PART-CURRENT
MATERIAL          : STEEL-A
THICKNESS         : 0.075 in

TOOL LIST

TOOL   TYPE                 SIZE             RADS    ANGLE   DIE/CL EHL   Tool No.
 201s RECTANGULAR       0.236 x   0.116 in    -      90.000   0.022  -  30236.116
 202  RECTANGULAR       1.750 x   0.250 in    -        -      0.022  -  31750.25
 311  SQUARE            0.500 x   0.500 in    -        -       -     -  20500

EXTRA NOTES
"""

NEW_SETUP = """\
SAMPLE PRESS - SETTING INFORMATION

DRAWING NAME      : PART-NEW
MATERIAL          : STEEL-B
THICKNESS         : 0.119 in

TOOL LIST

TOOL   TYPE                 SIZE             RADS    ANGLE   DIE/CL EHL   Tool No.
 101s RECTANGULAR       0.236 x   0.116 in    -      90.000   0.022  -  30236.116
 222  CIRCULAR           0.500 in dia         -        -       -     -  20500
 303  RECTANGULAR       1.000 x   0.250 in    -      45.000   0.018  -  31000.25

EXTRA NOTES
"""


class SetupWorkflowTests(unittest.TestCase):
    def test_parse_extracts_setup_metadata_station_tn_and_angle(self):
        setup = parse_setup_text(CURRENT_SETUP, "current.stp")

        self.assertEqual(setup.source_name, "current.stp")
        self.assertEqual(setup.drawing_name, "PART-CURRENT")
        self.assertEqual(setup.material, "STEEL-A")
        self.assertEqual(setup.thickness, Decimal("0.075"))
        self.assertEqual(
            [(item.station, item.tn, item.angle) for item in setup.requirements],
            [
                ("201s", "30236.116", "90"),
                ("202", "31750.25", None),
                ("311", "20500", None),
            ],
        )

    def test_parse_fails_explicitly_when_tool_list_or_thickness_is_invalid(self):
        with self.assertRaisesRegex(SetupParseError, "TOOL LIST"):
            parse_setup_text("THICKNESS : 0.075 in\nEXTRA NOTES")

        with self.assertRaisesRegex(SetupParseError, "THICKNESS"):
            parse_setup_text(CURRENT_SETUP.replace("0.075 in", "unknown"))

    def test_duplicate_tn_and_angle_blocks_setup(self):
        duplicate = CURRENT_SETUP.replace(
            " 311  SQUARE            0.500 x   0.500 in    -        -       -     -  20500",
            " 311  SQUARE            0.500 x   0.500 in    -      90.000   -     -  20500",
        ).replace(
            " 202  RECTANGULAR       1.750 x   0.250 in    -        -      0.022  -  31750.25",
            " 202  RECTANGULAR       1.750 x   0.250 in    -      90.000   0.022  -  20500",
        )
        setup = parse_setup_text(duplicate)

        with self.assertRaisesRegex(SetupParseError, "Duplicate TN"):
            validate_setup_requirements(setup)

    def test_repeated_tn_without_angle_requires_manual_review(self):
        repeated_without_angle = CURRENT_SETUP.replace(
            " 311  SQUARE            0.500 x   0.500 in    -        -       -     -  20500",
            " 311  SQUARE            0.500 x   0.500 in    -      45.000   -     -  20500",
        ).replace(
            " 202  RECTANGULAR       1.750 x   0.250 in    -        -      0.022  -  31750.25",
            " 202  RECTANGULAR       1.750 x   0.250 in    -        -      0.022  -  20500",
        )
        setup = parse_setup_text(repeated_without_angle)

        with self.assertRaisesRegex(SetupReviewRequired, "manual review"):
            validate_setup_requirements(setup)

    def test_same_tn_at_different_angles_is_allowed_and_compared_by_tn_angle(self):
        current = parse_setup_text(CURRENT_SETUP)
        new = parse_setup_text(NEW_SETUP)
        comparison = compare_setup_requirements(current, new)

        self.assertIn(("30236.116", "90"), {item.key for item in comparison["unchanged"]})
        self.assertIn(("20500", None), {item.key for item in comparison["unchanged"]})
        self.assertTrue(comparison["thickness_changed"])
        self.assertEqual({item.tn for item in comparison["added"]}, {"31000.25"})
        self.assertEqual({item.tn for item in comparison["removed"]}, {"31750.25"})

    def test_same_tn_at_different_angles_is_two_distinct_requirements(self):
        setup_text = CURRENT_SETUP.replace(
            " 201s RECTANGULAR       0.236 x   0.116 in    -      90.000   0.022  -  30236.116",
            " 201s RECTANGULAR       0.236 x   0.116 in    -      45.000   0.022  -  30236.116",
        ).replace(
            "EXTRA NOTES",
            " 401  RECTANGULAR       0.236 x   0.116 in    -      90.000   0.022  -  30236.116\n\nEXTRA NOTES",
        )
        setup = parse_setup_text(setup_text)
        validate_setup_requirements(setup)

        self.assertEqual(
            {item.key for item in setup.requirements if item.tn == "30236.116"},
            {("30236.116", "45"), ("30236.116", "90")},
        )

    def test_thickness_bands_include_lower_exclude_upper_and_close_last_upper(self):
        self.assertEqual(
            clearance_range_for_thickness(Decimal("0.020")),
            (Decimal("0.004"), Decimal("0.006")),
        )
        self.assertEqual(
            clearance_range_for_thickness(Decimal("0.036")),
            (Decimal("0.006"), Decimal("0.008")),
        )
        self.assertEqual(
            clearance_range_for_thickness(Decimal("0.120")),
            (Decimal("0.018"), Decimal("0.022")),
        )
        self.assertIsNone(clearance_range_for_thickness(Decimal("0.019")))
        self.assertIsNone(clearance_range_for_thickness(Decimal("0.121")))


if __name__ == "__main__":
    unittest.main()
