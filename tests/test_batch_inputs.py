"""Tests for standalone CLI directory input.

Copyright (C) 2026 MP4-to-DCP contributors
SPDX-License-Identifier: GPL-2.0-or-later
"""

from argparse import Namespace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import mp4_to_dcp


class BatchInputTests(unittest.TestCase):
    def test_directory_discovers_only_top_level_mp4_files_in_name_order(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("z-last.mp4", "A-first.MP4", "ignore.mov", "notes.txt"):
                (root / name).touch()
            nested = root / "nested"
            nested.mkdir()
            (nested / "ignored.mp4").touch()

            discovered = mp4_to_dcp.discover_inputs(root)

            self.assertEqual(
                [path.name for path in discovered],
                ["A-first.MP4", "z-last.mp4"],
            )

    def test_batch_attempts_every_file_after_a_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for number in range(5):
                (root / f"trailer-{number}.mp4").touch()
            output = root / "output"
            args = Namespace(input=root, output_dir=output, name=None)
            attempted = []

            def fake_convert(_args, input_file, output_dir):
                attempted.append((input_file.name, output_dir))
                return 1 if input_file.name == "trailer-2.mp4" else 0

            with patch.object(mp4_to_dcp, "parse_args", return_value=args), patch.object(
                mp4_to_dcp, "convert_one", side_effect=fake_convert
            ):
                result = mp4_to_dcp.main()

            self.assertEqual(result, 1)
            self.assertEqual(len(attempted), 5)
            self.assertTrue(all(item[1] == output.resolve() for item in attempted))


if __name__ == "__main__":
    unittest.main()
