"""Nonblocking, at-most-once multi-device reminder dispatch.

A claim reserves an attempt, not a guaranteed delivery. Failed sends are not retried.
"""
from __future__ import annotations

from datetime import datetime, timezone
import logging
import threading
from zoneinfo import ZoneInfo

from .config_model import Config, validate_config
from .recurrence import reminder_slots

LOG = logging.getLogger(__name__)


class Scheduler:
    """Call tick() periodically (preferably every few seconds), or run() in a thread.

    send(slot, collection, device) is called on a daemon worker. A target UUID
    cannot have two active workers in this Scheduler instance.
    """

    def __init__(self, config_provider, delivery_store, send, *, clock=None, grace_seconds=30,
                 poll_seconds=5):
        if not 0 < grace_seconds < 60 or poll_seconds <= 0:
            raise ValueError("invalid poll/grace interval")
        self.config_provider = config_provider
        self.delivery_store = delivery_store
        self.send = send
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.grace_seconds = grace_seconds
        self.poll_seconds = poll_seconds
        self.stop_event = threading.Event()
        self._lock = threading.Lock()
        self._busy = set()
        self._workers = []

    def tick(self) -> int:
        """Schedule only local-today slots 0 <= elapsed < grace; return worker count."""
        if self.stop_event.is_set():
            return 0
        with self._lock:
            self._workers = [worker for worker in self._workers if worker.is_alive()]
        try:
            config = self.config_provider()
            if not isinstance(config, Config):
                config = validate_config(config)
            now = self.clock()
            if now.tzinfo is None or now.utcoffset() is None:
                raise ValueError("clock must return timezone-aware datetime")
            day = now.astimezone(ZoneInfo(config.timezone)).date()
            slots = reminder_slots(config, day, day)
        except (ValueError, TypeError, KeyError, OSError):
            LOG.exception("Invalid schedule; no delivery")
            return 0
        collections = {collection.id: collection for collection in config.collections}
        devices = {device.uuid: device for device in config.devices}
        started = 0
        for slot in slots:
            elapsed = now.timestamp() - slot.when.timestamp()
            if not 0 <= elapsed < self.grace_seconds:
                continue
            for uuid in slot.device_uuids:
                with self._lock:
                    if self.stop_event.is_set() or uuid in self._busy:
                        continue
                    self._busy.add(uuid)
                try:
                    claimed = self.delivery_store.claim(slot.collection_date.isoformat(),
                        slot.reminder_id, slot.when.strftime("%Y-%m-%dT%H:%M"), uuid,
                        collection_id=slot.collection_id)
                    if claimed:
                        worker = threading.Thread(target=self._deliver,
                            args=(slot, collections[slot.collection_id], devices[uuid], uuid), daemon=True)
                        with self._lock:
                            self._workers.append(worker)
                            worker.start()
                        started += 1
                    else:
                        with self._lock:
                            self._busy.remove(uuid)
                except Exception:
                    with self._lock:
                        self._busy.discard(uuid)
                    LOG.exception("Could not reserve/dispatch reminder to %s", uuid)
        return started

    def _deliver(self, slot, collection, device, uuid):
        try:
            self.send(slot, collection, device)
        except Exception:
            LOG.exception("Reminder delivery failed for %s", uuid)
        finally:
            with self._lock:
                self._busy.discard(uuid)

    def run(self):
        """Blocking poll loop; stop() wakes it for graceful shutdown."""
        while not self.stop_event.is_set():
            self.tick()
            self.stop_event.wait(self.poll_seconds)
        self.join()

    def stop(self):
        self.stop_event.set()

    def join(self, timeout=None):
        with self._lock:
            workers = self._workers[:]
        for worker in workers:
            worker.join(timeout)
        with self._lock:
            self._workers = [worker for worker in self._workers if worker.is_alive()]
