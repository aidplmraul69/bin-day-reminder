"""Durable delivery-claim tests using isolated SQLite databases."""
from concurrent.futures import ThreadPoolExecutor

import pytest

from src.delivery_store import DeliveryStore


def test_claim_survives_reopening_database(tmp_path):
    path = tmp_path / "state" / "delivery.sqlite3"
    store = DeliveryStore(path)
    assert store.claim("2026-10-27", "reminder-1", "18:00", "device-uuid") is True
    assert DeliveryStore(path).claim("2026-10-27", "reminder-1", "18:00", "device-uuid") is False

def test_same_reminder_id_in_different_collections_has_distinct_claims(tmp_path):
    store = DeliveryStore(tmp_path / "claims.db")
    assert store.claim("2026-10-27", "r", "18:00", "device", collection_id="first")
    assert store.claim("2026-10-27", "r", "18:00", "device", collection_id="second")
    assert not DeliveryStore(tmp_path / "claims.db").claim("2026-10-27", "r", "18:00", "device", collection_id="first")


def test_only_one_concurrent_claim_wins_and_other_keys_are_distinct(tmp_path):
    path = tmp_path / "delivery.sqlite3"
    store = DeliveryStore(path)
    with ThreadPoolExecutor(max_workers=16) as pool:
        outcomes = list(pool.map(lambda _: store.claim("2026-10-27", "r1", "18:00", "device-a"), range(64)))
    assert outcomes.count(True) == 1
    assert outcomes.count(False) == 63
    assert store.claim("2026-10-28", "r1", "18:00", "device-a")
    assert store.claim("2026-10-27", "r2", "18:00", "device-a")
    assert store.claim("2026-10-27", "r1", "19:00", "device-a")
    assert store.claim("2026-10-27", "r1", "18:00", "device-b")


def test_claim_rejects_empty_or_null_key_components(tmp_path):
    store = DeliveryStore(tmp_path / "delivery.sqlite3")
    for value in (None, "", "  "):
        with pytest.raises(ValueError):
            store.claim("2026-10-27", value, "18:00", "device-a")
