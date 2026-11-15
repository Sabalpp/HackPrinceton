import unittest

from farcare_collector.integrations.fitbit_hr import decode_bpm


class DecoderTests(unittest.TestCase):
    def test_uint8(self):
        self.assertEqual(decode_bpm(bytes.fromhex("0048")), 72)

    def test_uint16_little_endian(self):
        self.assertEqual(decode_bpm(bytes.fromhex("012c01")), 300)

    def test_other_flags_and_trailing_fields(self):
        self.assertEqual(decode_bpm(bytes.fromhex("1e4800000000")), 72)

    def test_truncated(self):
        for payload in (b"", b"\x00", b"\x01", b"\x01\x48"):
            with self.assertRaises(ValueError):
                decode_bpm(payload)


if __name__ == "__main__":
    unittest.main()
