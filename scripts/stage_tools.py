#!/usr/bin/env python3
"""Validate and stage a platform tool payload for application packaging.

Copyright (C) 2026 MP4-to-DCP contributors
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import sys


REQUIRED_COMPONENT_FIELDS = {
    "version",
    "license_expression",
    "source_url",
    "source_sha256",
    "license_files",
    "version_output_file",
}
REQUIRED_TOOLS = {
    "ffmpeg",
    "ffprobe",
    "dcpomatic2_create",
    "dcpomatic2_cli",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def safe_relative(value: str) -> Path:
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError(f"Manifest path is not a safe relative path: {value!r}")
    return path


def validate_payload(payload: Path) -> dict:
    manifest_path = payload / "bundle-manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ValueError(f"Missing {manifest_path}") from error
    except json.JSONDecodeError as error:
        raise ValueError(f"Invalid JSON in {manifest_path}: {error}") from error

    if manifest.get("schema") != 1:
        raise ValueError("bundle-manifest.json must use schema 1")
    if not str(manifest.get("platform", "")).strip():
        raise ValueError("Manifest platform is missing")

    components = manifest.get("components")
    if not isinstance(components, dict) or set(components) != {"ffmpeg", "dcpomatic"}:
        raise ValueError("Manifest must describe ffmpeg and dcpomatic components")

    for name, component in components.items():
        missing = REQUIRED_COMPONENT_FIELDS - set(component)
        if missing:
            raise ValueError(f"{name} metadata is missing: {', '.join(sorted(missing))}")
        if not component["license_files"]:
            raise ValueError(f"{name} must carry at least one license file")
        for field in ("version", "license_expression", "source_url"):
            value = str(component[field]).strip()
            if not value or "REPLACE" in value:
                raise ValueError(f"{name}.{field} has not been completed")
        source_digest = str(component["source_sha256"]).lower()
        if len(source_digest) != 64 or any(
            character not in "0123456789abcdef" for character in source_digest
        ):
            raise ValueError(f"{name}.source_sha256 is not a SHA-256 digest")

        notice_paths = [*component["license_files"], component["version_output_file"]]
        for relative in notice_paths:
            notice = payload / safe_relative(relative)
            if not notice.is_file() or notice.stat().st_size == 0:
                raise ValueError(f"Missing or empty {name} notice: {notice}")

    tools = manifest.get("required_tools")
    if not isinstance(tools, dict) or set(tools) != REQUIRED_TOOLS:
        raise ValueError("Manifest required_tools must list exactly the four runtime tools")
    for name, relative in tools.items():
        executable = payload / safe_relative(relative)
        if not executable.is_file():
            raise ValueError(f"Missing required tool {name}: {executable}")
        if os.name != "nt" and not executable.stat().st_mode & stat.S_IXUSR:
            raise ValueError(f"Required tool is not executable: {executable}")

    recorded = manifest.get("files")
    if not isinstance(recorded, dict) or not recorded:
        raise ValueError("Manifest files table is empty")

    actual = {
        path.relative_to(payload).as_posix()
        for path in payload.rglob("*")
        if path.is_file() and path != manifest_path
    }
    declared = set(recorded)
    if actual != declared:
        missing = sorted(actual - declared)
        stale = sorted(declared - actual)
        raise ValueError(f"Manifest inventory mismatch; unlisted={missing}, missing={stale}")

    for relative, expected in recorded.items():
        expected = str(expected).lower()
        actual_digest = sha256(payload / safe_relative(relative))
        if actual_digest != expected:
            raise ValueError(f"SHA-256 mismatch for {relative}")

    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("payload", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    payload = args.payload.resolve()
    destination = args.destination.resolve()

    try:
        manifest = validate_payload(payload)
        if destination.exists():
            raise ValueError(f"Destination already exists: {destination}")
        shutil.copytree(payload, destination, copy_function=shutil.copy2)

        project_root = Path(__file__).resolve().parent.parent
        license_dir = destination / "licenses" / "mp4-to-dcp"
        license_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(project_root / "LICENSE", license_dir / "COPYING")
        shutil.copy2(
            project_root / "THIRD_PARTY_NOTICES.md",
            destination / "THIRD_PARTY_NOTICES.md",
        )
    except (OSError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    print(f"Validated {manifest['platform']} payload and staged it at {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
