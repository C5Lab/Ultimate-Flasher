from __future__ import annotations

import os
import select
import struct
from pathlib import Path

try:
    import fcntl
except ImportError:  # screenshot/test mode on non-Linux hosts
    fcntl = None

import numpy as np
from PIL import Image, ImageFont


WIDTH, HEIGHT = 320, 170
FRAMEBUFFER = os.environ.get("CZ_FB", "/dev/fb0")
KEYBOARD = os.environ.get("CZ_KBD", "/dev/input/cardputer-zero-internal")

KEY_ESC, KEY_BACKSPACE, KEY_TAB, KEY_ENTER, KEY_SPACE = 1, 14, 15, 28, 57
KEY_UP, KEY_DOWN, KEY_LEFT, KEY_RIGHT = 103, 108, 105, 106
KEY_SEMICOLON, KEY_DOT, KEY_COMMA, KEY_SLASH = 39, 52, 51, 53
KEY_F, KEY_X, KEY_Z, KEY_C = 33, 45, 44, 46
KEY_Q = 16

UP_KEYS = {KEY_UP, KEY_SEMICOLON, KEY_F}
DOWN_KEYS = {KEY_DOWN, KEY_DOT, KEY_X}
LEFT_KEYS = {KEY_LEFT, KEY_COMMA, KEY_Z}
RIGHT_KEYS = {KEY_RIGHT, KEY_SLASH, KEY_C}
OK_KEYS = {KEY_ENTER}
BACK_KEYS = {KEY_ESC, KEY_BACKSPACE}

BACKLIGHT = Path("/sys/class/backlight/backlight")


def set_brightness(percent: int) -> bool:
    try:
        maximum = int((BACKLIGHT / "max_brightness").read_text().strip())
        value = round(maximum * max(0, min(100, percent)) / 100)
        (BACKLIGHT / "brightness").write_text(f"{value}\n")
        return True
    except (OSError, ValueError):
        return False

_EVENT_FORMAT = "llHHi"
_EVENT_SIZE = struct.calcsize(_EVENT_FORMAT)
_EV_KEY = 1
_EVIOCGRAB = 0x40044590


def font(size: int, bold: bool = False):
    names = ("DejaVuSans-Bold.ttf", "NotoSans-Bold.ttf") if bold else ("DejaVuSans.ttf", "NotoSans-Regular.ttf")
    roots = (
        "/usr/share/fonts/truetype/dejavu",
        "/usr/share/fonts/dejavu",
        "/usr/share/fonts/truetype/noto",
    )
    for root in roots:
        for name in names:
            path = os.path.join(root, name)
            if os.path.exists(path):
                return ImageFont.truetype(path, size)
    for name in names:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


class Screen:
    def __init__(self, path: str | None = FRAMEBUFFER):
        if path == "/dev/fb0" and not os.path.exists(path) and os.path.exists("/dev/fb_lcd"):
            path = "/dev/fb_lcd"
        self.handle = open(path, "r+b", buffering=0) if path else None

    def show(self, image: Image.Image) -> None:
        if self.handle is None:
            return
        pixels = np.asarray(image.convert("RGB"), dtype=np.uint16)
        rgb565 = (
            ((pixels[..., 0] >> 3) << 11)
            | ((pixels[..., 1] >> 2) << 5)
            | (pixels[..., 2] >> 3)
        )
        self.handle.seek(0)
        self.handle.write(rgb565.astype("<u2").tobytes())

    def close(self) -> None:
        if self.handle is not None:
            self.show(Image.new("RGB", (WIDTH, HEIGHT), "black"))
            self.handle.close()
            self.handle = None


class Keyboard:
    def __init__(self, path: str = KEYBOARD):
        self.fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        self.grabbed = False
        if fcntl is not None:
            try:
                fcntl.ioctl(self.fd, _EVIOCGRAB, 1)
                self.grabbed = True
            except OSError:
                pass

    def read(self, timeout: float = 0.1) -> list[int]:
        ready, _, _ = select.select([self.fd], [], [], timeout)
        if not ready:
            return []
        try:
            data = os.read(self.fd, _EVENT_SIZE * 64)
        except BlockingIOError:
            return []
        keys = []
        for offset in range(0, len(data) - _EVENT_SIZE + 1, _EVENT_SIZE):
            _, _, event_type, code, value = struct.unpack_from(_EVENT_FORMAT, data, offset)
            if event_type == _EV_KEY and value in (1, 2):
                keys.append(code)
        return keys

    def close(self) -> None:
        if self.grabbed and fcntl is not None:
            try:
                fcntl.ioctl(self.fd, _EVIOCGRAB, 0)
            except OSError:
                pass
        os.close(self.fd)
