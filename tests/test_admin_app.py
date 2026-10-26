"""HTTP contract for the local-only configuration editor."""
import http.client
import json
import threading
import subprocess
import shutil
from html.parser import HTMLParser
from pathlib import Path

import pytest

from src.admin_app import MAX_BODY, create_server, validate_config
from src.config_store import ConfigStore
from src.config_model import validate_config as validate_real_config


class MemoryStore:
    def __init__(self, data=None, revision=None):
        self.data = data
        self.revision = revision

    def load(self):
        if self.data is None:
            raise FileNotFoundError("not configured")
        return self.data, self.revision

    def save(self, data, revision):
        if revision != self.revision:
            raise ValueError("revision conflict")
        self.data = data
        self.revision = "r2"
        return self.revision


@pytest.fixture
def portal():
    store = MemoryStore()
    server = create_server(store, validator=validate_config, discover=lambda: [{"uuid": "cast-1", "label": "Kitchen"}])
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server, store
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)

@pytest.fixture
def real_portal(tmp_path):
    store = ConfigStore(tmp_path / "config.json", validate_real_config)
    server = create_server(store)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server, store
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)

def save_headers(portal):
    server, _ = portal
    token = json.loads(request(portal, "GET", "/api/csrf")[2])["csrf_token"]
    return {"Content-Type": "application/json", "Origin": f"http://localhost:{server.server_port}", "X-CSRF-Token": token}

def test_real_store_empty_get_and_put_round_trip(real_portal):
    status, _, body = request(real_portal, "GET", "/api/config")
    assert status == 200
    config = json.loads(body)["config"]
    assert json.loads(body)["revision"] is None
    validate_real_config(config)
    config["timezone"] = "Europe/Bucharest"
    status, _, body = request(real_portal, "PUT", "/api/config", json.dumps({"config": config, "revision": None}), save_headers(real_portal))
    assert status == 200
    revision = json.loads(body)["revision"]
    assert json.loads(body)["config"] == config
    assert real_portal[1].load() == (config, revision)
    assert json.loads(request(real_portal, "GET", "/api/config")[2]) == {"config": config, "revision": revision}

def test_real_store_full_collection_save_and_reload(real_portal):
    config = {"version": 2, "timezone": "Etc/UTC", "devices": [{"uuid": "target", "label": "Test screen"}],
              "collections": [{"id": "paper", "label": "Paper", "category": "Recycling", "text": "Put paper out",
                               "color": "#336699", "recurrence": {"kind": "biweekly", "weekday": 1, "anchor_date": "2026-10-20"},
                               "reminders": [{"id": "night", "lead_days": 1, "start_time": "18:00",
                                              "repeat_for_minutes": 60, "every_minutes": 15, "display_seconds": 30,
                                              "device_uuids": ["target"]}]}]}
    status, _, body = request(real_portal, "PUT", "/api/config",
                              json.dumps({"config": config, "revision": None}), save_headers(real_portal))
    assert status == 200, body
    server = create_server(ConfigStore(real_portal[1].path, validate_real_config))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, _, body = request((server, None), "GET", "/api/config")
        assert status == 200
        assert json.loads(body) == {"config": config, "revision": json.loads(request(real_portal, "GET", "/api/config")[2])["revision"]}
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)

def large_config(collection_count, reminder_count):
    reminder = {"lead_days": 1, "start_time": "18:00", "repeat_for_minutes": 60,
                "every_minutes": 15, "display_seconds": 30, "device_uuids": ["target"]}
    return {"version": 2, "timezone": "Etc/UTC", "devices": [{"uuid": "target", "label": "Display"}],
            "collections": [{"id": f"collection-{i}", "label": "Paper", "category": "Recycling",
                             "text": "Put paper out", "color": "#336699", "recurrence": {"kind": "weekly", "weekday": 0},
                             "reminders": [{**reminder, "id": f"reminder-{j}"} for j in range(reminder_count)]}
                            for i in range(collection_count)]}

