#!/bin/sh
set -eu
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO=$(CDPATH= cd -- "$HERE/.." && pwd)
trap 'systemctl --user start APPLaunch.service >/dev/null 2>&1 || true' EXIT INT TERM
systemctl --user stop APPLaunch.service >/dev/null 2>&1 || true
cd "$REPO"
exec python3 -m CardputerZero
