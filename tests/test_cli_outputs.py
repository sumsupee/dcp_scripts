"""Command-line destination behavior shared by the video converters."""

from contextlib import redirect_stderr, redirect_stdout
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import mp4_fest
import mp4_to_dcp
import mp4_to_proludio


class OutputCliTests(unittest.TestCase):
    def test_all_converters_accept_optional_output_directory(self):
        for module in (mp4_to_dcp, mp4_to_proludio, mp4_fest):
            parse = getattr(module, "command_line", None) or module.parse_args
            for options, expected in (
                ([], Path.cwd()),
                (["--output-dir", "output folder"], Path("output folder")),
            ):
                with self.subTest(module=module.__name__, options=options), patch(
                    "sys.argv", [module.__name__, "source.mov", *options]
                ):
                    self.assertEqual(parse().output_dir, expected)

    def test_mp4_destinations_reach_conversion(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mkv"
            source.touch()
            for module in (mp4_to_proludio, mp4_fest):
                for input_path in (source, root):
                    for options, expected in (
                        ([], Path.cwd() / "source.mp4"),
                        (["--output-dir", str(root / "new.mp4")], root / "new.mp4" / "source.mp4"),
                        ([str(root / "legacy")], root / "legacy" / "source.mp4"),
                    ):
                        with self.subTest(module=module.__name__, input=input_path, options=options), patch(
                            "sys.argv", [module.__name__, str(input_path), *options]
                        ), patch.object(module, "require_tool", side_effect=lambda name: name), patch.object(
                            module, "convert_one"
                        ) as convert, redirect_stdout(io.StringIO()):
                            self.assertEqual(module.main(), 0)
                            self.assertEqual(convert.call_args.args[3], expected)

                with patch("sys.argv", [module.__name__, str(source), str(root / "renamed.mp4")]), patch.object(
                    module, "require_tool", side_effect=lambda name: name
                ), patch.object(module, "convert_one") as convert, redirect_stdout(io.StringIO()):
                    self.assertEqual(module.main(), 0)
                    self.assertEqual(convert.call_args.args[3], root / "renamed.mp4")

    def test_mp4_converters_reject_conflicting_destinations(self):
        for module in (mp4_to_proludio, mp4_fest):
            with self.subTest(module=module.__name__), patch(
                "sys.argv", [module.__name__, "source.mov", "legacy.mp4", "--output-dir", "output"]
            ), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                module.command_line()
            self.assertEqual(error.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