def test_real_portal_saves_fifty_collections_with_ten_reminders(real_portal):
    config = large_config(50, 10)
    payload = json.dumps({"config": config, "revision": None})
    assert len(payload.encode()) > 65536
    validate_real_config(config)
    status, _, body = request(real_portal, "PUT", "/api/config", payload, save_headers(real_portal))
    assert status == 200, body
    revision = json.loads(body)["revision"]
    assert real_portal[1].load() == (config, revision)

def test_real_portal_rejects_schema_size_excess_without_overwriting(real_portal):
    initial = large_config(1, 1)
    headers = save_headers(real_portal)
    status, _, body = request(real_portal, "PUT", "/api/config", json.dumps({"config": initial, "revision": None}), headers)
    assert status == 200, body
    revision = json.loads(body)["revision"]
    oversized = large_config(365, 24)
    payload = json.dumps({"config": oversized, "revision": revision})
    assert 1024 * 1024 < len(json.dumps(oversized, separators=(",", ":")).encode())
    assert len(payload.encode()) < MAX_BODY
    with pytest.raises(ValueError, match="configuration too large"):
        validate_real_config(oversized)
    status, _, body = request(real_portal, "PUT", "/api/config", payload, headers)
    assert status == 422, body
    assert real_portal[1].load() == (initial, revision)

def test_biweekly_default_anchor_matches_weekday_on_non_monday():
    if not shutil.which("node"):
        pytest.skip("Node.js needed to exercise browser date helper")
    script = (Path(__file__).parents[1] / "src/static/app.js").read_text()
    # Evaluate the actual helper with an injected non-Monday date, then use the real validator.
    expression = script.split("function biweeklyDefault(", 1)[1].split("\n}", 1)[0]
    js = "function biweeklyDefault(" + expression + "\n}\nconsole.log(JSON.stringify(biweeklyDefault(new Date(2026, 9, 27, 23, 30))))"
    recurrence = json.loads(subprocess.check_output(["node", "-e", js], text=True))
    assert recurrence == {"kind": "biweekly", "weekday": 1, "anchor_date": "2026-10-27"}
    validate_real_config({"version": 2, "timezone": "Etc/UTC", "devices": [], "collections": [
        {"id": "x", "label": "X", "category": "X", "text": "X", "color": "#112233",
         "recurrence": recurrence, "reminders": []}]})

@pytest.mark.parametrize(("anchor", "weekday", "expected"), [
    ("2026-12-31", 0, "2026-12-28"),
    ("2026-12-31", 6, "2027-01-03"),
    ("2027-01-03", 2, "2026-12-30"),
])
def test_biweekly_weekday_ui_moves_anchor_within_same_iso_week(anchor, weekday, expected):
    if not shutil.which("node"):
        pytest.skip("Node.js needed to exercise browser editor")
    script = Path(__file__).parents[1] / "src/static/app.js"
    js = r"""
const fs = require('fs');
class Element {
  constructor(tag) { this.tag = tag; this.children = []; this.listeners = {}; this.value = ''; }
  append(child) { this.children.push(child); }
  replaceChildren() { this.children = []; }
  addEventListener(name, fn) { this.listeners[name] = fn; }
  querySelector(tag) { return this.children.find(child => child.tag === tag); }
}
const roots = Object.fromEntries(['general','devices','collections','message','add-device','add-collection','discover','candidates','save'].map(id => [id, new Element('root')]));
global.document = {getElementById: id => roots[id], createElement: tag => new Element(tag)};
global.fetch = async path => ({ok: true, json: async () => path === '/api/config' ? {config: {version:2, timezone:'Etc/UTC', devices:[], collections:[{id:'x', label:'X', category:'X', text:'X', color:'#112233', recurrence:{kind:'biweekly', weekday:3, anchor_date:process.argv[2]}, reminders:[]}]}, revision:null} : {csrf_token:'token'}});
const source = fs.readFileSync(process.argv[1], 'utf8');
function walk(el) { return [el, ...el.children.flatMap(walk)]; }
eval(source.replace(/\binit\(\);\s*$/, '') + `\n global.run = async () => {
  await init();
  const label = walk(roots.collections).find(el => el.tag === 'label' && el.children[0]?.textContent === 'Weekday');
  const selector = label.children[1];
  selector.value = process.argv[3];
  selector.listeners.change();
  console.log(JSON.stringify({recurrence:config.collections[0].recurrence, anchorInput:walk(roots.collections).find(el => el.tag === 'label' && el.children[0]?.textContent === 'Anchor date').children[1].value}));
};`);
run().catch(error => { console.error(error); process.exitCode = 1; });
"""
    outcome = json.loads(subprocess.check_output(["node", "-e", js, str(script), anchor, str(weekday)], text=True))
    assert outcome["recurrence"] == {"kind": "biweekly", "weekday": weekday, "anchor_date": expected}
    assert outcome["anchorInput"] == expected
    validate_real_config({"version": 2, "timezone": "Etc/UTC", "devices": [], "collections": [
        {"id": "x", "label": "X", "category": "X", "text": "X", "color": "#112233",
         "recurrence": outcome["recurrence"], "reminders": []}]})

