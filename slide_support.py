"""Optional end slides shared by all three command-line converters."""
import argparse
from difflib import SequenceMatcher
from fractions import Fraction
import json
import math
import os
from pathlib import Path
import subprocess
import tempfile
import unicodedata

IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.bmp', '.tif', '.tiff', '.webp'}
VIDEO_EXTENSIONS = {'.mp4', '.mov', '.mkv', '.m4v', '.avi', '.webm'}


def positive_seconds(value):
    try:
        seconds = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError('slide duration must be a positive number')
    if not math.isfinite(seconds) or seconds <= 0:
        raise argparse.ArgumentTypeError('slide duration must be finite and greater than zero')
    return seconds


def add_slide_arguments(parser):
    parser.add_argument('--slide-duration', type=positive_seconds, default=3.5,
                        metavar='SECONDS', help='end slide runtime (default: 3.5 seconds); also applies to folders')


def normalized_name(path):
    return ''.join(c for c in unicodedata.normalize('NFKC', path.stem).casefold() if c.isalnum())


def find_slide(source):
    """Prefer exact names; only accept a unique, reciprocal close match."""
    try:
        siblings = [p for p in source.parent.iterdir() if p.is_file() and not p.name.startswith('.')]
        images = sorted((p for p in siblings if p.suffix.lower() in IMAGE_EXTENSIONS), key=lambda p: p.name)
        exact = [p for p in images if p.stem.casefold() == source.stem.casefold()]
        matches = exact or [p for p in images if normalized_name(p) == normalized_name(source)]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            print(f'  Warning: ambiguous slides for {source.name}; skipping slide.')
            return None
        key = normalized_name(source)
        ranked = sorted(((SequenceMatcher(None, key, normalized_name(p)).ratio(), p) for p in images),
                        key=lambda pair: pair[0], reverse=True)
        if not ranked or ranked[0][0] < 0.85 or len(key) < 5:
            return None
        score, candidate = ranked[0]
        competitors = [SequenceMatcher(None, normalized_name(p), normalized_name(candidate)).ratio()
                       for p in siblings if p.suffix.lower() in VIDEO_EXTENSIONS and p != source]
        if (len(ranked) > 1 and score - ranked[1][0] < 0.08) or any(score - s < 0.08 for s in competitors):
            print(f'  Warning: ambiguous slide match for {source.name}; skipping slide.')
            return None
        print(f'  Using approximate slide match: {source.name} -> {candidate.name}')
        return candidate
    except OSError as error:
        print(f'  Warning: could not search for optional slide: {error}')
        return None


def append_slide(ffmpeg, ffprobe, media, slide, seconds=2.0, *, mp4=False):
    """Atomically append a silent slide, keeping the original on any failure."""
    try:
        info = json.loads(subprocess.check_output([
            ffprobe, '-v', 'error', '-show_streams', '-show_format', '-of', 'json', os.fspath(media)
        ], text=True))
        video = next(s for s in info['streams'] if s['codec_type'] == 'video')
        audio = next(s for s in info['streams'] if s['codec_type'] == 'audio')
        rate = Fraction(video['avg_frame_rate'])
        if rate <= 0:
            raise ValueError('unknown video frame rate')
        duration = float(video.get('duration') or info['format']['duration'])
        layout = 'stereo' if audio['channels'] == 2 else '5.1'
        width, height = video['width'], video['height']
        # Coded dimensions alone do not describe anamorphic video. Keep its
        # pixel aspect ratio so appending a slide cannot change its framing.
        sar_text = video.get('sample_aspect_ratio') or '1:1'
        sar = Fraction(sar_text.replace(':', '/')) if sar_text not in ('N/A', '0:1') else Fraction(1)
        if sar <= 0:
            sar = Fraction(1)
        # Fit in display space, then express the size in the video's pixels.
        # Even dimensions keep chroma alignment; padding preserves all text.
        slide_scale = (
            f"scale=w='max(2,trunc(min({width},{height}*dar/({sar}))/2)*2)'"
            f":h='max(2,trunc(min({height},{width}*({sar})/dar)/2)*2)'"
            ':flags=lanczos'
        )
        frames = max(1, round(seconds * float(rate)))
        slide_time = frames / float(rate)
        filters = (
            f'[0:v:0]setpts=PTS-STARTPTS,setsar=ratio={sar}:max=65535,fps={rate},format=yuv420p[v0];'
            f'[1:v:0]{slide_scale},'
            f'pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,setsar=ratio={sar}:max=65535,'
            f'fps={rate},format=yuv420p,trim=end_frame={frames},setpts=PTS-STARTPTS[v1];'
            f'[0:a:0]aresample=48000,aformat=channel_layouts={layout},apad,'
            f'atrim=duration={duration},asetpts=PTS-STARTPTS[a0];'
            f'anullsrc=r=48000:cl={layout},atrim=duration={slide_time},asetpts=PTS-STARTPTS[a1];'
            '[v0][a0][v1][a1]concat=n=2:v=1:a=1[v][a]'
        )
        with tempfile.TemporaryDirectory(prefix='.slide-', dir=media.parent) as directory:
            target = Path(directory) / media.name
            codec = (['-c:v', 'libx264', '-preset', 'slow', '-crf', '18', '-profile:v', 'high',
                      '-level:v', '4.1', '-maxrate', '20M', '-bufsize', '25M', '-tag:v', 'avc1',
                      '-color_primaries', 'bt709', '-color_trc', 'bt709', '-colorspace', 'bt709',
                      '-color_range', 'tv', '-c:a', 'aac', '-b:a', '512k' if layout == '5.1' else '320k',
                      '-movflags', '+faststart', '-video_track_timescale', '24000']
                     if mp4 else ['-c:v', 'ffv1', '-c:a', 'pcm_s24le'])
            subprocess.run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
                            '-i', os.fspath(media), '-loop', '1', '-framerate', str(rate),
                            '-i', os.fspath(slide), '-filter_complex', filters,
                            '-map', '[v]', '-map', '[a]', *codec, os.fspath(target)],
                           check=True)
            os.replace(target, media)
        print(f'  Appended slide: {slide.name} ({slide_time:.3f}s, silent)')
        return True
    except (OSError, ValueError, KeyError, StopIteration, ZeroDivisionError, subprocess.CalledProcessError) as error:
        print(f'  Warning: optional slide could not be appended; continuing without it: {error}')
        return False
