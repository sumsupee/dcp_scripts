#!/usr/bin/env python3

# Copyright (C) 2026 MP4-to-DCP contributors
# SPDX-License-Identifier: GPL-2.0-or-later

import argparse
from slide_support import add_slide_arguments, append_slide, find_slide
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

CONTENT_TYPES = {
    "trailer": "TLR",
    "feature": "FTR",
}

CONTAINERS = {
    "flat": "185",
    "scope": "239",
}

DEFAULT_TARGET_LUFS = -24.0
DEFAULT_MAX_TRUE_PEAK = -2.0
LOUDNESS_TOLERANCE = 0.5
TRUE_PEAK_TOLERANCE = 0.1
MAX_AUDIO_ADJUSTMENT_ATTEMPTS = 3
OUTPUT_AUDIO_CHANNELS = 6
OUTPUT_CHANNEL_LAYOUT = "5.1"

# FFmpeg's surround filter works in the frequency domain.  The surrounds are
# deliberately reduced by 6 dB and LFE synthesis is disabled: an automatic
# upmix should add restrained ambience, not invent theatrical effects.
STEREO_SURROUND_FILTER = (
    "surround=chl_in=stereo:chl_out=5.1:lfe=false:smooth=0.5:"
    "sl_out=0.5:sr_out=0.5"
)

TOOL_FILENAMES = {
    "ffmpeg": "ffmpeg.exe" if os.name == "nt" else "ffmpeg",
    "ffprobe": "ffprobe.exe" if os.name == "nt" else "ffprobe",
    "dcpomatic2_create": (
        "dcpomatic2_create.exe" if os.name == "nt" else "dcpomatic2_create"
    ),
    "dcpomatic2_cli": (
        "dcpomatic2_cli.exe" if os.name == "nt" else "dcpomatic2_cli"
    ),
}


def bundled_tools_root():
    """Return the optional bundled-tools directory for this platform."""
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


def required_tool(name):
    """Find a bundled executable first, then fall back to the system PATH."""
    try:
        filename = TOOL_FILENAMES[name]
    except KeyError as error:
        raise RuntimeError(f"Unknown external tool: {name}") from error

    component = "ffmpeg" if name in {"ffmpeg", "ffprobe"} else "dcpomatic"
    candidate = bundled_tools_root() / component / filename
    if candidate.is_file():
        if os.name != "nt" and not os.access(candidate, os.X_OK):
            raise RuntimeError(f"Bundled tool is not executable: {candidate}")
        return os.fspath(candidate)

    system_path = shutil.which(filename)
    if system_path:
        return system_path

    raise RuntimeError(
        f"Required tool '{name}' was not found. Install it in PATH or place "
        f"a bundled copy at {candidate}."
    )


def probe_source(ffprobe, input_file, audio_stream_index=0):
    command = [
        ffprobe,
        "-v",
        "error",
        "-show_entries",
        "stream=index,codec_type,codec_name,channels,channel_layout,avg_frame_rate",
        "-of",
        "json",
        os.fspath(input_file),
    ]

    try:
        result = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
        )
        streams = json.loads(result.stdout).get("streams", [])
    except subprocess.CalledProcessError as error:
        detail = error.stderr.strip() or "ffprobe could not read the file."
        raise RuntimeError(detail) from error
    except json.JSONDecodeError as error:
        raise RuntimeError("ffprobe returned invalid stream information.") from error

    video_streams = [stream for stream in streams if stream.get("codec_type") == "video"]
    audio_streams = [stream for stream in streams if stream.get("codec_type") == "audio"]

    if not video_streams:
        raise RuntimeError("The source does not contain a video stream.")
    if not audio_streams:
        if audio_stream_index != 0:
            raise RuntimeError(
                "The source has no audio streams; only --audio-stream 0 is valid."
            )
        return video_streams[0], None, 0
    if audio_stream_index < 0 or audio_stream_index >= len(audio_streams):
        raise RuntimeError(
            f"Audio stream {audio_stream_index} was requested, but the source has "
            f"{len(audio_streams)} audio stream(s). Audio stream numbering starts at 0."
        )

    return video_streams[0], audio_streams[audio_stream_index], len(audio_streams)


