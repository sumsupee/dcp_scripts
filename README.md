# MP4-to-DCP

## Projector-safe MP4 conversion

`mp4_to_proludio.py` creates a conservative MP4 for players that require
1920x1080, 23.976 fps, H.264 video, and AAC audio. It letterboxes or
pillarboxes without stretching and performs two-pass `-24 LUFS`
normalization. In the default `--audio-layout auto` mode, it uses the same
audio preparation as `mp4_to_dcp.py`: genuine 5.1 remains discrete 5.1,
stereo receives a restrained frequency-domain 5.1 upmix, and mono is placed
in the centre channel. A source with no soundtrack gets a silent 5.1 track.

Convert one file:

```bash
python3 mp4_to_proludio.py "/path/to/source.mov" "/path/to/approved.mp4"
```

Convert every supported video in a folder:

```bash
python3 mp4_to_proludio.py "/path/to/input folder" "/path/to/output folder"
```

Existing output files are protected by default. Add `--overwrite` to replace
them. On Windows, use `python` instead of `python3` if that is how Python was
installed. FFmpeg must be on `PATH`; test with both `ffmpeg -version` and
`ffprobe -version`.

Use `--audio-stream N` when the desired 5.1 mix is not the first audio stream
(numbering starts at zero). Use `--audio-layout stereo` only when a stereo MP4
is specifically required. The automatic stereo upmix derives centre and
surround information while deliberately avoiding synthesized LFE. It cannot
recover the original production stems; only a source containing discrete 5.1
can provide a genuine original surround mix.

`mp4_to_dcp.py` converts an MP4, MOV, or similar video into unencrypted 5.1
SMPTE 2K DCPs using FFmpeg and DCP-o-matic. It prepares, normalizes, and
verifies six-channel audio before and after DCP creation.

## Optional end slides

Place an image beside its video with the same stem, for example `Trailer.mp4`
and `Trailer.png`. All three converters automatically append it in single-file and
folder mode. Supported images: PNG, JPG/JPEG, BMP, TIF/TIFF, and WebP.
Slides are fitted to the converted video's dimensions with black bars, without
stretching or cropping text. The video's pixel and display aspect ratios are
preserved, including anamorphic scope sources. Slides last 2 seconds by
default, rounded to the nearest video frame. Their audio is silent, with the
same channel layout as the converted video; the MP4 audio track continues
through the slide.

Set a custom duration with either script (also applies to every match in a folder):

```bash
python3 mp4_to_dcp.py Trailer.mp4 --output-dir ./output --slide-duration 3.5
python3 mp4_to_proludio.py Trailer.mp4 ./output/Trailer.mp4 --slide-duration 3.5
```

Matching prefers exact names, then ignores case, spaces, and punctuation.
Minor typos or missing letters are accepted only for a clear close match;
approximate matches are printed. Ambiguous matches are skipped with a warning.
Missing or unreadable slides do not prevent video conversion. Rename an
ambiguous image to exactly match the intended video to resolve the match.

Appending a slide requires an additional video encoding pass. DCP masters use
lossless FFV1 compression for this pass and may require substantial temporary
storage; MP4s retain the projector-compatible H.264/AAC settings.

## Requirements

Install these four command-line programs and ensure they are available on
`PATH`:

- `ffmpeg`
- `ffprobe`
- `dcpomatic2_create`
- `dcpomatic2_cli`

Confirm the installation with:

```bash
ffmpeg -version
ffprobe -version
dcpomatic2_create --version
dcpomatic2_cli --version
```

The source must contain video. If it has no audio, the converter adds a silent
5.1 track so DCP creation can proceed. Otherwise, the first audio stream is
selected by default; use `--audio-stream` for a different language or mix.

Audio is prepared as follows:

- mono is placed in the centre channel, with the other channels silent;
- stereo is conservatively upmixed by FFmpeg's frequency-domain `surround`
  filter, with reduced surrounds and no synthesized LFE;
- native six-channel 5.1 is preserved and normalized;
- unusual layouts are downmixed to stereo before 5.1 preparation, with a
  warning in the conversion log.

Automatic upmixing cannot recover the original dialogue, music, effects, and
ambience stems. Always audition an upmixed master or DCP on a properly mapped
5.1 system before public delivery. Use `--audio-mode preserve` when retaining
the original mono/stereo presentation is more important than surround output.

## Basic usage

Keep `slide_support.py` alongside both conversion scripts when copying them
to another directory or computer. The external FFmpeg and DCP-o-matic programs
are still required.

Open a terminal in the directory containing the script and run:

```bash
python3 mp4_to_dcp.py "/path/to/video.mp4" --output-dir "/path/to/output"
```

This creates both Flat and Scope DCPs. For example:

```bash
python3 mp4_to_dcp.py "$HOME/Videos/trailer.mp4" \
  --output-dir "$HOME/DCPs"
```

Expected project directories:

```text
$HOME/DCPs/trailer_DCP_Flat/
$HOME/DCPs/trailer_DCP_Scope/
```

The exact finished DCP directories are printed after a successful conversion.

## Convert every MP4 in a folder

Pass a directory instead of a file:

```bash
python3 mp4_to_dcp.py "$HOME/Videos/Trailers" \
  --output-dir "$HOME/DCPs"
```

