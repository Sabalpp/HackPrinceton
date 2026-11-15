"""Exercise launcher shutdown and offline removal using disposable local fixtures."""

import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.local._paths import REPO_ROOT, WORKSPACE_ROOT  # noqa: E402

ROOT = WORKSPACE_ROOT


def module(name):
    spec = importlib.util.spec_from_file_location(
        name, REPO_ROOT / "scripts/local" / (name + ".py")
    )
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def main():
    launcher = module("run_farcare")
    remover = module("remove_chatoverflow")
    with tempfile.TemporaryDirectory(prefix="farcare-launch-check-") as tmp:
        fixture = Path(tmp)
        marker = fixture / "child-ready"
        child = subprocess.Popen(
            [
                sys.executable,
                "-B",
                "-c",
                "import pathlib,sys,time; pathlib.Path(sys.argv[1]).touch(); time.sleep(60)",
                str(marker),
            ],
            start_new_session=True,
        )
        try:
            launcher.stop_children([child])
            assert child.poll() is not None, "Launcher left its child running"
        finally:
            if child.poll() is None:
                os.killpg(child.pid, 9)
                child.wait()

        home = fixture / "home"
        hooks = home / ".codex/hooks.json"
        hooks.parent.mkdir(parents=True)
        original = {
            "hooks": {
                "Stop": [
                    {
                        "hooks": [
                            {"type": "command", "command": "bash chatoverflow-stop.sh"},
                            {"type": "command", "command": "echo keep-me"},
                        ]
                    }
                ]
            },
            "other_setting": True,
        }
        hooks.write_text(json.dumps(original))
        instructions = home / ".codex/AGENTS.md"
        instructions.write_text(
            "Preserve this.\n<!-- chatoverflow:start -->\nremove\n<!-- chatoverflow:end -->\n"
        )
        config = home / ".config/chatoverflow"
        config.mkdir(parents=True)
        (config / "config.json").write_text("{}")
        (config / "queued-note.json").write_text('{"synthetic":true}')
        package = home / ".nvm/versions/node/v99/lib/node_modules/chatoverflow"
        (package / "bin").mkdir(parents=True)
        (package / "package.json").write_text('{"name":"chatoverflow"}')
        (package / "bin/chatoverflow.js").write_text("// fixture")
        bindir = package.parents[2] / "bin"
        bindir.mkdir()
        (bindir / "node").write_text("keep runtime")
        (bindir / "chatoverflow").symlink_to(package / "bin/chatoverflow.js")
        with (
            patch.object(remover.Path, "home", return_value=home),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            remover.remove()
        cleaned = json.loads(hooks.read_text())
        assert cleaned["other_setting"] is True
        assert cleaned["hooks"]["Stop"][0]["hooks"] == [
            {"type": "command", "command": "echo keep-me"}
        ]
        assert instructions.read_text() == "Preserve this.\n"
        assert not config.exists() and not package.exists()
        assert not (bindir / "chatoverflow").exists()
        assert (bindir / "node").read_text() == "keep runtime"
    print(
        "PASS: launcher reaps its child; offline removal preserves unrelated settings/runtime in a disposable fixture."
    )
    print("No actual global settings were changed; no network connections or messages were made.")


if __name__ == "__main__":
    main()
