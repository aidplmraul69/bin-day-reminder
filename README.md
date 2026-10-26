# Bin Day Reminder

A long-running service with a **loopback-only configuration portal**, a durable reminder scheduler, and a separate LAN JPEG server for Google Cast. Configure collection dates (weekly, biweekly, monthly), reminder lead days, message/color, repeat interval, display duration, and multiple explicit device UUIDs. Cast hardware behavior varies and has **not** been verified by offline tests.

## Quick start (Linux Docker Engine)

Docker Compose uses host networking for Cast discovery. Choose an address or DNS name that **each Cast display can reach** on this host; do not use `localhost`, `127.0.0.1`, `0.0.0.0`, or a container-only hostname. Allow media TCP port 8080 from trusted LAN devices, and restrict the host firewall to your LAN. The admin listener is **always bound to 127.0.0.1**, never the LAN. Verify chosen ports are free; defaults are media 8080 and admin 8765.

```sh
export LAN_HOST='192.0.2.10' # replace with YOUR actual reachable LAN address
# optional: export MEDIA_PORT=8080 ADMIN_PORT=8765
docker compose config
docker compose build
docker compose up -d
# On the Docker host, or via an SSH tunnel:
ssh -L 8765:127.0.0.1:8765 user@YOUR_DOCKER_HOST
# Open http://127.0.0.1:8765/ in your browser.
```

If browsing on the Docker host itself, no tunnel is necessary. **Do not publish/proxy the admin port to the LAN** or use `ssh -L` with a non-loopback local bind. The portal uses a CSRF token, same-origin checks and strict Host checks, but does not provide user authentication; localhost access must be trusted. Host networking is for Linux Docker Engine; Docker Desktop behavior differs and mDNS may fail. `docker compose logs -f reminder` shows startup/errors. `docker compose down` stops the service; omit `-v` to preserve data. A named volume `reminder-data` contains `/data/config.json` and `/data/claims.sqlite3`, surviving container rebuild/restart. Do not expose the volume or commit personal config. The image runs as non-root with a read-only root filesystem and dropped capabilities. Healthcheck probes `http://127.0.0.1:$MEDIA_PORT/healthz` even when no reminders are due.

### Portal workflow

Add devices by exact UUID (use Discover to inspect candidates, then explicitly Add); optionally specify `known_host` for direct discovery. Add collections, set category/text/color, recurrence and reminders. Choose multiple target devices per reminder, then Save. Discovery is read-only. The portal saves raw v2 JSON atomically with revision checks; invalid or stale edits are rejected without replacing the stored config. A missing config opens an empty editor. A corrupt existing config returns an error and is **not** overwritten; restore a valid backup manually. Keep a backup of the named volume before upgrades. The example `config/reminders.v2.example.json` is illustrative only; it is not mounted automatically.

`timezone` is an IANA zone (`Etc/UTC`, etc.). Weekly and biweekly `weekday` is Monday=0 … Sunday=6; a biweekly `anchor_date` must be on that weekday and establishes alternating calendar weeks. Monthly `day_of_month` matches only months containing that date. Each collection has an `id`, `label`, `category`, `text`, `color` (`#RRGGBB`) and `reminders`. Each reminder has a unique ID within the collection, `lead_days` (days before the collection), `start_time` (`HH:MM` local), `repeat_for_minutes`, `every_minutes`, `display_seconds`, and `device_uuids`. Repeats begin at the start time, stepping while the offset is less than `repeat_for_minutes`; no slots cross local midnight. Spring-forward nonexistent times are omitted; fall-back ambiguous slots use the first occurrence. Each due slot starts within a 30-second grace interval. A late or crashed attempt is **not retried**; SQLite reserves each collection/reminder/time/device attempt before Cast, providing at-most-once attempts rather than guaranteed delivery. Only one active Cast session per target is allowed per process; skipped busy slots may be picked up on the next tick if still inside grace. On restart, claimed slots remain claimed.

Only an idle/backdrop Cast receiver is used; the sender does not select an arbitrary first device or steal an active app. Each session gets a new unguessable `/media/<token>.jpg` URL, held in memory for its duration and revoked after it ends. The service issues a session-targeted STOP only if it still owns the same Default Media Receiver session. If ownership cannot be confirmed, it leaves the current app alone. Media URLs are bearer URLs without authentication: keep the media port on the trusted LAN and do not log/share them. A sender-side test cannot prove display-side reachability.

## Local development and offline checks

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt -r requirements-dev.txt
.venv/bin/python -m pytest -q tests
.venv/bin/python -m src.service --help
# To launch locally (intentional Cast delivery when a configured reminder becomes due):
.venv/bin/python -m src.service --data-dir ./local-data --advertise-host YOUR_LAN_HOST --media-port 8080 --admin-port 8765
```

The test suite uses fake Cast sessions and local HTTP; it does not contact actual Cast devices. For a no-Cast smoke test, launch with an empty `local-data` directory, open the portal at `http://127.0.0.1:8765/`, and check `http://127.0.0.1:8080/healthz`. SIGINT/SIGTERM stops scheduling, waits for sessions to exit, then closes both listeners. The v2 portal service is the only supported entrypoint; use `config/reminders.v2.example.json` as an illustrative schema reference, not as a real household schedule.