def audio_preparation_filter(audio, audio_mode):
    """Return the filter that prepares one source stream as ordered 5.1 PCM."""
    channels = audio.get("channels")
    layout = (audio.get("channel_layout") or "").lower()

    if channels == 1:
        return (
            "pan=5.1|c0=0*c0|c1=0*c0|c2=c0|c3=0*c0|c4=0*c0|c5=0*c0"
        )

    if channels == 2:
        if audio_mode == "preserve":
            return (
                "pan=5.1|c0=c0|c1=c1|c2=0*c0|c3=0*c0|c4=0*c0|c5=0*c0"
            )
        return STEREO_SURROUND_FILTER

    if channels == 6:
        if layout == "5.1(side)":
            return "pan=5.1|FL=FL|FR=FR|FC=FC|LFE=LFE|BL=SL|BR=SR"
        # Standard 5.1 order is L, R, C, LFE, Ls, Rs.  Positional mapping also
        # preserves a six-channel source whose container omitted layout tags.
        return "pan=5.1|c0=c0|c1=c1|c2=c2|c3=c3|c4=c4|c5=c5"

    if not isinstance(channels, int) or channels < 1:
        raise RuntimeError("The selected audio stream has an unknown channel count.")

    # FFmpeg applies its layout-aware standard downmix.  In auto mode, the same
    # restrained stereo upmix follows; preserve mode keeps only front L/R.
    stereo_downmix = "aformat=channel_layouts=stereo"
    if audio_mode == "preserve":
        front_only = (
            "pan=5.1|c0=c0|c1=c1|c2=0*c0|c3=0*c0|c4=0*c0|c5=0*c0"
        )
        return join_audio_filters(stereo_downmix, front_only)
    return join_audio_filters(stereo_downmix, STEREO_SURROUND_FILTER)


def join_audio_filters(*filters):
    return ",".join(filter(None, filters))


def parse_loudnorm_stats(output):
    matches = re.findall(r'\{\s*"input_i".*?\}', output, flags=re.DOTALL)
    if not matches:
        raise RuntimeError("FFmpeg did not return loudness measurements.")

    try:
        stats = json.loads(matches[-1])
        numeric_stats = {
            key: float(stats[key])
            for key in (
                "input_i",
                "input_tp",
                "input_lra",
                "input_thresh",
                "target_offset",
            )
        }
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise RuntimeError("FFmpeg returned invalid loudness measurements.") from error

    if not all(math.isfinite(value) for value in numeric_stats.values()):
        raise RuntimeError(
            "The audio is silent or too quiet for reliable loudness normalization."
        )
    return numeric_stats


def analyze_audio(
    ffmpeg,
    input_file,
    target_lufs,
    max_true_peak,
    audio_stream_index=0,
    preparation_filter=None,
):
    loudnorm = (
        f"loudnorm=I={target_lufs}:LRA=50:TP={max_true_peak}:"
        "print_format=json"
    )
    command = [
        ffmpeg,
        "-hide_banner",
        "-nostats",
        "-i",
        os.fspath(input_file),
        "-map",
        f"0:a:{audio_stream_index}",
        "-af",
        join_audio_filters(preparation_filter, loudnorm),
        "-f",
        "null",
        "-",
    ]
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    return parse_loudnorm_stats(result.stderr)


