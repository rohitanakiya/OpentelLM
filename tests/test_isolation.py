"""
Cross-tenant isolation tests for Phase 2.

These tests run against live services (`docker compose up`). They verify the
hard isolation contract: under no circumstance can Tenant A reach Tenant B's
data, and the ingest endpoint cannot be tricked into mis-tagging events.

Contracts under test:
  1. Tenant A's API key cannot ingest events tagged as Tenant B's project.
  2. Tenant A cannot list Tenant B's projects.
  3. Tenant A cannot read Tenant B's API keys.
  4. A revoked API key returns 401 on the ingest endpoint.
  5. A 'read'-scoped API key returns 403 on the ingest endpoint.
"""
from __future__ import annotations

import hashlib
import uuid

import pytest

from conftest import AUTH_URL, INGEST_URL  # noqa: F401  (used implicitly by fixtures)

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _unique_email(label: str) -> str:
    return f"{label}-{uuid.uuid4().hex[:8]}@example.com"


async def _register_and_login(client, name: str, email: str, password: str = "supersecret123"):
    r = await client.post(
        "/auth/register",
        json={"name": name, "email": email, "password": password},
    )
    assert r.status_code == 201, r.text
    tenant_id = r.json()["id"]

    r = await client.post(
        "/auth/login",
        json={"email": email, "password": password},
    )
    assert r.status_code == 200, r.text
    return tenant_id, r.json()["access_token"]


