"""Tests for 5.1 preparation and DCP channel configuration."""

import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import mp4_to_dcp


class AudioPreparationTests(unittest.TestCase):
    def test_mono_is_mapped_only_to_center(self):
        audio_filter = mp4_to_dcp.audio_preparation_filter(
            {"channels": 1, "channel_layout": "mono"}, "auto"
        )

        self.assertIn("c2=c0", audio_filter)
        self.assertIn("c0=0*c0", audio_filter)
        self.assertIn("c5=0*c0", audio_filter)

    def test_stereo_auto_uses_restrained_surround_without_lfe(self):
        audio_filter = mp4_to_dcp.audio_preparation_filter(
            {"channels": 2, "channel_layout": "stereo"}, "auto"
        )

        self.assertEqual(audio_filter, mp4_to_dcp.STEREO_SURROUND_FILTER)
        self.assertIn("lfe=false", audio_filter)
        self.assertIn("sl_out=0.5:sr_out=0.5", audio_filter)

    def test_stereo_preserve_keeps_only_left_and_right(self):
        audio_filter = mp4_to_dcp.audio_preparation_filter(
            {"channels": 2, "channel_layout": "stereo"}, "preserve"
        )

        self.assertIn("c0=c0|c1=c1", audio_filter)
        self.assertIn("c2=0*c0", audio_filter)
        self.assertIn("c5=0*c0", audio_filter)

    def test_side_surround_layout_is_remapped_to_dcp_order(self):
        audio_filter = mp4_to_dcp.audio_preparation_filter(
            {"channels": 6, "channel_layout": "5.1(side)"}, "auto"
        )

        self.assertEqual(
            audio_filter,
            "pan=5.1|FL=FL|FR=FR|FC=FC|LFE=LFE|BL=SL|BR=SR",
        )

    def test_unusual_layout_honors_preserve_mode(self):
        audio_filter = mp4_to_dcp.audio_preparation_filter(
            {"channels": 8, "channel_layout": "7.1"}, "preserve"
        )

        self.assertTrue(audio_filter.startswith("aformat=channel_layouts=stereo,"))
        self.assertNotIn("surround=", audio_filter)


class AudioStreamTests(unittest.TestCase):
    @patch("mp4_to_dcp.subprocess.run")
    def test_probe_source_accepts_video_without_audio(self, run):
        run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps({"streams": [{"codec_type": "video", "index": 0}]}),
            stderr="",
        )

        video, audio, count = mp4_to_dcp.probe_source(
            "ffprobe", Path("silent.mp4"), 0
        )

        self.assertEqual(video["index"], 0)
        self.assertIsNone(audio)
        self.assertEqual(count, 0)

    @patch("mp4_to_dcp.subprocess.run")
    def test_probe_source_rejects_nonzero_audio_stream_when_none_exist(self, run):
        run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps({"streams": [{"codec_type": "video", "index": 0}]}),
            stderr="",
        )

        with self.assertRaisesRegex(RuntimeError, "only --audio-stream 0 is valid"):
            mp4_to_dcp.probe_source("ffprobe", Path("silent.mp4"), 1)

    @patch("mp4_to_dcp.subprocess.run")
    def test_probe_source_selects_requested_audio_stream(self, run):
        run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps(
                {
                    "streams": [
                        {"codec_type": "video", "index": 0},
                        {"codec_type": "audio", "index": 1, "channels": 2},
                        {"codec_type": "audio", "index": 2, "channels": 6},
                    ]
                }
            ),
            stderr="",
        )

        _video, audio, count = mp4_to_dcp.probe_source(
            "ffprobe", Path("movie.mp4"), 1
        )

        self.assertEqual(audio["channels"], 6)
        self.assertEqual(count, 2)

    @patch("mp4_to_dcp.subprocess.run")
    def test_probe_source_rejects_missing_audio_stream_number(self, run):
        run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps(
                {
                    "streams": [
                        {"codec_type": "video", "index": 0},
                        {"codec_type": "audio", "index": 1, "channels": 2},
                    ]
                }
            ),
            stderr="",
        )

        with self.assertRaisesRegex(RuntimeError, "source has 1 audio stream"):
            mp4_to_dcp.probe_source("ffprobe", Path("movie.mp4"), 1)


