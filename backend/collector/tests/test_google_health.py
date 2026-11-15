"""Offline tests only; fixtures never enter the user's API export directory."""

import json
import stat
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from farcare_collector.integrations import google_health as gh


class GoogleHealthTests(unittest.TestCase):
    def setUp(self):
        self.start = datetime(2026, 11, 1, tzinfo=timezone.utc)
        self.end = datetime(2026, 11, 8, tzinfo=timezone.utc)

    def test_callback_rejects_wrong_state_and_duplicate_codes(self):
        for path in (
            "/auth/callback?state=wrong&code=test",
            "/auth/callback?code=test",
            "/auth/callback?state=right&code=a&code=b",
            "/favicon.ico",
        ):
            with self.assertRaises(ValueError):
                gh.parse_callback(path, "right")

    def test_callback_accepts_matching_state(self):
        self.assertEqual(gh.parse_callback("/auth/callback?state=right&code=test", "right"), "test")

    def test_callback_reports_denied_consent(self):
        with self.assertRaisesRegex(RuntimeError, "access_denied"):
            gh.parse_callback("/auth/callback?state=right&error=access_denied", "right")

    def test_sleep_filter_omits_unsupported_source_parameter(self):
        params = gh.query_parameters("sleep", self.start, self.end)
        self.assertNotIn("dataSourceFamily", params)
        self.assertEqual(params["pageSize"], 25)
        self.assertIn("sleep.interval.end_time", params["filter"])

    def test_hr_filter_selects_wearables_and_sample_timestamps(self):
        params = gh.query_parameters("heart-rate", self.start, self.end)
        self.assertIn("heart_rate.sample_time.physical_time", params["filter"])
        self.assertEqual(params["dataSourceFamily"], "users/me/dataSourceFamilies/google-wearables")

    def test_irn_has_no_invented_time_filter(self):
        params = gh.query_parameters("irregular-rhythm-notification", self.start, self.end)
        self.assertNotIn("filter", params)

    def test_tcx_export_preserves_bytes_and_deduplicates(self):
        with tempfile.TemporaryDirectory() as directory:
            dest = Path(directory)
            point = {"name": "users/me/dataTypes/exercise/dataPoints/123"}
            gh.private_json(dest / "exercise/page-00001.json", {"dataPoints": [point, point]})
            payload = b'<?xml version="1.0"?><TrainingCenterDatabase/>'
            with patch.object(gh, "request_bytes", return_value=payload) as request:
                result = gh.export_exercises("test-token", dest)
            self.assertEqual(result["files"], 1)
            request.assert_called_once()
            self.assertIn("alt=media", request.call_args.args[0])
            self.assertEqual((dest / "exercise-tcx/123.tcx").read_bytes(), payload)

    def test_tcx_rejects_unexpected_resource_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            dest = Path(directory)
            gh.private_json(
                dest / "exercise/page-00001.json",
                {"dataPoints": [{"name": "https://example.test/"}]},
            )
            with patch.object(gh, "request_bytes") as request:
                result = gh.export_exercises("test-token", dest)
            request.assert_not_called()
            self.assertEqual(result["status"], "partial")

    def test_pagination_preserves_full_pages_and_encoded_token(self):
        responses = [
            {"dataPoints": [{"name": "fixture-1"}], "nextPageToken": "a+b/c="},
            {"dataPoints": [{"name": "fixture-2"}]},
        ]
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(gh, "request_json", side_effect=responses) as request,
        ):
            dest = Path(directory)
            result = gh.fetch_type("heart-rate", "test-token", self.start, self.end, dest)
            self.assertEqual(result["records"], 2)
            query = parse_qs(urlsplit(request.call_args_list[1].args[0]).query)
            self.assertEqual(query["pageToken"], ["a+b/c="])
            self.assertEqual(
                json.loads((dest / "heart-rate/page-00001.json").read_text()), responses[0]
            )

    def test_repeated_page_token_is_an_error(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(gh, "request_json", return_value={"nextPageToken": "repeat"}),
        ):
            with self.assertRaisesRegex(RuntimeError, "repeated"):
                gh.fetch_type("steps", "test-token", self.start, self.end, Path(directory))

    def test_refresh_keeps_refresh_token(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tokens.json"
            gh.private_json(
                path,
                {
                    "client_id": "test-client",
                    "refresh_token": "test-refresh",
                    "scope": " ".join(gh.SCOPES),
                    "expires_at": 0,
                },
            )
            with (
                patch.object(gh, "TOKEN_FILE", path),
                patch.object(
                    gh,
                    "request_json",
                    return_value={"access_token": "test-access", "expires_in": 3600},
                ),
            ):
                tokens = gh.get_tokens({"client_id": "test-client", "client_secret": "test-secret"})
            self.assertEqual(tokens["refresh_token"], "test-refresh")
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_reauthorization_does_not_retain_previous_account_refresh_token(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tokens.json"
            gh.private_json(path, {"client_id": "test-client", "refresh_token": "old-account"})
            fresh = {"access_token": "new-account", "scope": " ".join(gh.SCOPES)}
            with (
                patch.object(gh, "TOKEN_FILE", path),
                patch.object(gh, "authorize", return_value=fresh),
            ):
                tokens = gh.get_tokens({"client_id": "test-client"}, reauthorize=True)
            self.assertNotIn("refresh_token", tokens)


if __name__ == "__main__":
    unittest.main()
