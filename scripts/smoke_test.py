"""
End-to-end smoke test for the local docker compose stack. Costs nothing:
it never calls OpenAI or Anthropic. It sends made-up LLM calls through the
real Python SDK with opentelm.track().

Run it with:   bash scripts/smoke-test.sh
(which runs this file in a python:3.12-slim container on the compose
network, so you don't need Python installed).

What it checks, in pipeline order:
  1. auth-service: register a fresh tenant, log in, create a project and
     an API key
  2. SDK -> ingest-api -> Kafka -> consumer -> ClickHouse: 200 events
     arrive, with the right error count and the cost the pricing table gives
  3. dashboard: log in through /api/auth/login (cookie session) and read
     /api/models and /api/traces
  4. stream-processor: per-minute metrics reach /api/overview/stats
     (can take ~70 s, the window is 60 s)
  5. prompt-regression detector + anomaly-consumer: v2 (1 in 3 calls fail)
     vs v1 (none fail) produces a prompt_regression alert in /api/alerts

Every run uses a new tenant, so earlier runs don't affect the counts.
Only the standard library is used, plus the SDK from sdk/python.
"""
from __future__ import annotations

import http.cookiejar
import json
import os
import random
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

AUTH = os.environ.get("AUTH_URL", "http://auth-service:8001")
INGEST = os.environ.get("INGEST_URL", "http://ingest-api:8000")
DASH = os.environ.get("DASHBOARD_URL", "http://dashboard:3000")

MODEL = "gpt-4o-mini"                 # $0.15 in / $0.60 out per 1M tokens
PRICE_IN, PRICE_OUT = 0.15, 0.60
V1_N, V2_N = 100, 100

failures: list[str] = []


def step(msg: str) -> None:
    print(f"\n== {msg}", flush=True)


def ok(msg: str) -> None:
    print(f"   PASS  {msg}", flush=True)


def fail(msg: str) -> None:
    print(f"   FAIL  {msg}", flush=True)
    failures.append(msg)


