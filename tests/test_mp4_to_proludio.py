"""Tests for the projector-safe MP4 converter."""

from pathlib import Path
import unittest

import mp4_to_proludio


class LoudnormTests(unittest.TestCase):
    def test_extracts_loudnorm_measurements_from_ffmpeg_log(self):
        log = '''
        unrelated output
        {
            "input_i": "-13.60",
            "input_tp": "-0.14",
            "input_lra": "14.70",
            "input_thresh": "-24.83",
            "target_offset": "-2.09"
        }
        '''

        values = mp4_to_proludio.parse_loudnorm_json(log)

        self.assertEqual(values["input_i"], "-13.60")
        self.assertEqual(values["target_offset"], "-2.09")


class CommandTests(unittest.TestCase):
    def test_output_command_pins_compatibility_properties(self):
        command = mp4_to_proludio.output_command(
            "ffmpeg",
            Path("input.mov"),
            Path("output.mp4"),
            0,
            True,
            {
                "input_i": "-13.60",
                "input_tp": "-0.14",
                "input_lra": "14.70",
                "input_thresh": "-24.83",
                "target_offset": "-2.09",
            },
            "aformat=channel_layouts=stereo",
            "stereo",
            2,
        )

        self.assertIn("libx264", command)
        self.assertIn("avc1", command)
        self.assertIn("yuv420p", command)
        self.assertIn("aac_low", command)
        self.assertIn("48000", command)
        self.assertIn("+faststart", command)
        video_filter = command[command.index("-vf") + 1]
        self.assertIn("scale=1920:1080", video_filter)
        self.assertIn("fps=24000/1001", video_filter)

    def test_no_audio_command_adds_silent_stereo_input(self):
        command = mp4_to_proludio.output_command(
            "ffmpeg",
            Path("input.mov"),
            Path("output.mp4"),
            0,
            False,
            None,
            "",
            "stereo",
            2,
        )

        self.assertIn("anullsrc=channel_layout=stereo:sample_rate=48000", command)
        self.assertIn("1:a:0", command)


class AudioLayoutTests(unittest.TestCase):
    def test_auto_preserves_native_5_1(self):
        layout, channels, audio_filter = mp4_to_proludio.audio_preparation(
            {"channels": 6, "channel_layout": "5.1"}, "auto"
        )

        self.assertEqual((layout, channels), ("5.1", 6))
        self.assertIn("c5=c5", audio_filter)

    def test_auto_uses_same_stereo_surround_extraction_as_dcp(self):
        layout, channels, audio_filter = mp4_to_proludio.audio_preparation(
            {"channels": 2, "channel_layout": "stereo"}, "auto"
        )

        self.assertEqual((layout, channels), ("5.1", 6))
        self.assertEqual(audio_filter, mp4_to_proludio.STEREO_SURROUND_FILTER)
        self.assertIn("lfe=false", audio_filter)
        self.assertIn("sl_out=0.5:sr_out=0.5", audio_filter)

    def test_side_5_1_is_remapped_to_standard_back_layout(self):
        layout, channels, audio_filter = mp4_to_proludio.audio_preparation(
            {"channels": 6, "channel_layout": "5.1(side)"}, "auto"
        )

        self.assertEqual((layout, channels), ("5.1", 6))
        self.assertIn("BL=SL", audio_filter)
        self.assertIn("BR=SR", audio_filter)

    def test_directory_ignores_mac_metadata_files(self):
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "trailer.mp4").touch()
            (root / "._trailer.mp4").touch()

            self.assertEqual(
                [item.name for item in mp4_to_proludio.discover_inputs(root)],
                ["trailer.mp4"],
            )


if __name__ == "__main__":
    unittest.main()