def test_real_store_rejects_invalid_and_stale_without_overwriting(real_portal):
    headers = save_headers(real_portal)
    config = {"version": 2, "timezone": "Etc/UTC", "devices": [], "collections": []}
    status, _, body = request(real_portal, "PUT", "/api/config", json.dumps({"config": config, "revision": None}), headers)
    assert status == 200
    revision = json.loads(body)["revision"]
    invalid = {**config, "timezone": "private-secret-not-a-zone"}
    status, _, body = request(real_portal, "PUT", "/api/config", json.dumps({"config": invalid, "revision": revision}), headers)
    assert status == 422
    assert b"private-secret" not in body
    status, _, body = request(real_portal, "PUT", "/api/config", json.dumps({"config": config, "revision": None}), headers)
    assert status == 409
    assert b"private-secret" not in body
    assert real_portal[1].load() == (config, revision)


def request(portal, method, path, body=None, headers=None):
    server, _ = portal
    conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
    headers = {"Host": f"localhost:{server.server_port}", **(headers or {})}
    conn.request(method, path, body, headers)
    response = conn.getresponse()
    result = response.status, dict(response.getheaders()), response.read()
    conn.close()
    return result


def test_missing_config_is_safe_empty_and_csrf_token_available(portal):
    status, headers, body = request(portal, "GET", "/api/config")
    assert status == 200
    data = json.loads(body)
    assert data == {"config": {"version": 2, "timezone": "Etc/UTC", "devices": [], "collections": []}, "revision": None}
    assert headers["Content-Security-Policy"].startswith("default-src 'none'")
    assert json.loads(request(portal, "GET", "/api/csrf")[2])["csrf_token"]


def test_save_requires_origin_csrf_and_revision(portal):
    server, store = portal
    token = json.loads(request(portal, "GET", "/api/csrf")[2])["csrf_token"]
    config = json.loads(request(portal, "GET", "/api/config")[2])["config"]
    headers = {"Content-Type": "application/json", "Origin": f"http://localhost:{server.server_port}", "X-CSRF-Token": token}
    payload = json.dumps({"config": config, "revision": None})
    assert request(portal, "PUT", "/api/config", payload, {"Content-Type": "application/json"})[0] == 403
    assert request(portal, "PUT", "/api/config", payload, {**headers, "Origin": "http://evil.example"})[0] == 403
    assert request(portal, "PUT", "/api/config", payload, {**headers, "X-CSRF-Token": "bad"})[0] == 403
    assert request(portal, "PUT", "/api/config", payload, headers)[0] == 200
    assert store.data == config
    assert json.loads(request(portal, "GET", "/api/config")[2])["revision"] == "r2"
    assert request(portal, "PUT", "/api/config", payload, headers)[0] == 409


