"""Tests for tool discovery and release-payload validation.

Copyright (C) 2026 MP4-to-DCP contributors
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from scripts.stage_tools import sha256, validate_payload
from tool_discovery import bundled_tool_candidate, required_tool


class ToolDiscoveryTests(unittest.TestCase):
    def test_environment_override_is_preferred(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / "ffmpeg" / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
            executable.parent.mkdir()
            executable.write_bytes(b"test")
            executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
            with patch.dict(os.environ, {"MP4_DCP_TOOLS_DIR": directory}):
                self.assertEqual(Path(required_tool("ffmpeg")), executable)

    def test_candidate_rejects_unknown_tool(self):
        with self.assertRaises(ValueError):
            bundled_tool_candidate("not-a-tool")


class PayloadTests(unittest.TestCase):
    def make_payload(self, root: Path) -> Path:
        executable_suffix = ".exe" if os.name == "nt" else ""
        files = {
            f"tools/ffmpeg/ffmpeg{executable_suffix}": b"ffmpeg",
            f"tools/ffmpeg/ffprobe{executable_suffix}": b"ffprobe",
            f"tools/dcpomatic/dcpomatic2_create{executable_suffix}": b"create",
            f"tools/dcpomatic/dcpomatic2_cli{executable_suffix}": b"cli",
            "licenses/ffmpeg/COPYING": b"ffmpeg license",
            "licenses/ffmpeg/version.txt": b"ffmpeg version",
            "licenses/dcpomatic/COPYING": b"dcpomatic license",
            "licenses/dcpomatic/version.txt": b"dcpomatic version",
        }
        for relative, content in files.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            if relative.startswith("tools/"):
                path.chmod(path.stat().st_mode | stat.S_IXUSR)

        required_tools = {
            "ffmpeg": f"tools/ffmpeg/ffmpeg{executable_suffix}",
            "ffprobe": f"tools/ffmpeg/ffprobe{executable_suffix}",
            "dcpomatic2_create": f"tools/dcpomatic/dcpomatic2_create{executable_suffix}",
            "dcpomatic2_cli": f"tools/dcpomatic/dcpomatic2_cli{executable_suffix}",
        }
        components = {
            "ffmpeg": {
                "version": "test",
                "license_expression": "GPL-2.0-or-later",
                "source_url": "https://example.test/ffmpeg-source.tar.xz",
                "source_sha256": "1" * 64,
                "license_files": ["licenses/ffmpeg/COPYING"],
                "version_output_file": "licenses/ffmpeg/version.txt",
            },
            "dcpomatic": {
                "version": "test",
                "license_expression": "GPL-2.0-only",
                "source_url": "https://example.test/dcpomatic-source.tar.xz",
                "source_sha256": "2" * 64,
                "license_files": ["licenses/dcpomatic/COPYING"],
                "version_output_file": "licenses/dcpomatic/version.txt",
            },
        }
        manifest = {
            "schema": 1,
            "platform": "test-platform",
            "components": components,
            "required_tools": required_tools,
            "files": {relative: sha256(root / relative) for relative in files},
        }
        (root / "bundle-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        return root

    def test_complete_payload_is_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            payload = self.make_payload(Path(directory))
            self.assertEqual(validate_payload(payload)["platform"], "test-platform")

    def test_unlisted_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            payload = self.make_payload(Path(directory))
            (payload / "unlicensed.dll").write_bytes(b"surprise")
            with self.assertRaisesRegex(ValueError, "inventory mismatch"):
                validate_payload(payload)

    def test_changed_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            payload = self.make_payload(Path(directory))
            ffmpeg = payload / next(
                path for path in json.loads((payload / "bundle-manifest.json").read_text())["files"]
                if path.startswith("tools/ffmpeg/ffmpeg")
            )
            ffmpeg.write_bytes(b"tampered")
            with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                validate_payload(payload)


if __name__ == "__main__":
    unittest.main()
