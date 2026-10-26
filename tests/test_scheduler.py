from datetime import datetime, timezone
from threading import Event
from zoneinfo import ZoneInfo

from src.config_model import validate_config
from src.delivery_store import DeliveryStore
from src.scheduler import Scheduler


def config(start="12:00", repeat=2, every=1, devices=("a", "b"), timezone_name="UTC"):
    return validate_config({"version": 2, "timezone": timezone_name,
        "devices": [{"uuid": uuid, "label": uuid} for uuid in devices],
        "collections": [{"id": "c", "label": "C", "category": "Bin", "text": "Out", "color": "#112233",
            "recurrence": {"kind": "weekly", "weekday": 0},
            "reminders": [{"id": "r", "lead_days": 0, "start_time": start,
                "repeat_for_minutes": repeat, "every_minutes": every,
                "display_seconds": 10, "device_uuids": list(devices)}]}]})


def test_due_repeats_send_to_each_device_once_even_after_restart(tmp_path):
    now = datetime(2026, 10, 26, 12, 0, 5, tzinfo=timezone.utc)
    sent = []
    store = DeliveryStore(tmp_path / "claims.db")
    def send(slot, collection, device):
        sent.append((slot.when.minute, collection.id, device.uuid))
    scheduler = Scheduler(lambda: config(), store, send, clock=lambda: now)
    scheduler.tick()
    scheduler.join()
    Scheduler(lambda: config(), DeliveryStore(tmp_path / "claims.db"), send, clock=lambda: now).tick()
    assert sorted(sent) == [(0, "c", "a"), (0, "c", "b")]
    now = datetime(2026, 10, 26, 12, 1, 5, tzinfo=timezone.utc)
    scheduler.tick()
    scheduler.join()
    assert sorted(sent) == [(0, "c", "a"), (0, "c", "b"), (1, "c", "a"), (1, "c", "b")]


def test_same_reminder_id_in_two_collections_delivers_both(tmp_path):
    from dataclasses import replace
    base = config(devices=("a",))
    other = replace(base.collections[0], id="other")
    cfg = replace(base, collections=(*base.collections, other))
    sent = []
    scheduler = Scheduler(lambda: cfg, DeliveryStore(tmp_path / "claims.db"),
                          lambda slot, collection, device: sent.append(collection.id),
                          clock=lambda: datetime(2026, 10, 26, 12, tzinfo=timezone.utc))
    scheduler.tick()
    scheduler.join()
    scheduler.tick()  # allow a serialized second session on the same target
    scheduler.join()
    assert sorted(sent) == ["c", "other"]

def test_completed_workers_are_reaped_in_long_running_scheduler(tmp_path):
    scheduler = Scheduler(lambda: config(devices=("a",)), DeliveryStore(tmp_path / "claims.db"),
                          lambda *args: None,
                          clock=lambda: datetime(2026, 10, 26, 12, tzinfo=timezone.utc))
    scheduler.tick()
    scheduler._workers[0].join(timeout=2)
    scheduler.tick()
    assert scheduler._workers == []


def test_stale_and_early_slots_are_never_backfilled(tmp_path):
    moment = [datetime(2026, 10, 26, 12, 0, 31, tzinfo=timezone.utc)]
    sent = []
    scheduler = Scheduler(lambda: config(), DeliveryStore(tmp_path / "claims.db"),
                          lambda slot, collection, device: sent.append(device.uuid), clock=lambda: moment[0])
    scheduler.tick()
    moment[0] = datetime(2026, 10, 26, 12, 1, 0, tzinfo=timezone.utc)
    scheduler.tick()
    scheduler.join()
    assert sent == ["a", "b"]


def test_device_failure_isolated_and_busy_target_not_interrupted(tmp_path):
    entered, release = Event(), Event()
    sent = []
    def send(slot, collection, device):
        sent.append((slot.when.minute, device.uuid))
        if device.uuid == "a":
            entered.set()
            release.wait(2)
            raise RuntimeError("offline")
    now = [datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc)]
    scheduler = Scheduler(lambda: config(), DeliveryStore(tmp_path / "claims.db"), send, clock=lambda: now[0])
    try:
        scheduler.tick()
        assert entered.wait(2)
        # Ensure b finished before its next slot; a remains busy.
        for worker in scheduler._workers:
            if worker is not scheduler._workers[0]:
                worker.join(timeout=2)
        now[0] = datetime(2026, 10, 26, 12, 1, tzinfo=timezone.utc)
        scheduler.tick()
    finally:
        release.set()
        scheduler.join()
    assert (0, "b") in sent and (1, "b") in sent
    assert (1, "a") not in sent


def test_invalid_config_never_casts(tmp_path):
    sent = []
    scheduler = Scheduler(lambda: {"version": 999}, DeliveryStore(tmp_path / "claims.db"),
                          lambda *args: sent.append(args),
                          clock=lambda: datetime(2026, 10, 26, 12, tzinfo=timezone.utc))
    assert scheduler.tick() == 0
    assert not sent


def test_dst_spring_gap_omitted_and_fall_fold_only_once(tmp_path):
    sent = []
    moment = [datetime(2026, 3, 29, 1, 30, tzinfo=timezone.utc)]
    cfg = config(start="02:30", repeat=1, devices=("a",), timezone_name="Europe/Berlin")
    # Sunday's collection, rather than Monday's.
    from dataclasses import replace
    from src.config_model import Recurrence
    cfg = replace(cfg, collections=(replace(cfg.collections[0], recurrence=Recurrence("weekly", weekday=6)),))
    scheduler = Scheduler(lambda: cfg, DeliveryStore(tmp_path / "claims.db"),
                          lambda slot, collection, device: sent.append(slot.when.fold), clock=lambda: moment[0])
    scheduler.tick()
    moment[0] = datetime(2026, 10, 25, 0, 30, tzinfo=timezone.utc)
    scheduler.tick()
    scheduler.join()
    moment[0] = datetime(2026, 10, 25, 1, 30, tzinfo=timezone.utc)
    scheduler.tick()
    scheduler.join()
    assert sent == [0]
