"""Integration tests for the assembled service; no Cast devices contacted."""
from datetime import date, datetime, timezone
import http.client
import json
import threading
from types import SimpleNamespace
from urllib.request import urlopen

import pytest

import src.service as service_module
from src.service import ReminderService


def test_service_http_save_reload_schedule_image_and_shutdown(tmp_path):
    seen = []

    class FakeCast:
        def run(self, device, url, duration, stop_event):
            with urlopen(url, timeout=2) as response:
                seen.append((device.uuid, duration, response.headers.get_content_type(), response.read()))

    service = ReminderService(tmp_path, advertise_host="127.0.0.1", media_host="127.0.0.1",
                              media_port=0, admin_port=0, cast_session=FakeCast(),
                              clock=lambda: datetime(2026, 10, 26, 12, tzinfo=timezone.utc),
                              allow_loopback_advertise=True)
    with service:
        conn = http.client.HTTPConnection("127.0.0.1", service.admin_port, timeout=3)
        conn.request("GET", "/api/csrf")
        csrf = json.loads(conn.getresponse().read())["csrf_token"]
        conn.request("GET", "/api/config")
        assert json.loads(conn.getresponse().read())["revision"] is None
        config = {"version": 2, "timezone": "Etc/UTC", "devices": [{"uuid": "fake-device", "label": "Demo"}],
                  "collections": [{"id": "paper", "label": "Paper", "category": "Paper", "text": "Take out paper",
                                   "color": "#336699", "recurrence": {"kind": "weekly", "weekday": 0},
                                   "reminders": [{"id": "notice", "lead_days": 0, "start_time": "12:00",
                                                  "repeat_for_minutes": 1, "every_minutes": 1, "display_seconds": 2,
                                                  "device_uuids": ["fake-device"]}]}]}
        payload = json.dumps({"config": config, "revision": None})
        conn.request("PUT", "/api/config", payload, {"Content-Type": "application/json",
                     "Origin": f"http://127.0.0.1:{service.admin_port}", "X-CSRF-Token": csrf})
        response = conn.getresponse()
        assert response.status == 200, response.read()
        response.read()
        conn.request("GET", "/api/config")
        assert json.loads(conn.getresponse().read())["config"] == config
        assert service.scheduler.tick() == 1
        service.scheduler.join()
        assert len(seen) == 1
        assert seen[0][:3] == ("fake-device", 2, "image/jpeg")
        assert seen[0][3].startswith(b"\xff\xd8")
        assert service.scheduler.tick() == 0
        conn.close()
    assert service.scheduler.stop_event.is_set()
    assert service.store.load()[0] == config


def test_send_passes_pickup_date_to_renderer(monkeypatch):
    sent = []
    monkeypatch.setattr(service_module, "render_bytes", lambda *args, **kwargs: sent.append((args, kwargs)) or b"jpeg")
    service = object.__new__(ReminderService)
    service.advertise_host = "example.test"
    service.media = SimpleNamespace(port=8080, publish=lambda data: "/card.jpg", remove=lambda path: None)
    service.cast = SimpleNamespace(run=lambda *args: None)
    service.scheduler = SimpleNamespace(stop_event=threading.Event())
    when = datetime(2026, 10, 26, 23, tzinfo=timezone.utc)
    slot = SimpleNamespace(when=when, collection_date=date(2026, 10, 27), display_seconds=10)
    collection = SimpleNamespace(category="Paper", text="Take out paper", color="#3377DD")
    service._send(slot, collection, SimpleNamespace(uuid="demo"))
    assert sent == [(("Paper", "Take out paper", "#3377DD", when, date(2026, 10, 27)), {})]


def test_advertised_host_must_not_be_loopback_or_unspecified(tmp_path):
    for host in ("localhost", "127.0.0.1", "0.0.0.0", "::1"):
        with pytest.raises(ValueError):
            ReminderService(tmp_path, advertise_host=host)