def request(opener, method: str, url: str, body=None, token: str | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with opener.open(req, timeout=15) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, raw.decode(errors="replace")[:300]


def wait_until(what: str, check, timeout_s: float, every_s: float = 3.0):
    """Poll check() until it returns something truthy or time runs out."""
    deadline = time.monotonic() + timeout_s
    last = None
    while time.monotonic() < deadline:
        try:
            last = check()
            if last:
                return last
        except Exception as exc:  # service still starting, etc.
            last = exc
        time.sleep(every_s)
    print(f"   ...gave up waiting for {what} after {timeout_s:.0f}s (last: {last!r})", flush=True)
    return None


def main() -> int:
    plain = urllib.request.build_opener()

    # ------------------------------------------------------------------
    step("1. services are up")
    for name, url in [("auth-service", f"{AUTH}/health"), ("ingest-api", f"{INGEST}/health"),
                      ("dashboard", f"{DASH}/login")]:
        up = wait_until(name, lambda u=url: plain.open(u, timeout=5).status == 200, 180)
        (ok if up else fail)(f"{name} answers at {url}")
    if failures:
        print("\nServices aren't up. Check `docker compose ps` and `docker compose logs <service>`.")
        return 1

    # ------------------------------------------------------------------
    step("2. sign up, create a project and an API key (auth-service)")
    email = f"smoke-{uuid.uuid4().hex[:10]}@example.com"
    password = "smoke-" + uuid.uuid4().hex
    status, body = request(plain, "POST", f"{AUTH}/auth/register",
                           {"name": "Smoke Test", "email": email, "password": password})
    if status != 201:
        fail(f"register returned {status}: {body}")
        return 1
    ok(f"registered {email}")
    status, tokens = request(plain, "POST", f"{AUTH}/auth/login", {"email": email, "password": password})
    if status != 200:
        fail(f"login returned {status}: {tokens}")
        return 1
    access = tokens["access_token"]
    status, project = request(plain, "POST", f"{AUTH}/projects", {"name": "smoke"}, token=access)
    if status != 201:
        fail(f"create project returned {status}: {project}")
        return 1
    status, key = request(plain, "POST", f"{AUTH}/projects/{project['id']}/keys",
                          {"permissions": "write"}, token=access)
    if status != 201:
        fail(f"create API key returned {status}: {key}")
        return 1
    ok(f"project {project['id']} with key {key['key_prefix']}...")

    # ------------------------------------------------------------------
    step(f"3. send {V1_N + V2_N} fake LLM calls with the Python SDK (no real provider calls)")
    # The SDK straight from this checkout (sdk/python), not from PyPI.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sdk" / "python"))
    import opentelm

    opentelm.init(api_key=key["api_key"], endpoint=INGEST, flush_interval_s=0.5)
    rng = random.Random(42)
    expected_cost = 0.0

    def send(i: int, failing: bool) -> None:
        nonlocal expected_cost
        prompt_tokens = 100 + i % 7
        completion_tokens = 0 if failing else 50 + i % 5
        expected_cost += (prompt_tokens * PRICE_IN + completion_tokens * PRICE_OUT) / 1e6
        opentelm.track(
            model=MODEL, provider="openai",
            prompt=f"smoke prompt {i % 10}", response="" if failing else f"answer {i % 10}",
            prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
            latency_ms=int(rng.gauss(400, 40)),
            status="error" if failing else "success",
            error_code="smoke_injected_error" if failing else None,
            tags={"source": "smoke-test"},
        )

    opentelm.tag_version("smoke-v1")
    for i in range(V1_N):
        send(i, failing=False)
    opentelm.flush(10)
    # Let v1 get counted before v2 starts, as in a real deploy.
    time.sleep(5)
    opentelm.tag_version("smoke-v2")
    v2_errors = 0
    for i in range(V2_N):
        failing = i % 3 == 0
        v2_errors += failing
        send(V1_N + i, failing=failing)
    opentelm.flush(10)
    opentelm.shutdown()
    total = V1_N + V2_N
    ok(f"queued and flushed {total} events (v1: 0 errors, v2: {v2_errors} errors)")

    # ------------------------------------------------------------------
    step("4. log in to the dashboard (cookie session)")
    jar = http.cookiejar.CookieJar()
    dash = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    status, body = request(dash, "POST", f"{DASH}/api/auth/login", {"email": email, "password": password})
    if status != 200:
        fail(f"dashboard login returned {status}: {body}")
        return 1
    names = sorted(c.name for c in jar)
    (ok if "otlm_access" in names else fail)(f"session cookies set: {names}")

    # ------------------------------------------------------------------
    step("5. events reached ClickHouse (ingest-api -> Kafka -> consumer)")

    def models_ready():
        status, rows = request(dash, "GET", f"{DASH}/api/models")
        if status != 200:
            raise RuntimeError(f"/api/models {status}: {rows}")
        row = next((r for r in rows if r["model"] == MODEL), None)
        return row if row and row["request_count"] >= total else None

    row = wait_until("all events in /api/models", models_ready, 90)
    if not row:
        fail("events did not all arrive. Check `docker compose logs ingest-api consumer`.")
    else:
        (ok if row["request_count"] == total else fail)(f"request_count = {row['request_count']} (expected {total})")
        expected_rate = v2_errors / total
        (ok if abs(row["error_rate"] - expected_rate) < 1e-6 else fail)(
            f"error_rate = {row['error_rate']:.4f} (expected {expected_rate:.4f})")
        (ok if abs(row["cost_usd"] - expected_cost) < 1e-6 else fail)(
            f"cost_usd = {row['cost_usd']:.6f} (expected {expected_cost:.6f} from the pricing table)")
        (ok if 300 < row["avg_latency_ms"] < 500 else fail)(f"avg_latency_ms = {row['avg_latency_ms']:.0f}")

    status, page = request(dash, "GET", f"{DASH}/api/traces?model={MODEL}")
    if status == 200 and page.get("rows"):
        r0 = page["rows"][0]
        (ok if len(str(r0.get("prompt_hash", ""))) == 64 else fail)(
            "/api/traces lists the calls, with a 64-char prompt hash (no raw prompt text)")
    else:
        fail(f"/api/traces returned {status}: {str(page)[:200]}")

    # ------------------------------------------------------------------
    step("6. per-minute metrics (stream-processor -> /api/overview/stats); up to ~2 min")

    def stats_ready():
        status, s = request(dash, "GET", f"{DASH}/api/overview/stats")
        if status != 200:
            raise RuntimeError(f"/api/overview/stats {status}: {s}")
        return s if s["requests_today"] >= total else None

    s = wait_until("overview stats", stats_ready, 150, every_s=5)
    if s:
        ok(f"overview: {s['requests_today']} requests, ${s['cost_today']:.4f}, "
           f"error rate {s['error_rate']:.3f}")
    else:
        fail("overview stats never counted the events. Check `docker compose logs stream-processor`.")

    # ------------------------------------------------------------------
    step("7. prompt-regression alert for smoke-v2 (stream-processor -> anomaly-consumer -> /api/alerts)")

    def alert_ready():
        status, alerts = request(dash, "GET", f"{DASH}/api/alerts")
        if status != 200:
            raise RuntimeError(f"/api/alerts {status}: {alerts}")
        return next((a for a in alerts if a.get("type") == "prompt_regression"
                     and a.get("project_id") == project["id"]), None)

    alert = wait_until("regression alert", alert_ready, 60)
    if alert:
        ok(f"prompt_regression alert ({alert.get('severity')}): {json.dumps(alert.get('details'))[:200]}")
    else:
        fail("no prompt_regression alert. Check `docker compose logs stream-processor anomaly-consumer`.")

    # ------------------------------------------------------------------
    print()
    if failures:
        print(f"SMOKE TEST FAILED ({len(failures)} check(s)):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("SMOKE TEST PASSED: SDK -> ingest -> Kafka -> ClickHouse -> stream processing -> dashboard all work.")
    print(f"Log in at http://localhost:3000 as {email} / {password} to see the data.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
