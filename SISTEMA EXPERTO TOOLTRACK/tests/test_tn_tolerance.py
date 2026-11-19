import unittest
from decimal import Decimal

from tn_tolerance import describe_difference, is_round, parse_tn, tn_compatible


class TnToleranceTests(unittest.TestCase):
    def test_dimensions_are_decoded_from_series_one_to_four(self):
        self.assertEqual(parse_tn("10230"), ("1", (Decimal("0.230"),)))
        self.assertEqual(parse_tn("20125"), ("2", (Decimal("0.125"),)))
        self.assertEqual(parse_tn("30236.116"), ("3", (Decimal("0.236"), Decimal("0.116"))))
        self.assertEqual(parse_tn("31750.25"), ("3", (Decimal("1.750"), Decimal("0.250"))))
        self.assertEqual(parse_tn("30750.1"), ("3", (Decimal("0.750"), Decimal("0.1"))))

    def test_tns_without_a_dimension_are_not_decoded(self):
        for tn in ("78454.88", "62400.2051", "4018.18", "101666", "", None, "ABC"):
            self.assertIsNone(parse_tn(tn), tn)

    def test_the_gap_applies_to_every_dimension(self):
        self.assertTrue(tn_compatible("30350.25", "30352.252"))
        self.assertTrue(tn_compatible("10230", "10233"))
        self.assertFalse(tn_compatible("10230", "10234"))
        self.assertFalse(tn_compatible("30350.25", "30352.254"))
        self.assertTrue(tn_compatible("10230", "10234", Decimal("0.005")))

    def test_different_shapes_and_special_tns_only_match_exactly(self):
        self.assertFalse(tn_compatible("10230", "20230"))
        self.assertFalse(tn_compatible("78454.88", "78454.89"))
        self.assertTrue(tn_compatible("78454.88", "78454.88"))

    def test_helpers(self):
        self.assertTrue(is_round("10230"))
        self.assertFalse(is_round("20230"))
        self.assertEqual(describe_difference("30350.25", "30352.252"), "+0.002 x +0.002")
        self.assertIsNone(describe_difference("10230", "10230"))


if __name__ == "__main__":
    unittest.main()
