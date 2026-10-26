from datetime import date

import pytest

from src.config_model import validate_config
from src.recurrence import collection_occurrences, reminder_slots


def config(kind="weekly", weekday=0, lead=0, start="09:00", duration=1, every=30):
    recurrence = {"kind": kind, "weekday": weekday}
    if kind == "biweekly":
        recurrence["anchor_date"] = "2025-12-29"
    if kind == "monthly":
        recurrence = {"kind": "monthly", "day_of_month": 31}
    return validate_config({
        "version": 2, "timezone": "Europe/Bucharest",
        "devices": [{"uuid": "a", "label": "Test"}],
        "collections": [{"id": "c", "label": "Collection", "category": "recycle",
                         "text": "Ready", "color": "#abcdef", "recurrence": recurrence,
                         "reminders": [{"id": "r", "lead_days": lead, "start_time": start,
                                        "repeat_for_minutes": duration, "every_minutes": every,
                                        "display_seconds": 10, "device_uuids": ["a"]}]}],
    })


def test_weekly_occurrences_inclusive_and_ordered():
    cfg = config()
    assert collection_occurrences(cfg.collections[0], date(2026, 1, 1), date(2026, 1, 19)) == (
        date(2026, 1, 5), date(2026, 1, 12), date(2026, 1, 19))


def test_biweekly_parity_across_year_in_both_directions():
    cfg = config("biweekly")
    assert collection_occurrences(cfg.collections[0], date(2025, 12, 15), date(2026, 1, 26)) == (
        date(2025, 12, 15), date(2025, 12, 29), date(2026, 1, 12), date(2026, 1, 26))


def test_monthly_31_skips_short_months_and_leap_year():
    cfg = config("monthly")
    assert collection_occurrences(cfg.collections[0], date(2024, 1, 1), date(2024, 5, 31)) == (
        date(2024, 1, 31), date(2024, 3, 31), date(2024, 5, 31))


def test_reversed_range_rejected():
    cfg = config()
    with pytest.raises(ValueError):
        collection_occurrences(cfg.collections[0], date(2026, 2, 1), date(2026, 1, 1))


def test_repeating_slots_and_cross_year_lead():
    cfg = config("weekly", weekday=3, lead=1, start="23:00", duration=60, every=30)
    slots = reminder_slots(cfg, date(2025, 12, 31), date(2025, 12, 31))
    assert [(slot.when.isoformat(), slot.collection_date.isoformat()) for slot in slots] == [
        ("2025-12-31T23:00:00+02:00", "2026-01-01"),
        ("2025-12-31T23:30:00+02:00", "2026-01-01")]
    assert slots[0].collection_id == "c"
    assert slots[0].reminder_id == "r"
    assert slots[0].device_uuids == ("a",)
    assert slots[0].display_seconds == 10


def test_spring_dst_gap_skipped_without_shifting_wall_time():
    cfg = config("weekly", weekday=6, start="02:30", duration=120, every=30)
    slots = reminder_slots(cfg, date(2026, 3, 29), date(2026, 3, 29))
    assert [s.when.strftime("%H:%M %z") for s in slots] == ["02:30 +0200", "04:00 +0300"]


def test_autumn_dst_fold_emitted_once_at_first_occurrence():
    cfg = config("weekly", weekday=6, start="03:00", duration=90, every=30)
    slots = reminder_slots(cfg, date(2026, 10, 25), date(2026, 10, 25))
    assert [s.when.strftime("%H:%M %z") for s in slots] == [
        "03:00 +0300", "03:30 +0300", "04:00 +0200"]
    assert all(s.when.fold == 0 for s in slots)


def test_multiple_collections_and_reminders_sorted_by_absolute_instant():
    base = {"version": 2, "timezone": "UTC", "devices": [{"uuid": "a", "label": "Test"}],
            "collections": []}
    for collection_id, hour in (("late", "11:00"), ("early", "08:00")):
        base["collections"].append({"id": collection_id, "label": "Test", "category": "test",
                                    "text": "Test", "color": "#123456",
                                    "recurrence": {"kind": "weekly", "weekday": 0},
                                    "reminders": [{"id": "r", "lead_days": 0, "start_time": hour,
                                                   "repeat_for_minutes": 1, "every_minutes": 1,
                                                   "display_seconds": 10, "device_uuids": ["a"]}]})
    slots = reminder_slots(validate_config(base), date(2026, 1, 5), date(2026, 1, 5))
    assert [s.collection_id for s in slots] == ["early", "late"]


def test_empty_config_has_no_slots():
    assert reminder_slots(validate_config({"version": 2, "timezone": "UTC", "devices": [], "collections": []}),
                          date(2026, 1, 1), date(2026, 1, 31)) == ()
