"""Loopback-only integration tests for the per-session Cast media server."""

import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from media_server import MediaServer


def test_simultaneous_images_have_opaque_independent_urls():
    with MediaServer(host="127.0.0.1", port=0) as server:
        first = server.publish(b"first-jpeg")
        second = server.publish(b"second-jpeg")
        assert first != second
        assert re.fullmatch(r"/media/[0-9a-f]{32}\.jpg", first)
        assert re.fullmatch(r"/media/[0-9a-f]{32}\.jpg", second)
        base = f"http://127.0.0.1:{server.port}"
        for path, expected in ((first, b"first-jpeg"), (second, b"second-jpeg")):
            with urlopen(base + path) as response:
                assert response.status == 200
                assert response.read() == expected
                assert response.headers["Content-Type"] == "image/jpeg"
                assert response.headers["Cache-Control"] == "no-store"
                assert response.headers["Content-Length"] == str(len(expected))


def test_health_is_live_while_idle_and_only_published_media_is_visible():
    with MediaServer(host="127.0.0.1", port=0) as server:
        base = f"http://127.0.0.1:{server.port}"
        with urlopen(base + "/healthz") as response:
            assert response.status == 200
            assert response.headers["Cache-Control"] == "no-store"
        path = server.publish(b"jpeg")
        for forbidden in ("/", "/admin", "/config", "/media/", "/media/not-a-token.jpg",
                          "/media/../healthz", "/media/%2e%2e/healthz", path + "?anything=1"):
            with pytest.raises(HTTPError) as error:
                urlopen(base + forbidden)
            assert error.value.code == 404
        for method in ("HEAD", "POST", "PUT", "OPTIONS", "DELETE"):
            with pytest.raises(HTTPError) as error:
                urlopen(Request(base + path, method=method))
            assert error.value.code == 405
            assert error.value.headers["Cache-Control"] == "no-store"
            if method == "HEAD":
                assert error.value.headers["Content-Length"] == "0"


def test_remove_revokes_only_that_session_and_is_idempotent():
    with MediaServer(host="127.0.0.1", port=0) as server:
        first = server.publish(b"first")
        second = server.publish(b"second")
        server.remove(first)
        server.remove(first)
        base = f"http://127.0.0.1:{server.port}"
        with pytest.raises(HTTPError) as error:
            urlopen(base + first)
        assert error.value.code == 404
        with urlopen(base + second) as response:
            assert response.read() == b"second"


def test_storage_limits_are_atomic_and_capacity_returns_after_remove():
    with MediaServer(host="127.0.0.1", port=0, max_images=2, max_stored_bytes=7) as server:
        first = server.publish(b"1234")
        with pytest.raises(ValueError, match="bytes"):
            server.publish(b"12345")
        second = server.publish(b"123")
        with pytest.raises(ValueError, match="images"):
            server.publish(b"x")
        server.remove(first)
        third = server.publish(b"1234")
        base = f"http://127.0.0.1:{server.port}"
        for path, expected in ((second, b"123"), (third, b"1234")):
            with urlopen(base + path) as response:
                assert response.read() == expected
        server.remove(second)
        with pytest.raises(ValueError, match="bytes"):
            server.publish(b"12345678")


def test_concurrent_publish_and_fetch_preserve_each_image():
    with MediaServer(host="127.0.0.1", port=0, max_images=20) as server:
        base = f"http://127.0.0.1:{server.port}"
        def work(index):
            image = f"jpeg-{index}".encode()
            path = server.publish(image)
            with urlopen(base + path) as response:
                return path, response.read() == image
        with ThreadPoolExecutor(max_workers=12) as pool:
            results = list(pool.map(work, range(20)))
        assert len({path for path, _ in results}) == 20
        assert all(matched for _, matched in results)


def test_bad_storage_limits_and_non_bytes_are_rejected():
    with pytest.raises(ValueError, match="positive"):
        MediaServer(host="127.0.0.1", port=0, max_images=0)
    with pytest.raises(ValueError, match="positive"):
        MediaServer(host="127.0.0.1", port=0, max_stored_bytes=0)
    with MediaServer(host="127.0.0.1", port=0) as server:
        with pytest.raises(TypeError, match="bytes"):
            server.publish("not bytes")
