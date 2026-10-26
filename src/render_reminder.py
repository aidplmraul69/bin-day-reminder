"""Draw a Cast-ready bin-day card from configured text, color and collection date.

The illustration is deliberately self-contained: no household schedule, assets or
locale-specific assumptions are needed to render a reminder.
"""
from datetime import date, datetime
from io import BytesIO
from pathlib import Path
import re

from PIL import Image, ImageDraw, ImageFont, ImageFilter

SIZE = (1024, 600)
COLOR_PATTERN = re.compile(r"#[0-9a-fA-F]{6}\Z")
TEXT_X = 72
TEXT_WIDTH = 515


def font(size: int, bold: bool = False):
    try:
        return ImageFont.truetype("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf", size)
    except OSError:
        return ImageFont.load_default()


def collection_label(now: datetime | None, collection_date: date | None) -> str:
    """Describe the pickup in the supplied reminder's local calendar, if known."""
    if collection_date is None:
        return "COLLECTION REMINDER"
    if now is not None:
        days = (collection_date - now.date()).days
        if days == 0:
            return "PICKUP TODAY"
        if days == 1:
            return "PICKUP TOMORROW"
    return f"PICKUP {collection_date.strftime('%A, %b')} {collection_date.day}".upper()


def _wrap(draw: ImageDraw.ImageDraw, text: str,
          face: ImageFont.FreeTypeFont | ImageFont.ImageFont) -> list[str]:
    """Wrap on words where possible; split exceptionally long tokens by glyph."""
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
    """Fit the text region or mark truncated text with an ellipsis."""
    for size in range(preferred, 17, -1):
        face = font(size, bold=preferred > 40)
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


def _shade(color: tuple[int, int, int], factor: float) -> tuple[int, int, int]:
    return (max(0, min(255, int(color[0] * factor))),
            max(0, min(255, int(color[1] * factor))),
            max(0, min(255, int(color[2] * factor))))


def _background(accent: tuple[int, int, int]) -> Image.Image:
    image = Image.new("RGB", SIZE)
    draw = ImageDraw.Draw(image)
    for y in range(SIZE[1]):
        mix = y / SIZE[1]
        draw.line((0, y, 1024, y), fill=(int(13 + 8 * mix), int(36 + 17 * mix), int(47 + 11 * mix)))
    glow = Image.new("RGBA", SIZE)
    halo = ImageDraw.Draw(glow)
    halo.ellipse((600, 60, 1030, 505), fill=(*accent, 68))
    glow = glow.filter(ImageFilter.GaussianBlur(75))
    return Image.alpha_composite(image.convert("RGBA"), glow).convert("RGB")


def _bin(draw: ImageDraw.ImageDraw, accent: tuple[int, int, int]) -> None:
    """One large, wheeled, colored recycling bin in the right-hand visual field."""
    dark = _shade(accent, .56)
    light = tuple(min(255, int(c * .65 + 88)) for c in accent)
    draw.ellipse((681, 477, 942, 510), fill=(8, 23, 29))  # ground shadow
    # Back handle, wheels and axle sit behind the molded plastic shell.
    draw.rounded_rectangle((847, 125, 914, 207), radius=16, outline=light, width=12)
    draw.rounded_rectangle((716, 448, 901, 479), radius=9, fill=(22, 36, 42))
    for x in (733, 869):
        draw.ellipse((x, 452, x + 49, 501), fill=(8, 18, 24), outline=(102, 121, 124), width=5)
        draw.ellipse((x + 17, 469, x + 32, 484), fill=(63, 78, 81))
    draw.polygon(((692, 193), (918, 193), (899, 463), (719, 463)), fill=dark)
    draw.polygon(((704, 200), (904, 200), (885, 452), (731, 452)), fill=accent)
    draw.line(((718, 207), (742, 442)), fill=light, width=7)
    draw.line(((897, 208), (875, 444)), fill=dark, width=7)
    # A few molded ribs leave the broad center recognizable as a bin, not a flat block.
    for x in (775, 839):
        draw.line(((x - 7, 248), (x - 4, 417)), fill=dark, width=10)
        draw.line(((x - 12, 248), (x - 9, 417)), fill=light, width=3)
    draw.rounded_rectangle((666, 158, 929, 209), radius=12, fill=dark)
    draw.rounded_rectangle((675, 153, 925, 188), radius=11, fill=accent)
    draw.line(((690, 161), (909, 161)), fill=light, width=5)
    draw.rounded_rectangle((680, 187, 918, 199), radius=4, fill=(18, 43, 47))
    # Recycling badge: three distinct arrows, lighter than the body.
    badge = (230, 249, 243)
    cx, cy = 810, 331
    draw.arc((cx - 32, cy - 33, cx + 32, cy + 31), 204, 320, fill=badge, width=6)
    draw.arc((cx - 32, cy - 33, cx + 32, cy + 31), 326, 442, fill=badge, width=6)
    draw.arc((cx - 32, cy - 33, cx + 32, cy + 31), 88, 198, fill=badge, width=6)
    draw.polygon(((829, 302), (841, 302), (831, 317)), fill=badge)
    draw.polygon(((825, 356), (818, 366), (812, 350)), fill=badge)
    draw.polygon(((781, 327), (776, 317), (793, 319)), fill=badge)


def render_bytes(category: str, description: str, color: str,
                 now: datetime | None = None, collection_date: date | None = None) -> bytes:
    """Return a JPEG in memory; `now` is local reminder time, not machine time."""
    if not COLOR_PATTERN.fullmatch(color):
        raise ValueError("color must be a #RRGGBB hex value")
    accent = (int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16))
    image = _background(accent)
    draw = ImageDraw.Draw(image)
    _bin(draw, accent)
    draw.rounded_rectangle((72, 75, 330, 118), radius=18, fill=_shade(accent, .42))
    draw.text((91, 81), "BIN DAY  /  REMINDER", font=font(17, bold=True), fill=(244, 249, 244))
    draw.rounded_rectangle((72, 146, 163, 153), radius=3, fill=accent)
    for text, preferred, top, bottom, fill in (
        (category, 51, 185, 310, (249, 251, 247)),
        (description, 27, 320, 460, (223, 235, 235)),
    ):
        lines, face, step = _fit_lines(draw, text, preferred, top, bottom)
        for index, line in enumerate(lines):
            draw.text((TEXT_X, top + index * step), line, font=face, fill=fill)
    draw.rounded_rectangle((72, 492, 405, 543), radius=14, fill=(29, 58, 68))
    draw.rounded_rectangle((72, 492, 80, 543), radius=3, fill=accent)
    draw.text((96, 505), collection_label(now, collection_date), font=font(19, bold=True),
              fill=(246, 251, 248))
    output = BytesIO()
    image.save(output, "JPEG", quality=92)
    return output.getvalue()


def render(category: str, description: str, color: str, target: str | Path,
           now: datetime | None = None, collection_date: date | None = None) -> Path:
    """File-based compatibility API for previews and direct use."""
    data = render_bytes(category, description, color, now, collection_date)
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return target
