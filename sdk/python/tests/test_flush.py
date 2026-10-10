from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from opentelm.flush import Flusher, _ingest_url


class IngestStub:
    """A real HTTP server standing in for ingest-api."""

    def __init__(self, status: int = 202, delay_s: float = 0.0) -> None:
        self.status = status
        self.delay_s = delay_s
        self.requests: list[dict] = []
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                body = self.rfile.read(int(self.headers["Content-Length"]))
                stub.requests.append({
                    "path": self.path,
                    "auth": self.headers.get("Authorization"),
                    "events": json.loads(body)["events"],
                })
                time.sleep(stub.delay_s)
                self.send_response(stub.status)
                self.end_headers()

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def events(self) -> list[dict]:
        return [e for r in self.requests for e in r["events"]]

    def close(self) -> None:
        self.server.shutdown()


@pytest.fixture
def ingest():
    stub = IngestStub()
    yield stub
    stub.close()


def make(endpoint: str, **kw) -> Flusher:
    kw.setdefault("flush_interval_s", 60)
    kw.setdefault("register_atexit", False)
    return Flusher(endpoint, "opentelm_live_abc", **kw)


def test_posts_batches_with_bearer_auth(ingest):
    f = make(ingest.url)
    for i in range(3):
        f.enqueue({"n": i})
    f.flush()
    assert ingest.requests[0]["path"] == "/v1/ingest"
    assert ingest.requests[0]["auth"] == "Bearer opentelm_live_abc"
    assert [e["n"] for e in ingest.events()] == [0, 1, 2]
    assert f.sent == 3
    f.shutdown()


def test_background_thread_flushes_on_interval(ingest):
    f = make(ingest.url, flush_interval_s=0.05)
    f.enqueue({"n": 1})
    deadline = time.monotonic() + 3
    while not ingest.requests and time.monotonic() < deadline:
        time.sleep(0.02)
    assert ingest.events() == [{"n": 1}]
    f.shutdown()


def test_full_batch_wakes_the_thread_early(ingest):
    f = make(ingest.url, flush_interval_s=60, batch_size=10)
    for i in range(10):
        f.enqueue({"n": i})
    deadline = time.monotonic() + 3
    while len(ingest.events()) < 10 and time.monotonic() < deadline:
        time.sleep(0.02)
    assert len(ingest.events()) == 10
    f.shutdown()


def test_splits_into_batches(ingest):
    f = make(ingest.url, batch_size=2)
    for i in range(5):
        f.enqueue({"n": i})
    f.flush()
    assert [len(r["events"]) for r in ingest.requests] == [2, 2, 1]
    f.shutdown()


def test_queue_overflow_drops_oldest(ingest):
    f = make(ingest.url, max_queue=3)
    for i in range(5):
        f.enqueue({"n": i})
    assert f.dropped == 2
    f.flush()
    assert [e["n"] for e in ingest.events()] == [2, 3, 4]
    f.shutdown()


def test_shutdown_flushes_remaining_events(ingest):
    f = make(ingest.url)
    f.enqueue({"n": 1})
    f.shutdown()
    assert ingest.events() == [{"n": 1}]
    f.enqueue({"n": 2})  # ignored after shutdown, never raises
    assert f.size() == 0


def test_client_errors_are_not_retried():
    ingest = IngestStub(status=401)
    try:
        f = make(ingest.url)
        f.enqueue({"n": 1})
        f.flush()
        assert len(ingest.requests) == 1
        assert f.failed == 1
        f.shutdown()
    finally:
        ingest.close()


def test_server_errors_are_retried_then_dropped():
    ingest = IngestStub(status=503)
    try:
        f = make(ingest.url)
        f.enqueue({"n": 1})
        f.flush(timeout_s=5)
        assert len(ingest.requests) == 3  # 1 try + 2 retries
        assert f.failed == 1
        f.shutdown()
    finally:
        ingest.close()


def test_unreachable_endpoint_never_raises_or_hangs():
    f = make("http://127.0.0.1:9", timeout_s=0.5)  # discard port: connection refused
    f.enqueue({"n": 1})
    t0 = time.monotonic()
    f.flush(timeout_s=1)
    f.shutdown(timeout_s=1)
    assert time.monotonic() - t0 < 4


def test_slow_endpoint_does_not_block_enqueue():
    ingest = IngestStub(delay_s=0.1)
    try:
        f = make(ingest.url, flush_interval_s=0.01)
        t0 = time.perf_counter()
        for i in range(1000):
            f.enqueue({"n": i})
        assert time.perf_counter() - t0 < 0.5
        f.shutdown(timeout_s=5)
    finally:
        ingest.close()


@pytest.mark.parametrize("endpoint", [
    "http://x:8000", "http://x:8000/", "http://x:8000/v1/ingest", "http://x:8000/v1/ingest/",
])
def test_ingest_url_normalisation(endpoint):
    assert _ingest_url(endpoint) == "http://x:8000/v1/ingest"
