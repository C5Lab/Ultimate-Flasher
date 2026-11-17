#!/bin/sh
set -eu

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO=$(CDPATH= cd -- "$HERE/.." && pwd)
VERSION=$(tr -d ' \r\n' < "$HERE/VERSION")
BUILD=$(mktemp -d "${TMPDIR:-/tmp}/ultimate-flasher-cardputerzero.XXXXXX")
ROOT="$BUILD/root"
APP_ROOT="$ROOT/usr/share/ultimate-flasher-cardputerzero"
OUT="$HERE/dist"
trap 'rm -rf "$BUILD"' EXIT INT TERM

if [ "${ULTIMATE_FLASHER_SKIP_VENDOR:-0}" != "1" ]; then
    if [ "$(uname -s)" != "Linux" ] || [ "$(dpkg --print-architecture)" != "arm64" ]; then
        printf '%s\n' "ERROR: production packages must be built on ARM64 Linux (normally the Cardputer Zero)." >&2
        printf '%s\n' "Use ULTIMATE_FLASHER_SKIP_VENDOR=1 only for a package-layout test." >&2
        exit 1
    fi
fi

mkdir -p "$ROOT/DEBIAN" "$APP_ROOT" "$OUT"

mkdir -p "$APP_ROOT/CardputerZero"
cp "$REPO/CardputerZero/__init__.py" "$REPO/CardputerZero/__main__.py" \
    "$REPO/CardputerZero/app.py" "$REPO/CardputerZero/platform_io.py" \
    "$REPO/CardputerZero/ui.py" "$APP_ROOT/CardputerZero/"
cp "$REPO/CardputerZero/VERSION" "$APP_ROOT/CardputerZero/VERSION"
cp -R "$REPO/CardputerZero/assets" "$APP_ROOT/CardputerZero/assets"
python3 - "$APP_ROOT/CardputerZero/assets/intro_uf.png" <<'PY'
import sys
from pathlib import Path
from PIL import Image, ImageOps

path = Path(sys.argv[1])
if path.exists():
    image = Image.open(path).convert("RGB")
    image = ImageOps.contain(image, (320, 170), Image.Resampling.LANCZOS)
    image.save(path, optimize=True)
PY
mkdir -p "$APP_ROOT/CardputerZero/anime"
python3 - "$REPO/CardputerZero/anime" "$APP_ROOT/CardputerZero/anime" <<'PY'
import sys
from pathlib import Path
from PIL import Image

source, destination = Path(sys.argv[1]), Path(sys.argv[2])
for name in ("base.png", "body.png", "left_hand.png", "right_hand.png"):
    path = source / name
    if not path.exists():
        continue
    image = Image.open(path).convert("RGBA")
    image.resize((210, 140), Image.Resampling.LANCZOS).save(destination / name, optimize=True)
PY
cp -R "$REPO/lab5_flasher" "$APP_ROOT/lab5_flasher"
cp -R "$REPO/registry" "$APP_ROOT/registry"
find "$APP_ROOT" -type d -name __pycache__ -prune -exec rm -rf '{}' +

if [ "${ULTIMATE_FLASHER_SKIP_VENDOR:-0}" != "1" ]; then
    mkdir -p "$APP_ROOT/vendor"
    python3 -m venv "$BUILD/pip-venv"
    PIP_PYTHON="$BUILD/pip-venv/bin/python"
    "$PIP_PYTHON" -m pip install --disable-pip-version-check --target "$APP_ROOT/vendor" \
        'esptool>=5.2,<6' 'pyserial>=3.5' 'protobuf==3.20.3'
    "$PIP_PYTHON" -m pip install --disable-pip-version-check --no-deps --target "$APP_ROOT/vendor" \
        'flipperzero-protobuf==0.1.20221108'
fi

mkdir -p "$ROOT/usr/share/APPLaunch/bin" "$ROOT/usr/share/APPLaunch/applications"
mkdir -p "$ROOT/usr/share/APPLaunch/share/images" "$ROOT/etc/udev/rules.d"

cat > "$ROOT/usr/share/APPLaunch/bin/ultimate-flasher" <<'LAUNCHER'
#!/bin/sh
APP_ROOT=/usr/share/ultimate-flasher-cardputerzero
export PYTHONPATH="$APP_ROOT/vendor:$APP_ROOT${PYTHONPATH:+:$PYTHONPATH}"
cd "$APP_ROOT"
exec /usr/bin/python3 -m CardputerZero "$@"
LAUNCHER
chmod 755 "$ROOT/usr/share/APPLaunch/bin/ultimate-flasher"

cat > "$ROOT/usr/share/APPLaunch/applications/ultimate-flasher.desktop" <<'DESKTOP'
[Desktop Entry]
Type=Application
Name=Ultimate Flasher
Comment=LAB5 USB firmware flasher
Exec=/usr/share/APPLaunch/bin/ultimate-flasher
Terminal=false
Sysplause=true
Icon=share/images/ultimate-flasher.png
Categories=Utility;Development;
StartupNotify=false
DESKTOP

python3 - "$REPO/CardputerZero/Flasher_icon.png" "$ROOT/usr/share/APPLaunch/share/images/ultimate-flasher.png" <<'PY'
import sys
from PIL import Image

image = Image.open(sys.argv[1]).convert("RGBA")
icon_size = 256
image.thumbnail((icon_size, icon_size), Image.Resampling.LANCZOS)
canvas = Image.new("RGBA", (icon_size, icon_size), (0, 0, 0, 0))
canvas.alpha_composite(image, ((icon_size - image.width) // 2, (icon_size - image.height) // 2))
canvas.save(sys.argv[2])
PY

install -m 644 "$HERE/42-ultimate-flasher.rules" "$ROOT/etc/udev/rules.d/42-ultimate-flasher.rules"

cat > "$ROOT/DEBIAN/control" <<CONTROL
Package: ultimate-flasher-cardputerzero
Version: $VERSION
Architecture: arm64
Maintainer: C5Lab
Depends: python3 (>= 3.10), python3-numpy, python3-pil, python3-qrcode
Section: utils
Priority: optional
Description: Native LAB5 Ultimate Flasher for M5Stack Cardputer Zero
 Uses USB Host to install registered ESP firmware and Flipper Zero FAP packages.
CONTROL

cat > "$ROOT/DEBIAN/postinst" <<'POSTINST'
#!/bin/sh
set -e
if command -v udevadm >/dev/null 2>&1; then
    udevadm control --reload-rules || true
    udevadm trigger || true
fi
exit 0
POSTINST
chmod 755 "$ROOT/DEBIAN/postinst"

dpkg-deb --root-owner-group --build "$ROOT" "$OUT/ultimate-flasher-cardputerzero_${VERSION}_arm64.deb"
printf 'Built %s\n' "$OUT/ultimate-flasher-cardputerzero_${VERSION}_arm64.deb"
