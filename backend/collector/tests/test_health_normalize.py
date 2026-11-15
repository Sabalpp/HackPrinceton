"""Deterministic adapter tests using fixtures; never write source health logs."""

import copy
import hashlib
import json
import math
import unittest

from farcare_collector.health.health_normalize import (
    normalize_api_record,
    normalize_ble_record,
    record_hash,
)

CANONICAL_KEYS = {
    "event_id",
    "subject_id",
    "source",
    "source_stream",
    "metric",
    "observed_at",
    "ended_at",
    "local_date",
    "value",
    "unit",
    "kind",
    "attributes",
}
SOURCE = {
    "device": {"displayName": "Owned fixture device"},
    "platform": "FITBIT",
    "recordingMethod": "AUTOMATICALLY_RECORDED",
}
DAY = {"year": 2026, "month": 11, "day": 14}
SAMPLE_TIME = {
    "physicalTime": "2026-11-14T15:09:48Z",
    "utcOffset": "-18000s",
    "civilTime": {"date": DAY, "time": {"hours": 10, "minutes": 9, "seconds": 48}},
}
INTERVAL = {
    "startTime": "2026-11-14T15:00:00Z",
    "endTime": "2026-11-14T15:01:00Z",
    "startUtcOffset": "-18000s",
    "endUtcOffset": "-18000s",
}


def api(key, body, name=None):
    point = {"dataSource": copy.deepcopy(SOURCE), key: copy.deepcopy(body)}
    if name is not None:
        point["name"] = name
    return point


def heart(bpm="78", time=None):
    return api(
        "heartRate", {"sampleTime": copy.deepcopy(time or SAMPLE_TIME), "beatsPerMinute": bpm}
    )


def ble(bpm=78, payload="014e00", timestamp="2026-11-14T15:09:48+00:00"):
    return {
        "receipt_timestamp": timestamp,
        "characteristic_uuid": "00002a37-0000-1000-8000-00805f9b34fb",
        "payload_hex": payload,
        "bpm": bpm,
    }


def sleep_record():
    return api(
        "sleep",
        {
            "interval": INTERVAL,
            "type": "STAGES",
            "metadata": {"nap": True, "mainSleep": True, "processed": True},
            "summary": {
                "minutesAsleep": "1",
                "minutesAwake": "0",
                "minutesInSleepPeriod": "1",
                "stagesSummary": [{"type": "LIGHT", "minutes": "1", "count": "1"}],
            },
            "stages": [dict(INTERVAL, type="LIGHT")],
            "createTime": "2026-11-14T16:00:00Z",
            "updateTime": "2026-11-14T17:00:00Z",
        },
        name="users/private/sleep/owned-session",
    )


