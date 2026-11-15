"""Offline analysis tests; all synthetic readings remain in memory or temporary dirs."""

import contextlib
import hashlib
import io
import json
import stat
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from farcare_collector.health import hr_analysis as analysis

START = datetime(2026, 1, 1, tzinfo=timezone.utc)


def row(second, bpm=70, **extra):
    result = {
        "receipt_timestamp": analysis.stamp(START + timedelta(seconds=second)),
        "bpm": bpm,
        "payload_hex": "01" + int(bpm).to_bytes(2, "little").hex(),
        "characteristic_uuid": analysis.HR_MEASUREMENT,
    }
    result.update(extra)
    return result


def encoded(rows):
    return ("\n".join(json.dumps(record) for record in rows) + "\n").encode()


class RecordingAnalysisTests(unittest.TestCase):
    def test_verifies_uint8_and_uint16_payloads(self):
        report = analysis.analyze_bytes(encoded([row(0, 72, payload_hex="0048"), row(1, 300)]))
        self.assertEqual(report["quality"]["payload_verified_usable_readings"], 2)
        self.assertEqual(report["summary"]["min_bpm"], 72)
        self.assertEqual(report["summary"]["max_bpm"], 300)
        self.assertEqual(
            report["source"]["sha256"],
            hashlib.sha256(encoded([row(0, 72, payload_hex="0048"), row(1, 300)])).hexdigest(),
        )

    def test_payload_disagreement_is_excluded_not_silently_repaired(self):
        report = analysis.analyze_bytes(encoded([row(0, 72), row(1, 73, payload_hex="014800")]))
        self.assertEqual(report["quality"]["payload_mismatch_rows"], 1)
        self.assertEqual(report["quality"]["rejected_rows"], 1)
        self.assertEqual(report["quality"]["usable_readings"], 1)
        self.assertEqual(report["summary"]["mean_bpm"], 72)

    def test_missing_payload_is_distinguished_from_verified_measurements(self):
        item = row(0)
        del item["payload_hex"]
        report = analysis.analyze_bytes(encoded([item]))
        self.assertEqual(report["quality"]["usable_readings"], 1)
        self.assertEqual(report["quality"]["payload_missing_rows"], 1)
        self.assertEqual(report["quality"]["payload_verified_usable_readings"], 0)

    def test_malformed_json_timestamp_values_and_payloads_are_counted(self):
        records = [
            None,
            row(1, receipt_timestamp="2026-01-01T00:00:00"),
            row(2, bpm=True),
            row(3, payload_hex="01"),
            row(4, 0),
            row(5, characteristic_uuid="unrelated-characteristic"),
            {**row(6), "bpm": 10**400},
        ]
        data = encoded(records) + b"{bad json}\n\xff\n\n"
        report = analysis.analyze_bytes(data)
        q = report["quality"]
        self.assertEqual(q["input_rows"], 9)
        self.assertEqual(q["rejected_rows"], 9)
        self.assertEqual(q["invalid_json_rows"], 2)
        self.assertEqual(q["invalid_timestamp_rows"], 1)
        self.assertEqual(q["invalid_bpm_rows"], 2)
        self.assertEqual(q["malformed_payload_rows"], 1)
        self.assertEqual(q["zero_bpm_rows"], 1)
        self.assertEqual(q["wrong_characteristic_rows"], 1)
        self.assertEqual(q["blank_lines"], 1)

    def test_duplicate_and_conflicting_timestamps_do_not_double_count(self):
        report = analysis.analyze_bytes(
            encoded([row(0, 70), row(0, 70), row(1, 71), row(1, 72), row(2, 73)])
        )
        q = report["quality"]
        self.assertEqual(q["duplicate_rows"], 1)
        self.assertEqual(q["conflicting_timestamp_rows"], 2)
        self.assertEqual(q["usable_readings"], 2)
        self.assertEqual([item["bpm"] for item in report["readings"]], [70, 73])
        self.assertEqual(
            q["usable_readings"] + q["duplicate_rows"] + q["rejected_rows"], q["input_rows"]
        )

    def test_duplicate_prefers_payload_verified_measurement(self):
        missing = row(0)
        del missing["payload_hex"]
        report = analysis.analyze_bytes(encoded([missing, row(0)]))
        self.assertEqual(report["quality"]["payload_verified_usable_readings"], 1)
        self.assertEqual(report["quality"]["duplicate_rows"], 1)

    def test_out_of_order_input_is_reported_and_sorted(self):
        report = analysis.analyze_bytes(encoded([row(2, 72), row(0, 70), row(1, 71)]))
        self.assertEqual(report["quality"]["out_of_order_rows"], 1)
        self.assertEqual([item["bpm"] for item in report["readings"]], [70, 71, 72])
        self.assertEqual(report["summary"]["median_interval_seconds"], 1)

    def test_gaps_are_reported_and_break_chart_segments(self):
        report = analysis.analyze_bytes(encoded([row(0), row(1), row(12), row(13)]))
        self.assertEqual(report["summary"]["gaps_over_threshold"], 1)
        self.assertEqual(report["gaps"][0]["seconds"], 11)
        self.assertEqual(analysis.chart_svg(report).count("<polyline"), 2)

    def test_only_sustained_changes_create_review_intervals(self):
        samples = [row(t, 70) for t in range(100)]
        samples += [row(100, 130)]
        samples += [row(t, 70) for t in range(101, 130)]
        samples += [row(t, 90) for t in range(130, 161)]
        samples += [row(t, 70) for t in range(161, 190)]
        report = analysis.analyze_bytes(encoded(samples))
        self.assertEqual(len(report["review_events"]), 1)
        event = report["review_events"][0]
        self.assertEqual(event["duration_seconds"], 30)
        self.assertEqual(event["reading_count"], 31)
        self.assertEqual(event["max_bpm"], 90)
        self.assertFalse(report["review_rule"]["clinical_threshold"])

    def test_recording_gap_resets_sustained_change(self):
        samples = [row(t, 70) for t in range(100)]
        samples += [row(t, 90) for t in range(100, 125)]
        samples += [row(t, 90) for t in range(140, 165)]
        report = analysis.analyze_bytes(encoded(samples))
        self.assertEqual(report["review_events"], [])
        self.assertEqual(len(report["gaps"]), 1)

    def test_rule_is_configurable_without_medical_cutoffs(self):
        samples = [row(t, 70) for t in range(100)] + [row(t, 80) for t in range(100, 111)]
        report = analysis.analyze_bytes(
            encoded(samples), analysis.Config(review_delta_bpm=8, review_min_seconds=10)
        )
        self.assertEqual(len(report["review_events"]), 1)
        self.assertEqual(report["review_rule"]["effective_distance_bpm"], 8)

    def test_empty_and_single_point_reports_render_without_nonfinite_values(self):
        for data in (b"", encoded([row(0)])):
            report = analysis.analyze_bytes(data)
            json.dumps(report, allow_nan=False)
            text = analysis.render_html(report)
            self.assertIn("Interpretation boundaries", text)
            self.assertNotIn("<script", text)
            self.assertNotIn("https://", text)
            self.assertIsNone(report["summary"]["end_minus_start_window_median_bpm"])

    def test_no_beat_interval_or_health_inferences_are_created(self):
        report = analysis.analyze_bytes(encoded([row(t, 70 + t % 4) for t in range(200)]))
        self.assertNotIn("hrv", report)
        self.assertNotIn("diagnosis", report)
        self.assertNotIn("risk_score", report)
        self.assertTrue(any("HRV" in item for item in report["limitations"]))

    def test_invalid_rule_parameters_are_rejected(self):
        for invalid in (0, -1, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                analysis.analyze_bytes(b"", analysis.Config(gap_seconds=invalid))

    def test_timezone_offsets_are_normalized_before_deduplication(self):
        report = analysis.analyze_bytes(
            encoded([row(0), row(0, receipt_timestamp="2025-12-31T19:00:00-05:00")])
        )
        self.assertEqual(report["quality"]["duplicate_rows"], 1)
        self.assertEqual(report["quality"]["usable_readings"], 1)


class ArtifactTests(unittest.TestCase):
    def test_cli_creates_private_reproducible_artifacts_without_mutating_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "live.jsonl"
            source.write_bytes(encoded([row(t, 70 + t % 3) for t in range(10)]))
            original = source.read_bytes()
            output = root / "demo"
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(
                    analysis.cli(["--input", str(source), "--output-dir", str(output)]), 0
                )
            self.assertEqual(source.read_bytes(), original)
            self.assertEqual((output / "source_snapshot.jsonl").read_bytes(), original)
            report = json.loads((output / "analysis.json").read_text())
            self.assertEqual(report["source"]["sha256"], hashlib.sha256(original).hexdigest())
            for name in (
                "analysis.json",
                "report.html",
                "heart_rate_trend.svg",
                "source_snapshot.jsonl",
            ):
                self.assertEqual(stat.S_IMODE((output / name).stat().st_mode), 0o600)
            # New live input must not silently replace the immutable snapshot.
            source.write_bytes(original + encoded([row(20)]))
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(
                    analysis.cli(["--input", str(source), "--output-dir", str(output)]), 1
                )
            self.assertEqual((output / "source_snapshot.jsonl").read_bytes(), original)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(
                    analysis.cli(
                        [
                            "--input",
                            str(output / "source_snapshot.jsonl"),
                            "--output-dir",
                            str(output),
                        ]
                    ),
                    0,
                )

    def test_missing_input_fails_without_creating_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(
                    analysis.cli(
                        ["--input", str(root / "missing"), "--output-dir", str(root / "demo")]
                    ),
                    1,
                )
            self.assertFalse((root / "demo").exists())


if __name__ == "__main__":
    unittest.main()
