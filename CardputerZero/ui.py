from __future__ import annotations

import math
import textwrap
import time
from functools import lru_cache
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

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
F12B = font(12, True)
F15B = font(15, True)
F19B = font(19, True)

ANIMATION_SIZE = (150, 100)


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
            Image.open(path).convert("RGBA").resize(ANIMATION_SIZE, Image.Resampling.LANCZOS)
            for path in paths
        )
    except (OSError, ValueError):
        return None


def _render_flash_character(percent: float, tick: float) -> Image.Image | None:
    layers = _flash_layers()
    if layers is None:
        return None
    body, left_hand, right_hand = layers
    frame = Image.new("RGBA", ANIMATION_SIZE, (0, 0, 0, 0))
    bob = int(round(math.sin(tick * 5.0)))
    frame.alpha_composite(body, (0, bob))

    # The supplied hand layers share the body's source canvas. Moving the whole
    # transparent layers therefore keeps their authored attachment points intact.
    normalized = max(0.0, min(1.0, percent / 100.0))
    eased = 1.0 - (1.0 - normalized) ** 3
    separation = int(round(18 * (1.0 - eased)))
    pulse = int(round(math.sin(tick * 8.0))) if normalized < 0.98 else 0
    frame.alpha_composite(left_hand, (-separation - pulse, bob))
    frame.alpha_composite(right_hand, (separation + pulse, bob))
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
    visible = 4
    start = max(0, min(selected - visible + 1, len(entries) - visible))
    for row, entry in enumerate(entries[start : start + visible]):
        index = start + row
        y = 31 + row * 29
        chosen = index == selected
        fill = PINK if chosen else CREAM
        _panel(draw, (12, y, 307, y + 23), fill)
        marker = ">" if chosen else "-"
        draw.text((20, y + 5), marker, font=F10B, fill=YELLOW if chosen else PURPLE)
        draw.text((38, y + 4), _fit(str(entry.get("label", "")), 34), font=F10B, fill=WHITE if chosen else INK)
        status = entry.get("status", "")
        if status:
            draw.text((297, y + 6), _fit(str(status), 12), font=F8, fill=WHITE if chosen else MUTED, anchor="ra")
    footer(draw)
    return image


def detail(title: str, lines: list[str], enabled: bool = True, prompt="ENTER: DETECT USB") -> Image.Image:
    image, draw = base(title, PURPLE)
    _panel(draw, (10, 31, 310, 148), WHITE)
    for index, line in enumerate(lines[:7]):
        color = INK if index == 0 else MUTED
        draw.text((20, 39 + index * 14), _fit(line, 47), font=F10B if index == 0 else F9, fill=color)
    if not enabled:
        draw.rounded_rectangle((80, 116, 240, 142), 8, fill=RED, outline=INK, width=2)
        draw.text((160, 123), "NIEDOSTĘPNE", font=F10B, fill=WHITE, anchor="ma")
        footer(draw, "", "ESC BACK")
    else:
        footer(draw, "", prompt)
    return image


def message(title: str, heading: str, body: str, color=PURPLE, footer_right="ESC BACK") -> Image.Image:
    image, draw = base(title, color)
    _panel(draw, (13, 34, 307, 148), WHITE)
    draw.text((160, 45), heading, font=F15B, fill=color, anchor="ma", stroke_width=1, stroke_fill=INK)
    wrapped = textwrap.wrap(body, width=47)[:5]
    for index, line in enumerate(wrapped):
        draw.text((160, 74 + index * 14), line, font=F9, fill=INK, anchor="ma")
    footer(draw, "", footer_right)
    return image


def confirmation(name: str, port: str, chip: str) -> Image.Image:
    image, draw = base("POTWIERDZENIE", RED)
    _panel(draw, (14, 32, 306, 149), WHITE)
    draw.text((160, 43), "UWAGA: ZAPIS FLASH", font=F15B, fill=RED, anchor="ma", stroke_width=1, stroke_fill=INK)
    draw.text((23, 70), _fit(name, 43), font=F10B, fill=INK)
    draw.text((23, 89), _fit(port, 45), font=F9, fill=MUTED)
    draw.text((23, 106), f"Wykryto: {chip}", font=F9, fill=GREEN)
    draw.rounded_rectangle((48, 123, 272, 144), 6, fill=YELLOW, outline=INK, width=2)
    draw.text((160, 128), "ENTER = POTWIERDZAM", font=F10B, fill=INK, anchor="ma")
    footer(draw, "", "ESC ANULUJ")
    return image


