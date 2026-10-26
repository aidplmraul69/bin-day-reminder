"""UUID-targeted Cast JPEG session with explicit receiver-session ownership."""
from __future__ import annotations

import logging
import threading
import time

LOG = logging.getLogger(__name__)
# Backdrop is the Cast idle screen; never take over another active application.
BACKDROP_APP_ID = "E8C28D3C"


class CastSession:
    """run(device, image_url, duration, stop_event=None) blocks until finished.

    Inject api (pychromecast-compatible), wait(seconds, stop_event), and monotonic
    for deterministic tests. Never uses name/first-device fallback.
    """

    def __init__(self, *, api=None, wait=None, monotonic=None, launch_timeout=10,
                 poll_seconds=0.5):
        if launch_timeout <= 0 or poll_seconds <= 0:
            raise ValueError("invalid Cast timeout")
        if api is None:
            import pychromecast
            api = pychromecast
        self.api = api
        self.wait = wait or (lambda seconds, event: event.wait(seconds))
        self.monotonic = monotonic or time.monotonic
        self.launch_timeout = launch_timeout
        self.poll_seconds = poll_seconds

    def run(self, device, image_url, duration, stop_event=None):
        """Display JPEG on exactly device.uuid; stop only our captured DMR session.

        Caller serves image_url over LAN and owns URL validation. A timed-out or
        unknown launch may leave an image visible, but cannot blindly stop a
        different app/session.
        """
        if not device.uuid or not image_url or duration <= 0:
            raise ValueError("device UUID, image URL and positive duration required")
        stop = stop_event or threading.Event()
        if stop.is_set():
            return
        kwargs = {"tries": 1, "retry_wait": 1, "timeout": 5}
        if device.known_host:
            kwargs["known_hosts"] = [device.known_host]
        casts, browser = self.api.get_chromecasts(**kwargs)
        cast = None
        owned_session = None
        receiver_id = self.api.config.APP_MEDIA_RECEIVER
        try:
            matches = [candidate for candidate in casts if str(candidate.uuid) == device.uuid]
            if len(matches) != 1:
                raise ValueError("target not found or UUID ambiguous")
            cast = matches[0]
            cast.wait(timeout=20)
            status = cast.status
            if status is None:
                raise RuntimeError("Cast status unknown; refusing to launch")
            if getattr(status, "app_id", None) not in (None, BACKDROP_APP_ID):
                raise RuntimeError("Cast target busy; refusing to interrupt app")
            if stop.is_set():
                return
            cast.media_controller.play_media(image_url, "image/jpeg", autoplay=True)
            deadline = self.monotonic() + self.launch_timeout
            while not stop.is_set():
                status = cast.status
                if status is not None and getattr(status, "app_id", None) == receiver_id:
                    owned_session = getattr(status, "session_id", None)
                    if owned_session:
                        break
                if self.monotonic() >= deadline:
                    raise TimeoutError("Cast receiver launch timed out without session ownership")
                self.wait(min(self.poll_seconds, max(0, deadline - self.monotonic())), stop)
            if not owned_session:
                return
            deadline = self.monotonic() + duration
            while not stop.is_set():
                status = cast.status
                if not self._owned(status, receiver_id, owned_session):
                    LOG.info("Cast session ownership lost; leaving receiver alone")
                    break
                remaining = deadline - self.monotonic()
                if remaining <= 0:
                    break
                self.wait(min(self.poll_seconds, remaining), stop)
        finally:
            if cast is not None:
                try:
                    if owned_session and self._owned(cast.status, receiver_id, owned_session):
                        cast.socket_client.receiver_controller.send_message(
                            {"type": "STOP", "sessionId": owned_session})
                except Exception:
                    LOG.exception("Could not stop owned Cast receiver")
                try:
                    cast.disconnect(timeout=2)
                except Exception:
                    LOG.exception("Could not disconnect Cast")
            try:
                browser.stop_discovery()
            except Exception:
                LOG.exception("Could not stop Cast discovery")

    @staticmethod
    def _owned(status, receiver_id, session_id):
        return (status is not None and getattr(status, "app_id", None) == receiver_id
                and getattr(status, "session_id", None) == session_id)
