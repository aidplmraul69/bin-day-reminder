"""The Cast illustration remains readable and reflects the configured pickup date."""
from datetime import date, datetime, timezone
from io import BytesIO

from PIL import Image, ImageDraw

from src.render_reminder import collection_label, render_bytes


def test_collection_label_uses_calendar_day_not_elapsed_hours():
    now = datetime(2026, 10, 26, 23, 30, tzinfo=timezone.utc)
    assert collection_label(now, date(2026, 10, 26)) == "PICKUP TODAY"
    assert collection_label(now, date(2026, 10, 27)) == "PICKUP TOMORROW"
    assert collection_label(now, date(2026, 10, 29)) == "PICKUP THURSDAY, OCT 29"
    assert collection_label(now, None) == "COLLECTION REMINDER"


def test_configured_color_appears_in_large_right_bin_and_card_has_gradient():
    def image_for(color):
        data = render_bytes("Paper / Cardboard", "Put clean paper out", color,
                            datetime(2026, 10, 26, 9, tzinfo=timezone.utc), date(2026, 10, 26))
        with Image.open(BytesIO(data)) as image:
            assert image.format == "JPEG"
            assert image.size == (1024, 600)
            return image.convert("RGB")

    blue, yellow = image_for("#3377DD"), image_for("#E6BB36")
    # The bin body, not just a small accent stripe, must follow the configuration.
    for image, channel in ((blue, 2), (yellow, 0)):
        body = image.getpixel((760, 310))
        assert body[channel] > max(body[(channel + 1) % 3], body[(channel + 2) % 3]) + 15
        assert image.getpixel((10, 10)) != image.getpixel((10, 550))
    assert blue.getpixel((760, 310)) != yellow.getpixel((760, 310))


def test_purpose_and_long_text_stay_in_left_column(monkeypatch):
    written = []
    original = ImageDraw.ImageDraw.text

    def record(self, xy, text, *args, **kwargs):
        written.append((xy, text, kwargs.get("font")))
        return original(self, xy, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", record)
    render_bytes("Paper / Cardboard", "Flatten boxes for pickup", "#3377DD",
                 datetime(2026, 10, 26, 9, tzinfo=timezone.utc), date(2026, 10, 26))
    assert any("PICKUP TODAY" in text for _, text, _ in written)
    assert any("Paper / Cardboard" in text for _, text, _ in written)
    assert any("Flatten boxes" in text for _, text, _ in written)
    draw = ImageDraw.Draw(Image.new("RGB", (1024, 600)))
    for (x, y), text, face in written:
        if 175 <= y <= 460:
            box = draw.textbbox((x, y), text, font=face)
            assert box[2] < 610  # no text covers the bin
            assert box[3] <= 460
