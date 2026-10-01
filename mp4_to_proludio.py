#!/usr/bin/env python3
"""Create conservative, hardware-player-compatible 1080p MP4 files."""

from __future__ import annotations

import argparse
from slide_support import add_slide_arguments, append_slide, find_slide
from fractions import Fraction
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


VIDEO_EXTENSIONS = {".avi", ".m4v", ".mkv", ".mov", ".mp4", ".webm"}
TARGET_RATE = Fraction(24000, 1001)
LOUDNORM_KEYS = ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")
STEREO_SURROUND_FILTER = (
    "surround=chl_in=stereo:chl_out=5.1:lfe=false:smooth=0.5:"
    "sl_out=0.5:sr_out=0.5"
)


class ConversionError(RuntimeError):
    """A source could not be converted or the result failed verification."""


def command_line() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Convert one video, or every supported video in a folder, to a "
            "projector-safe 1920x1080 H.264/AAC MP4."
        )
    )
    parser.add_argument("input", type=Path, help="input video or folder")
    parser.add_argument(
        "output",
        type=Path,
        help=(
            "output MP4 (for one input file) or output folder; an existing "
            "directory is always treated as a folder"
        ),
    )
    parser.add_argument(
        "--audio-stream",
        type=int,
        default=0,
        metavar="N",
        help="zero-based audio stream to use (default: 0)",
    )
    parser.add_argument(
        "--audio-layout",
        choices=("auto", "stereo", "5.1"),
        default="auto",
        help=(
            "auto prepares 5.1 using the same rules as mp4_to_dcp; stereo forces "
            "a downmix; 5.1 is an explicit alias for auto (default: auto)"
        ),
    )
    parser.add_argument(
        "--overwrite", action="store_true", help="replace existing output MP4 files"
    )
    add_slide_arguments(parser)
    return parser.parse_args()


def require_tool(name: str) -> str:
    executable = shutil.which(name)
    if executable:
        return executable
    raise ConversionError(
        f"'{name}' was not found on PATH. Install FFmpeg and confirm that "
        f"`{name} -version` works in this terminal."
    )


def run(command: list[str], *, capture: bool = False) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            command,
            check=True,
            text=capture,
            capture_output=capture,
        )
    except subprocess.CalledProcessError as error:
        detail = ""
        if capture and error.stderr:
            detail = f"\n{error.stderr.strip()}"
        raise ConversionError(f"Command failed with exit code {error.returncode}.{detail}") from error


def probe_source(ffprobe: str, source: Path, audio_stream: int) -> tuple[dict, dict | None]:
    result = run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "stream=index,codec_type,codec_name,channels,channel_layout",
            "-of",
            "json",
            os.fspath(source),
        ],
        capture=True,
    )
    try:
        streams = json.loads(result.stdout).get("streams", [])
    except json.JSONDecodeError as error:
        raise ConversionError("ffprobe returned invalid JSON for the source.") from error

    videos = [item for item in streams if item.get("codec_type") == "video"]
    audios = [item for item in streams if item.get("codec_type") == "audio"]
    if not videos:
        raise ConversionError("The source has no video stream.")
    if audio_stream < 0:
        raise ConversionError("--audio-stream cannot be negative.")
    if audios and audio_stream >= len(audios):
        raise ConversionError(
            f"Audio stream {audio_stream} was requested, but the source has "
            f"{len(audios)} audio stream(s)."
        )
    if not audios and audio_stream != 0:
        raise ConversionError("The source has no audio streams; only --audio-stream 0 is valid.")
    return videos[0], audios[audio_stream] if audios else None


def parse_loudnorm_json(stderr: str) -> dict[str, str]:
    """Extract and validate FFmpeg's loudnorm measurement object."""
    candidates = re.findall(r"\{[^{}]*\}", stderr, flags=re.DOTALL)
    for candidate in reversed(candidates):
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if not all(key in data for key in LOUDNORM_KEYS):
            continue
        return {key: str(data[key]) for key in LOUDNORM_KEYS}
    raise ConversionError("FFmpeg did not return usable loudness measurements.")


