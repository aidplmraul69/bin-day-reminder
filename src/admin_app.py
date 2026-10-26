"""Loopback-only configuration editor.

Integration: create_server(store, validator=validate_config, discover=callback).
store.load() -> (dict, opaque_revision), raises FileNotFoundError ONLY if absent.
store.save(config, expected_revision) -> new_revision; rejects stale revisions
with ValueError (or a ConflictError). The store must validate revision and write
atomically. validator(config) raises ValueError for invalid v2 data, optionally
returns normalized data. discover() returns a list of candidate devices; never
persists anything. No Cast operation is performed by this module.
"""
import json
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from .config_model import validate_config
from .config_store import ConfigConflictError, ConfigInvalidError

MAX_BODY = 2 * 1024 * 1024  # Envelope/whitespace headroom above the 1 MiB config limit.
STATIC = {"/": ("index.html", "text/html; charset=utf-8"),
          "/static/app.js": ("app.js", "text/javascript; charset=utf-8"),
          "/static/style.css": ("style.css", "text/css; charset=utf-8")}
DEFAULT = {"version": 2, "timezone": "Etc/UTC", "devices": [], "collections": []}
CSP = "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"


class AdminServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, store, validator, discover):
        host, _ = address
        if host not in ("localhost", "127.0.0.1"):
            raise ValueError("admin server must bind loopback")
        self.store = store
        self.validator = validator
        self.discover = discover
        self.csrf_token = secrets.token_urlsafe(32)
        super().__init__(address, AdminHandler)


def create_server(store, *, host="127.0.0.1", port=0, validator=validate_config, discover=None):
    return AdminServer((host, port), store, validator, discover or (lambda: []))


class AdminHandler(BaseHTTPRequestHandler):
    server: AdminServer

    def log_message(self, format, *args):
        pass  # Avoid logging configuration paths or rejected request contents.

    def _send(self, status, payload, mime="application/json; charset=utf-8"):
        content = json.dumps(payload).encode("utf-8") if not isinstance(payload, bytes) else payload
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        self.wfile.write(content)

    def _error(self, status, message):
        self._send(status, {"error": message})

    def _authorized_host(self):
        # Reject DNS rebinding, userinfo, extra host headers, and alternate ports.
        host = self.headers.get("Host", "")
        return host in (f"localhost:{self.server.server_port}", f"127.0.0.1:{self.server.server_port}") and len(self.headers.get_all("Host", [])) == 1 and self.client_address[0] in ("127.0.0.1", "::1")

    def _precheck(self):
        if not self._authorized_host():
            self._error(403, "localhost only")
            return False
        return True

    def do_GET(self):
        if not self._precheck():
            return
        if self.path in STATIC:
            name, mime = STATIC[self.path]
            self._send(200, (Path(__file__).parent / "static" / name).read_bytes(), mime)
        elif self.path == "/api/csrf":
            self._send(200, {"csrf_token": self.server.csrf_token})
        elif self.path == "/api/config":
            try:
                config, revision = self.server.store.load()
            except FileNotFoundError:
                config, revision = DEFAULT.copy(), None
            except ConfigInvalidError:
                self._error(422, "existing configuration is invalid; no changes made")
                return
            except Exception:
                self._error(500, "unable to load configuration")
                return
            try:
                self.server.validator(config)
            except (ValueError, TypeError):
                self._error(422, "existing configuration is invalid; no changes made")
                return
            self._send(200, {"config": config, "revision": revision})
        elif self.path == "/api/discover":
            try:
                self._send(200, {"devices": self.server.discover()})
            except Exception:
                self._error(503, "discovery unavailable")
        else:
            self._error(404, "not found")

    def do_PUT(self):
        if not self._precheck():
            return
        if self.path != "/api/config":
            self._error(404, "not found")
            return
        origin = self.headers.get("Origin", "")
        if origin != "http://" + self.headers["Host"] or self.headers.get("X-CSRF-Token") != self.server.csrf_token:
            self._error(403, "same-origin CSRF token required")
            return
        if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
            self._error(415, "JSON required")
            return
        try:
            length = int(self.headers.get("Content-Length", "-1"))
        except ValueError:
            length = -1
        if length > MAX_BODY:
            self._error(413, "payload too large")
            return
        if length < 0 or self.headers.get("Transfer-Encoding"):
            self._error(400, "Content-Length required")
            return
        try:
            body = json.loads(self.rfile.read(length))
            if type(body) is not dict or type(body.get("config")) is not dict:
                raise ValueError("invalid payload")
            revision = body.get("revision")
            if "revision" not in body or type(revision) not in (str, type(None)):
                raise ValueError("revision required")
            if_match = self.headers.get("If-Match")
            if if_match is not None and if_match != revision:
                self._error(409, "revision mismatch")
                return
        except (ValueError, UnicodeDecodeError):
            self._error(400, "invalid JSON payload")
            return
        try:
            self.server.validator(body["config"])
        except (ValueError, TypeError):
            self._error(422, "invalid configuration")
            return
        config = body["config"]
        try:
            # The store must atomically check expected revision before replacing data.
            new_revision = self.server.store.save(config, revision)
        except (ConfigConflictError, ValueError) as exc:
            if isinstance(exc, ConfigInvalidError):
                self._error(422, "invalid configuration")
                return
            self._error(409, "configuration changed; reload before saving")
            return
        except Exception:
            self._error(500, "unable to save configuration")
            return
        self._send(200, {"config": config, "revision": new_revision})

    def do_POST(self):
        if self._precheck():
            self._error(405, "method not allowed")

    do_DELETE = do_POST
    do_PATCH = do_POST
