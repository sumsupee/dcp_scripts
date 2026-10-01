# Third-party software

MP4-to-DCP is distributed under GPL-2.0-or-later. The full application
license is in `LICENSE`.

Release bundles may include the following separate command-line programs.
The exact versions, file hashes, build configuration, license selection, and
corresponding-source locations for a particular release are recorded in its
`bundle-manifest.json`.

## FFmpeg and FFprobe

Copyright belongs to the FFmpeg developers and other contributors.

FFmpeg is normally available under LGPL-2.1-or-later, but a build containing
GPL components is distributed under GPL-2.0-or-later. Our release process must
record the output of `ffmpeg -version`, including its configuration line, and
carry the license files applicable to the exact binary being shipped.

- Project: https://ffmpeg.org/
- Source: https://ffmpeg.org/download.html#get-sources
- License information: https://ffmpeg.org/legal.html

## DCP-o-matic

Copyright belongs to Carl Hetherington and other DCP-o-matic contributors.
DCP-o-matic is distributed under GNU GPL version 2. Release bundles must carry
its unmodified `COPYING` file and the notices for the libraries included in the
specific DCP-o-matic runtime payload.

- Project: https://dcpomatic.com/
- Source: https://dcpomatic.com/development
- License: https://dcpomatic.com/manual/html/ch01s03.html

## Qt for Python

The GUI distribution must also retain the notices and license materials placed
in the build output by PySide6/Qt's deployment tooling. Do not delete those
files while assembling an installer.

## Corresponding source

For every published binary release, publish the application's source and the
complete corresponding source for bundled GPL components from the same release
page or distribution location. Keep build scripts, patches, and configuration
needed to reproduce modified binaries with that source. An upstream homepage
alone is not a substitute for corresponding source for a binary you distribute.