def create_normalized_master(
    ffmpeg,
    input_file,
    master_file,
    target_lufs,
    max_true_peak,
    audio_stream_index,
    preparation_filter,
):
    print("\n=== Measuring source loudness ===")
    measured = analyze_audio(
        ffmpeg,
        input_file,
        target_lufs,
        max_true_peak,
        audio_stream_index,
        preparation_filter,
    )
    print(f"Source integrated loudness: {measured['input_i']:.1f} LUFS")
    print(f"Source true peak: {measured['input_tp']:.1f} dBTP")

    loudnorm = (
        f"loudnorm=I={target_lufs}:LRA=50:TP={max_true_peak}:"
        f"measured_I={measured['input_i']}:"
        f"measured_LRA={measured['input_lra']}:"
        f"measured_TP={measured['input_tp']}:"
        f"measured_thresh={measured['input_thresh']}:"
        f"offset={measured['target_offset']}:"
        "linear=true:print_format=summary,aresample=48000"
    )

    print("\n=== Creating normalized 5.1 master ===")
    command = [
        ffmpeg,
        "-hide_banner",
        "-y",
        "-i",
        os.fspath(input_file),
        "-map",
        "0:v:0",
        "-map",
        f"0:a:{audio_stream_index}",
        "-map_metadata",
        "0",
        "-c:v",
        "copy",
        "-c:a",
        "pcm_s24le",
        "-ar",
        "48000",
        "-af",
        join_audio_filters(preparation_filter, loudnorm),
        "-channel_layout:a",
        OUTPUT_CHANNEL_LAYOUT,
        os.fspath(master_file),
    ]
    subprocess.run(command, check=True)

    normalized = analyze_audio(ffmpeg, master_file, target_lufs, max_true_peak)
    return correct_master_levels(
        ffmpeg,
        master_file,
        normalized,
        target_lufs,
        max_true_peak,
    )


def create_silent_master(ffmpeg, input_file, master_file):
    """Copy the source video and add a silent 5.1 PCM track of equal duration."""
    print("\n=== Creating silent 5.1 master ===")
    command = [
        ffmpeg,
        "-hide_banner",
        "-y",
        "-i",
        os.fspath(input_file),
        "-f",
        "lavfi",
        "-i",
        "anullsrc=channel_layout=5.1:sample_rate=48000",
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-map_metadata",
        "0",
        "-c:v",
        "copy",
        "-c:a",
        "pcm_s24le",
        "-ar",
        "48000",
        "-channel_layout:a",
        OUTPUT_CHANNEL_LAYOUT,
        "-shortest",
        os.fspath(master_file),
    ]
    subprocess.run(command, check=True)


def verify_audio_levels(stats, target_lufs, max_true_peak, label):
    integrated = stats["input_i"]
    true_peak = stats["input_tp"]
    print(f"{label} integrated loudness: {integrated:.1f} LUFS")
    print(f"{label} true peak: {true_peak:.1f} dBTP")

    if abs(integrated - target_lufs) > LOUDNESS_TOLERANCE:
        raise RuntimeError(
            f"{label} measured {integrated:.1f} LUFS; target is {target_lufs:.1f} "
            f"LUFS (+/- {LOUDNESS_TOLERANCE:.1f})."
        )
    if true_peak > max_true_peak + TRUE_PEAK_TOLERANCE:
        raise RuntimeError(
            f"{label} true peak is {true_peak:.1f} dBTP; maximum is "
            f"{max_true_peak:.1f} dBTP."
        )


def audio_levels_are_valid(stats, target_lufs, max_true_peak):
    return (
        abs(stats["input_i"] - target_lufs) <= LOUDNESS_TOLERANCE
        and stats["input_tp"] <= max_true_peak + TRUE_PEAK_TOLERANCE
    )


def calculate_gain_adjustment(stats, target_lufs, max_true_peak):
    loudness_gain = target_lufs - stats["input_i"]
    peak_headroom = max_true_peak - stats["input_tp"]

    if abs(loudness_gain) <= LOUDNESS_TOLERANCE:
        loudness_gain = 0.0
    if peak_headroom >= -TRUE_PEAK_TOLERANCE:
        peak_headroom = float("inf")

    gain = min(loudness_gain, peak_headroom)
    return 0.0 if math.isinf(gain) else gain


