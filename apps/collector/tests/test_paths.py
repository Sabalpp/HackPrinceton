"""Move regressions: subprocess imports, SQL resources, and private data locations."""

import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from farcare_collector.integrations import google_health
from farcare_collector.paths import COLLECTOR_ROOT, REPO_ROOT, SQL_ROOT
from farcare_collector.services import health_pipeline


class RelocationTests(unittest.TestCase):
    def test_commands_import_from_an_unrelated_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            env = dict(os.environ, PYTHONPATH=str(COLLECTOR_ROOT), FARCARE_WORKSPACE=directory)
            commands = [
                ["-m", "farcare_collector.integrations.fitbit_hr"],
                ["-m", "farcare_collector.integrations.google_health"],
                ["-m", "farcare_collector.services.health_pipeline"],
                ["-m", "farcare_collector.health.hr_analysis"],
                [str(REPO_ROOT / "scripts/local/run_farcare.py")],
                [str(REPO_ROOT / "scripts/local/prepare_farcare_preview.py")],
                [str(REPO_ROOT / "research/fitbit_air/air_probe.py")],
            ]
            for command in commands:
                with self.subTest(command=command):
                    result = subprocess.run(
                        [sys.executable, *command, "--help"],
                        cwd=directory,
                        env=env,
                        capture_output=True,
                        text=True,
                        timeout=10,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn("usage:", result.stdout)
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_integrations_share_explicit_private_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            script = (
                "from farcare_collector.integrations import fitbit_hr, google_health; "
                "from farcare_collector.services import health_pipeline; "
                "import json; print(json.dumps([str(fitbit_hr.ROOT), str(google_health.ROOT), "
                "str(health_pipeline.ROOT)]))"
            )
            result = subprocess.run(
                [sys.executable, "-c", script],
                cwd=directory,
                env=dict(os.environ, PYTHONPATH=str(COLLECTOR_ROOT), FARCARE_WORKSPACE=directory),
                capture_output=True,
                text=True,
                check=True,
                timeout=10,
            )
            self.assertEqual(json.loads(result.stdout), [str(Path(directory).resolve())] * 3)

    def test_refresh_runs_importable_module_and_keeps_data_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            token_file = Path(directory) / "tokens.json"
            token_file.write_text(
                json.dumps(
                    {
                        "refresh_token": "synthetic-refresh",
                        "client_id": "synthetic-client",
                        "scope": " ".join(google_health.SCOPES),
                    }
                )
            )
            process = Mock()
            process.wait.return_value = 0
            process.poll.return_value = 0
            with (
                patch.object(google_health, "TOKEN_FILE", token_file),
                patch.object(
                    google_health,
                    "load_credentials",
                    return_value={"client_id": "synthetic-client"},
                ),
                patch.object(health_pipeline, "ROOT", Path(directory)),
                patch.object(health_pipeline.subprocess, "Popen", return_value=process) as start,
            ):
                health_pipeline.cloud_refresh(2, threading.Event())
            command = start.call_args.args[0]
            self.assertEqual(
                command[:3], [sys.executable, "-m", "farcare_collector.integrations.google_health"]
            )
            options = start.call_args.kwargs
            self.assertEqual(options["env"]["FARCARE_WORKSPACE"], directory)
            # This is how a launched collector refresh starts: no editable install
            # or inherited PYTHONPATH is needed to find the sibling package.
            env = options["env"].copy()
            env.pop("PYTHONPATH", None)
            result = subprocess.run(
                [*command[:3], "--help"],
                cwd=options["cwd"],
                env=env,
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_sql_is_resolved_from_source_instead_of_private_data(self):
        for name in ("schema.sql", "archive.sql", "ble_rollup.sql", "verify.sql"):
            self.assertTrue((SQL_ROOT / name).is_file(), name)
            self.assertTrue((SQL_ROOT / name).read_text().strip(), name)

    def test_mitmproxy_addon_loader_can_resolve_relocated_imports(self):
        with tempfile.TemporaryDirectory() as directory:
            script = (
                "from importlib.machinery import SourceFileLoader; import sys; "
                "module = SourceFileLoader('__mitmproxy_script__.air_key_capture', sys.argv[1]).load_module(); "
                "assert str(module.ROOT) == sys.argv[2]"
            )
            env = dict(os.environ, FARCARE_WORKSPACE=directory)
            env.pop("PYTHONPATH", None)
            result = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    script,
                    str(REPO_ROOT / "research/fitbit_air/air_key_capture.py"),
                    str(Path(directory).resolve()),
                ],
                cwd=directory,
                env=env,
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(list(Path(directory).iterdir()), [])
