import unittest

from farcare_collector.services.health_pipeline import timestamp


class RFC3339Tests(unittest.TestCase):
    def test_python39_variable_fraction_widths(self):
        for fraction in ("2", "12", "123", "1234", "12345", "123456", "123456789"):
            self.assertEqual(
                timestamp("2026-11-14T18:00:00." + fraction + "Z"),
                "2026-11-14T18:00:00." + fraction[:6].ljust(6, "0") + "Z",
            )

    def test_offset_and_naive_time(self):
        self.assertEqual(timestamp("2026-11-14T13:00:00.2-05:00"), "2026-11-14T18:00:00.200000Z")
        with self.assertRaises(ValueError):
            timestamp("2026-11-14T13:00:00")

    def test_exercise_event_metadata_does_not_drop_reported_metrics(self):
        from farcare_collector.health.health_normalize import normalize_api_record
        from test_health_normalize import INTERVAL, api

        events = [
            {
                "eventTime": "2026-11-14T15:00:30Z",
                "eventUtcOffset": "-18000s",
                "exerciseEventType": "PAUSE",
            }
        ]
        record = api(
            "exercise",
            {
                "interval": INTERVAL,
                "exerciseType": "WALK",
                "exerciseEvents": events,
                "metricsSummary": {"steps": "12"},
            },
        )
        points = normalize_api_record("exercise", record)
        self.assertEqual(
            {p["metric"]: p["value"] for p in points},
            {"exercise_duration_seconds": 60, "exercise_steps": 12},
        )
        self.assertTrue(all(p["attributes"]["source_exercise_events"] == events for p in points))
