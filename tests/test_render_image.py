import sys
from datetime import datetime
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from render_reminder import render


def test_renders_configurable_jpeg(tmp_path):
    target = tmp_path / "reminder.jpg"
    render("Glass", "Take out glass", "#75cf9a", target,
           now=datetime.fromisoformat("2026-10-21T18:00:00+00:00"))
    with Image.open(target) as image:
        assert image.format == "JPEG"
        assert image.size == (1024, 600)


def test_rejects_invalid_color(tmp_path):
    import pytest
    with pytest.raises(ValueError, match="color"):
        render("Glass", "Take out glass", "invalid", tmp_path / "reminder.jpg")
