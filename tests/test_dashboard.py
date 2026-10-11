"""
End-to-end tests for the Phase 4 dashboard BFF.

These tests run against the dashboard service started by `docker compose up`.
We exercise the real Next.js API routes — same code path the browser uses.

Run:
    pytest tests/test_dashboard.py -v
"""
from __future__ import annotations

import os
import uuid

import httpx
import pytest
import pytest_asyncio

from tests.conftest import AUTH_URL  # registered via Phase 2/3 conftest

DASHBOARD_URL = os.environ.get("DASHBOARD_URL", "http://localhost:3000")

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _dashboard_up():
    async with httpx.AsyncClient(timeout=2.0) as client:
        try:
            r = await client.get(f"{DASHBOARD_URL}/login")
            if r.status_code >= 500:
                pytest.skip(f"Dashboard /login returned {r.status_code}")
        except (httpx.HTTPError, OSError) as exc:
            pytest.skip(f"Dashboard unreachable at {DASHBOARD_URL}: {exc}")


@pytest_asyncio.fixture
async def dashboard_client():
    # follow_redirects=False so we can assert the 302 to /login on bounce
    async with httpx.AsyncClient(
        base_url=DASHBOARD_URL, timeout=10.0, follow_redirects=False,
    ) as client:
        yield client


async def _register(name: str, email: str, password: str = "supersecret123"):
    """Register a tenant via auth-service directly so the dashboard login has someone to authenticate."""
    async with httpx.AsyncClient(timeout=5.0) as c:
        r = await c.post(
            f"{AUTH_URL}/auth/register",
            json={"name": name, "email": email, "password": password},
        )
        assert r.status_code == 201, r.text


async def _login(client, email: str, password: str = "supersecret123") -> httpx.Response:
    """Hit the dashboard login route — sets HttpOnly cookies on the client."""
    return await client.post(
        "/api/auth/login", json={"email": email, "password": password},
    )


# ---------------------------------------------------------------------------
# 1. Login sets HttpOnly cookie
# ---------------------------------------------------------------------------
async def test_login_sets_httponly_cookie(dashboard_client):
    email = f"dash-{uuid.uuid4().hex[:8]}@example.com"
    await _register("Dash Tester", email)

    r = await _login(dashboard_client, email)
    assert r.status_code == 200, r.text

    # Set-Cookie may be present once or as a list, depending on httpx version
    set_cookies = r.headers.get_list("set-cookie") if hasattr(r.headers, "get_list") \
                  else [r.headers.get("set-cookie", "")]
    joined = " | ".join(set_cookies).lower()

    assert "otlm_access" in joined,  "access cookie missing from Set-Cookie"
    assert "otlm_refresh" in joined, "refresh cookie missing from Set-Cookie"
    assert "httponly" in joined,     "cookies must be HttpOnly"
    assert "samesite=strict" in joined, "cookies must be SameSite=Strict"


# ---------------------------------------------------------------------------
# 2. Unauthenticated API → 401
# ---------------------------------------------------------------------------
async def test_unauthenticated_api_returns_401(dashboard_client):
    r = await dashboard_client.get("/api/overview/stats")
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# 3. Stats response shape
# ---------------------------------------------------------------------------
async def test_stats_returns_expected_shape(dashboard_client):
    email = f"dash-{uuid.uuid4().hex[:8]}@example.com"
    await _register("Stats Tester", email)
    r = await _login(dashboard_client, email)
    assert r.status_code == 200

    r = await dashboard_client.get("/api/overview/stats")
    assert r.status_code == 200, r.text
    data = r.json()
    for key in ("requests_today", "cost_today", "p99_latency", "error_rate"):
        assert key in data, f"missing key: {key}"
        assert isinstance(data[key], (int, float)), f"{key} should be numeric"


# ---------------------------------------------------------------------------
# 4. Traces pagination uses cursor (next_cursor present, accepted on page 2)
# ---------------------------------------------------------------------------
async def test_traces_pagination(dashboard_client):
    email = f"dash-{uuid.uuid4().hex[:8]}@example.com"
    await _register("Traces Tester", email)
    await _login(dashboard_client, email)

    r = await dashboard_client.get("/api/traces")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "rows" in body and "next_cursor" in body
    assert isinstance(body["rows"], list)
    # next_cursor may be null if this fresh tenant has < PAGE_SIZE events,
    # which is the common case for a brand-new tenant. Either passes.
    if body["next_cursor"]:
        r2 = await dashboard_client.get(f"/api/traces?cursor={body['next_cursor']}")
        assert r2.status_code == 200
        assert "rows" in r2.json()


# ---------------------------------------------------------------------------
# 5. Alerts → list (possibly empty, but must be a JSON array)
# ---------------------------------------------------------------------------
async def test_alerts_returns_list(dashboard_client):
    email = f"dash-{uuid.uuid4().hex[:8]}@example.com"
    await _register("Alerts Tester", email)
    await _login(dashboard_client, email)

    r = await dashboard_client.get("/api/alerts")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list)


# ---------------------------------------------------------------------------
# 6. Logout clears cookies
# ---------------------------------------------------------------------------
async def test_logout_clears_cookies(dashboard_client):
    email = f"dash-{uuid.uuid4().hex[:8]}@example.com"
    await _register("Logout Tester", email)
    await _login(dashboard_client, email)

    r = await dashboard_client.post("/api/auth/logout")
    assert r.status_code == 200

    # Subsequent API call should be unauthenticated
    r = await dashboard_client.get("/api/overview/stats")
    assert r.status_code == 401
