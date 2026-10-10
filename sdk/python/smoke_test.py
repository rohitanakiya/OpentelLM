"""
Smoke test: sends a fake LLM event directly to the ingest API.

Run after `docker compose up -d` and after seeding a tenant + API key in Postgres.
Usage:
    python smoke_test.py --api-key sk-test-...
"""
import argparse
import hashlib
import json
import time
import urllib.error
import urllib.request
import uuid


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def send_event(api_key: str, endpoint: str = "http://localhost:8000") -> None:
    event = {
        "trace_id": str(uuid.uuid4()),
        "span_id": str(uuid.uuid4()),
        "model": "gpt-4o",
        "provider": "openai",
        "prompt_tokens": 120,
        "completion_tokens": 85,
        "latency_ms": 342,
        "status": "success",
        "prompt_hash": sha256("What is the capital of France?"),
        "response_hash": sha256("The capital of France is Paris."),
        "sdk_version": "smoke-test",
        "deployment_version": "smoke-test-v1",
        "tags": {"env": "local"},
        "timestamp_ms": int(time.time() * 1000),
    }

    req = urllib.request.Request(
        f"{endpoint}/v1/ingest",
        data=json.dumps({"events": [event]}).encode(),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            status, body = resp.status, resp.read()
    except urllib.error.HTTPError as err:
        status, body = err.code, err.read()
    print(f"Status: {status}")
    print(json.dumps(json.loads(body or b"{}"), indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-key", required=True)
    parser.add_argument("--endpoint", default="http://localhost:8000")
    args = parser.parse_args()
    send_event(args.api_key, args.endpoint)
