"""Offline tests: every record here is synthetic and no patient data is read."""

import copy
import json
import unittest
from datetime import date, timedelta

from farcare_collector.health.health_features import build_features


def measurement(day="2026-01-01", value=70.0, metric="heart_rate_bpm", **overrides):
    item = {
        "event_id": day + ":" + metric,
        "subject_id": "synthetic-subject",
        "source": "google_health",
        "source_stream": "synthetic-stream",
        "metric": metric,
        "observed_at": day + "T12:00:00Z",
        "ended_at": None,
        "local_date": day,
        "value": value,
        "unit": "bpm",
        "kind": "sample",
        "attributes": {},
    }
    item.update(overrides)
    return item


def interval(event_id, start, end, value=10.0, metric="steps", **overrides):
    item = measurement(
        start[:10],
        value,
        metric,
        event_id=event_id,
        observed_at=start,
        ended_at=end,
        unit="count",
        kind="interval",
    )
    item.update(overrides)
    return item


def summary(result, metric="heart_rate_bpm", row=0):
    return result["daily"][row]["metrics"][metric]


class DailyFeatureTests(unittest.TestCase):
    def test_empty_input(self):
        result = build_features([])
        self.assertEqual(result["daily"], [])
        self.assertEqual(result["comparisons"], [])
        self.assertEqual(result["accepted_count"], 0)
        json.dumps(result, allow_nan=False)

    def test_missing_days_are_not_zeroes(self):
        rows = [measurement("2026-01-01", 50), measurement("2026-01-03", 80)]
        result = build_features(rows)
        self.assertEqual(
            [row["local_date"] for row in result["daily"]], ["2026-01-01", "2026-01-03"]
        )
        available = result["availability"][0]
        self.assertEqual(available["observed_day_count"], 2)
        self.assertEqual(available["missing_day_count"], 1)
        self.assertFalse(available["missing_is_zero"])
        self.assertEqual(available["wear_coverage"], "unknown")

    def test_subject_source_and_stream_are_separate(self):
        rows = [
            measurement(value=60),
            measurement(value=70, source="ble"),
            measurement(value=80, source_stream="different-stream"),
            measurement(value=90, subject_id="different-subject"),
        ]
        result = build_features(rows)
        self.assertEqual(len(result["daily"]), 4)
        self.assertEqual(
            sorted(summary(result, row=i)["value"] for i in range(4)), [60, 70, 80, 90]
        )

    def test_sample_mean_is_not_resting_hr_and_does_not_create_hrv(self):
        result = build_features(
            [
                measurement(value=60),
                measurement(value=100, event_id="second", observed_at="2026-01-01T12:01:00Z"),
            ]
        )
        data = summary(result)
        self.assertEqual(data["value"], 80)
        self.assertEqual(data["aggregation"], "recorded_sample_mean")
        self.assertEqual(data["sample_statistics"]["count"], 2)
        self.assertEqual(data["coverage"]["observed_span_seconds"], 60)
        self.assertEqual(set(result["daily"][0]["metrics"]), {"heart_rate_bpm"})

    def test_positive_step_intervals_are_recorded_totals_with_unknown_wear(self):
        result = build_features(
            [
                interval("a", "2026-01-01T12:00:00Z", "2026-01-01T12:01:00Z", 10),
                interval("b", "2026-01-01T22:00:00Z", "2026-01-01T22:01:00Z", 20),
            ]
        )
        data = summary(result, "steps")
        self.assertEqual(data["value"], 30)
        self.assertEqual(data["aggregation"], "recorded_interval_sum")
        self.assertEqual(data["coverage"]["recorded_interval_union_seconds"], 120)
        self.assertEqual(data["coverage"]["wear_coverage"], "unknown")
        self.assertFalse(data["coverage"]["missing_is_zero"])

    def test_overlapping_step_intervals_are_not_summed(self):
        result = build_features(
            [
                interval("a", "2026-01-01T12:00:00Z", "2026-01-01T12:02:00Z", 10),
                interval("b", "2026-01-01T12:01:00Z", "2026-01-01T12:03:00Z", 20),
            ]
        )
        data = summary(result, "steps")
        self.assertIsNone(data["value"])
        self.assertFalse(data["baseline_eligible"])
        self.assertIn("overlapping_intervals", data["quality_flags"])
        self.assertEqual(data["coverage"]["recorded_interval_union_seconds"], 180)

    def test_adjacent_step_intervals_can_be_summed(self):
        result = build_features(
            [
                interval("a", "2026-01-01T12:00:00Z", "2026-01-01T12:01:00Z", 10),
                interval("b", "2026-01-01T12:01:00Z", "2026-01-01T12:02:00Z", 20),
            ]
        )
        self.assertEqual(summary(result, "steps")["value"], 30)

    def test_exercise_steps_never_added_to_step_intervals(self):
        rows = [
            interval("a", "2026-01-01T12:00:00Z", "2026-01-01T12:01:00Z", 10),
            interval(
                "exercise",
                "2026-01-01T12:00:00Z",
                "2026-01-01T12:10:00Z",
                100,
                metric="exercise_steps",
                kind="session",
                attributes={"representation": "exercise_summary"},
            ),
        ]
        result = build_features(rows)
        self.assertEqual(summary(result, "steps")["value"], 10)
        self.assertEqual(summary(result, "exercise_steps")["value"], 100)

    def test_misnamed_exercise_summary_is_excluded(self):
        row = interval(
            "exercise",
            "2026-01-01T12:00:00Z",
            "2026-01-01T12:10:00Z",
            100,
            kind="session",
            attributes={"representation": "exercise_summary"},
        )
        result = build_features([row])
        self.assertIsNone(summary(result, "steps")["value"])
        self.assertIn(
            "exercise_summary_requires_separate_metric", summary(result, "steps")["quality_flags"]
        )

    def test_cross_midnight_additive_interval_is_not_allocated(self):
        row = interval(
            "cross", "2026-01-02T04:59:00Z", "2026-01-02T05:01:00Z", 30, local_date="2026-01-01"
        )
        result = build_features([row])
        self.assertIsNone(summary(result, "steps")["value"])
        self.assertEqual(len(result["daily"]), 1)
        self.assertIn("cross_midnight_additive_interval", summary(result, "steps")["quality_flags"])

    def test_end_exclusive_midnight_stays_in_previous_day(self):
        row = interval(
            "end", "2026-01-02T04:59:00Z", "2026-01-02T05:00:00Z", 30, local_date="2026-01-01"
        )
        result = build_features([row])
        self.assertEqual(summary(result, "steps")["value"], 30)

    def test_source_civil_date_and_explicit_cross_midnight_metadata_are_authoritative(self):
        row = interval(
            "offset",
            "2026-01-02T04:59:00Z",
            "2026-01-02T05:01:00Z",
            30,
            local_date="2026-01-02",
            attributes={"crosses_local_midnight": False, "start_local_date": "2026-01-02"},
        )
        result = build_features([row])
        self.assertEqual(result["daily"][0]["local_date"], "2026-01-02")
        self.assertEqual(summary(result, "steps")["value"], 30)

    def test_sleep_session_crossing_midnight_is_preserved(self):
        row = interval(
            "sleep",
            "2026-01-02T03:00:00Z",
            "2026-01-02T11:00:00Z",
            400,
            metric="sleep_minutes_asleep",
            unit="min",
            kind="session",
            local_date="2026-01-01",
            attributes={"crosses_local_midnight": True, "main_sleep": True},
        )
        result = build_features([row])
        self.assertEqual(summary(result, "sleep_minutes_asleep")["value"], 400)
        self.assertTrue(summary(result, "sleep_minutes_asleep")["baseline_eligible"])

    def test_overlapping_sleep_sessions_same_stream_are_invalid(self):
        rows = [
            interval(
                "sleep-a",
                "2026-01-02T03:00:00Z",
                "2026-01-02T11:00:00Z",
                400,
                metric="sleep_minutes_asleep",
                unit="min",
                kind="session",
                local_date="2026-01-01",
            ),
            interval(
                "sleep-b",
                "2026-01-02T04:00:00Z",
                "2026-01-02T12:00:00Z",
                400,
                metric="sleep_minutes_asleep",
                unit="min",
                kind="session",
                local_date="2026-01-01",
            ),
        ]
        result = build_features(rows)
        self.assertIsNone(summary(result, "sleep_minutes_asleep")["value"])
        self.assertIn(
            "overlapping_sessions", summary(result, "sleep_minutes_asleep")["quality_flags"]
        )

    def test_overlapping_sleep_providers_are_flagged_and_not_combined(self):
        a = interval(
            "sleep-a",
            "2026-01-02T03:00:00Z",
            "2026-01-02T11:00:00Z",
            400,
            metric="sleep_minutes_asleep",
            unit="min",
            kind="session",
            local_date="2026-01-01",
        )
        b = dict(a, event_id="sleep-b", source_stream="other-provider", value=420)
        result = build_features([a, b])
        self.assertEqual(len(result["daily"]), 2)
        self.assertEqual(
            sorted(summary(result, "sleep_minutes_asleep", i)["value"] for i in range(2)),
            [400, 420],
        )
        for row in result["daily"]:
            data = row["metrics"]["sleep_minutes_asleep"]
            self.assertIn("overlapping_sleep_providers", data["quality_flags"])
            self.assertFalse(data["baseline_eligible"])

    def test_sleep_overlap_cannot_hide_across_different_source_dates(self):
        a = interval(
            "sleep-a",
            "2026-01-02T03:00:00Z",
            "2026-01-02T11:00:00Z",
            400,
            metric="sleep_minutes_asleep",
            unit="min",
            kind="session",
            local_date="2026-01-01",
        )
        b = dict(a, event_id="sleep-b", local_date="2026-01-02", observed_at="2026-01-02T05:00:00Z")
        result = build_features([a, b])
        for row in result["daily"]:
            data = row["metrics"]["sleep_minutes_asleep"]
            self.assertIsNone(data["value"])
            self.assertIn("overlapping_sessions_across_dates", data["quality_flags"])

    def test_multiple_daily_summaries_are_ambiguous(self):
        a = measurement(value=60, metric="resting_heart_rate_bpm", kind="daily")
        b = dict(a, event_id="b", value=62)
        result = build_features([a, b])
        data = summary(result, "resting_heart_rate_bpm")
        self.assertIsNone(data["value"])
        self.assertIn("multiple_daily_summaries", data["quality_flags"])

    def test_daily_storage_anchor_is_not_measurement_coverage(self):
        row = measurement(
            value=60,
            metric="resting_heart_rate_bpm",
            kind="daily",
            attributes={
                "timestamp_is_storage_anchor": True,
                "observed_at_is_measurement_time": False,
            },
        )
        data = summary(build_features([row]), "resting_heart_rate_bpm")
        self.assertIsNone(data["coverage"]["observed_start"])
        self.assertIsNone(data["coverage"]["observed_span_seconds"])
        self.assertEqual(data["value"], 60)

    def test_identical_event_duplicates_collapse_without_mutating_input(self):
        rows = [measurement(), measurement()]
        before = copy.deepcopy(rows)
        result = build_features(iter(rows))
        self.assertEqual(result["duplicate_count"], 1)
        self.assertEqual(result["accepted_count"], 1)
        self.assertEqual(rows, before)

    def test_conflicting_event_revision_is_not_selected_arbitrarily(self):
        result = build_features([measurement(value=60), measurement(value=90)])
        self.assertEqual(result["accepted_count"], 0)
        self.assertEqual(result["conflicting_event_count"], 1)
        self.assertTrue(
            any(flag["code"] == "conflicting_event_revision" for flag in result["quality_flags"])
        )

    def test_excluding_conflict_does_not_make_partial_day_comparable(self):
        a = interval("a", "2026-01-01T12:00:00Z", "2026-01-01T12:01:00Z", 10)
        revision = dict(a, value=20)
        b = interval("b", "2026-01-01T12:01:00Z", "2026-01-01T12:02:00Z", 30)
        result = build_features([a, revision, b])
        self.assertIsNone(summary(result, "steps")["value"])
        self.assertFalse(summary(result, "steps")["baseline_eligible"])
        self.assertEqual(
            result["input_count"],
            result["accepted_count"]
            + result["duplicate_count"]
            + result["rejected_count"]
            + result["conflicting_record_count"],
        )

    def test_large_finite_inputs_remain_json_finite(self):
        rows = [measurement(value=1e308), measurement(value=1e308, event_id="second")]
        result = build_features(rows)
        self.assertEqual(summary(result)["sample_statistics"]["median"], 1e308)
        json.dumps(result, allow_nan=False)

    def test_invalid_values_units_timestamps_and_dates_are_rejected(self):
        invalid = [
            measurement(value=float("nan")),
            measurement(value=float("inf")),
            measurement(value=True),
            measurement(unit="ms"),
            measurement(local_date="2026-02-30"),
            measurement(observed_at="2026-01-01T12:00:00"),
            interval("backwards", "2026-01-01T12:01:00Z", "2026-01-01T12:00:00Z"),
            interval("negative", "2026-01-01T12:00:00Z", "2026-01-01T12:01:00Z", -1),
        ]
        result = build_features(invalid)
        self.assertEqual(result["rejected_count"], len(invalid))
        self.assertEqual(result["daily"], [])
        json.dumps(result, allow_nan=False)

    def test_mixed_representations_are_not_doublecounted(self):
        a = interval("a", "2026-01-01T12:00:00Z", "2026-01-01T12:01:00Z", 10)
        b = measurement(value=100, metric="steps", unit="count", kind="daily")
        result = build_features([a, b])
        self.assertIsNone(summary(result, "steps")["value"])
        self.assertIn("mixed_representations", summary(result, "steps")["quality_flags"])