def test_rejects_nonloopback_binding_and_bad_host(portal):
    with pytest.raises(ValueError):
        create_server(MemoryStore(), host="0.0.0.0")
    assert request(portal, "GET", "/api/config", headers={"Host": "attacker.example"})[0] == 403
    assert request(portal, "GET", "/api/config", headers={"Host": "localhost:9999"})[0] == 403


def test_malformed_existing_config_is_not_replaced(portal):
    _, store = portal
    store.data = {"bad": "existing"}
    server, _ = portal
    server.validator = lambda data: (_ for _ in ()).throw(ValueError("invalid config"))
    status, _, _ = request(portal, "GET", "/api/config")
    assert status == 422
    assert store.data == {"bad": "existing"}


def test_discovery_read_only_and_static_allowlist(portal):
    status, _, body = request(portal, "GET", "/api/discover")
    assert status == 200 and json.loads(body) == {"devices": [{"uuid": "cast-1", "label": "Kitchen"}]}
    assert portal[1].data is None
    for path in ("/", "/static/app.js", "/static/style.css"):
        assert request(portal, "GET", path)[0] == 200
    for path in ("/static/../admin_app.py", "/static/unknown.js", "/api/secret"):
        assert request(portal, "GET", path)[0] == 404


def test_editor_exposes_all_schedule_fields_without_external_assets(portal):
    page = request(portal, "GET", "/")[2].decode()
    script = request(portal, "GET", "/static/app.js")[2].decode()
    assert 'src="/static/app.js"' in page
    assert 'href="/static/style.css"' in page
    assert "https://" not in page
    for field in ("timezone", "weekday", "anchor_date", "day_of_month", "lead_days", "start_time", "repeat_for_minutes", "every_minutes", "display_seconds", "device_uuids"):
        assert field in script
    assert "textContent" in script
    assert "innerHTML" not in script
    assert "r.device_uuids = r.device_uuids.map" in script

def test_portal_identifies_bin_collection_and_reminder_scope(portal):
    page = request(portal, "GET", "/")[2].decode()
    class PageText(HTMLParser):
        def __init__(self):
            super().__init__()
            self.parts = []

        def handle_data(self, data):
            self.parts.append(data)

    parser = PageText()
    parser.feed(page)
    visible_text = " ".join(" ".join(parser.parts).split())
    assert "<title>Bin Day | Garbage &amp; Recycling Reminders</title>" in page
    assert "Never miss collection day." in visible_text
    assert "garbage and recycling pickup" in visible_text
    assert "Pickup schedules" in visible_text
    assert "Cast displays" in visible_text
    assert "pickup reminders" in visible_text
    assert "aria-hidden=\"true\"" in page  # decorative bin motif stays out of the accessibility tree

