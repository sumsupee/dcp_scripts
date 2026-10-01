# Vendored tool payloads

Do not commit or release an unverified collection of binaries. Prepare one
payload for each operating-system/architecture pair:

```
vendor/payloads/<platform>/
├── bundle-manifest.json
├── tools/
│   ├── ffmpeg/
│   │   ├── ffmpeg[.exe]
│   │   └── ffprobe[.exe]
│   └── dcpomatic/
│       ├── dcpomatic2_create[.exe]
│       ├── dcpomatic2_cli[.exe]
│       └── all required runtime libraries and data
└── licenses/
    ├── ffmpeg/
    └── dcpomatic/
```

Copy `bundle-manifest.example.json` to the payload as
`bundle-manifest.json`, fill in its metadata, and list every payload file with
its SHA-256 digest. Run `scripts/stage_tools.py` to validate and stage it.

The payload must include license notices from the exact upstream binary/source
distribution; do not replace them with links. Corresponding source archives
should be published beside the application release, and their URLs and hashes
recorded in the manifest.