def progress_screen(title: str, percent: float, label: str, phase: str, tick: float | None = None) -> Image.Image:
    image, draw = base(title, PINK)
    tick = tick if tick is not None else time.monotonic()
    _panel(draw, (9, 31, 311, 125), WHITE)
    pct = max(0.0, min(100.0, percent))
    character = _render_flash_character(pct, tick)
    if character is not None:
        image.paste(character, (85, 27), character)
        draw.rounded_rectangle((16, 39, 76, 59), 5, fill=YELLOW, outline=INK, width=2)
        draw.text((46, 44), _fit(phase.upper(), 10), font=F8, fill=INK, anchor="ma")
        draw.rounded_rectangle((244, 39, 303, 59), 5, fill=CYAN, outline=INK, width=2)
        draw.text((273, 43), f"{pct:05.1f}%", font=F9, fill=INK, anchor="ma")
    else:
        draw.rounded_rectangle((19, 41, 116, 100), 8, fill=(238, 235, 231), outline=INK, width=3)
        draw.text((67, 51), "LAB5", font=F15B, fill=PINK, anchor="ma")
        draw.text((67, 72), "MASCOT", font=F10B, fill=INK, anchor="ma")
        draw.text((67, 86), "SPRITE SLOT", font=F8, fill=MUTED, anchor="ma")
        travel = int(8 * (0.5 + 0.5 * math.sin(tick * 6)))
        left_x = 145 + travel
        right_x = 259 - travel
        draw.line((114, 72, left_x, 72), fill=PINK, width=5)
        draw.line((right_x, 72, 301, 72), fill=CYAN, width=5)
        draw.rectangle((left_x, 62, left_x + 18, 82), fill=YELLOW, outline=INK, width=3)
        draw.rectangle((right_x - 18, 62, right_x, 82), fill=GREEN, outline=INK, width=3)
    draw.rounded_rectangle((18, 132, 301, 149), 6, fill=INK)
    if pct > 0:
        draw.rounded_rectangle((21, 135, 21 + int(277 * pct / 100), 146), 4, fill=GREEN)
    draw.text((160, 135), f"{pct:05.1f}%  {_fit(phase.upper(), 16)}", font=F9, fill=WHITE, anchor="ma")
    if character is not None:
        draw.rounded_rectangle((77, 109, 243, 123), 4, fill=INK)
        draw.text((160, 112), _fit(label, 27), font=F8, fill=WHITE, anchor="ma")
    else:
        draw.text((160, 117), _fit(label, 52), font=F8, fill=INK, anchor="ma")
    footer(draw, "", "ESC CANCEL")
    return image


def result(title: str, success: bool, body: str, tick: float | None = None) -> Image.Image:
    color = GREEN if success else RED
    heading = "SUKCES!" if success else "ERROR"
    image = message(title, heading, body, color, "ENTER HOME")
    draw = ImageDraw.Draw(image)
    draw.rectangle((17, 68, 301, 137), fill=WHITE)
    wrapped = textwrap.wrap(body, width=38)[:4]
    for index, line in enumerate(wrapped):
        draw.text((190, 73 + index * 14), line, font=F9, fill=INK, anchor="ma")
    tick = tick if tick is not None else time.monotonic()
    pulse = int(3 * (0.5 + 0.5 * math.sin(tick * 5)))
    draw.ellipse((20 - pulse, 40 - pulse, 65 + pulse, 85 + pulse), outline=color, width=2)
    symbol = "✓" if success else "!"
    draw.ellipse((23, 43, 62, 82), fill=color, outline=INK, width=3)
    draw.text((42, 50), symbol, font=F19B, fill=WHITE, anchor="ma")
    return image
