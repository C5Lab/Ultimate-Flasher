from __future__ import annotations

import math
import textwrap
import time
from functools import lru_cache
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageOps

from .platform_io import HEIGHT, WIDTH, font


INK = (24, 19, 35)
CREAM = (255, 244, 215)
PINK = (244, 50, 119)
YELLOW = (255, 218, 54)
CYAN = (62, 219, 230)
GREEN = (77, 218, 116)
RED = (239, 71, 78)
PURPLE = (105, 69, 183)
WHITE = (255, 255, 255)
MUTED = (103, 94, 116)

F8 = font(8)
F9 = font(9)
F10 = font(10)
F10B = font(10, True)
F11 = font(11)
F11B = font(11, True)
F12B = font(12, True)
F15B = font(15, True)
F19B = font(19, True)

ANIMATION_LAYER_SIZE = (210, 140)
ANIMATION_FRAME_SIZE = (WIDTH, 140)


@lru_cache(maxsize=1)
def intro_screen() -> Image.Image:
    path = Path(__file__).resolve().parent / "assets" / "intro_uf.png"
    if not path.exists():
        image, draw = base("ULTIMATE FLASHER")
        draw.text((WIDTH // 2, HEIGHT // 2), "LAB5", font=F19B, fill=WHITE, anchor="mm")
        return image
    try:
        with Image.open(path) as source:
            intro = ImageOps.contain(source.convert("RGB"), (WIDTH, HEIGHT), Image.Resampling.LANCZOS)
    except (OSError, ValueError):
        image, draw = base("ULTIMATE FLASHER")
        draw.text((WIDTH // 2, HEIGHT // 2), "LAB5", font=F19B, fill=WHITE, anchor="mm")
        return image
    canvas = Image.new("RGB", (WIDTH, HEIGHT), (9, 4, 20))
    canvas.paste(intro, ((WIDTH - intro.width) // 2, (HEIGHT - intro.height) // 2))
    return canvas


@lru_cache(maxsize=1)
def _flash_layers() -> tuple[Image.Image, Image.Image, Image.Image] | None:
    asset_dir = Path(__file__).resolve().parent / "anime"
    body_path = asset_dir / "base.png"
    if not body_path.exists():
        body_path = asset_dir / "body.png"
    paths = (body_path, asset_dir / "left_hand.png", asset_dir / "right_hand.png")
    if not all(path.exists() for path in paths):
        return None
    try:
        return tuple(
            Image.open(path).convert("RGBA").resize(ANIMATION_LAYER_SIZE, Image.Resampling.LANCZOS)
            for path in paths
        )
    except (OSError, ValueError):
        return None


def _render_flash_character(percent: float, tick: float) -> Image.Image | None:
    layers = _flash_layers()
    if layers is None:
        return None
    body, left_hand, right_hand = layers
    frame = Image.new("RGBA", ANIMATION_FRAME_SIZE, (0, 0, 0, 0))
    origin_x = (ANIMATION_FRAME_SIZE[0] - ANIMATION_LAYER_SIZE[0]) // 2
    frame.alpha_composite(body, (origin_x, 0))

    # The supplied hand layers share the body's source canvas. Moving the whole
    # transparent layers therefore keeps their authored attachment points intact.
    normalized = max(0.0, min(1.0, percent / 100.0))
    separation = int(round(42 * (1.0 - normalized)))
    pulse = int(round(3 * math.sin(tick * 8.0))) if normalized < 0.98 else 0
    frame.alpha_composite(left_hand, (origin_x - separation - pulse, 0))
    frame.alpha_composite(right_hand, (origin_x + separation + pulse, 0))
    return frame


def _panel(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], fill=CREAM, radius=8) -> None:
    x1, y1, x2, y2 = box
    draw.rounded_rectangle((x1 + 2, y1 + 3, x2 + 3, y2 + 4), radius, fill=INK)
    draw.rounded_rectangle(box, radius, fill=fill, outline=INK, width=3)


def _fit(text: str, width: int) -> str:
    return text if len(text) <= width else text[: max(1, width - 1)] + "…"


def base(title: str, accent=PINK) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    image = Image.new("RGB", (WIDTH, HEIGHT), CYAN)
    draw = ImageDraw.Draw(image)
    draw.polygon([(0, 0), (160, 0), (115, 170), (0, 170)], fill=YELLOW)
    draw.polygon([(250, 0), (320, 0), (320, 170), (290, 170)], fill=PURPLE)
    draw.rectangle((0, 0, WIDTH, 25), fill=accent, outline=INK, width=3)
    draw.text((8, 4), _fit(title.upper(), 30), font=F12B, fill=WHITE, stroke_width=1, stroke_fill=INK)
    return image, draw


def footer(draw: ImageDraw.ImageDraw, left="F/X MOVE", right="ENTER OK  ESC BACK") -> None:
    draw.rectangle((0, 154, WIDTH, 170), fill=INK)
    draw.text((6, 157), left, font=F8, fill=YELLOW)
    draw.text((314, 157), right, font=F8, fill=WHITE, anchor="ra")


def menu(title: str, entries: list[dict[str, Any]], selected: int, subtitle: str = "") -> Image.Image:
    image, draw = base(title)
    if subtitle:
        draw.text((312, 7), _fit(subtitle, 22), font=F8, fill=WHITE, anchor="ra")
    visible = 5
    start = max(0, min(selected - visible + 1, len(entries) - visible))
    for row, entry in enumerate(entries[start : start + visible]):
        index = start + row
        y = 29 + row * 28
        chosen = index == selected
        fill = PINK if chosen else CREAM
        _panel(draw, (12, y, 307, y + 24), fill)
        marker = ">" if chosen else "-"
        draw.text((20, y + 5), marker, font=F11B, fill=YELLOW if chosen else PURPLE)
        draw.text((38, y + 4), _fit(str(entry.get("label", "")), 31), font=F11B, fill=WHITE if chosen else INK)
        status = entry.get("status", "")
        if status:
            draw.text((297, y + 6), _fit(str(status), 11), font=F9, fill=WHITE if chosen else MUTED, anchor="ra")
    return image


def detail(title: str, lines: list[str], enabled: bool = True, prompt="ENTER: DETECT USB") -> Image.Image:
    image, draw = base(title, PURPLE)
    _panel(draw, (10, 31, 310, 166), WHITE)
    for index, line in enumerate(lines[:7]):
        color = INK if index == 0 else MUTED
        draw.text((20, 38 + index * 17), _fit(line, 42), font=F11B if index == 0 else F11, fill=color)
    if not enabled:
        draw.rounded_rectangle((80, 116, 240, 142), 8, fill=RED, outline=INK, width=2)
        draw.text((160, 123), "UNAVAILABLE", font=F10B, fill=WHITE, anchor="ma")
    return image


def message(title: str, heading: str, body: str, color=PURPLE, footer_right="ESC BACK") -> Image.Image:
    image, draw = base(title, color)
    _panel(draw, (13, 34, 307, 166), WHITE)
    draw.text((160, 45), heading, font=F15B, fill=color, anchor="ma", stroke_width=1, stroke_fill=INK)
    wrapped = textwrap.wrap(body, width=40)[:4]
    for index, line in enumerate(wrapped):
        draw.text((160, 73 + index * 17), line, font=F11, fill=INK, anchor="ma")
    return image


def confirmation(name: str, port: str, chip: str) -> Image.Image:
    image, draw = base("CONFIRMATION", RED)
    _panel(draw, (14, 32, 306, 166), WHITE)
    draw.text((160, 43), "WARNING: FLASH WRITE", font=F15B, fill=RED, anchor="ma", stroke_width=1, stroke_fill=INK)
    draw.text((23, 69), _fit(name, 39), font=F11B, fill=INK)
    draw.text((23, 89), _fit(port, 39), font=F11, fill=MUTED)
    draw.text((23, 108), f"Detected: {chip}", font=F11B, fill=GREEN)
    draw.rounded_rectangle((48, 123, 272, 144), 6, fill=YELLOW, outline=INK, width=2)
    draw.text((160, 128), "ENTER = CONFIRM", font=F10B, fill=INK, anchor="ma")
    return image


def progress_screen(title: str, percent: float, label: str, phase: str, tick: float | None = None) -> Image.Image:
    image = Image.new("RGB", (WIDTH, HEIGHT), CREAM)
    draw = ImageDraw.Draw(image)
    tick = tick if tick is not None else time.monotonic()
    pct = max(0.0, min(100.0, percent))
    character = _render_flash_character(pct, tick)
    if character is not None:
        image.paste(character, (0, 0), character)
    else:
        draw.rounded_rectangle((65, 12, 255, 128), 8, fill=WHITE, outline=INK, width=3)
        draw.text((160, 44), "LAB5", font=F19B, fill=PINK, anchor="ma")
        draw.text((160, 72), "MASCOT", font=F15B, fill=INK, anchor="ma")
        draw.text((160, 94), "SPRITE SLOT", font=F11, fill=MUTED, anchor="ma")
        travel = int(8 * (0.5 + 0.5 * math.sin(tick * 6)))
        left_x = 28 + travel
        right_x = 292 - travel
        draw.line((0, 72, left_x, 72), fill=PINK, width=5)
        draw.line((right_x, 72, WIDTH, 72), fill=CYAN, width=5)
        draw.rectangle((left_x, 62, left_x + 18, 82), fill=YELLOW, outline=INK, width=3)
        draw.rectangle((right_x - 18, 62, right_x, 82), fill=GREEN, outline=INK, width=3)
    draw.rounded_rectangle((10, 143, 310, 168), 7, fill=INK)
    if pct > 0:
        fill_right = 13 + int(294 * pct / 100)
        draw.rounded_rectangle((13, 146, fill_right, 165), 5, fill=GREEN)
    draw.text((160, 148), f"{pct:05.1f}%", font=F12B, fill=WHITE, anchor="ma")
    return image


def result(title: str, success: bool, body: str, tick: float | None = None) -> Image.Image:
    color = GREEN if success else RED
    heading = "SUCCESS!" if success else "ERROR"
    image = message(title, heading, body, color, "ENTER HOME")
    draw = ImageDraw.Draw(image)
    draw.rectangle((17, 68, 301, 137), fill=WHITE)
    wrapped = textwrap.wrap(body, width=32)[:4]
    for index, line in enumerate(wrapped):
        draw.text((190, 72 + index * 16), line, font=F11, fill=INK, anchor="ma")
    tick = tick if tick is not None else time.monotonic()
    pulse = int(3 * (0.5 + 0.5 * math.sin(tick * 5)))
    draw.ellipse((20 - pulse, 40 - pulse, 65 + pulse, 85 + pulse), outline=color, width=2)
    symbol = "✓" if success else "!"
    draw.ellipse((23, 43, 62, 82), fill=color, outline=INK, width=3)
    draw.text((42, 50), symbol, font=F19B, fill=WHITE, anchor="ma")
    return image


def screen_settings(brightness: int, timeout: int, selected: int) -> Image.Image:
    image, draw = base("SCREEN", PURPLE)
    _panel(draw, (12, 32, 308, 166), WHITE)
    rows = (("BRIGHTNESS", f"{brightness}%"), ("SCREEN OFF", f"{timeout}s"))
    for index, (label, value) in enumerate(rows):
        y = 48 + index * 56
        chosen = index == selected
        draw.text((24, y), label, font=F12B, fill=PINK if chosen else INK)
        draw.text((291, y), value, font=F12B, fill=PURPLE, anchor="ra")
        draw.rounded_rectangle((24, y + 24, 291, y + 37), 6, fill=INK)
        if index == 0:
            width = round(261 * brightness / 100)
        else:
            width = {5: 87, 30: 174, 60: 261}.get(timeout, 87)
        draw.rounded_rectangle((27, y + 27, 27 + width, y + 34), 4, fill=CYAN if chosen else GREEN)
    return image


def serial_console(port: str, lines: list[str]) -> Image.Image:
    image = Image.new("RGB", (WIDTH, HEIGHT), INK)
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, WIDTH, 23), fill=PURPLE)
    draw.text((7, 4), f"SERIAL 115200  {_fit(port, 21)}", font=F11B, fill=WHITE)
    visible = lines[-10:] or ["Waiting for serial data..."]
    for index, line in enumerate(visible):
        draw.text((6, 28 + index * 14), _fit(line.replace("\t", " "), 48), font=F10, fill=GREEN)
    return image


def about(version: str) -> Image.Image:
    image, draw = base("ABOUT", PURPLE)
    _panel(draw, (20, 34, 300, 164), WHITE)
    draw.text((160, 48), "LAB5", font=F19B, fill=PINK, anchor="ma", stroke_width=1, stroke_fill=INK)
    draw.text((160, 82), f"Ultimate Flasher v{version}", font=F12B, fill=INK, anchor="ma")
    draw.rounded_rectangle((45, 116, 275, 147), 7, fill=INK, outline=PINK, width=2)
    draw.text((160, 125), "PRESS Q FOR GITHUB QR", font=F11B, fill=WHITE, anchor="ma")
    return image


@lru_cache(maxsize=1)
def github_qr() -> Image.Image:
    import qrcode

    qr = qrcode.QRCode(version=None, error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=5, border=2)
    qr.add_data("https://github.com/C5Lab/Ultimate-Flasher")
    qr.make(fit=True)
    code = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    code = ImageOps.contain(code, (170, 170), Image.Resampling.NEAREST)
    canvas = Image.new("RGB", (WIDTH, HEIGHT), WHITE)
    canvas.paste(code, ((WIDTH - code.width) // 2, (HEIGHT - code.height) // 2))
    return canvas