def audio_preparation(audio: dict | None, requested_layout: str) -> tuple[str, int, str]:
    """Return output layout, channel count, and pre-normalization filter."""
    channels = audio.get("channels") if audio else 0
    source_layout = (audio.get("channel_layout") or "").lower() if audio else ""
    if requested_layout == "stereo":
        return "stereo", 2, "aformat=channel_layouts=stereo"
    if not audio:
        return "5.1", 6, ""
    if channels == 1:
        return (
            "5.1",
            6,
            "pan=5.1|FL=0*c0|FR=0*c0|FC=c0|LFE=0*c0|BL=0*c0|BR=0*c0",
        )
    if channels == 2:
        return "5.1", 6, STEREO_SURROUND_FILTER
    if channels == 6 and source_layout == "5.1(side)":
        return "5.1", 6, "pan=5.1|FL=FL|FR=FR|FC=FC|LFE=LFE|BL=SL|BR=SR"
    if channels == 6:
        return "5.1", 6, "pan=5.1|c0=c0|c1=c1|c2=c2|c3=c3|c4=c4|c5=c5"

    # This is the same fallback used by mp4_to_dcp.py: make a standard stereo
    # downmix first, then apply the restrained frequency-domain upmix.
    return "5.1", 6, f"aformat=channel_layouts=stereo,{STEREO_SURROUND_FILTER}"


def measure_audio(
    ffmpeg: str,
    source: Path,
    audio_stream: int,
    preparation_filter: str,
) -> dict[str, str] | None:
    print("  Pass 1/2: measuring audio loudness...")
    result = run(
        [
            ffmpeg,
            "-hide_banner",
            "-nostdin",
            "-i",
            os.fspath(source),
            "-map",
            f"0:a:{audio_stream}",
            "-vn",
            "-sn",
            "-dn",
            "-af",
            (
                f"{preparation_filter}," if preparation_filter else ""
            )
            + "loudnorm=I=-24:TP=-2:LRA=7:print_format=json",
            "-f",
            "null",
            "-",
        ],
        capture=True,
    )
    values = parse_loudnorm_json(result.stderr)

    # A fully silent track reports +/-inf. Those values cannot be supplied to
    # loudnorm's measured_* options, so use its safe single-pass mode instead.
    try:
        finite = all(math.isfinite(float(value)) for value in values.values())
    except ValueError:
        finite = False
    return values if finite else None


def loudnorm_filter(measurements: dict[str, str] | None) -> str:
    base = "loudnorm=I=-24:TP=-2:LRA=7"
    if measurements is None:
        return base
    return (
        f"{base}:measured_I={measurements['input_i']}:"
        f"measured_TP={measurements['input_tp']}:"
        f"measured_LRA={measurements['input_lra']}:"
        f"measured_thresh={measurements['input_thresh']}:"
        f"offset={measurements['target_offset']}:linear=true"
    )


