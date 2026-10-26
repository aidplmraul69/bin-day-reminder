"""In-memory, per-session JPEG delivery for Cast receivers."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from secrets import token_hex
from threading import Lock, Thread


class MediaServer:
    """Serve independent opaque JPEG paths from memory, without any static directory.

    ``with MediaServer(host="0.0.0.0", port=8080) as server`` starts a
    background HTTP thread. ``publish(jpeg_bytes)`` returns a URL path to
    append to the Cast-accessible host/port; ``remove(path)`` revokes it.
    Storage defaults to at most 64 images and 32 MiB combined. The caller
    supplies JPEG-encoded bytes; this server does not decode or validate them.
    """

    def __init__(self, host="0.0.0.0", port=8080, *, max_images=64,
                 max_stored_bytes=32 * 1024 * 1024):
        if max_images < 1 or max_stored_bytes < 1:
            raise ValueError("storage limits must be positive")
        self._images = {}
        self._lock = Lock()
        self._stored_bytes = 0
        self._max_images = max_images
        self._max_stored_bytes = max_stored_bytes
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/healthz":
                    self._reply(200, b"ok\n", "text/plain; charset=utf-8")
                    return
                with owner._lock:
                    body = owner._images.get(self.path)
                if body is None:
                    self._reply(404, b"Not Found\n", "text/plain; charset=utf-8")
                    return
                self._reply(200, body, "image/jpeg")

            def _reply(self, status, body, content_type):
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_HEAD(self):
                self._reply(405, b"", "text/plain; charset=utf-8")

            do_POST = do_HEAD
            do_PUT = do_HEAD
            do_DELETE = do_HEAD
            do_OPTIONS = do_HEAD
            do_PATCH = do_HEAD

            def log_message(self, format, *args):
                pass  # Never log bearer URLs.

        self._server = ThreadingHTTPServer((host, port), Handler)
        self._thread = None

    @property
    def port(self):
        return self._server.server_port

    def publish(self, jpeg_bytes):
        """Store bytes and return a fresh 128-bit random bearer URL path."""
        if not isinstance(jpeg_bytes, bytes):
            raise TypeError("jpeg_bytes must be bytes")
        with self._lock:
            if len(self._images) >= self._max_images:
                raise ValueError("max images exceeded")
            if self._stored_bytes + len(jpeg_bytes) > self._max_stored_bytes:
                raise ValueError("max stored bytes exceeded")
            path = "/media/" + token_hex(16) + ".jpg"
            while path in self._images:
                path = "/media/" + token_hex(16) + ".jpg"
            self._images[path] = jpeg_bytes
            self._stored_bytes += len(jpeg_bytes)
        return path

    def remove(self, path):
        """Revoke a session URL; unknown paths are harmless."""
        with self._lock:
            removed = self._images.pop(path, None)
            if removed is not None:
                self._stored_bytes -= len(removed)

    def __enter__(self):
        self._thread = Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self._server.shutdown()
        self._thread.join()
        self._server.server_close()
