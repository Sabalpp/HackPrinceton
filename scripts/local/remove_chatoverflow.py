#!/usr/bin/env python3
"""Remove local ChatOverflow integration offline. Does not contact its servers."""

import json
import os
import re
import shutil
import sys
from pathlib import Path


def own_command(value):
    return isinstance(value, str) and bool(re.search(r"\b(?:chatoverflow|chato)\b", value))


def clean_settings(settings):
    if isinstance(settings.get("hooks"), dict):
        for event, groups in list(settings["hooks"].items()):
            if not isinstance(groups, list):
                continue
            kept = []
            for group in groups:
                if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                    kept.append(group)
                    continue
                entries = [
                    entry
                    for entry in group["hooks"]
                    if not (isinstance(entry, dict) and own_command(entry.get("command")))
                ]
                if entries:
                    kept.append({**group, "hooks": entries})
            if kept:
                settings["hooks"][event] = kept
            else:
                del settings["hooks"][event]
        if not settings["hooks"]:
            del settings["hooks"]
    permissions = settings.get("permissions")
    if isinstance(permissions, dict):
        for key in ("allow", "ask", "deny"):
            if isinstance(permissions.get(key), list):
                permissions[key] = [rule for rule in permissions[key] if not own_command(rule)]
    return settings


def write_atomic(path, content):
    temporary = path.with_name("." + path.name + ".remove-chatoverflow.tmp")
    fd = os.open(str(temporary), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w") as output:
            output.write(content)
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def remove():
    home = Path.home()
    config_dir = home / ".config/chatoverflow"
    config_file = config_dir / "config.json"
    config = json.loads(config_file.read_text()) if config_file.exists() else {}
    settings = [home / ".codex/hooks.json", home / ".claude/settings.json"]
    instructions = [
        home / ".codex/AGENTS.md",
        home / ".codex/AGENTS.override.md",
        home / ".claude/CLAUDE.md",
    ]
    if config.get("scope") == "project" and config.get("project_path"):
        project = Path(config["project_path"])
        settings.append(project / ".claude/settings.local.json")
        instructions.append(project / "CLAUDE.local.md")
    for path in settings:
        if not path.exists():
            continue
        old = path.read_text()
        new = json.dumps(clean_settings(json.loads(old)), indent=2) + "\n"
        if new != old:
            write_atomic(path, new)
    for path in instructions:
        if not path.exists():
            continue
        old = path.read_text()
        new = re.sub(
            r"<!-- chatoverflow:start -->.*?<!-- chatoverflow:end -->\s*", "", old, flags=re.S
        )
        if old != new:
            write_atomic(path, new)
    for path in (
        home / ".codex/hooks/chatoverflow-stop.sh",
        home / ".claude/hooks/chatoverflow-stop.sh",
        home / ".codex/rules/chatoverflow.rules",
    ):
        if path.exists():
            path.unlink()
    # Remove only packages that declare the exact ChatOverflow package name.
    for package in (home / ".nvm/versions/node").glob("*/lib/node_modules/chatoverflow"):
        manifest = package / "package.json"
        if not manifest.exists() or json.loads(manifest.read_text()).get("name") != "chatoverflow":
            continue
        bindir = package.parents[2] / "bin"
        for name in ("chatoverflow", "chato"):
            binary = bindir / name
            if binary.is_symlink() and package.resolve() in binary.resolve().parents:
                binary.unlink()
        shutil.rmtree(package)
    if config_dir.exists():
        shutil.rmtree(config_dir)
    for path in settings:
        if path.exists() and clean_settings(json.loads(path.read_text())) != json.loads(
            path.read_text()
        ):
            raise RuntimeError("A ChatOverflow hook remains.")
    print(
        "Removed local ChatOverflow hooks, instructions, configuration and CLI. No notes were uploaded."
    )


if __name__ == "__main__":
    try:
        remove()
    except PermissionError:
        print(
            "Permission denied: global settings are outside this workspace. Run this script in your Mac Terminal.",
            file=sys.stderr,
        )
        sys.exit(1)
