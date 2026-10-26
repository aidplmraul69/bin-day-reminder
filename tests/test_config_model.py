from copy import deepcopy

import pytest

from src.config_model import validate_config


@pytest.fixture
def raw():
    return {
        "version": 2,
        "timezone": "Europe/Bucharest",
        "devices": [{"uuid": "speaker-a", "label": "Kitchen", "known_host": "cast.local"},
                    {"uuid": "speaker-b", "label": "Hall"}],
        "collections": [{
            "id": "paper", "label": "Paper", "category": "recycling", "text": "Set out paper",
            "color": "#1122aa", "recurrence": {"kind": "weekly", "weekday": 2},
            "reminders": [{"id": "eve", "lead_days": 1, "start_time": "18:30",
                           "repeat_for_minutes": 60, "every_minutes": 30,
                           "display_seconds": 12, "device_uuids": ["speaker-a", "speaker-b"]}],
        }],
    }


def test_valid_config_is_typed_and_preserves_order(raw):
    cfg = validate_config(raw)
    assert cfg.version == 2
    assert cfg.timezone == "Europe/Bucharest"
    assert cfg.collections[0].reminders[0].device_uuids == ("speaker-a", "speaker-b")
    assert cfg.collections[0].recurrence.weekday == 2


def test_empty_collections_allowed(raw):
    raw["collections"] = []
    assert validate_config(raw).collections == ()


@pytest.mark.parametrize("path,value", [
    (("version",), 1), (("version",), True), (("timezone",), "Mars/Phobos"),
    (("devices", 0, "uuid"), ""), (("devices", 0, "label"), "x" * 101),
    (("collections", 0, "color"), "blue"),
    (("collections", 0, "recurrence", "weekday"), 7),
    (("collections", 0, "reminders", 0, "start_time"), "9:30"),
    (("collections", 0, "reminders", 0, "lead_days"), -1),
    (("collections", 0, "reminders", 0, "display_seconds"), 0),
    (("collections", 0, "reminders", 0, "device_uuids"), []),
])
def test_invalid_field_rejected(raw, path, value):
    obj = raw
    for key in path[:-1]:
        obj = obj[key]
    obj[path[-1]] = value
    with pytest.raises(ValueError):
        validate_config(raw)


def test_unknown_fields_at_every_level_rejected(raw):
    paths = [(), ("devices", 0), ("collections", 0),
             ("collections", 0, "recurrence"), ("collections", 0, "reminders", 0)]
    for path in paths:
        changed = deepcopy(raw)
        obj = changed
        for key in path:
            obj = obj[key]
        obj["extra"] = 1
        with pytest.raises(ValueError):
            validate_config(changed)


def test_duplicate_ids_and_uuid_references_rejected(raw):
    mutations = [
        lambda r: r["devices"].append(deepcopy(r["devices"][0])),
        lambda r: r["collections"].append(deepcopy(r["collections"][0])),
        lambda r: r["collections"][0]["reminders"].append(deepcopy(r["collections"][0]["reminders"][0])),
        lambda r: r["collections"][0]["reminders"][0]["device_uuids"].append("speaker-a"),
        lambda r: r["collections"][0]["reminders"][0]["device_uuids"].append("unknown"),
    ]
    for mutate in mutations:
        changed = deepcopy(raw)
        mutate(changed)
        with pytest.raises(ValueError):
            validate_config(changed)


@pytest.mark.parametrize("recurrence", [
    {"kind": "biweekly", "weekday": 0},
    {"kind": "biweekly", "weekday": 0, "anchor_date": "2025-02-30"},
    {"kind": "biweekly", "weekday": 0, "anchor_date": "2025-01-07"},
    {"kind": "monthly", "day_of_month": 0},
    {"kind": "monthly", "day_of_month": 32},
    {"kind": "monthly", "weekday": 0, "day_of_month": 10},
])
def test_invalid_recurrence_rejected(raw, recurrence):
    raw["collections"][0]["recurrence"] = recurrence
    with pytest.raises(ValueError):
        validate_config(raw)


def test_explicit_null_known_host_is_rejected(raw):
    raw["devices"][0]["known_host"] = None
    with pytest.raises(ValueError):
        validate_config(raw)


def test_valid_biweekly_and_monthly(raw):
    raw["collections"][0]["recurrence"] = {"kind": "biweekly", "weekday": 0, "anchor_date": "2025-01-06"}
    assert validate_config(raw).collections[0].recurrence.anchor_date.isoformat() == "2025-01-06"
    raw["collections"][0]["recurrence"] = {"kind": "monthly", "day_of_month": 31}
    assert validate_config(raw).collections[0].recurrence.day_of_month == 31


@pytest.mark.parametrize("updates", [
    {"start_time": "23:45", "repeat_for_minutes": 30},
    {"display_seconds": 1801},
    {"every_minutes": 0},
    {"repeat_for_minutes": 0},
    {"start_time": "23:59", "repeat_for_minutes": 1, "display_seconds": 120},
])
def test_invalid_slot_bounds_rejected(raw, updates):
    raw["collections"][0]["reminders"][0].update(updates)
    with pytest.raises(ValueError):
        validate_config(raw)
