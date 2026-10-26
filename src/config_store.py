"""Atomic, revision-checked JSON persistence for portal configuration.

The injected validator accepts a decoded mapping and raises on invalid data. Its
return value is not used, so validation cannot silently mutate persisted input.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Callable


class ConfigConflictError(Exception):
    """The expected revision does not match the file currently on disk."""


class ConfigInvalidError(ValueError):
    """The stored or submitted JSON is invalid; raw values are never included."""


class ConfigStore:
    def __init__(self, path: str | Path, validator: Callable[[dict], object]):
        self.path = Path(path)
        self.validator = validator

    def load(self) -> tuple[dict, str]:
        try:
            raw = self.path.read_bytes()
        except FileNotFoundError:
            raise
        return self._decode(raw), hashlib.sha256(raw).hexdigest()

    def _decode(self, raw: bytes) -> dict:
        try:
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("not an object")
            self.validator(data)
        except Exception:
            raise ConfigInvalidError("invalid configuration") from None
        return data

    def save(self, config: dict, expected_revision: str | None) -> str:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock():
            try:
                _, current = self.load()
            except FileNotFoundError:
                current = None
            if current != expected_revision:
                raise ConfigConflictError("configuration revision changed")
            try:
                self.validator(config)
                raw = (json.dumps(config, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")
            except Exception:
                raise ConfigInvalidError("invalid configuration") from None
            fd, name = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=self.path.parent)
            try:
                with os.fdopen(fd, "wb") as out:
                    out.write(raw)
                    out.flush()
                    os.fsync(out.fileno())
                os.replace(name, self.path)
                dir_fd = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
            finally:
                if os.path.exists(name):
                    os.unlink(name)
            return hashlib.sha256(raw).hexdigest()

    @contextmanager
    def _lock(self):
        fd = os.open(self.path.with_name(self.path.name + ".lock"), os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