def apply_master_gain(ffmpeg, master_file, gain_db):
    adjusted_file = master_file.with_name(f"{master_file.stem}_adjusted.mkv")
    print(f"Applying {gain_db:+.2f} dB gain correction to normalized master.")
    command = [
        ffmpeg,
        "-hide_banner",
        "-y",
        "-i",
        os.fspath(master_file),
        "-map",
        "0:v:0",
        "-map",
        "0:a:0",
        "-map_metadata",
        "0",
        "-c:v",
        "copy",
        "-c:a",
        "pcm_s24le",
        "-ar",
        "48000",
        "-af",
        f"volume={gain_db}dB",
        "-channel_layout:a",
        OUTPUT_CHANNEL_LAYOUT,
        os.fspath(adjusted_file),
    ]
    try:
        subprocess.run(command, check=True)
        os.replace(adjusted_file, master_file)
    finally:
        if adjusted_file.exists():
            adjusted_file.unlink()


def correct_master_levels(
    ffmpeg,
    master_file,
    stats,
    target_lufs,
    max_true_peak,
):
    for attempt in range(1, MAX_AUDIO_ADJUSTMENT_ATTEMPTS + 1):
        label = "Normalized master" if attempt == 1 else "Adjusted master"
        print(f"{label} integrated loudness: {stats['input_i']:.1f} LUFS")
        print(f"{label} true peak: {stats['input_tp']:.1f} dBTP")
        if audio_levels_are_valid(stats, target_lufs, max_true_peak):
            return stats

        gain_db = calculate_gain_adjustment(stats, target_lufs, max_true_peak)
        if abs(gain_db) < 0.01:
            break
        apply_master_gain(ffmpeg, master_file, gain_db)
        stats = analyze_audio(ffmpeg, master_file, target_lufs, max_true_peak)

    verify_audio_levels(stats, target_lufs, max_true_peak, "Adjusted master")
    return stats


def find_sound_file(dcp_dir):
    sound_files = sorted(dcp_dir.glob("pcm_*.mxf"))
    if len(sound_files) != 1:
        raise RuntimeError(
            f"Expected one PCM sound MXF in {dcp_dir}, found {len(sound_files)}."
        )
    return sound_files[0]


def prepare_project_directory(project_dir, overwrite):
    if not project_dir.exists():
        return

    if not overwrite:
        raise RuntimeError(
            f"Project directory already exists: {project_dir}\n"
            "Choose another --output-dir or pass --overwrite."
        )

    if project_dir.is_dir():
        shutil.rmtree(project_dir)
    else:
        project_dir.unlink()


def prepare_master_file(master_file, overwrite):
    if not master_file.exists():
        return
    if not overwrite:
        raise RuntimeError(
            f"Normalized master already exists: {master_file}\n"
            "Pass --overwrite or choose another --output-dir."
        )
    if master_file.is_dir():
        shutil.rmtree(master_file)
    else:
        master_file.unlink()


def find_dcp_directory(project_dir):
    asset_maps = list(project_dir.rglob("ASSETMAP")) + list(project_dir.rglob("ASSETMAP.xml"))
    if not asset_maps:
        return project_dir
    return asset_maps[0].parent


def create_dcp(create_tool, cli_tool, input_file, project_dir, name, content_type, format_name):
    container_ratio = CONTAINERS[format_name]
    display_format = format_name.capitalize()

    print(f"\n=== Creating {display_format} project ({container_ratio[0]}.{container_ratio[1:]}:1) ===")
    create_command = [
        create_tool,
        "--output",
        os.fspath(project_dir),
        "--name",
        name,
        "--dcp-content-type",
        content_type,
        "--container-ratio",
        container_ratio,
        "--audio-channels",
        str(OUTPUT_AUDIO_CHANNELS),
        "--standard",
        "SMPTE",
        "--twok",
        "--twod",
        "--no-encrypt",
        os.fspath(input_file),
    ]
    subprocess.run(create_command, check=True)

    print(f"\n=== Encoding {display_format} DCP ===")
    subprocess.run([cli_tool, "make-dcp", os.fspath(project_dir)], check=True)
    return find_dcp_directory(project_dir)


