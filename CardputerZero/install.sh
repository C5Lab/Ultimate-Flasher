#!/bin/sh
set -eu

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

if [ "$(dpkg --print-architecture)" != "arm64" ]; then
    printf '%s\n' "ERROR: this installer must run on the ARM64 Cardputer Zero." >&2
    exit 1
fi

sudo apt-get update
sudo apt-get install -y --no-install-recommends \
    python3 python3-pip python3-venv python3-numpy python3-pil python3-qrcode dpkg-dev ca-certificates

sh "$HERE/build_deb.sh"
PACKAGE=$(find "$HERE/dist" -maxdepth 1 -name 'ultimate-flasher-cardputerzero_*_arm64.deb' -print | sort | tail -n 1)
sudo apt-get install -y "$PACKAGE"
systemctl --user restart APPLaunch.service || true
printf '%s\n' "Ultimate Flasher installed. Reconnect USB devices after the udev rule update."
