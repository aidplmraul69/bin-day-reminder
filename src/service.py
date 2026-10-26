"""Long-running portal, durable scheduler, and Cast-accessible ephemeral media."""
from __future__ import annotations

import argparse
import ipaddress
import logging
from pathlib import Path
import signal
import threading
from urllib.parse import urlsplit

from .admin_app import create_server
from .cast_session import CastSession
from .config_model import validate_config
from .config_store import ConfigStore
from .delivery_store import DeliveryStore
from .media_server import MediaServer
from .render_reminder import render_bytes
from .scheduler import Scheduler

LOG = logging.getLogger(__name__)


def validate_advertise_host(host: str, allow_loopback: bool = False) -> str:
    if not host or host.lower() == "localhost" or any(c in host for c in "/?#@"):
        if not (allow_loopback and host == "localhost"):
            raise ValueError("advertise host must be a LAN-reachable host, not localhost")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if not host or not all(label and label.replace("-", "").isalnum() for label in host.split(".")):
            raise ValueError("invalid advertise host") from None
    else:
        if (address.is_loopback and not allow_loopback) or address.is_unspecified or address.is_multicast:
            raise ValueError("advertise host must be LAN-reachable")
    return host


def discover_devices():
    """Return candidates only; discovery never modifies configuration."""
    import pychromecast
    casts, browser = pychromecast.get_chromecasts(tries=1, retry_wait=1, timeout=5)
    try:
        return [{"uuid": str(cast.uuid), "label": str(cast.name),
                 **({"known_host": cast.cast_info.host} if getattr(getattr(cast, "cast_info", None), "host", None) else {})}
                for cast in casts]
    finally:
        browser.stop_discovery()


class ReminderService:
    def __init__(self, data_dir: str | Path, *, advertise_host: str, media_host="0.0.0.0",
                 media_port=8080, admin_port=8765, cast_session=None, clock=None,
                 discover=discover_devices, allow_loopback_advertise=False):
        self.advertise_host = validate_advertise_host(advertise_host, allow_loopback_advertise)
        self.store = ConfigStore(Path(data_dir) / "config.json", validate_config)
        self.deliveries = DeliveryStore(Path(data_dir) / "claims.sqlite3")
        self.media = MediaServer(host=media_host, port=media_port)
        self.admin = create_server(self.store, port=admin_port, discover=discover)
        self.admin_port = self.admin.server_port
        self.cast = cast_session or CastSession()
        self.scheduler = Scheduler(self._config, self.deliveries, self._send, clock=clock)
        self._admin_thread = None
        self._scheduler_thread = None

    def _config(self):
        try:
            raw, _ = self.store.load()
        except FileNotFoundError:
            return {"version": 2, "timezone": "Etc/UTC", "devices": [], "collections": []}
        return raw

    def _send(self, slot, collection, device):
        image = render_bytes(collection.category, collection.text, collection.color, slot.when)
        path = self.media.publish(image)
        host = self.advertise_host
        if ":" in host:  # IPv6 URL literal
            host = f"[{host}]"
        url = f"http://{host}:{self.media.port}{path}"
        try:
            self.cast.run(device, url, slot.display_seconds, self.scheduler.stop_event)
        finally:
            self.media.remove(path)

    def __enter__(self):
        self.media.__enter__()
        self._admin_thread = threading.Thread(target=self.admin.serve_forever, daemon=True)
        self._scheduler_thread = threading.Thread(target=self.scheduler.run, daemon=True)
        self._admin_thread.start()
        self._scheduler_thread.start()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.scheduler.stop()
        self._scheduler_thread.join()
        self.admin.shutdown()
        self._admin_thread.join()
        self.admin.server_close()
        self.media.__exit__(exc_type, exc, tb)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("/data"))
    parser.add_argument("--advertise-host", required=True, help="LAN address or DNS name reachable by Cast devices")
    parser.add_argument("--media-host", default="0.0.0.0")
    parser.add_argument("--media-port", type=int, default=8080)
    parser.add_argument("--admin-port", type=int, default=8765)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    stopping = threading.Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: stopping.set())
    with ReminderService(args.data_dir, advertise_host=args.advertise_host,
                         media_host=args.media_host, media_port=args.media_port,
                         admin_port=args.admin_port) as service:
        LOG.info("Portal on 127.0.0.1:%s; media on %s:%s", service.admin_port, args.media_host, service.media.port)
        stopping.wait()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