def probe_audio_channel_count(ffprobe, input_file):
    command = [
        ffprobe,
        "-v",
        "error",
        "-select_streams",
        "a:0",
        "-show_entries",
        "stream=channels",
        "-of",
        "json",
        os.fspath(input_file),
    ]
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    try:
        streams = json.loads(result.stdout).get("streams", [])
        return int(streams[0]["channels"])
    except (IndexError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Could not determine audio channels in {input_file}.") from error


def verify_six_channel_audio(ffprobe, input_file, label):
    channels = probe_audio_channel_count(ffprobe, input_file)
    if channels != OUTPUT_AUDIO_CHANNELS:
        raise RuntimeError(
            f"{label} contains {channels} audio channels; expected "
            f"{OUTPUT_AUDIO_CHANNELS} (5.1)."
        )
    print(f"{label} channel count: {channels} (5.1)")


def discover_inputs(input_path):
    """Return one source file, or the top-level MP4 and MKV files in a directory."""
    if input_path.is_file():
        return [input_path]
    if not input_path.is_dir():
        raise RuntimeError(f"Could not find source file or directory: {input_path}")

    input_files = sorted(
        (
            path
            for path in input_path.iterdir()
            if path.is_file() and path.suffix.lower() in {".mp4", ".mkv"}
        ),
        key=lambda path: path.name.casefold(),
    )
    if not input_files:
        raise RuntimeError(f"No MP4 or MKV files found in source directory: {input_path}")
    return input_files


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Create 5.1 SMPTE 2K Flat and/or Scope DCPs with DCP-o-matic. "
            "A directory input processes all top-level MP4 and MKV files."
        )
    )
    parser.add_argument(
        "input",
        type=Path,
        help="source MP4/MOV/MKV file, or directory containing MP4 and MKV files",
    )
    parser.add_argument(
        "--name",
        help="DCP composition title (defaults to the source filename)",
    )
    parser.add_argument(
        "--content-type",
        choices=CONTENT_TYPES,
        default="trailer",
        help="DCP content type (default: trailer)",
    )
    parser.add_argument(
        "--format",
        choices=("both", "flat", "scope"),
        default="both",
        help="container format to create (default: both)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path.cwd(),
        help="parent directory for DCP-o-matic projects (default: current directory)",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="replace existing project directories",
    )
    parser.add_argument(
        "--audio-stream",
        type=int,
        default=0,
        help="zero-based audio stream to use (default: 0)",
    )
    parser.add_argument(
        "--audio-mode",
        choices=("auto", "preserve"),
        default="auto",
        help=(
            "auto upmixes stereo to restrained 5.1; preserve keeps stereo in "
            "L/R with the other 5.1 channels silent (default: auto)"
        ),
    )
    parser.add_argument(
        "--target-lufs",
        type=float,
        default=DEFAULT_TARGET_LUFS,
        help=f"integrated loudness target (default: {DEFAULT_TARGET_LUFS:g} LUFS)",
    )
    parser.add_argument(
        "--max-true-peak",
        type=float,
        default=DEFAULT_MAX_TRUE_PEAK,
        help=f"maximum true peak (default: {DEFAULT_MAX_TRUE_PEAK:g} dBTP)",
    )
    parser.add_argument(
        "--keep-master",
        action="store_true",
        help="keep the normalized MKV master so projects can be re-encoded",
    )
    add_slide_arguments(parser)
    return parser.parse_args()


