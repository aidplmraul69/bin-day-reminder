# Portal configuration schema (v2)

This is a standalone portal schema, not an implicit migration of the existing runtime config. Pass a JSON-decoded object to `src.config_model.validate_config(raw)` before saving or scheduling; it returns immutable `Config`, `Device`, `Collection`, `Recurrence`, and `Reminder` values or raises `ValueError`. Unknown or missing keys are rejected at every level. Lists preserve input order; there are no implicit devices, schedules, or reminders.

```json
{
  "version": 2,
  "timezone": "Etc/UTC",
  "devices": [
    {"uuid": "example-device-a", "label": "Example display", "known_host": "display.example.invalid"}
  ],
  "collections": [
    {
      "id": "example-collection", "label": "Example collection",
      "category": "example", "text": "Example reminder text", "color": "#2255AA",
      "recurrence": {"kind": "weekly", "weekday": 0},
      "reminders": [
        {"id": "example-reminder", "lead_days": 1, "start_time": "18:00",
         "repeat_for_minutes": 60, "every_minutes": 30, "display_seconds": 15,
         "device_uuids": ["example-device-a"]}
      ]
    }
  ]
}
```

## Fields and limits

- `version`: integer **2** (not a boolean). `timezone`: nonempty IANA timezone accepted by Python `zoneinfo` (timezone database required at runtime).
- `devices`: JSON array, at most 64; each requires `uuid` (1–128 characters) and `label` (1–100). Optional `known_host` is a nonempty string of at most 255 characters, not null. UUIDs are opaque identifiers, unique within `devices`; the library does not resolve hosts or discover devices.
- `collections`: JSON array, at most 365; **empty is valid**. Each requires unique `id` (1–100), `label` (1–100), `category` (1–100), `text` (1–2000), six-digit RGB `color` (`#RRGGBB`), `recurrence`, and `reminders` (JSON array of at most 32). Collections may have no reminders.
- `recurrence` is exactly one of:
  - `{"kind":"weekly","weekday":0}` — weekday integer 0–6, Monday=0.
  - `{"kind":"biweekly","weekday":0,"anchor_date":"2025-12-29"}` — real ISO calendar date, same weekday as `weekday`; collection dates are every second calendar week in **both** directions from that anchor, even across year boundaries.
  - `{"kind":"monthly","day_of_month":31}` — integer 1–31; months without that day are skipped, not clamped.
- Each reminder requires a unique `id` within its collection (1–100), `lead_days` integer 0–366, `start_time` exactly `HH:MM` in 24-hour local time, `repeat_for_minutes` and `every_minutes` integers 1–1440, `display_seconds` integer 1–86400, and a nonempty JSON array `device_uuids` of at most 64 unique strings. Every selected UUID must match a device in the same config. No fallback to all devices exists.
- All strings must be nonempty, trimmed, and free of control characters. All objects reject unknown fields; numbers must be integers, not booleans. A reminder window must finish by local midnight. The last display must also finish by midnight; when a window yields multiple slots, `display_seconds` cannot exceed `every_minutes * 60`.
- The entire config is limited to 1 MiB of compact UTF-8 JSON (`ensure_ascii=False`, without optional whitespace). The portal accepts PUT request bodies up to 2 MiB to allow for the `config`/`revision` envelope and formatting overhead. A body above 2 MiB receives HTTP 413; a structurally valid config above 1 MiB receives HTTP 422 and is not saved. Numeric collection/reminder limits remain per-array limits, not a guarantee that every maximum-filled combination fits the aggregate byte limit.

## Scheduling API and semantics

`src.recurrence.collection_occurrences(collection, start, end)` returns an ordered tuple of **inclusive** collection calendar dates (`datetime.date`). `src.recurrence.reminder_slots(config, start, end)` returns a tuple of `ReminderSlot(when, collection_date, collection_id, reminder_id, device_uuids, display_seconds)` for reminders whose **local reminder date** is in the inclusive `[start, end]` range. Ranges must be ascending dates. All slots have timezone-aware `when`, sorted by absolute instant, then collection/reminder ID for ties.

A collection date minus `lead_days` determines the local reminder date, including month/year crossings. Repeats occur at `start_time + n * every_minutes` while the offset is **less than** `repeat_for_minutes` (half-open window); a duration no longer than the interval yields one slot. Wall-clock times in a spring-forward DST gap are omitted, not shifted. In an autumn DST fold, each wall-clock slot occurs **once**, at the first occurrence (`fold=0`). This module is pure: it does not persist, render, contact devices, or perform network requests.