def test_new_collection_uses_bin_pickup_defaults_without_changing_existing_data():
    if not shutil.which("node"):
        pytest.skip("Node.js needed to exercise browser editor")
    script = Path(__file__).parents[1] / "src/static/app.js"
    js = r"""
const fs = require('fs');
class Element {
  constructor(tag) { this.tag = tag; this.children = []; this.listeners = {}; this.value = ''; }
  append(child) { this.children.push(child); }
  replaceChildren() { this.children = []; }
  addEventListener(name, fn) { this.listeners[name] = fn; }
  querySelector(tag) { return this.children.find(child => child.tag === tag); }
}
const roots = Object.fromEntries(['general','devices','collections','message','add-device','add-collection','discover','candidates','save'].map(id => [id, new Element('root')]));
global.document = {getElementById: id => roots[id], createElement: tag => new Element(tag)};
const existing = {id:'old', label:'Paper', category:'Recycling', text:'Set out paper', color:'#112233', recurrence:{kind:'weekly',weekday:2}, reminders:[]};
global.fetch = async path => ({ok: true, json: async () => path === '/api/config' ? {config: {version:2, timezone:'Etc/UTC', devices:[], collections:[existing]}, revision:null} : {csrf_token:'token'}});
const source = fs.readFileSync(process.argv[1], 'utf8');
eval(source.replace(/\binit\(\);\s*$/, '') + `\n global.run = async () => {
  await init();
  roots['add-collection'].listeners.click();
  console.log(JSON.stringify(config.collections));
};`);
run().catch(error => { console.error(error); process.exitCode = 1; });
"""
    existing, created = json.loads(subprocess.check_output(["node", "-e", js, str(script)], text=True))
    assert existing == {"id": "old", "label": "Paper", "category": "Recycling", "text": "Set out paper",
                        "color": "#112233", "recurrence": {"kind": "weekly", "weekday": 2}, "reminders": []}
    created_id = created.pop("id")
    assert created_id != existing["id"]
    assert created == {"label": "New pickup schedule", "category": "Garbage",
                       "text": "Garbage pickup is coming up. Put your bin out.", "color": "#56c3a6",
                       "recurrence": {"kind": "weekly", "weekday": 0}, "reminders": []}
    validate_real_config({"version": 2, "timezone": "Etc/UTC", "devices": [], "collections": [{"id": created_id, **created}]})


def test_complex_v2_config_round_trip_and_revision_header(portal):
    server, store = portal
    token = json.loads(request(portal, "GET", "/api/csrf")[2])["csrf_token"]
    config = {"version": 2, "timezone": "Europe/Bucharest", "devices": [
        {"uuid": "first", "label": "Living room"}, {"uuid": "second", "label": "Kitchen", "known_host": "192.0.2.1"}],
        "collections": [{"id": "bins", "label": "Bins", "category": "Waste", "text": "Put them out", "color": "#55aa77",
                         "recurrence": {"kind": "biweekly", "weekday": 1, "anchor_date": "2026-10-20"},
                         "reminders": [{"id": "r1", "lead_days": 2, "start_time": "18:30", "repeat_for_minutes": 90,
                                        "every_minutes": 15, "display_seconds": 35, "device_uuids": ["first", "second"]}]}]}
    headers = {"Content-Type": "application/json", "Origin": f"http://localhost:{server.server_port}", "X-CSRF-Token": token}
    payload = json.dumps({"config": config, "revision": None})
    assert request(portal, "PUT", "/api/config", payload, {**headers, "If-Match": "wrong"})[0] == 409
    status, _, body = request(portal, "PUT", "/api/config", payload, headers)
    assert status == 200
    assert json.loads(body) == {"config": config, "revision": "r2"}
    assert json.loads(request(portal, "GET", "/api/config")[2])["config"] == config
    assert store.data == config


def test_payload_limit_validation_and_method_restrictions(portal):
    server, store = portal
    token = json.loads(request(portal, "GET", "/api/csrf")[2])["csrf_token"]
    headers = {"Content-Type": "application/json", "Origin": f"http://localhost:{server.server_port}", "X-CSRF-Token": token}
    # The server rejects the declared length before reading a multi-megabyte body.
    # Avoid a client-side BrokenPipe race from transmitting a body it will never read.
    conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
    conn.putrequest("PUT", "/api/config", skip_host=True)
    conn.putheader("Host", f"localhost:{server.server_port}")
    for key, value in headers.items():
        conn.putheader(key, value)
    conn.putheader("Content-Length", str(MAX_BODY + 1))
    conn.endheaders()
    response = conn.getresponse()
    assert response.status == 413
    response.read()
    conn.close()
    assert request(portal, "PUT", "/api/config", "garbage", headers)[0] == 400
    assert request(portal, "PUT", "/api/config", json.dumps({"config": {}, "revision": None}), headers)[0] == 422
    assert request(portal, "POST", "/api/discover", "", headers)[0] == 405
    assert store.data is None
