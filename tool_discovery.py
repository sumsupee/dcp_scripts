"""Find bundled media tools, with PATH as a development fallback.

Copyright (C) 2026 MP4-to-DCP contributors
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import sys


TOOL_FILENAMES = {
    "ffmpeg": "ffmpeg.exe" if os.name == "nt" else "ffmpeg",
    "ffprobe": "ffprobe.exe" if os.name == "nt" else "ffprobe",
    "dcpomatic2_create": (
        "dcpomatic2_create.exe" if os.name == "nt" else "dcpomatic2_create"
    ),
    "dcpomatic2_cli": (
        "dcpomatic2_cli.exe" if os.name == "nt" else "dcpomatic2_cli"
    ),
    "dcpomatic2_verify_cli": (
        "dcpomatic2_verify_cli.exe" if os.name == "nt" else "dcpomatic2_verify_cli"
    ),
}


def bundled_tools_root() -> Path:
    """Return the tools directory for source and installed application layouts."""
    override = os.environ.get("MP4_DCP_TOOLS_DIR")
    if override:
        return Path(override).expanduser().resolve()

    executable = Path(sys.executable).resolve()
    if sys.platform == "darwin" and executable.parent.name == "MacOS":
        return executable.parent.parent / "Resources" / "tools"

    app_dir = os.environ.get("APPDIR")
    if sys.platform.startswith("linux") and app_dir:
        return Path(app_dir) / "usr" / "lib" / "mp4-to-dcp" / "tools"

    installed = executable.parent / "tools"
    if installed.is_dir():
        return installed

    return Path(__file__).resolve().parent / "tools"


def bundled_tool_candidate(name: str) -> Path:
    try:
        filename = TOOL_FILENAMES[name]
    except KeyError as error:
        raise ValueError(f"Unknown external tool: {name}") from error

    component = "ffmpeg" if name in {"ffmpeg", "ffprobe"} else "dcpomatic"
    return bundled_tools_root() / component / filename


def required_tool(name: str) -> str:
    """Resolve a required executable, preferring the audited bundled copy."""
    candidate = bundled_tool_candidate(name)
    if candidate.is_file():
        if os.name != "nt" and not os.access(candidate, os.X_OK):
            raise RuntimeError(f"Bundled tool is not executable: {candidate}")
        return os.fspath(candidate)

    filename = TOOL_FILENAMES.get(name, name)
    system_path = shutil.which(filename)
    if system_path:
        return system_path

    raise RuntimeError(
        f"Required tool '{name}' was not found. Expected bundled copy at "
        f"{candidate}, or install it in PATH."
    )
