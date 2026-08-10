# Ultimate LAB5 Flasher

A unified flasher for LAB5 firmware, dedicated ESP devices, and companion app packages.

## Core Idea

The source firmware repositories remain the source of truth. This project keeps:

- a central target registry in `registry/firmware.json`,
- a local firmware cache in `firmware/`,
- a shared sync and flashing engine,
- a static web flasher in `web/`.

Before every CLI `flash`, the tool checks GitHub by default:

1. finds the latest commit for the firmware directory,
2. reads the file list from the source repository,
3. compares GitHub blob SHA values with the local cache,
4. downloads only missing or changed files,
5. runs `esptool` after the cache is up to date.

If the update check fails, flashing stops unless you explicitly use `--allow-stale`
to flash from the local cache.

## Quick Start

```bash
python ultimate.py list
python ultimate.py sync --dry-run
python ultimate.py sync projectzero-c5
python ultimate.py flash projectzero-c5 --port /dev/ttyACM0
```

The first `ultimate.py` run creates a local `.venv` and installs `esptool` and
`pyserial`.

## Web Flasher

The web UI lives in `web/`. Generate web assets from the current firmware cache:

```bash
python ultimate.py export-web --sync
python -m http.server 8088 --directory web
```

Then open:

```text
http://localhost:8088/
```

The page loads `web/generated/targets.json`, sets the selected firmware
manifest, and uses `esp-web-install-button` for the browser flashing flow. The
`Detect device` button uses WebSerial to read USB VID/PID and tries to match a
target using `web.usb` rules from `registry/firmware.json`.

Current web export includes:

- `projectzero-c5`
- `cardputer-adv`
- `tab5`
- `flipper-light` as a downloadable `.fap` package

ESP Web Tools handles chip-family matching during installation. Exact product
matching, such as CoreS3 versus another ESP32-S3 board, needs `web.usb` rules or
a deeper future fingerprint step through `esptool-js`.

## Examples

List targets:

```bash
python ultimate.py list --include-disabled
```

Sync all enabled targets:

```bash
python ultimate.py sync
```

Flash with automatic new-port detection:

```bash
python ultimate.py flash cardputer-adv
```

Flash with erase and serial monitor:

```bash
python ultimate.py flash projectzero-c5 --erase --monitor
```

Flash without optional entries:

```bash
python ultimate.py flash projectzero-c5 --skip-optional
```

Show local cache status:

```bash
python ultimate.py status
```

## GitHub Tokens And Private Repositories

Public repositories work without configuration, but anonymous GitHub API limits
are low. For reliable automation, set a token with `contents:read` access:

```bash
export GITHUB_TOKEN=ghp_...
python ultimate.py sync
```

The same token is required for private repositories:

```bash
export GITHUB_TOKEN=ghp_...
python ultimate.py sync cores3 --include-disabled
```

If the cache is already current and GitHub temporarily rate-limits requests, you
can export the web UI from cached assets:

```bash
python ultimate.py export-web --sync --allow-stale
```

`cores3` and `mate` are currently disabled templates because their source
repositories are private or not ready yet. Enable them after file names and
offsets are confirmed.

## Adding Firmware

Add a target to `registry/firmware.json`:

```json
{
  "enabled": true,
  "name": "Device Name",
  "chip": "esp32s3",
  "baud": 460800,
  "source": {
    "type": "github",
    "repo": "C5Lab/repo",
    "branch": "main",
    "path": "path/to/binaries"
  },
  "flash": [
    { "offset": "0x0", "file": "bootloader.bin" },
    { "offset": "0x8000", "file": "partition-table.bin" },
    { "offset": "0x10000", "file": "firmware.bin" }
  ]
}
```

Use `extras` for files that should be cached but not flashed.

## Notes

- `sync.py` exists as a compatibility shortcut.
- The main launcher is `python ultimate.py ...`.
- `firmware/` and `web/generated/` are generated cache/output directories and
  are intentionally ignored by git.