The script snapshots all top-level files ending in `.mp4` (case-insensitive),
sorts them by filename, and converts them sequentially. It does not search
subdirectories and ignores MOV, MKV, hidden non-MP4 files, and other file
types in directory mode.

Each MP4 filename becomes that trailer's DCP name. Consequently, `--name`
cannot be used with a directory input. All other options apply to every MP4.
If one conversion fails, the remaining files are still attempted and a batch
summary is printed at the end.

The default `--format both` creates Flat and Scope DCPs for every trailer. To
create exactly one DCP per trailer, add either `--format flat` or
`--format scope`.

## Common commands

Create only a Flat DCP:

```bash
python3 mp4_to_dcp.py video.mp4 --format flat --output-dir ./output
```

Create only a Scope DCP:

```bash
python3 mp4_to_dcp.py video.mp4 --format scope --output-dir ./output
```

Create a feature instead of a trailer:

```bash
python3 mp4_to_dcp.py feature.mov \
  --content-type feature \
  --name "My Feature" \
  --output-dir ./output
```

Replace existing projects with the same names:

```bash
python3 mp4_to_dcp.py video.mp4 --output-dir ./output --overwrite
```

**Warning:** `--overwrite` deletes matching existing project directories and
the matching normalized master before rebuilding them.

Keep the normalized MKV master:

```bash
python3 mp4_to_dcp.py video.mp4 --output-dir ./output --keep-master
```

Without `--keep-master`, the normalized master is removed when processing
finishes. The completed DCPs are retained.

Set custom audio limits:

```bash
python3 mp4_to_dcp.py video.mp4 \
  --target-lufs -24 \
  --max-true-peak -2 \
  --output-dir ./output
```

The defaults are `-24 LUFS` integrated loudness and `-2 dBTP` maximum true
peak.

Keep stereo in the front left and right channels of the six-channel DCP,
without generating centre or surround content:

```bash
python3 mp4_to_dcp.py video.mp4 \
  --audio-mode preserve \
  --output-dir ./output
```

Select the second audio stream (stream numbering starts at zero):

```bash
python3 mp4_to_dcp.py video.mp4 \
  --audio-stream 1 \
  --output-dir ./output
```

## All options

```text
python3 mp4_to_dcp.py INPUT_FILE_OR_DIRECTORY
    [--name NAME]
    [--content-type trailer|feature]
    [--format both|flat|scope]
    [--output-dir DIRECTORY]
    [--overwrite]
    [--audio-stream NUMBER]
    [--audio-mode auto|preserve]
    [--target-lufs NUMBER]
    [--max-true-peak NUMBER]
    [--keep-master]
    [--slide-duration SECONDS]
```

Display the built-in help:

```bash
python3 mp4_to_dcp.py --help
```

## Troubleshooting

### Required tool is not installed or not in PATH

Run the four version checks from the Requirements section. If a program is
installed elsewhere, add its directory to `PATH` or set `MP4_DCP_TOOLS_DIR` to
an assembled tools directory containing:

```text
tools/
├── ffmpeg/
│   ├── ffmpeg
│   └── ffprobe
└── dcpomatic/
    ├── dcpomatic2_create
    └── dcpomatic2_cli
```

### The wrong language or soundtrack was selected

Run `ffprobe INPUT_FILE` to inspect the streams, then pass the zero-based
soundtrack number with `--audio-stream`. The default is `0`.

### Automatic surround sounds unnatural

Use `--audio-mode preserve` to retain stereo in front L/R (or mono in centre)
inside the six-channel DCP. Automatic upmixing is content-dependent and is not
a substitute for a mix made from production stems.

### Loudness and cinema playback

The default `-24 LUFS` / `-2 dBTP` setting is a conservative safety default,
not a universal theatrical mixing standard. Cinema playback depends on a
calibrated room and sound-rack level. The pipeline measures after upmixing so
the derived centre and surrounds are included, then verifies both the master
and final DCP sound MXF. Preserve intentional dynamics by choosing a suitable
target and auditioning the result rather than relying only on a meter.

## Audio design references

- [FFmpeg audio filter documentation](https://ffmpeg.org/ffmpeg-filters.html)
- [DCP-o-matic audio mapping documentation](https://dcpomatic.com/manual/html/ch06s05.html)
- [EBU loudness guidance](https://tech.ebu.ch/loudness)
- [ITU-R BS.775 multichannel sound recommendation](https://www.itu.int/rec/R-REC-BS.775-4-202212-I)
- [Frequency-domain multichannel upmix research](https://scispace.com/papers/a-frequency-domain-approach-to-multichannel-upmix-46k4eyq91l)

### Output already exists

Choose a different `--output-dir`, move the existing project, or use
`--overwrite` after confirming that the existing output can be deleted.

### Interrupted or failed conversion

The normalized master is normally removed unless `--keep-master` was used.
An abruptly terminated process can leave a partial master or DCP-o-matic
project; inspect those files before removing or overwriting them.

## Optional GUI preview

The command-line script does not require PySide6. To preview the optional GUI:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-gui.txt
python app.py
```

## Windows packaging and bundled tools

Windows build instructions are in `scripts/build_windows.ps1`. Requirements
for audited FFmpeg and DCP-o-matic payloads are documented in
`vendor/README.md`.

## License

MP4-to-DCP is free software under GPL-2.0-or-later. See `LICENSE` and
`THIRD_PARTY_NOTICES.md`.
