"""Persistent portal configuration behaviors, without network or user data."""
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

import src.config_store as config_module

from src.config_store import ConfigStore, ConfigConflictError, ConfigInvalidError


def validate(config):
    if not isinstance(config, dict) or not isinstance(config.get("rules"), list):
        raise ValueError("invalid")
    return config


def test_create_and_load_with_revision(tmp_path):
    path = tmp_path / "state" / "portal.json"
    store = ConfigStore(path, validate)
    revision = store.save({"rules": []}, expected_revision=None)
    loaded, loaded_revision = store.load()
    assert loaded == {"rules": []}
    assert loaded_revision == revision
    assert json.loads(path.read_text()) == loaded


def test_stale_revision_rejected_without_overwriting(tmp_path):
    path = tmp_path / "portal.json"
    store = ConfigStore(path, validate)
    first = store.save({"rules": ["old"]}, expected_revision=None)
    second = store.save({"rules": ["new"]}, expected_revision=first)
    with pytest.raises(ConfigConflictError):
        store.save({"rules": ["lost"]}, expected_revision=first)
    assert store.load() == ({"rules": ["new"]}, second)


def test_malformed_existing_config_is_preserved_without_exposing_data(tmp_path):
    path = tmp_path / "portal.json"
    secret = b'{"rules":["PRIVATE_SECRET"], invalid}'
    path.write_bytes(secret)
    store = ConfigStore(path, validate)
    with pytest.raises(ConfigInvalidError) as err:
        store.load()
    assert "PRIVATE_SECRET" not in str(err.value)
    with pytest.raises(ConfigInvalidError):
        store.save({"rules": []}, expected_revision=None)
    assert path.read_bytes() == secret


def test_replace_failure_preserves_old_config_and_removes_temp_file(tmp_path, monkeypatch):
    path = tmp_path / "portal.json"
    store = ConfigStore(path, validate)
    revision = store.save({"rules": ["before"]}, expected_revision=None)
    old_bytes = path.read_bytes()

    def fail_replace(source, destination):
        assert path.read_bytes() == old_bytes
        raise OSError("simulated rename failure")

    monkeypatch.setattr(config_module.os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated rename failure"):
        store.save({"rules": ["after"]}, expected_revision=revision)
    assert path.read_bytes() == old_bytes
    assert sorted(p.name for p in tmp_path.iterdir()) == ["portal.json", "portal.json.lock"]


def test_concurrent_stale_writers_cannot_both_commit(tmp_path):
    store = ConfigStore(tmp_path / "portal.json", validate)
    revision = store.save({"rules": []}, expected_revision=None)

    def write(value):
        try:
            store.save({"rules": [value]}, expected_revision=revision)
            return True
        except ConfigConflictError:
            return False

    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(write, range(24)))
    assert results.count(True) == 1


def test_validator_exception_never_leaks_config_values(tmp_path):
    def rejecting_validator(data):
        raise RuntimeError(f"bad contents: {data}")

    store = ConfigStore(tmp_path / "portal.json", rejecting_validator)
    with pytest.raises(ConfigInvalidError) as err:
        store.save({"rules": ["PRIVATE_SECRET"]}, expected_revision=None)
    assert "PRIVATE_SECRET" not in str(err.value)
    assert not store.path.exists()