class BaselineTests(unittest.TestCase):
    def daily_rows(self, values, **overrides):
        start = date(2026, 1, 1)
        return [
            measurement(
                (start + timedelta(days=i)).isoformat(),
                value,
                metric="resting_heart_rate_bpm",
                kind="daily",
                **overrides,
            )
            for i, value in enumerate(values)
        ]

    def test_baseline_uses_prior_days_without_current_or_future_leakage(self):
        result = build_features(reversed(self.daily_rows(list(range(60, 74)) + [200, 999])))
        day15 = next(row for row in result["comparisons"] if row["local_date"] == "2026-01-15")
        self.assertEqual(day15["prior_valid_day_count"], 14)
        self.assertEqual(day15["baseline_median"], 66.5)
        self.assertEqual(day15["baseline_mad"], 3.5)
        self.assertEqual(day15["delta"], 133.5)
        self.assertAlmostEqual(day15["robust_z"], 133.5 / (1.4826 * 3.5))
        self.assertEqual(day15["baseline_end_date"], "2026-01-14")
        self.assertEqual(day15["baseline_status"], "ready")
        self.assertTrue(
            all(
                row["baseline_status"] == "insufficient_history"
                for row in result["comparisons"][:14]
            )
        )

    def test_many_samples_in_few_days_do_not_satisfy_baseline(self):
        rows = [measurement("2026-01-01", 60 + i, event_id=str(i)) for i in range(40)]
        rows.append(measurement("2026-01-02", 80))
        result = build_features(rows)
        self.assertTrue(
            all(row["baseline_status"] == "insufficient_history" for row in result["comparisons"])
        )
        self.assertEqual(result["comparisons"][-1]["prior_valid_day_count"], 1)

    def test_zero_mad_returns_raw_delta_without_infinity(self):
        result = build_features(self.daily_rows([60] * 14 + [75]))
        current = result["comparisons"][-1]
        self.assertEqual(current["baseline_status"], "insufficient_variation")
        self.assertEqual(current["baseline_mad"], 0)
        self.assertEqual(current["delta"], 15)
        self.assertIsNone(current["robust_z"])
        json.dumps(result, allow_nan=False)

    def test_large_finite_baseline_does_not_overflow_median(self):
        result = build_features(self.daily_rows([1e308] * 15))
        self.assertEqual(result["comparisons"][-1]["baseline_median"], 1e308)
        self.assertEqual(result["comparisons"][-1]["baseline_status"], "insufficient_variation")
        json.dumps(result, allow_nan=False)

    def test_baseline_never_crosses_stream_source_or_subject(self):
        rows = self.daily_rows(list(range(60, 74)))
        target_date = "2026-01-15"
        rows.extend(
            [
                measurement(
                    target_date, 80, metric="resting_heart_rate_bpm", kind="daily", source="ble"
                ),
                measurement(
                    target_date,
                    80,
                    metric="resting_heart_rate_bpm",
                    kind="daily",
                    source_stream="new-stream",
                ),
                measurement(
                    target_date,
                    80,
                    metric="resting_heart_rate_bpm",
                    kind="daily",
                    subject_id="other",
                ),
            ]
        )
        result = build_features(rows)
        for item in result["comparisons"]:
            if item["local_date"] == target_date:
                self.assertEqual(item["prior_valid_day_count"], 0)
                self.assertEqual(item["baseline_status"], "insufficient_history")

    def test_invalid_daily_summary_is_excluded_from_history(self):
        rows = self.daily_rows(list(range(60, 75)))
        rows.append(dict(rows[2], event_id="conflicting-provider-summary", value=500))
        result = build_features(rows)
        current = result["comparisons"][-1]
        self.assertEqual(current["prior_valid_day_count"], 13)
        self.assertEqual(current["baseline_status"], "insufficient_history")

    def test_minimum_fourteen_days_cannot_be_disabled(self):
        for value in [0, 1, 13, True, 14.0]:
            with self.assertRaises(ValueError):
                build_features([], min_baseline_days=value)
        self.assertEqual(build_features([], min_baseline_days=21)["min_baseline_days"], 21)


if __name__ == "__main__":
    unittest.main()
