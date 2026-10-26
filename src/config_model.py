"""Strict, pure v2 portal configuration validation."""

from dataclasses import dataclass
from datetime import date
import json
import re
from typing import Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

MAX_CONFIG_BYTES = 1024 * 1024  # Compact UTF-8 JSON; independent of request whitespace.

@dataclass(frozen=True)
class Device:
    uuid: str
    label: str
    known_host: str | None = None


@dataclass(frozen=True)
class Recurrence:
    kind: str
    weekday: int | None = None  # Monday=0
    anchor_date: date | None = None
    day_of_month: int | None = None


@dataclass(frozen=True)
class Reminder:
    id: str
    lead_days: int
    start_time: str
    repeat_for_minutes: int
    every_minutes: int
    display_seconds: int
    device_uuids: tuple[str, ...]


@dataclass(frozen=True)
class Collection:
    id: str
    label: str
    category: str
    text: str
    color: str
    recurrence: Recurrence
    reminders: tuple[Reminder, ...]


@dataclass(frozen=True)
class Config:
    version: int
    timezone: str
    devices: tuple[Device, ...]
    collections: tuple[Collection, ...]


def _object(value, required, optional=()):
    if not isinstance(value, Mapping) or set(value) - (set(required) | set(optional)) or set(required) - set(value):
        raise ValueError(f"expected fields {sorted(required)}; optional {sorted(optional)}")
    return value


def _string(value, name, maximum=100):
    if not isinstance(value, str) or not 1 <= len(value) <= maximum or value != value.strip() or any(ord(c) < 32 for c in value):
        raise ValueError(f"invalid {name}")
    return value


def _int(value, name, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"invalid {name}")
    return value


def _array(value, name, maximum):
    if not isinstance(value, list) or len(value) > maximum:
        raise ValueError(f"invalid {name}")
    return value


def _unique(values, name):
    if len(values) != len(set(values)):
        raise ValueError(f"duplicate {name}")


def _recurrence(raw):
    if not isinstance(raw, Mapping):
        raise ValueError("invalid recurrence")
    kind = raw.get("kind")
    if kind == "weekly":
        _object(raw, {"kind", "weekday"})
        return Recurrence(kind, weekday=_int(raw["weekday"], "weekday", 0, 6))
    if kind == "biweekly":
        _object(raw, {"kind", "weekday", "anchor_date"})
        weekday = _int(raw["weekday"], "weekday", 0, 6)
        anchor = raw["anchor_date"]
        if not isinstance(anchor, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", anchor):
            raise ValueError("invalid anchor_date")
        try:
            anchor_day = date.fromisoformat(anchor)
        except ValueError as exc:
            raise ValueError("invalid anchor_date") from exc
        if anchor_day.weekday() != weekday:
            raise ValueError("anchor_date must be collection weekday")
        return Recurrence(kind, weekday=weekday, anchor_date=anchor_day)
    if kind == "monthly":
        _object(raw, {"kind", "day_of_month"})
        return Recurrence(kind, day_of_month=_int(raw["day_of_month"], "day_of_month", 1, 31))
    raise ValueError("invalid recurrence kind")


def validate_config(raw: Mapping) -> Config:
    """Validate untrusted JSON-like data and return immutable typed configuration."""
    _object(raw, {"version", "timezone", "devices", "collections"})
    if type(raw["version"]) is not int or raw["version"] != 2:
        raise ValueError("version must be 2")
    timezone = _string(raw["timezone"], "timezone")
    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError, KeyError) as exc:
        raise ValueError("unknown IANA timezone") from exc
    devices = []
    for item in _array(raw["devices"], "devices", 64):
        _object(item, {"uuid", "label"}, {"known_host"})
        host = _string(item["known_host"], "known_host", 255) if "known_host" in item else None
        devices.append(Device(_string(item["uuid"], "uuid", 128), _string(item["label"], "label"), host))
    known = [device.uuid for device in devices]
    _unique(known, "device uuid")
    collections = []
    for item in _array(raw["collections"], "collections", 365):
        _object(item, {"id", "label", "category", "text", "color", "recurrence", "reminders"})
        color = item["color"]
        if not isinstance(color, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
            raise ValueError("color must be #RRGGBB")
        reminders = []
        for entry in _array(item["reminders"], "reminders", 32):
            _object(entry, {"id", "lead_days", "start_time", "repeat_for_minutes", "every_minutes", "display_seconds", "device_uuids"})
            start = entry["start_time"]
            if not isinstance(start, str) or not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", start):
                raise ValueError("start_time must be HH:MM")
            duration = _int(entry["repeat_for_minutes"], "repeat_for_minutes", 1, 1440)
            every = _int(entry["every_minutes"], "every_minutes", 1, 1440)
            display = _int(entry["display_seconds"], "display_seconds", 1, 86400)
            minute = int(start[:2]) * 60 + int(start[3:])
            # A window is half-open; its final slot must still fit before midnight.
            last_slot_minute = minute + ((duration - 1) // every) * every
            if (minute + duration > 1440 or last_slot_minute * 60 + display > 86400
                    or (duration > every and display > every * 60)):
                raise ValueError("repeating slots cross midnight or overlap")
            selected = tuple(_string(uuid, "device uuid", 128) for uuid in _array(entry["device_uuids"], "device_uuids", 64))
            if not selected or not set(selected).issubset(known):
                raise ValueError("select at least one known device")
            _unique(selected, "selected device uuid")
            reminders.append(Reminder(_string(entry["id"], "reminder id"), _int(entry["lead_days"], "lead_days", 0, 366), start, duration, every, display, selected))
        _unique([r.id for r in reminders], "reminder id")
        collections.append(Collection(_string(item["id"], "collection id"), _string(item["label"], "label"),
                                      _string(item["category"], "category"), _string(item["text"], "text", 2000),
                                      color, _recurrence(item["recurrence"]), tuple(reminders)))
    _unique([c.id for c in collections], "collection id")
    try:
        size = len(json.dumps(raw, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8"))
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ValueError("invalid configuration JSON") from exc
    if size > MAX_CONFIG_BYTES:
        raise ValueError("configuration too large")
    return Config(2, timezone, tuple(devices), tuple(collections))
