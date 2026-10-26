"""Render a generic 1024×600 JPEG card for a Cast media receiver.

Text is word-wrapped inside fixed category and description areas. Font size is
reduced to 18 px when needed; if text still cannot fit, an ellipsis on the
last visible line explicitly marks the omitted remainder.
"""
from datetime import datetime
from io import BytesIO
from pathlib import Path
import re

from PIL import Image, ImageDraw, ImageFont

SIZE = (1024, 600)
COLOR_PATTERN = re.compile(r"#[0-9a-fA-F]{6}\Z")
TEXT_X = 105
TEXT_WIDTH = 820


def font(size: int):
    try:
        return ImageFont.truetype("DejaVuSans.ttf", size)
    except OSError:
        return ImageFont.load_default()


def _wrap(draw: ImageDraw.ImageDraw, text: str,
          face: ImageFont.FreeTypeFont | ImageFont.ImageFont) -> list[str]:
    """Wrap at words when possible, splitting unbroken words at glyph boundaries."""
    lines = []
    for paragraph in text.split("\n"):
        line = ""
        for word in paragraph.split():
            candidate = f"{line} {word}" if line else word
            if draw.textlength(candidate, font=face) <= TEXT_WIDTH:
                line = candidate
                continue
            if line:
                lines.append(line)
                line = ""
            for char in word:
                if line and draw.textlength(line + char, font=face) > TEXT_WIDTH:
                    lines.append(line)
                    line = ""
                line += char
        lines.append(line)
    return lines


def _fit_lines(draw: ImageDraw.ImageDraw, text: str, preferred: int,
               top: int, bottom: int) -> tuple[list[str], ImageFont.FreeTypeFont | ImageFont.ImageFont, int]:
    """Pick the largest size fitting the region, or ellipsize at minimum size."""
    for size in range(preferred, 17, -1):
        face = font(size)
        step = max(size + 8, int(draw.textbbox((0, 0), "Ágj", font=face)[3]) + 4)
        capacity = max(1, (bottom - top) // step)
        lines = _wrap(draw, text, face)
        if len(lines) <= capacity or size == 18:
            if len(lines) > capacity:
                lines = lines[:capacity]
                last = lines[-1].rstrip()
                while last and draw.textlength(last + "…", font=face) > TEXT_WIDTH:
                    last = last[:-1].rstrip()
                lines[-1] = last + "…"
            return lines, face, step
    raise AssertionError("unreachable")


def render_bytes(category: str, description: str, color: str,
                 now: datetime | None = None) -> bytes:
    """Return a complete JPEG in memory without creating files or directories."""
    if not COLOR_PATTERN.fullmatch(color):
        raise ValueError("color must be a #RRGGBB hex value")
    accent = tuple(bytes.fromhex(color[1:]))
    image = Image.new("RGB", SIZE, (18, 31, 40))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((58, 60, 966, 540), radius=32, fill=(28, 46, 57))
    draw.rectangle((58, 60, 70, 540), fill=accent)
    draw.text((TEXT_X, 105), "REMINDER", font=font(32), fill=accent)
    for text, preferred, top, bottom, fill in (
        (category, 52, 185, 310, (250, 250, 245)),
        (description, 27, 320, 460, (218, 228, 229)),
    ):
        lines, face, step = _fit_lines(draw, text, preferred, top, bottom)
        for index, line in enumerate(lines):
            draw.text((TEXT_X, top + index * step), line, font=face, fill=fill)
    if now:
        draw.text((TEXT_X, 470), now.strftime("%Y-%m-%d %H:%M"),
                  font=font(20), fill=(170, 190, 195))
    output = BytesIO()
    image.save(output, "JPEG", quality=90)
    return output.getvalue()


def render(category: str, description: str, color: str, target: str | Path,
           now: datetime | None = None) -> Path:
    """Legacy file API: write the same JPEG and return its path."""
    data = render_bytes(category, description, color, now)
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return target