def output_command(
    ffmpeg: str,
    source: Path,
    temporary_output: Path,
    audio_stream: int,
    has_audio: bool,
    measurements: dict[str, str] | None,
    preparation_filter: str,
    output_layout: str,
    output_channels: int,
) -> list[str]:
    video_filter = (
        "scale=1920:1080:force_original_aspect_ratio=decrease:flags=lanczos,"
        "pad=1920:1080:(ow-iw)/2:(oh-ih)/2:color=black,"
        "setsar=1,fps=24000/1001"
    )
    final_audio_format = (
        "aformat=sample_fmts=fltp:sample_rates=48000:"
        f"channel_layouts={output_layout},apad"
    )
    audio_filter_parts = []
    if preparation_filter:
        audio_filter_parts.append(preparation_filter)
    if has_audio:
        audio_filter_parts.append(loudnorm_filter(measurements))
    audio_filter_parts.append(final_audio_format)
    audio_filter = ",".join(audio_filter_parts)

    command = [ffmpeg, "-hide_banner", "-nostdin", "-y", "-i", os.fspath(source)]
    if not has_audio:
        command += [
            "-f",
            "lavfi",
            "-i",
            f"anullsrc=channel_layout={output_layout}:sample_rate=48000",
        ]
    command += [
        "-map",
        "0:v:0",
        "-map",
        f"0:a:{audio_stream}" if has_audio else "1:a:0",
        "-map_metadata",
        "-1",
        "-map_chapters",
        "-1",
        "-vf",
        video_filter,
        "-af",
        audio_filter,
        "-c:v",
        "libx264",
        "-tag:v",
        "avc1",
        "-profile:v",
        "high",
        "-level:v",
        "4.1",
        "-preset",
        "slow",
        "-crf",
        "18",
        "-maxrate",
        "20M",
        "-bufsize",
        "25M",
        "-pix_fmt",
        "yuv420p",
        "-color_primaries",
        "bt709",
        "-color_trc",
        "bt709",
        "-colorspace",
        "bt709",
        "-color_range",
        "tv",
        "-c:a",
        "aac",
        "-profile:a",
        "aac_low",
        "-b:a",
        "512k" if output_channels == 6 else "320k",
        "-ar",
        "48000",
        "-ac",
        str(output_channels),
        "-shortest",
        "-movflags",
        "+faststart",
        "-video_track_timescale",
        "24000",
        os.fspath(temporary_output),
    ]
    return command


def verify_output(ffprobe: str, output: Path, expected_audio_channels: int) -> None:
    result = run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            (
                "stream=codec_type,codec_name,profile,width,height,pix_fmt,"
                "avg_frame_rate,channels,sample_rate"
            ),
            "-of",
            "json",
            os.fspath(output),
        ],
        capture=True,
    )
    try:
        streams = json.loads(result.stdout).get("streams", [])
    except json.JSONDecodeError as error:
        raise ConversionError("ffprobe returned invalid JSON for the output.") from error

    videos = [item for item in streams if item.get("codec_type") == "video"]
    audios = [item for item in streams if item.get("codec_type") == "audio"]
    problems: list[str] = []
    if len(videos) != 1:
        problems.append(f"expected 1 video stream, found {len(videos)}")
    else:
        video = videos[0]
        expected = {
            "codec_name": "h264",
            "width": 1920,
            "height": 1080,
            "pix_fmt": "yuv420p",
        }
        for key, value in expected.items():
            if video.get(key) != value:
                problems.append(f"video {key} is {video.get(key)!r}, expected {value!r}")
        try:
            actual_rate = Fraction(video.get("avg_frame_rate", "0/1"))
        except (ValueError, ZeroDivisionError):
            actual_rate = Fraction(0, 1)
        if actual_rate != TARGET_RATE:
            problems.append(
                f"frame rate is {video.get('avg_frame_rate')!r}, expected 24000/1001"
            )

    if len(audios) != 1:
        problems.append(f"expected 1 audio stream, found {len(audios)}")
    else:
        audio = audios[0]
        if audio.get("codec_name") != "aac":
            problems.append(f"audio codec is {audio.get('codec_name')!r}, expected 'aac'")
        if audio.get("channels") != expected_audio_channels:
            problems.append(
                f"audio has {audio.get('channels')!r} channels, "
                f"expected {expected_audio_channels}"
            )
        if audio.get("sample_rate") != "48000":
            problems.append(
                f"audio sample rate is {audio.get('sample_rate')!r}, expected '48000'"
            )
    if problems:
        raise ConversionError("Output verification failed: " + "; ".join(problems))