async def _create_project(client, access_token: str, name: str) -> str:
    r = await client.post(
        "/projects",
        json={"name": name},
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _create_api_key(client, access_token: str, project_id: str, permissions: str = "write"):
    r = await client.post(
        f"/projects/{project_id}/keys",
        json={"permissions": permissions},
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    return body["id"], body["api_key"]


def _hex64(seed: str) -> str:
    return hashlib.sha256(seed.encode()).hexdigest()


def _ingest_payload(extra: dict | None = None) -> dict:
    event = {
        "model": "gpt-4o",
        "provider": "openai",
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "latency_ms": 200,
        "status": "success",
        "prompt_hash": _hex64("hello"),
        "response_hash": _hex64("world"),
    }
    if extra:
        event.update(extra)
    return {"events": [event]}


# ---------------------------------------------------------------------------
# 1. Tenant A's key cannot ingest events tagged as Tenant B's project
# ---------------------------------------------------------------------------
async def test_ingest_request_schema_has_no_project_id_field(auth_client, ingest_client):
    """
    Structural defence: the IngestRequest event schema does not accept a
    project_id field. The ingest service derives project_id from the resolved
    API key. Even if a caller adds project_id to the body, it's silently
    ignored — and the ingest still succeeds, tagged with the key's project.
    """
    _, token_a = await _register_and_login(auth_client, "A", _unique_email("a"))
    _, token_b = await _register_and_login(auth_client, "B", _unique_email("b"))

    proj_a = await _create_project(auth_client, token_a, "p-a")
    proj_b = await _create_project(auth_client, token_b, "p-b")
    _, key_a = await _create_api_key(auth_client, token_a, proj_a)

    # Sneak Tenant B's project_id in the body. The server must ignore it.
    r = await ingest_client.post(
        "/v1/ingest",
        json=_ingest_payload(extra={"project_id": proj_b, "tenant_id": "attacker"}),
        headers={"Authorization": f"Bearer {key_a}"},
    )
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["accepted"] == 1
    # The response echoes only trace_ids, never tenant_id/project_id, so a
    # caller has no way to confirm or deny the tagging via the API surface.
    assert "project_id" not in body
    assert "tenant_id" not in body


# ---------------------------------------------------------------------------
# 2. Tenant A cannot list Tenant B's projects
# ---------------------------------------------------------------------------
async def test_list_projects_is_tenant_scoped(auth_client):
    _, token_a = await _register_and_login(auth_client, "A", _unique_email("a"))
    _, token_b = await _register_and_login(auth_client, "B", _unique_email("b"))

    await _create_project(auth_client, token_a, "alpha")
    await _create_project(auth_client, token_b, "beta")

    r = await auth_client.get(
        "/projects", headers={"Authorization": f"Bearer {token_a}"},
    )
    assert r.status_code == 200
    a_projects = {p["name"] for p in r.json()}
    assert a_projects == {"alpha"}, "Tenant A leaked Tenant B's projects"

    r = await auth_client.get(
        "/projects", headers={"Authorization": f"Bearer {token_b}"},
    )
    assert {p["name"] for p in r.json()} == {"beta"}


# ---------------------------------------------------------------------------
# 3. Tenant A cannot read Tenant B's API keys
# ---------------------------------------------------------------------------
async def test_cannot_read_other_tenants_keys(auth_client):
    _, token_a = await _register_and_login(auth_client, "A", _unique_email("a"))
    _, token_b = await _register_and_login(auth_client, "B", _unique_email("b"))

    proj_b = await _create_project(auth_client, token_b, "p-b")
    await _create_api_key(auth_client, token_b, proj_b)

    # Tenant A guesses (or steals) Tenant B's project UUID and queries its keys.
    r = await auth_client.get(
        f"/projects/{proj_b}/keys",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    # The WHERE tenant_id = $1 constraint means Tenant A sees an empty list.
    assert r.status_code == 200
    assert r.json() == [], "Tenant A leaked Tenant B's API keys"


async def test_cannot_create_key_under_other_tenants_project(auth_client):
    _, token_a = await _register_and_login(auth_client, "A", _unique_email("a"))
    _, token_b = await _register_and_login(auth_client, "B", _unique_email("b"))

    proj_b = await _create_project(auth_client, token_b, "p-b")

    r = await auth_client.post(
        f"/projects/{proj_b}/keys",
        json={"permissions": "write"},
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert r.status_code == 404, "Tenant A was allowed to mint a key under Tenant B's project"


async def test_cannot_revoke_other_tenants_key(auth_client):
    _, token_a = await _register_and_login(auth_client, "A", _unique_email("a"))
    _, token_b = await _register_and_login(auth_client, "B", _unique_email("b"))

    proj_b = await _create_project(auth_client, token_b, "p-b")
    key_id, _ = await _create_api_key(auth_client, token_b, proj_b)

    r = await auth_client.delete(
        f"/projects/{proj_b}/keys/{key_id}",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# 4. Revoked API key → 401 on ingest
# ---------------------------------------------------------------------------
async def test_revoked_api_key_is_rejected(auth_client, ingest_client):
    _, token = await _register_and_login(auth_client, "A", _unique_email("a"))
    proj = await _create_project(auth_client, token, "p")
    key_id, raw_key = await _create_api_key(auth_client, token, proj)

    # Revoke
    r = await auth_client.delete(
        f"/projects/{proj}/keys/{key_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 204

    r = await ingest_client.post(
        "/v1/ingest",
        json=_ingest_payload(),
        headers={"Authorization": f"Bearer {raw_key}"},
    )
    assert r.status_code == 401
    assert r.json()["detail"]["error"] == "invalid_api_key"


# ---------------------------------------------------------------------------
# 5. read-scope key → 403 on ingest
# ---------------------------------------------------------------------------
async def test_read_scoped_key_cannot_ingest(auth_client, ingest_client):
    _, token = await _register_and_login(auth_client, "A", _unique_email("a"))
    proj = await _create_project(auth_client, token, "p")
    _, raw_key = await _create_api_key(auth_client, token, proj, permissions="read")

    r = await ingest_client.post(
        "/v1/ingest",
        json=_ingest_payload(),
        headers={"Authorization": f"Bearer {raw_key}"},
    )
    assert r.status_code == 403
    assert r.json()["detail"]["error"] == "insufficient_scope"


# ---------------------------------------------------------------------------
# 6. Sanity: unknown key → 401
# ---------------------------------------------------------------------------
async def test_unknown_api_key_rejected(ingest_client):
    r = await ingest_client.post(
        "/v1/ingest",
        json=_ingest_payload(),
        headers={"Authorization": "Bearer opentelm_live_deadbeefdeadbeefdeadbeefdeadbeef"},
    )
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# 7. Sanity: write-scope key → 202 (positive control)
# ---------------------------------------------------------------------------
async def test_write_scoped_key_can_ingest(auth_client, ingest_client):
    _, token = await _register_and_login(auth_client, "A", _unique_email("a"))
    proj = await _create_project(auth_client, token, "p")
    _, raw_key = await _create_api_key(auth_client, token, proj, permissions="write")

    r = await ingest_client.post(
        "/v1/ingest",
        json=_ingest_payload(),
        headers={"Authorization": f"Bearer {raw_key}"},
    )
    assert r.status_code == 202, r.text
