"""In-memory Cast card rendering and long-text layout."""
import io
import sys
from datetime import datetime
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from render_reminder import render_bytes, render


def test_render_bytes_returns_jpeg_without_writing_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    data = render_bytes("Hârtie ♻", "Dépôt demain — бумаги", "#75cf9a",
                        now=datetime.fromisoformat("2026-10-21T18:00:00+00:00"))
    assert isinstance(data, bytes)
    with Image.open(io.BytesIO(data)) as image:
        assert image.format == "JPEG"
        assert image.size == (1024, 600)
        image.load()
    assert list(tmp_path.iterdir()) == []


def test_text_beyond_old_character_limits_is_visible(monkeypatch):
    written = []
    original = ImageDraw.ImageDraw.text

    def recording_text(self, xy, text, *args, **kwargs):
        written.append(text)
        return original(self, xy, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", recording_text)
    render_bytes("Hârtie ♻ " + "collection " * 5 + "FINCAT",
                 "Dépôt demain — бумаги " + "preparation " * 6 + "FINDESC",
                 "#75cf9a")
    assert any("FINCAT" in line for line in written)
    assert any("FINDESC" in line for line in written)
    assert not any(line.endswith("…") for line in written)


def test_render_bytes_rejects_invalid_color():
    with pytest.raises(ValueError, match="color"):
        render_bytes("Glass", "Take it out", "red", None)


def test_legacy_render_writes_same_jpeg_as_bytes(tmp_path):
    when = datetime(2026, 10, 21, 18)
    expected = render_bytes("Glass", "Take out glass", "#75cf9a", when)
    target = tmp_path / "nested" / "reminder.jpg"
    assert render("Glass", "Take out glass", "#75cf9a", target, when) == target
    assert target.read_bytes() == expected


def test_long_text_wraps_within_card_and_indicates_overflow(monkeypatch):
    calls = []
    original = ImageDraw.ImageDraw.text

    def recording_text(self, xy, text, *args, **kwargs):
        calls.append((xy, text, kwargs.get("font")))
        return original(self, xy, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", recording_text)
    category = "Collection écologique exceptionnelle " * 12
    description = "Please separate every reusable item before collection. " * 40
    render_bytes(category, description, "#abcdef", None)
    body = [(xy, text, face) for xy, text, face in calls
            if xy[1] >= 175 and text != "REMINDER"]
    assert len(body) > 2  # multiple lines rather than a single clipped string
    assert any("écologique" in text for _, text, _ in body)
    assert any("reusable" in text for _, text, _ in body)
    assert any(text.endswith("…") for _, text, _ in body)
    canvas = ImageDraw.Draw(Image.new("RGB", (1024, 600)))
    for (x, y), text, face in body:
        box = canvas.textbbox((x, y), text, font=face)
        assert box[2] <= 925
        assert box[3] <= 460