def convert_one(args, input_file, output_dir):
    master_file = None
    remove_master = False

    try:
        if not input_file.is_file():
            raise RuntimeError(f"Could not find source file: {input_file}")
        if not -70.0 <= args.target_lufs <= -5.0:
            raise RuntimeError("--target-lufs must be between -70 and -5 LUFS.")
        if not -9.0 <= args.max_true_peak <= 0.0:
            raise RuntimeError("--max-true-peak must be between -9 and 0 dBTP.")
        if args.audio_stream < 0:
            raise RuntimeError("--audio-stream must be 0 or greater.")

        ffmpeg = required_tool("ffmpeg")
        ffprobe = required_tool("ffprobe")
        create_tool = required_tool("dcpomatic2_create")
        cli_tool = required_tool("dcpomatic2_cli")
        video, audio, audio_stream_count = probe_source(
            ffprobe, input_file, args.audio_stream
        )
        preparation_filter = (
            audio_preparation_filter(audio, args.audio_mode) if audio else None
        )

        name = args.name.strip() if args.name else input_file.stem
        if not name:
            raise RuntimeError("DCP name cannot be empty.")

        output_dir.mkdir(parents=True, exist_ok=True)
        master_file = output_dir / f"{input_file.stem}_DCP_NormalizedMaster.mkv"
        formats = CONTAINERS if args.format == "both" else (args.format,)
        project_dirs = {
            format_name: output_dir / f"{input_file.stem}_DCP_{format_name.capitalize()}"
            for format_name in formats
        }
        for project_dir in project_dirs.values():
            prepare_project_directory(project_dir, args.overwrite)
        prepare_master_file(master_file, args.overwrite)
        remove_master = not args.keep_master

        frame_rate = video.get("avg_frame_rate", "unknown")
        print(f"Source: {input_file}")
        print(f"Video frame rate: {frame_rate}")
        source_channels = audio.get("channels", "unknown") if audio else 0
        if not audio:
            print("Audio stream: none")
            print("Audio processing: adding a silent 5.1 track")
        else:
            audio_codec = audio.get("codec_name", "unknown")
            source_layout = audio.get("channel_layout") or f"{source_channels} channel(s)"
            print(
                f"Audio stream: {args.audio_stream} of {audio_stream_count} "
                f"({source_layout}, {audio_codec})"
            )
        if source_channels == 1:
            print("Audio processing: mono placed in centre; other 5.1 channels silent")
        elif source_channels == 2 and args.audio_mode == "auto":
            print("Audio processing: restrained FFT stereo-to-5.1 upmix; LFE silent")
        elif source_channels == 2:
            print("Audio processing: stereo preserved in L/R of a 5.1 container")
        elif source_channels == 6:
            print("Audio processing: native 5.1 preserved")
        elif audio:
            print(
                "Warning: unusual source layout will be downmixed to stereo, then "
                "upmixed to 5.1. Listen to the master before delivery."
            )
        if audio:
            print(
                f"Audio target: {args.target_lufs:.1f} LUFS, "
                f"maximum {args.max_true_peak:.1f} dBTP"
            )
        print(f"DCP name: {name}")
        print(f"Content type: {args.content_type} ({CONTENT_TYPES[args.content_type]})")

        if audio:
            create_normalized_master(
                ffmpeg,
                input_file,
                master_file,
                args.target_lufs,
                args.max_true_peak,
                args.audio_stream,
                preparation_filter,
            )
            master_label = "Normalized master"
        else:
            create_silent_master(ffmpeg, input_file, master_file)
            master_label = "Silent master"
        slide = find_slide(input_file)
        if slide:
            append_slide(ffmpeg, ffprobe, master_file, slide,
                         getattr(args, "slide_duration", 2.0))
        verify_six_channel_audio(ffprobe, master_file, master_label)

        completed = []
        for dcp_attempt in range(1, MAX_AUDIO_ADJUSTMENT_ATTEMPTS + 1):
            if dcp_attempt > 1:
                print("\n=== Rebuilding DCPs with corrected audio gain ===")
                for project_dir in project_dirs.values():
                    prepare_project_directory(project_dir, overwrite=True)

            completed = []
            for format_name, project_dir in project_dirs.items():
                dcp_dir = create_dcp(
                    create_tool,
                    cli_tool,
                    master_file,
                    project_dir,
                    name,
                    CONTENT_TYPES[args.content_type],
                    format_name,
                )
                print(f"\n=== Verifying {format_name.capitalize()} DCP audio ===")
                sound_file = find_sound_file(dcp_dir)
                verify_six_channel_audio(
                    ffprobe,
                    sound_file,
                    f"{format_name.capitalize()} DCP sound MXF",
                )
                dcp_stats = None
                if audio:
                    dcp_stats = analyze_audio(
                        ffmpeg,
                        sound_file,
                        args.target_lufs,
                        args.max_true_peak,
                    )
                    print(
                        f"{format_name.capitalize()} DCP integrated loudness: "
                        f"{dcp_stats['input_i']:.1f} LUFS"
                    )
                    print(
                        f"{format_name.capitalize()} DCP true peak: "
                        f"{dcp_stats['input_tp']:.1f} dBTP"
                    )
                else:
                    print(f"{format_name.capitalize()} DCP audio: silent 5.1")
                completed.append((format_name.capitalize(), dcp_dir, dcp_stats))

            invalid = [
                item
                for item in completed
                if item[2] is not None and not audio_levels_are_valid(
                    item[2], args.target_lufs, args.max_true_peak
                )
            ]
            if not invalid:
                break

            gain_db = min(
                calculate_gain_adjustment(
                    stats, args.target_lufs, args.max_true_peak
                )
                for _, _, stats in invalid
            )
            if dcp_attempt == MAX_AUDIO_ADJUSTMENT_ATTEMPTS or abs(gain_db) < 0.01:
                format_name, _, stats = invalid[0]
                verify_audio_levels(
                    stats,
                    args.target_lufs,
                    args.max_true_peak,
                    f"{format_name} DCP after automatic gain correction",
                )

            print(
                "Finished DCP audio is outside the requested limits; "
                f"automatically correcting gain by {gain_db:+.2f} dB."
            )
            apply_master_gain(ffmpeg, master_file, gain_db)

    except (RuntimeError, OSError, subprocess.CalledProcessError) as error:
        if isinstance(error, subprocess.CalledProcessError):
            message = f"Command failed with exit code {error.returncode}: {' '.join(error.cmd)}"
        else:
            message = str(error)
        print(f"\nError: {message}", file=sys.stderr)
        return 1
    finally:
        if remove_master and master_file and master_file.exists():
            try:
                master_file.unlink()
            except OSError as error:
                print(f"Warning: could not remove normalized master: {error}", file=sys.stderr)

    print("\n=== SUCCESS ===")
    for format_name, dcp_dir, stats in completed:
        print(f"{format_name} DCP: {dcp_dir}")
        if stats is None:
            print("  Audio: silent 5.1")
        else:
            print(
                f"  Audio: {stats['input_i']:.1f} LUFS, "
                f"{stats['input_tp']:.1f} dBTP true peak"
            )
    if args.keep_master:
        print(f"Normalized master: {master_file}")
    print("Verify the packages with dcpomatic2_verify_cli and screen them before delivery.")
    return 0


def main():
    args = parse_args()
    input_path = args.input.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()

    try:
        input_files = discover_inputs(input_path)
        if input_path.is_dir() and args.name:
            raise RuntimeError(
                "--name can only be used with a single input file; directory inputs "
                "use each source filename as its DCP name."
            )
    except (RuntimeError, OSError) as error:
        print(f"\nError: {error}", file=sys.stderr)
        return 1

    if len(input_files) == 1 and input_path.is_file():
        return convert_one(args, input_files[0], output_dir)

    print(f"Found {len(input_files)} MP4/MKV file(s) in: {input_path}")
    failures = []
    for index, input_file in enumerate(input_files, start=1):
        print("\n" + "=" * 72)
        print(f"BATCH ITEM {index}/{len(input_files)}: {input_file.name}")
        print("=" * 72)
        if convert_one(args, input_file, output_dir) != 0:
            failures.append(input_file)

    print("\n" + "=" * 72)
    print(
        f"BATCH COMPLETE: {len(input_files) - len(failures)} succeeded, "
        f"{len(failures)} failed."
    )
    if failures:
        for input_file in failures:
            print(f"  Failed: {input_file}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