class DcpCommandTests(unittest.TestCase):
    @patch("mp4_to_dcp.subprocess.run")
    def test_silent_master_adds_six_channel_null_audio(self, run):
        mp4_to_dcp.create_silent_master(
            "ffmpeg", Path("silent.mp4"), Path("master.mkv")
        )

        command = run.call_args.args[0]
        self.assertIn("anullsrc=channel_layout=5.1:sample_rate=48000", command)
        self.assertIn("-shortest", command)
        self.assertEqual(command[command.index("-map") + 1], "0:v:0")
        self.assertIn("1:a:0", command)

    @patch("mp4_to_dcp.find_dcp_directory", return_value=Path("project"))
    @patch("mp4_to_dcp.subprocess.run")
    def test_dcp_project_requests_six_audio_channels(self, run, _find):
        mp4_to_dcp.create_dcp(
            "create",
            "cli",
            Path("master.mkv"),
            Path("project"),
            "Title",
            "TLR",
            "flat",
        )

        create_command = run.call_args_list[0].args[0]
        option_index = create_command.index("--audio-channels")
        self.assertEqual(create_command[option_index + 1], "6")


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg required")
class FfmpegIntegrationTests(unittest.TestCase):
    def test_video_only_source_gets_silent_5_1_master(self):
        ffmpeg = shutil.which("ffmpeg")
        ffprobe = shutil.which("ffprobe")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "silent.mkv"
            master = root / "master.mkv"
            subprocess.run(
                [
                    ffmpeg,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=size=320x180:rate=24:duration=1",
                    "-c:v",
                    "mpeg4",
                    str(source),
                ],
                check=True,
            )

            mp4_to_dcp.create_silent_master(ffmpeg, source, master)

            self.assertEqual(
                mp4_to_dcp.probe_audio_channel_count(ffprobe, master), 6
            )
            video, audio, count = mp4_to_dcp.probe_source(ffprobe, master)
            self.assertIsNotNone(video)
            self.assertEqual(audio["channels"], 6)
            self.assertEqual(count, 1)

    def test_stereo_master_is_normalized_5_1_with_silent_lfe(self):
        ffmpeg = shutil.which("ffmpeg")
        ffprobe = shutil.which("ffprobe")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "stereo.mkv"
            master = root / "master.mkv"
            subprocess.run(
                [
                    ffmpeg,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=size=320x180:rate=24:duration=5",
                    "-f",
                    "lavfi",
                    "-i",
                    (
                        "aevalsrc=0.08*sin(2*PI*440*t)|"
                        "0.08*sin(2*PI*660*t):s=48000:d=5:c=stereo"
                    ),
                    "-shortest",
                    "-c:v",
                    "mpeg4",
                    "-c:a",
                    "pcm_s16le",
                    str(source),
                ],
                check=True,
            )

            mp4_to_dcp.create_normalized_master(
                ffmpeg,
                source,
                master,
                -24.0,
                -2.0,
                0,
                mp4_to_dcp.STEREO_SURROUND_FILTER,
            )

            self.assertEqual(
                mp4_to_dcp.probe_audio_channel_count(ffprobe, master), 6
            )
            lfe = subprocess.run(
                [
                    ffmpeg,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-i",
                    str(master),
                    "-map",
                    "0:a:0",
                    "-af",
                    "pan=mono|c0=LFE",
                    "-f",
                    "s32le",
                    "-",
                ],
                check=True,
                capture_output=True,
            ).stdout
            self.assertTrue(lfe)
            self.assertFalse(any(lfe), "Automatically generated LFE must be silent")


if __name__ == "__main__":
    unittest.main()
