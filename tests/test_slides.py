import argparse
from fractions import Fraction
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from slide_support import find_slide, positive_seconds, append_slide


class SlideTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'requires FFmpeg')
    def test_slide_preserves_video_geometry_and_fits_without_stretching(self):
        # Square-pixel scope, anamorphic scope, and the MP4 delivery canvas.
        for width, height, sar, mp4 in [(320, 134, '1/1', False),
                                       (320, 180, '4/3', False),
                                       (320, 180, '1/1', True)]:
            with self.subTest(width=width, height=height, sar=sar, mp4=mp4), tempfile.TemporaryDirectory() as directory:
                media = Path(directory) / ('video.mp4' if mp4 else 'video.mkv')
                slide = Path(directory) / 'square.ppm'
                slide.write_bytes(b'P6\n100 100\n255\n' + b'\xff\xff\xff' * 10000)
                subprocess.run([
                    'ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                    f'color=red:s={width}x{height}:r=24:d=0.5,setsar={sar}',
                    '-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo',
                    '-t', '0.5', '-c:v', 'libx264' if mp4 else 'ffv1',
                    '-c:a', 'aac' if mp4 else 'pcm_s24le', str(media),
                ], check=True)
                self.assertTrue(append_slide('ffmpeg', 'ffprobe', media, slide, 0.5, mp4=mp4))
                stream = json.loads(subprocess.check_output([
                    'ffprobe', '-v', 'error', '-select_streams', 'v:0',
                    '-show_streams', '-of', 'json', str(media),
                ]))['streams'][0]
                self.assertEqual((stream['width'], stream['height']), (width, height))
                self.assertEqual(Fraction(stream['sample_aspect_ratio'].replace(':', '/')), Fraction(sar))
                pixels = subprocess.check_output([
                    'ffmpeg', '-v', 'error', '-i', str(media), '-ss', '0.75',
                    '-frames:v', '1', '-f', 'rawvideo', '-pix_fmt', 'gray', '-',
                ])
                self.assertEqual(len(pixels), width * height)
                white = [(i % width, i // width) for i, value in enumerate(pixels) if value > 220]
                left, right = min(x for x, y in white), max(x for x, y in white)
                top, bottom = min(y for x, y in white), max(y for x, y in white)
                # A square slide stays square on screen, with centered black bars.
                self.assertAlmostEqual((right - left + 1) * float(Fraction(sar)) / (bottom - top + 1), 1, delta=0.03)
                self.assertLessEqual(abs(left - (width - 1 - right)), 2)
                self.assertTrue(all(pixels[y * width] < 20 for y in range(height)))

    def match(self, names, source='My Trailer.mp4'):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in [source, *names]:
                (root / name).touch()
            result = find_slide(root / source)
            return result.name if result else None

    def test_exact_and_normalized(self):
        self.assertEqual(self.match(['My Trailer.png', 'My Traile.jpg']), 'My Trailer.png')
        self.assertEqual(self.match(['my_trailer.JPG']), 'my_trailer.JPG')

    def test_typo(self):
        self.assertEqual(self.match(['My Traier.png']), 'My Traier.png')

    def test_missing_and_ambiguous(self):
        self.assertIsNone(self.match([]))
        self.assertIsNone(self.match(['My Trailer.png', 'My Trailer.jpg']))
        self.assertIsNone(self.match(['My Traier.png', 'My Traile.jpg']))
        self.assertIsNone(self.match(['My Traier.png', 'My Traier.mp4']))

    def test_invalid_duration(self):
        for value in ['0', '-1', 'nan', 'inf', 'abc']:
            with self.assertRaises(argparse.ArgumentTypeError):
                positive_seconds(value)
        self.assertEqual(positive_seconds('3.5'), 3.5)

    def test_slide_failure_keeps_original(self):
        with tempfile.TemporaryDirectory() as directory:
            media = Path(directory) / 'original.mp4'
            media.write_bytes(b'original')
            with patch('slide_support.subprocess.check_output', side_effect=OSError('bad image')):
                self.assertFalse(append_slide('ffmpeg', 'ffprobe', media, Path('bad.png')))
            self.assertEqual(media.read_bytes(), b'original')
