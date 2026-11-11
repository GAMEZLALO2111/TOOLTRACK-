import unittest

from fastapi import HTTPException

import scan_codes


class ParseScanTests(unittest.TestCase):
    def test_bare_values_are_accepted_so_typing_always_works(self):
        scan = scan_codes.parse_scan("  123755 ")
        self.assertEqual((scan.kind, scan.code), (None, "123755"))

    def test_scanner_terminators_are_ignored(self):
        for suffix in ("\r", "\n", "\r\n", "\t", "\x1d"):
            self.assertEqual(scan_codes.parse_scan(f"M00001{suffix}").code, "M00001")

    def test_prefixes_identify_the_kind_ignoring_case(self):
        cases = {
            "TOOLTRACK-TOOL:123755": ("tool", "123755"),
            "tooltrack-tool: 123755": ("tool", "123755"),
            "TOOLTRACK-LOCATION:TOGU 1": ("location", "TOGU 1"),
            "TOOLTRACK-MACHINE:T-115": ("machine", "T-115"),
            "TOOLTRACK-CART:C-01": ("cart", "C-01"),
            "TOOLTRACK-KIT:abc": ("kit", "abc"),
        }
        for raw, expected in cases.items():
            scan = scan_codes.parse_scan(raw)
            self.assertEqual((scan.kind, scan.code), expected, raw)

    def test_legacy_code128_label_reads_as_a_tool(self):
        scan = scan_codes.parse_scan("123755|TN10248|A|ROUND|1.200|SO-1")
        self.assertEqual((scan.kind, scan.code), ("tool", "123755"))

    def test_tool_code_rejects_a_label_of_another_kind(self):
        self.assertEqual(scan_codes.tool_code("TOOLTRACK-TOOL:123755"), "123755")
        self.assertEqual(scan_codes.tool_code("123755"), "123755")
        for wrong in ("TOOLTRACK-LOCATION:R-06-B", "TOOLTRACK-MACHINE:T-115", "TOOLTRACK-KIT:abc"):
            with self.assertRaises(HTTPException) as error:
                scan_codes.tool_code(wrong)
            self.assertEqual(error.exception.status_code, 422)
            self.assertIn("not a tool", error.exception.detail)

    def test_location_code_is_uppercase_and_accepts_machines_and_carts(self):
        self.assertEqual(scan_codes.location_code("togu 1"), "TOGU 1")
        self.assertEqual(scan_codes.location_code("TOOLTRACK-LOCATION:r-06-b"), "R-06-B")
        self.assertEqual(scan_codes.location_code("TOOLTRACK-MACHINE:T-115"), "T-115")
        with self.assertRaises(HTTPException):
            scan_codes.location_code("TOOLTRACK-TOOL:123755")

    def test_machine_code_accepts_the_bare_code_and_both_prefixes(self):
        self.assertEqual(scan_codes.machine_code("t-115"), "T-115")
        self.assertEqual(scan_codes.machine_code("TOOLTRACK-MACHINE:T-115"), "T-115")
        self.assertEqual(scan_codes.machine_code("TOOLTRACK-LOCATION:T-115"), "T-115")
        with self.assertRaises(HTTPException):
            scan_codes.machine_code("TOOLTRACK-KIT:abc")

    def test_capture_mode_only_keeps_known_values(self):
        self.assertEqual(scan_codes.capture_mode("scan"), "scan")
        self.assertEqual(scan_codes.capture_mode(" Manual "), "manual")
        self.assertEqual(scan_codes.capture_mode("camera"), "camera")
        for unknown in (None, "", "voice", 5):
            self.assertIsNone(scan_codes.capture_mode(unknown))


if __name__ == "__main__":
    unittest.main()
