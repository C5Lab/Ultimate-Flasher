# Ultimate Flasher for Cardputer Zero

Native, keyboard-first Python application for M5Stack Cardputer Zero (ARM64 Linux, 320×170).
It is a real USB flasher, not a UI-only demonstration.

## What works

- APPLaunch `.desktop` integration and ARM64 `.deb` packaging;
- direct RGB565 framebuffer rendering and exclusive evdev keyboard input;
- a 2.5-second, skippable startup intro loaded from `assets/intro_uf.png`;
- LAB5 GEAR, M5 DEVICES, FLIPPER ZERO and SETTINGS menus;
- all targets come from the shared `registry/firmware.json` file;
- incomplete targets remain visible but cannot be selected for flashing;
- ESP chip detection with `esptool --chip auto chip-id` and a hard compatibility gate;
- multiple binaries and duplicate images at their registry offsets;
- current GitHub blob check and a persistent per-user cache in
  `~/.cache/ultimate-flasher/` (or `$ULTIMATE_FLASHER_CACHE`);
- atomic cache updates: unchanged binaries are reused, changed binaries are staged and verified
  before replacing the previous version, and registry-orphaned files are removed afterwards;
- real progress parsed from esptool output, byte-for-byte `verify-flash`, disconnect/error handling
  and cancellation with `ESC`;
- Flipper Zero `.fap` transfer through its protobuf Storage RPC, followed by MD5 verification on
  the Flipper. It never invokes esptool for `.fap` files.

## Device controls

- `F` / `X` or arrows: previous / next item;
- `ENTER`: open, select, or confirm;
- `ESC`: back; during an operation it requests cancellation;
- holding `ESC` is still handled by APPLaunch when the process is no longer responsive.

The app requires a second `ENTER` on a red confirmation screen after USB detection and chip
compatibility validation. It cannot write flash before this confirmation.

## Install on Cardputer Zero

Clone the repository on the device, then run:

```sh
cd Ultimate-Flasher/CardputerZero
sh install.sh
```

The installer:

1. verifies the Debian architecture is `arm64`;
2. installs Pillow/numpy and packaging prerequisites;
3. vendors esptool 5.x, pyserial and the Flipper protobuf client into the package;
4. builds and installs `dist/ultimate-flasher-cardputerzero_<version>_arm64.deb`;
5. installs udev rules and restarts APPLaunch.

The production package is intentionally built on ARM64 Linux because esptool's dependency set can
contain native wheels. `build_deb.sh` refuses to label macOS/x86 binaries as an ARM64 Linux package.

For development directly from the checkout:

```sh
sudo apt install python3-numpy python3-pil python3-serial
sh CardputerZero/dev.sh
```

Desktop UI preview (no framebuffer or USB access required):

```sh
python3 -m pip install -e '.[cardputerzero]'
python3 -m CardputerZero --screenshot preview.png
```

## USB Host checklist

Before flashing, switch Cardputer Zero's shared USB port to **Host** mode and connect the target
using a data-capable cable/adapter. The SETTINGS → USB DIAGNOSTICS page checks:

- Linux USB host sysfs availability;
- 64-bit Python;
- esptool and pyserial imports;
- discovered serial ports and read/write permissions.

ESP boards may still require holding BOOT while connecting if their reset circuit does not support
automatic download mode. Some targets can draw more current than the handheld can reliably supply;
use a powered USB hub when the device browns out or repeatedly disconnects.

## MonsterRF provenance

The `monsterrf` registry entry is transcribed from elpadrino26's original
[JanosRF web flasher](https://github.com/elpadrino26/janosrf-web-flasher): ESP32-C5, 460800 baud,
DIO/80 MHz/8 MB, bootloader at `0x2000`, partition table at `0x10000`, and the same
`projectZero.bin` image at `0x20000` and `0x410000`. Its optional running-firmware transition is
also preserved as `download\n` at 115200 before the ROM reset path. Source and author remain in the
shared registry.

## Honest limitations

- This checkout can test the UI, registry, cache transaction and generated commands, but a physical
  Cardputer Zero plus every supported target is required for end-to-end electrical validation.
- Flipper transfer uses the upstream `flipperzero-protobuf` Python client, which upstream labels
  alpha. The app verifies VID/PID `0483:5740`, the RPC result and the on-device MD5; firmware forks
  that remove or alter the standard RPC service are reported as unsupported instead of simulated.
- Flipper `.fap` API compatibility is determined by the FAP and the firmware installed on the
  Flipper. A successful file transfer cannot make an incompatible FAP ABI compatible.
- `cores3` and `mate` have no confirmed flash map in the shared registry, so the UI intentionally
  shows them as unavailable.
- The flashing animation composites the approved `anime/body.png`, `left_hand.png`, and
  `right_hand.png` layers. Hand separation follows measured operation progress; animation timing
  only adds a subtle one-pixel motion and never invents progress.

## References

- [Official Cardputer Zero usage and APPLaunch controls](https://docs.m5stack.com/en/compute/cardputerzero/usage)
- [CardputerZero launcher source (320×170, framebuffer, evdev, ARM64)](https://github.com/CardputerZero/launcher)
- [CardputerZero application template and package layout](https://github.com/CardputerZero/Template)
- [Espressif ESP32-C5 esptool documentation](https://docs.espressif.com/projects/esptool/en/latest/esp32c5/)
- [Official Flipper Zero protobuf definitions](https://github.com/flipperdevices/flipperzero-protobuf)
- [Official Python protobuf client](https://github.com/flipperdevices/flipperzero_protobuf_py)
- [C5Lab Flipper Light source and confirmed `/ext/apps/GPIO` install path](https://github.com/C5Lab/flipperLight)