class NormalizationTests(unittest.TestCase):
    def assert_canonical(self, event):
        self.assertEqual(set(event), CANONICAL_KEYS)
        self.assertEqual(len(event["event_id"]), 64)
        self.assertEqual(len(event["source_stream"]), 64)
        self.assertIsInstance(event["value"], float)
        self.assertTrue(math.isfinite(event["value"]))
        self.assertTrue(event["observed_at"].endswith("Z"))
        self.assertEqual(len(event["attributes"]["raw_record_hash"]), 64)

    def test_record_hash_deterministic_and_finite(self):
        obj = {"b": [1, 2], "a": "é"}
        expected = hashlib.sha256(
            json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        ).hexdigest()
        self.assertEqual(record_hash(obj), expected)
        self.assertEqual(record_hash(obj), record_hash({"a": "é", "b": [1, 2]}))
        for invalid in ({"x": float("nan")}, {"x": float("inf")}, {"x": object()}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                record_hash(invalid)

    def test_actual_schema_shape_and_provenance(self):
        point = heart()
        before = copy.deepcopy(point)
        (event,) = normalize_api_record("heart-rate", point)
        self.assert_canonical(event)
        self.assertEqual(event["metric"], "heart_rate_bpm")
        self.assertEqual(event["value"], 78)
        self.assertEqual(event["unit"], "bpm")
        self.assertEqual(event["observed_at"], "2026-11-14T15:09:48Z")
        self.assertEqual(event["source_stream"], record_hash(SOURCE))
        self.assertEqual(event["attributes"]["raw_record_hash"], record_hash(point))
        self.assertEqual(event["attributes"]["reported_local_date"], "2026-11-14")
        self.assertEqual(point, before)
        self.assertNotIn("Owned fixture device", json.dumps(event))

    def test_value_revision_keeps_id_and_changes_raw_hash(self):
        (one,) = normalize_api_record("heart-rate", heart("78"))
        (two,) = normalize_api_record("heart-rate", heart("79"))
        self.assertEqual(one["event_id"], two["event_id"])
        self.assertNotEqual(
            one["attributes"]["raw_record_hash"], two["attributes"]["raw_record_hash"]
        )

    def test_equivalent_instants_and_nanoseconds_have_correct_identity(self):
        (one,) = normalize_api_record(
            "heart-rate", heart(time={"physicalTime": "2026-11-14T15:09:48.123456789Z"})
        )
        (two,) = normalize_api_record(
            "heart-rate", heart(time={"physicalTime": "2026-11-14T10:09:48.123456789-05:00"})
        )
        (adjacent,) = normalize_api_record(
            "heart-rate", heart(time={"physicalTime": "2026-11-14T15:09:48.123456788Z"})
        )
        self.assertEqual(one["event_id"], two["event_id"])
        self.assertNotEqual(one["event_id"], adjacent["event_id"])
        self.assertEqual(one["observed_at"], "2026-11-14T15:09:48.123456789Z")
        (plain,) = normalize_api_record(
            "heart-rate", heart(time={"physicalTime": "2026-11-14T15:09:48Z"})
        )
        (zeros,) = normalize_api_record(
            "heart-rate", heart(time={"physicalTime": "2026-11-14T15:09:48.000+00:00"})
        )
        self.assertEqual(plain["event_id"], zeros["event_id"])

    def test_sources_and_subjects_stay_separate(self):
        (first,) = normalize_api_record("heart-rate", heart())
        second_point = heart()
        second_point["dataSource"]["device"]["displayName"] = "Different owned fixture"
        (second,) = normalize_api_record("heart-rate", second_point)
        (other_subject,) = normalize_api_record("heart-rate", heart(), subject_id="second")
        (live,) = normalize_ble_record(ble())
        self.assertEqual(
            len(
                {first["event_id"], second["event_id"], other_subject["event_id"], live["event_id"]}
            ),
            4,
        )
        self.assertNotEqual(first["source_stream"], second["source_stream"])
        self.assertEqual(live["source"], "ble")

    def test_named_session_revision_keeps_identity_across_corrected_boundaries(self):
        original = sleep_record()
        updated = copy.deepcopy(original)
        updated["sleep"]["interval"]["endTime"] = "2026-11-14T15:02:00Z"
        updated["sleep"]["summary"]["minutesAsleep"] = "2"
        updated["sleep"]["updateTime"] = "2026-11-15T01:00:00+00:00"
        before = {e["metric"]: e for e in normalize_api_record("sleep", original)}
        after = {e["metric"]: e for e in normalize_api_record("sleep", updated)}
        self.assertEqual(
            before["sleep_minutes_asleep"]["event_id"], after["sleep_minutes_asleep"]["event_id"]
        )
        self.assertNotEqual(
            before["sleep_minutes_asleep"]["ended_at"], after["sleep_minutes_asleep"]["ended_at"]
        )
        self.assertEqual(
            after["sleep_minutes_asleep"]["attributes"]["source_update_time"],
            "2026-11-15T01:00:00Z",
        )
        self.assertNotIn("users/private", json.dumps(list(after.values())))

    def test_reported_civil_date_wins_and_source_offset_is_fallback(self):
        reported = {
            "physicalTime": "2026-11-14T01:00:00Z",
            "utcOffset": "36000s",
            "civilTime": {"date": {"year": 2026, "month": 11, "day": 14}, "time": {"hours": 11}},
        }
        (event,) = normalize_api_record(
            "heart-rate", heart(time=reported), timezone_name="America/New_York"
        )
        self.assertEqual(event["local_date"], "2026-11-14")
        self.assertEqual(event["attributes"]["local_date_basis"], "source_civil_date")
        del reported["civilTime"]
        (offset_event,) = normalize_api_record("heart-rate", heart(time=reported))
        self.assertEqual(offset_event["local_date"], "2026-11-14")
        self.assertEqual(offset_event["attributes"]["local_date_basis"], "source_utc_offset")
        del reported["utcOffset"]
        (fallback,) = normalize_api_record("heart-rate", heart(time=reported))
        self.assertEqual(fallback["local_date"], "2026-11-13")

    def test_daily_anchors_are_explicit_and_not_measurement_times(self):
        point = api(
            "dailyRestingHeartRate",
            {"date": {"year": 2026, "month": 11, "day": 1}, "beatsPerMinute": "55"},
        )
        (local,) = normalize_api_record("daily-resting-heart-rate", point)
        (utc,) = normalize_api_record("daily-resting-heart-rate", point, timezone_name="UTC")
        self.assertEqual(local["observed_at"], "2026-11-01T04:00:00Z")
        self.assertEqual(local["event_id"], utc["event_id"])
        self.assertEqual(local["local_date"], "2026-11-01")
        self.assertIsNone(local["ended_at"])
        self.assertEqual(local["kind"], "daily")
        self.assertTrue(local["attributes"]["timestamp_is_storage_anchor"])
        self.assertFalse(local["attributes"]["observed_at_is_measurement_time"])

    def test_cross_midnight_steps_remain_whole_and_marked(self):
        interval = {
            "startTime": "2026-11-15T04:59:00Z",
            "endTime": "2026-11-15T05:01:00Z",
            "startUtcOffset": "-18000s",
            "endUtcOffset": "-18000s",
        }
        events = normalize_api_record("steps", api("steps", {"interval": interval, "count": "15"}))
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event["value"], 15)
        self.assertEqual(event["local_date"], "2026-11-14")
        self.assertEqual(event["attributes"]["coverage_end_local_date"], "2026-11-15")
        self.assertTrue(event["attributes"]["crosses_local_midnight"])
        interval["endTime"] = "2026-11-15T05:00:00Z"
        (exclusive,) = normalize_api_record(
            "steps", api("steps", {"interval": interval, "count": "15"})
        )
        self.assertEqual(exclusive["attributes"]["end_local_date"], "2026-11-15")
        self.assertEqual(exclusive["attributes"]["coverage_end_local_date"], "2026-11-14")
        self.assertFalse(exclusive["attributes"]["crosses_local_midnight"])

    def test_distance_unit_conversion_and_interval_identity(self):
        point = api("distance", {"interval": INTERVAL, "millimeters": "3400"})
        (event,) = normalize_api_record("distance", point)
        self.assertEqual(
            (event["metric"], event["value"], event["unit"], event["kind"]),
            ("distance_m", 3.4, "m", "interval"),
        )
        point["distance"]["interval"]["endTime"] = "2026-11-14T15:02:00Z"
        (revised_interval,) = normalize_api_record("distance", point)
        self.assertNotEqual(event["event_id"], revised_interval["event_id"])

    def test_active_zone_labels_are_preserved_and_distinct(self):
        point = api(
            "activeZoneMinutes",
            {"interval": INTERVAL, "activeZoneMinutes": "1", "heartRateZone": "FAT_BURN"},
        )
        (fat,) = normalize_api_record("active-zone-minutes", point)
        point["activeZoneMinutes"]["heartRateZone"] = "CARDIO"
        (cardio,) = normalize_api_record("active-zone-minutes", point)
        self.assertEqual(fat["attributes"]["heart_rate_zone"], "FAT_BURN")
        self.assertNotEqual(fat["event_id"], cardio["event_id"])

    def test_reported_hrv_and_oxygen_metrics_keep_units(self):
        cases = [
            (
                "heart-rate-variability",
                "heartRateVariability",
                {
                    "sampleTime": SAMPLE_TIME,
                    "rootMeanSquareOfSuccessiveDifferencesMilliseconds": 47.5,
                    "metadata": {"highFrequencyPower": 121.0},
                },
                {"hrv_rmssd_ms": ("ms", 47.5)},
            ),
            (
                "oxygen-saturation",
                "oxygenSaturation",
                {"sampleTime": SAMPLE_TIME, "percentage": 96.5},
                {"spo2_percent": ("%", 96.5)},
            ),
            (
                "daily-heart-rate-variability",
                "dailyHeartRateVariability",
                {
                    "date": DAY,
                    "averageHeartRateVariabilityMilliseconds": 55.4,
                    "nonRemHeartRateBeatsPerMinute": "55",
                    "deepSleepRootMeanSquareOfSuccessiveDifferencesMilliseconds": 41.9,
                    "entropy": 2.998,
                },
                {
                    "hrv_daily_average_ms": ("ms", 55.4),
                    "non_rem_heart_rate_bpm": ("bpm", 55),
                    "hrv_deep_sleep_rmssd_ms": ("ms", 41.9),
                },
            ),
            (
                "daily-oxygen-saturation",
                "dailyOxygenSaturation",
                {
                    "date": DAY,
                    "averagePercentage": 95.1,
                    "lowerBoundPercentage": 92.3,
                    "upperBoundPercentage": 98.5,
                    "standardDeviationPercentage": 0.8,
                },
                {
                    "spo2_daily_mean_percent": ("%", 95.1),
                    "spo2_daily_lower_bound_percent": ("%", 92.3),
                    "spo2_daily_upper_bound_percent": ("%", 98.5),
                    "spo2_daily_stddev_percent": ("%", 0.8),
                },
            ),
            (
                "daily-respiratory-rate",
                "dailyRespiratoryRate",
                {"date": DAY, "breathsPerMinute": 17.6},
                {"respiratory_rate_bpm": ("bpm", 17.6)},
            ),
            (
                "daily-sleep-temperature-derivations",
                "dailySleepTemperatureDerivations",
                {
                    "date": DAY,
                    "nightlyTemperatureCelsius": 33.45,
                    "baselineTemperatureCelsius": 34.05,
                    "relativeNightlyStddev30dCelsius": 0.543,
                },
                {
                    "skin_temp_nightly_c": ("degC", 33.45),
                    "skin_temp_baseline_c": ("degC", 34.05),
                    "skin_temp_relative_stddev30d_c": ("degC", 0.543),
                },
            ),
        ]
        for data_type, key, body, expected in cases:
            with self.subTest(data_type=data_type):
                events = normalize_api_record(data_type, api(key, body))
                for event in events:
                    self.assert_canonical(event)
                self.assertEqual({e["metric"]: (e["unit"], e["value"]) for e in events}, expected)

    def test_sleep_summary_preserves_metadata_without_duplicate_stages(self):
        events = normalize_api_record("sleep", sleep_record())
        self.assertEqual(
            {e["metric"] for e in events},
            {"sleep_minutes_asleep", "sleep_minutes_awake", "sleep_period_minutes"},
        )
        for event in events:
            self.assertEqual(event["kind"], "session")
            self.assertTrue(event["attributes"]["nap"])
            self.assertTrue(event["attributes"]["main_sleep"])
            self.assertEqual(event["attributes"]["representation"], "sleep_session_summary")
            self.assertFalse(event["attributes"]["stage_intervals_normalized"])
            self.assertEqual(event["attributes"]["stages_count"], 1)

    def test_exercise_summaries_cannot_double_count_plain_steps(self):
        point = api(
            "exercise",
            {
                "interval": INTERVAL,
                "exerciseType": "WALKING",
                "activeDuration": "45s",
                "metricsSummary": {
                    "steps": "50",
                    "distanceMillimeters": 30000,
                    "heartRateZoneDurations": {"lightTime": "30s", "moderateTime": "15s"},
                },
            },
            name="users/private/exercise/owned-session",
        )
        events = normalize_api_record("exercise", point)
        by_metric = {e["metric"]: e for e in events}
        self.assertNotIn("steps", by_metric)
        self.assertNotIn("distance_m", by_metric)
        self.assertEqual(by_metric["exercise_steps"]["value"], 50)
        self.assertEqual(by_metric["exercise_distance_m"]["value"], 30)
        self.assertEqual(by_metric["exercise_duration_seconds"]["value"], 60)
        self.assertEqual(by_metric["exercise_active_duration_seconds"]["value"], 45)
        zones = [e for e in events if e["metric"] == "exercise_heart_rate_zone_seconds"]
        self.assertEqual(len({e["event_id"] for e in zones}), 2)
        self.assertTrue(
            all(e["attributes"]["representation"] == "exercise_summary" for e in events)
        )
        point["exercise"]["interval"]["endTime"] = "2026-11-14T15:02:00Z"
        revision = {e["metric"]: e for e in normalize_api_record("exercise", point)}
        self.assertEqual(
            revision["exercise_duration_seconds"]["event_id"],
            by_metric["exercise_duration_seconds"]["event_id"],
        )

    def test_missing_numeric_fields_do_not_create_zero_measurements(self):
        self.assertEqual(
            normalize_api_record("heart-rate", api("heartRate", {"sampleTime": SAMPLE_TIME})), []
        )
        self.assertEqual(normalize_api_record("steps", api("steps", {"interval": INTERVAL})), [])
        self.assertEqual(
            normalize_api_record(
                "daily-resting-heart-rate", api("dailyRestingHeartRate", {"date": DAY})
            ),
            [],
        )
        point = sleep_record()
        point["sleep"]["summary"] = {}
        self.assertEqual(normalize_api_record("sleep", point), [])

    def test_invalid_numeric_values_are_rejected(self):
        for value in (
            True,
            False,
            None,
            float("nan"),
            float("inf"),
            "NaN",
            "Infinity",
            "",
            "-1",
            [],
            {},
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_api_record("heart-rate", heart(value))
        with self.assertRaises(ValueError):
            normalize_api_record("steps", api("steps", {"interval": INTERVAL, "count": "1.5"}))
        with self.assertRaises(ValueError):
            normalize_api_record(
                "daily-sleep-temperature-derivations",
                api(
                    "dailySleepTemperatureDerivations",
                    {"date": DAY, "relativeNightlyStddev30dCelsius": -0.3},
                ),
            )

    def test_naive_bad_offset_and_invalid_dates_rejected(self):
        for value in (
            "2026-11-14T15:09:48",
            "2026-11-14",
            "2026-11-14T15:09:48+00:99",
            "2026-11-14T15:09:48+24:00",
            "2026-02-30T15:09:48Z",
            None,
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_api_record("heart-rate", heart(time={"physicalTime": value}))
        for day in ({"year": 2026, "month": 2, "day": 30}, {"year": True, "month": 1, "day": 1}):
            with self.assertRaises(ValueError):
                normalize_api_record(
                    "daily-resting-heart-rate",
                    api("dailyRestingHeartRate", {"date": day, "beatsPerMinute": 55}),
                )

    def test_invalid_intervals_and_stages_are_rejected(self):
        for end in ("2026-11-14T15:00:00Z", "2026-11-14T14:59:00Z", "2026-11-14T15:01:00"):
            with self.subTest(end=end), self.assertRaises(ValueError):
                normalize_api_record(
                    "steps", api("steps", {"interval": dict(INTERVAL, endTime=end), "count": 5})
                )
        point = sleep_record()
        point["sleep"]["stages"][0]["endTime"] = "2026-11-14T15:02:00Z"
        with self.assertRaises(ValueError):
            normalize_api_record("sleep", point)
        point = sleep_record()
        point["sleep"]["stages"][0]["updateTime"] = "2026-11-14T17:00:00"
        with self.assertRaises(ValueError):
            normalize_api_record("sleep", point)

    def test_unknown_shapes_raise_value_error_for_quarantine(self):
        for data_type, point in [
            ("new-sensor", heart()),
            (None, heart()),
            ("heart-rate", []),
            ("heart-rate", {"heartRate": {"sampleTime": SAMPLE_TIME, "beatsPerMinute": 78}}),
            ("heart-rate", api("heartRate", {"sampleTime": SAMPLE_TIME, "futureBpm": 78})),
        ]:
            with self.subTest(data_type=data_type, point=point), self.assertRaises(ValueError):
                normalize_api_record(data_type, point)
        point = heart()
        point["unexpectedEnvelope"] = "new-schema"
        with self.assertRaises(ValueError):
            normalize_api_record("heart-rate", point)

    def test_ble_uint8_and_little_endian_uint16_are_verified(self):
        (one,) = normalize_ble_record(ble(payload="004e"))
        (two,) = normalize_ble_record(ble(bpm=300, payload="012c01"))
        self.assertEqual(one["value"], 78)
        self.assertEqual(two["value"], 300)
        self.assert_canonical(one)
        self.assertEqual(one["attributes"]["time_semantics"], "mac_receipt_time")
        self.assertFalse(one["attributes"]["observed_at_is_measurement_time"])
        self.assertFalse(one["attributes"]["device_identity_available"])
        self.assertTrue(one["attributes"]["payload_verified"])
        self.assertEqual(one["event_id"], two["event_id"])  # value revision at same receipt time

    def test_ble_optional_energy_and_rr_validate_without_invented_hrv(self):
        events = normalize_ble_record(ble(payload="194e0001000004"))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["metric"], "heart_rate_bpm")
        self.assertFalse(events[0]["attributes"]["rr_intervals_used"])

    def test_ble_malformed_packets_and_record_mismatches_are_rejected(self):
        for payload in ("", "zz", "01", "014e", "084e00", "104e", "104e01", "004e00", "204e"):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                normalize_ble_record(ble(payload=payload))
        for changes in (
            {"bpm": 79},
            {"bpm": True},
            {"decode_error": "short"},
            {"receipt_timestamp": "2026-11-14T15:09:48"},
            {"characteristic_uuid": "0000180d-0000-1000-8000-00805f9b34fb"},
            {"payload_hex": None},
        ):
            point = ble()
            point.update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                normalize_ble_record(point)

    def test_ble_explicit_identity_is_hashed_and_not_inferred(self):
        (unidentified,) = normalize_ble_record(ble())
        point = ble()
        point["device_identifier"] = "AABBCC-OWNED-DEVICE"
        (identified,) = normalize_ble_record(point)
        point["device_identifier"] = point["device_identifier"].lower()
        (lowercase,) = normalize_ble_record(point)
        self.assertNotEqual(unidentified["event_id"], identified["event_id"])
        self.assertEqual(identified["event_id"], lowercase["event_id"])
        self.assertTrue(identified["attributes"]["device_identity_available"])
        self.assertNotIn("owned-device", json.dumps(identified).lower())

    def test_bad_timezone_subject_or_nonfinite_metadata_rejected(self):
        for kwargs in ({"timezone_name": "Not/AZone"}, {"subject_id": ""}, {"subject_id": 12}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                normalize_api_record("heart-rate", heart(), **kwargs)
        point = heart()
        point["heartRate"]["metadata"] = {"bad": float("nan")}
        with self.assertRaises(ValueError):
            normalize_api_record("heart-rate", point)


if __name__ == "__main__":
    unittest.main()