def convert_one(
    ffmpeg: str,
    ffprobe: str,
    source: Path,
    output: Path,
    audio_stream: int,
    requested_audio_layout: str,
    overwrite: bool,
    slide_duration: float = 2.0,
) -> None:
    source = source.resolve()
    output = output.resolve()
    if source == output:
        raise ConversionError("Input and output paths must be different.")
    if output.exists() and not overwrite:
        raise ConversionError("Output already exists (use --overwrite to replace it).")
    output.parent.mkdir(parents=True, exist_ok=True)

    _video, audio = probe_source(ffprobe, source, audio_stream)
    output_layout, output_channels, preparation_filter = audio_preparation(
        audio, requested_audio_layout
    )
    measurements = (
        measure_audio(ffmpeg, source, audio_stream, preparation_filter)
        if audio
        else None
    )
    if not audio:
        print(f"  Source has no audio; adding a silent {output_layout} AAC track.")
    elif measurements is None:
        print("  Audio is silent or unmeasurable; using safe single-pass normalization.")
    if output_channels == 6:
        if audio and audio.get("channels") == 6:
            print("  Preserving the selected source's genuine 5.1 channels.")
        elif audio and audio.get("channels") == 2:
            print("  Extracting a restrained 5.1 mix from stereo using the DCP audio logic.")
        elif audio and audio.get("channels") == 1:
            print("  Placing mono audio in the 5.1 centre channel.")
        else:
            print("  Preparing a 5.1 mix using the DCP audio logic.")
    else:
        print("  Writing stereo AAC audio.")

    temporary = output.with_name(f".{output.stem}.projector-temp-{os.getpid()}.mp4")
    try:
        print("  Pass 2/2: encoding 1080p H.264/AAC MP4...")
        run(
            output_command(
                ffmpeg,
                source,
                temporary,
                audio_stream,
                audio is not None,
                measurements,
                preparation_filter,
                output_layout,
                output_channels,
            )
        )
        slide = find_slide(source)
        if slide:
            append_slide(ffmpeg, ffprobe, temporary, slide, slide_duration, mp4=True)
        print("  Verifying output streams...")
        verify_output(ffprobe, temporary, output_channels)
        os.replace(temporary, output)
    finally:
        if temporary.exists():
            temporary.unlink()


def discover_inputs(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    if not path.is_dir():
        raise ConversionError(f"Input does not exist: {path}")
    return sorted(
        (
            item
            for item in path.iterdir()
            if (
                item.is_file()
                and not item.name.startswith(".")
                and item.suffix.lower() in VIDEO_EXTENSIONS
            )
        ),
        key=lambda item: item.name.casefold(),
    )


def output_paths(input_path: Path, output_arg: Path, sources: list[Path]) -> list[Path]:
    folder_mode = input_path.is_dir() or output_arg.is_dir() or output_arg.suffix.lower() != ".mp4"
    if len(sources) > 1 and not folder_mode:
        raise ConversionError("A folder input requires an output folder.")
    if folder_mode:
        return [output_arg / f"{source.stem}.mp4" for source in sources]
    return [output_arg]


def main() -> int:
    args = command_line()
    try:
        ffmpeg = require_tool("ffmpeg")
        ffprobe = require_tool("ffprobe")
        sources = discover_inputs(args.input)
        if not sources:
            raise ConversionError("No supported video files were found in the input folder.")
        outputs = output_paths(args.input, args.output, sources)
    except ConversionError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2

    failures = 0
    for number, (source, output) in enumerate(zip(sources, outputs), start=1):
        print(f"\n[{number}/{len(sources)}] {source.name} -> {output}")
        try:
            convert_one(
                ffmpeg,
                ffprobe,
                source,
                output,
                args.audio_stream,
                args.audio_layout,
                args.overwrite,
                args.slide_duration,
            )
            print(f"  OK: {output}")
        except (ConversionError, OSError) as error:
            failures += 1
            print(f"  FAILED: {error}", file=sys.stderr)

    print(f"\nComplete: {len(sources) - failures} succeeded, {failures} failed.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
