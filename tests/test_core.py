import io
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from tiny_ip_geolocation_loader import (  # noqa: E402
    IPRange,
    IPRangeIndex,
    ip_to_int,
    load_csv,
)


HEADER = "start,end,country\n"


class IpToIntTests(unittest.TestCase):
    def test_zero(self):
        self.assertEqual(ip_to_int("0.0.0.0"), 0)

    def test_max(self):
        self.assertEqual(ip_to_int("255.255.255.255"), 0xFFFFFFFF)

    def test_whitespace_stripped(self):
        self.assertEqual(ip_to_int("  10.0.0.1  "), ip_to_int("10.0.0.1"))

    def test_rejects_ipv6(self):
        with self.assertRaises(ValueError):
            ip_to_int("::1")

    def test_rejects_garbage(self):
        with self.assertRaises(ValueError):
            ip_to_int("not-an-ip")

    def test_rejects_out_of_range_octet(self):
        with self.assertRaises(ValueError):
            ip_to_int("256.0.0.1")


class IPRangeTests(unittest.TestCase):
    def test_contains_inclusive(self):
        r = IPRange(start=10, end=20, country="AA")
        self.assertTrue(r.contains(10))
        self.assertTrue(r.contains(20))
        self.assertFalse(r.contains(9))
        self.assertFalse(r.contains(21))

    def test_end_before_start_rejected(self):
        with self.assertRaises(ValueError):
            IPRange(start=20, end=10, country="AA")

    def test_empty_country_rejected(self):
        with self.assertRaises(ValueError):
            IPRange(start=1, end=2, country="")


class IndexLookupTests(unittest.TestCase):
    def setUp(self):
        self.index = IPRangeIndex(
            [
                IPRange(start=ip_to_int("10.0.0.0"), end=ip_to_int("10.0.0.255"), country="AA"),
                IPRange(start=ip_to_int("192.168.0.0"), end=ip_to_int("192.168.0.255"), country="BB"),
                IPRange(start=ip_to_int("203.0.113.0"), end=ip_to_int("203.0.113.255"), country="CC"),
            ]
        )

    def test_hit_inside(self):
        self.assertEqual(self.index.lookup("10.0.0.5"), "AA")

    def test_hit_at_lower_bound(self):
        self.assertEqual(self.index.lookup("192.168.0.0"), "BB")

    def test_hit_at_upper_bound(self):
        self.assertEqual(self.index.lookup("203.0.113.255"), "CC")

    def test_miss_between_ranges(self):
        self.assertIsNone(self.index.lookup("11.0.0.1"))

    def test_miss_below_all(self):
        self.assertIsNone(self.index.lookup("9.255.255.255"))

    def test_miss_above_all(self):
        self.assertIsNone(self.index.lookup("255.0.0.1"))

    def test_empty_index_returns_none(self):
        self.assertIsNone(IPRangeIndex([]).lookup("10.0.0.1"))

    def test_unsorted_input_is_sorted(self):
        idx = IPRangeIndex(
            [
                IPRange(start=100, end=200, country="LATE"),
                IPRange(start=10, end=20, country="EARLY"),
            ]
        )
        self.assertEqual(idx.lookup_int(15), "EARLY")
        self.assertEqual(idx.lookup_int(150), "LATE")

    def test_lookup_int_rejects_out_of_range(self):
        self.assertIsNone(self.index.lookup_int(-1))
        self.assertIsNone(self.index.lookup_int(0x1_0000_0000))

    def test_lookup_invalid_address_raises(self):
        with self.assertRaises(ValueError):
            self.index.lookup("300.1.1.1")

    def test_overlapping_ranges_later_start_wins(self):
        idx = IPRangeIndex(
            [
                IPRange(start=10, end=100, country="WIDE"),
                IPRange(start=50, end=60, country="NARROW"),
            ]
        )
        self.assertEqual(idx.lookup_int(55), "NARROW")
        # Only the rightmost range whose start is <= the query is checked, so
        # a query inside WIDE but past NARROW's end does not fall back to WIDE.
        self.assertIsNone(idx.lookup_int(70))

    def test_len_and_ranges_property(self):
        self.assertEqual(len(self.index), 3)
        self.assertEqual(len(self.index.ranges), 3)
        self.assertEqual(self.index.ranges[0].country, "AA")


class LoadCsvTests(unittest.TestCase):
    def test_basic_load(self):
        data = HEADER + (
            "10.0.0.0,10.0.0.255,AA\n"
            "192.168.0.0,192.168.0.255,BB\n"
        )
        idx = load_csv(io.StringIO(data))
        self.assertEqual(len(idx), 2)
        self.assertEqual(idx.lookup("10.0.0.1"), "AA")
        self.assertEqual(idx.lookup("192.168.0.1"), "BB")

    def test_column_order_is_not_assumed(self):
        data = "country,end,start\nAA,10.0.0.255,10.0.0.0\n"
        idx = load_csv(io.StringIO(data))
        self.assertEqual(idx.lookup("10.0.0.1"), "AA")

    def test_header_case_and_whitespace_tolerated(self):
        data = " Start , END , Country \n10.0.0.0,10.0.0.255,AA\n"
        idx = load_csv(io.StringIO(data))
        self.assertEqual(idx.lookup("10.0.0.1"), "AA")

    def test_blank_lines_skipped(self):
        data = HEADER + "\n10.0.0.0,10.0.0.255,AA\n\n   \n"
        idx = load_csv(io.StringIO(data))
        self.assertEqual(len(idx), 1)

    def test_empty_stream_yields_empty_index(self):
        idx = load_csv(io.StringIO(""))
        self.assertEqual(len(idx), 0)

    def test_header_only_yields_empty_index(self):
        idx = load_csv(io.StringIO(HEADER))
        self.assertEqual(len(idx), 0)

    def test_missing_column_raises(self):
        with self.assertRaises(ValueError):
            load_csv(io.StringIO("start,end\n10.0.0.0,10.0.0.1\n"))

    def test_short_row_raises(self):
        with self.assertRaises(ValueError):
            load_csv(io.StringIO(HEADER + "10.0.0.0,AA\n"))

    def test_bad_address_reports_line_number(self):
        data = HEADER + "10.0.0.0,10.0.0.255,AA\n" + "nope,10.0.0.255,BB\n"
        with self.assertRaises(ValueError) as ctx:
            load_csv(io.StringIO(data))
        self.assertIn("line 3", str(ctx.exception))

    def test_reversed_range_reports_line_number(self):
        data = HEADER + "10.0.0.255,10.0.0.0,AA\n"
        with self.assertRaises(ValueError) as ctx:
            load_csv(io.StringIO(data))
        self.assertIn("line 2", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
