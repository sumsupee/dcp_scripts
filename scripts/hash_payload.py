#!/usr/bin/env python3
"""Refresh the file hashes in a tool payload's bundle manifest.

Copyright (C) 2026 MP4-to-DCP contributors
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from stage_tools import sha256


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("payload", type=Path)
    args = parser.parse_args()
    payload = args.payload.resolve()
    manifest_path = payload / "bundle-manifest.json"

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["files"] = {
            path.relative_to(payload).as_posix(): sha256(path)
            for path in sorted(payload.rglob("*"))
            if path.is_file() and path != manifest_path
        }
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except (OSError, json.JSONDecodeError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    print(f"Recorded {len(manifest['files'])} files in {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
