"""Pure local-calendar collection occurrences and timezone-aware reminder slots."""

from dataclasses import dataclass
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from .config_model import Collection, Config


@dataclass(frozen=True)
class ReminderSlot:
    when: datetime
    collection_date: date
    collection_id: str
    reminder_id: str
    device_uuids: tuple[str, ...]
    display_seconds: int


def collection_occurrences(collection: Collection, start: date, end: date) -> tuple[date, ...]:
    """Inclusive collection dates; biweekly parity is measured from the anchor's calendar week."""
    if not isinstance(start, date) or not isinstance(end, date) or isinstance(start, datetime) or isinstance(end, datetime) or start > end:
        raise ValueError("expected ascending date range")
    rule = collection.recurrence
    result = []
    for ordinal in range(start.toordinal(), end.toordinal() + 1):
        day = date.fromordinal(ordinal)
        if rule.kind == "monthly":
            matches = day.day == rule.day_of_month
        else:
            matches = day.weekday() == rule.weekday
            if matches and rule.kind == "biweekly":
                assert rule.anchor_date is not None  # guaranteed by validate_config
                matches = ((day - rule.anchor_date).days // 7) % 2 == 0
        if matches:
            result.append(day)
    return tuple(result)


def _localize_once(day: date, minute: int, zone: ZoneInfo) -> datetime | None:
    naive = datetime(day.year, day.month, day.day, minute // 60, minute % 60)
    aware = naive.replace(tzinfo=zone, fold=0)
    # A nonexistent spring-forward time fails a UTC round trip. Ambiguous autumn
    # times remain valid and fold=0 deliberately selects their first occurrence.
    if aware.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) != naive:
        return None
    return aware


def reminder_slots(config: Config, start: date, end: date) -> tuple[ReminderSlot, ...]:
    """Return slots whose *reminder local date* is in the inclusive date range.

    Repetition uses wall-clock minute increments; nonexistent times are omitted,
    ambiguous times are emitted once (first fold). Results sort by UTC instant.
    """
    if not isinstance(start, date) or not isinstance(end, date) or isinstance(start, datetime) or isinstance(end, datetime) or start > end:
        raise ValueError("expected ascending date range")
    zone = ZoneInfo(config.timezone)
    result = []
    for collection in config.collections:
        for reminder in collection.reminders:
            first = date.fromordinal(min(date.max.toordinal(), start.toordinal() + reminder.lead_days))
            last = date.fromordinal(min(date.max.toordinal(), end.toordinal() + reminder.lead_days))
            for collection_day in collection_occurrences(collection, first, last):
                reminder_day = date.fromordinal(collection_day.toordinal() - reminder.lead_days)
                if not start <= reminder_day <= end:
                    continue
                minute = int(reminder.start_time[:2]) * 60 + int(reminder.start_time[3:])
                for offset in range(0, reminder.repeat_for_minutes, reminder.every_minutes):
                    when = _localize_once(reminder_day, minute + offset, zone)
                    if when is not None:
                        result.append(ReminderSlot(when, collection_day, collection.id, reminder.id,
                                                   reminder.device_uuids, reminder.display_seconds))
    return tuple(sorted(result, key=lambda slot: (slot.when.astimezone(timezone.utc), slot.collection_id, slot.reminder_id)))
