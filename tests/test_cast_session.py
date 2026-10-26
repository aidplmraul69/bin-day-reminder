from threading import Event
from types import SimpleNamespace

import pytest

from src.cast_session import CastSession
from src.config_model import Device


class Status:
    def __init__(self, app_id=None, session_id=None):
        self.app_id = app_id
        self.session_id = session_id


class FakeCast:
    def __init__(self, initial=Status(), after=None):
        self.status = initial
        self.after = after
        self.calls = []
        self.media_controller = SimpleNamespace(play_media=self.play)
        self.socket_client = SimpleNamespace(receiver_controller=SimpleNamespace(send_message=self.stop))
        self.uuid = "target"

    def wait(self, timeout):
        self.calls.append("wait")

    def play(self, url, mime, **kwargs):
        self.calls.append((url, mime))
        self.status = self.after

    def stop(self, message):
        self.calls.append(message)

    def disconnect(self, timeout):
        self.calls.append("disconnect")


class FakeBrowser:
    def __init__(self):
        self.stopped = False

    def stop_discovery(self):
        self.stopped = True


def adapter(casts, *, wait=None):
    browser = FakeBrowser()
    discovery = []
    def get_chromecasts(**kwargs):
        discovery.append(kwargs)
        return casts, browser
    api = SimpleNamespace(get_chromecasts=get_chromecasts,
                          config=SimpleNamespace(APP_MEDIA_RECEIVER="DMR"))
    def finish(seconds, stop):
        stop.set()
        return True
    session = CastSession(api=api, wait=wait or finish, launch_timeout=0.01)
    return session, browser, discovery


def test_targets_uuid_and_stops_only_launched_session():
    wrong = FakeCast()
    wrong.uuid = "wrong"
    target = FakeCast(after=Status("DMR", "ours"))
    session, browser, discovery = adapter([wrong, target])
    session.run(Device("target", "TV", "192.0.2.5"), "http://lan/reminder.jpg", 10)
    assert discovery == [{"tries": 1, "retry_wait": 1, "timeout": 5, "known_hosts": ["192.0.2.5"]}]
    assert target.calls[1] == ("http://lan/reminder.jpg", "image/jpeg")
    assert {"type": "STOP", "sessionId": "ours"} in target.calls
    assert wrong.calls == []
    assert browser.stopped and target.calls[-1] == "disconnect"


def test_missing_uuid_never_falls_back_and_discovery_is_closed():
    other = FakeCast()
    other.uuid = "other"
    session, browser, _ = adapter([other])
    with pytest.raises(ValueError, match="target not found"):
        session.run(Device("target", "TV"), "http://lan/reminder.jpg", 10)
    assert not other.calls and browser.stopped


def test_busy_app_not_interrupted():
    cast = FakeCast(Status("YouTube", "their-session"))
    session, browser, _ = adapter([cast])
    with pytest.raises(RuntimeError, match="busy"):
        session.run(Device("target", "TV"), "http://lan/reminder.jpg", 10)
    assert cast.calls == ["wait", "disconnect"]
    assert browser.stopped


def test_unknown_status_or_launch_timeout_never_blind_stops():
    for after in (None, Status("DMR", None)):
        cast = FakeCast(after=after)
        session, browser, _ = adapter([cast], wait=lambda seconds, stop: False)
        with pytest.raises(TimeoutError):
            session.run(Device("target", "TV"), "http://lan/reminder.jpg", 10)
        assert not any(isinstance(call, dict) for call in cast.calls)
        assert browser.stopped and cast.calls[-1] == "disconnect"


def test_takeover_does_not_stop_replacement_session():
    cast = FakeCast(after=Status("DMR", "ours"))
    def takeover(seconds, stop):
        cast.status = Status("DMR", "theirs")
        return False
    session, browser, _ = adapter([cast], wait=takeover)
    session.run(Device("target", "TV"), "http://lan/reminder.jpg", 10)
    assert not any(isinstance(call, dict) for call in cast.calls)
    assert browser.stopped


def test_stop_event_cleans_owned_session():
    cast = FakeCast(after=Status("DMR", "ours"))
    stop = Event()
    stop.set()
    session, browser, _ = adapter([cast])
    session.run(Device("target", "TV"), "http://lan/reminder.jpg", 10, stop_event=stop)
    assert not any(isinstance(call, tuple) for call in cast.calls)
    assert not browser.stopped
