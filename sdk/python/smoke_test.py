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
import uuid

import httpx


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
        "sdk_version": "0.1.0",
        "deployment_version": "smoke-test-v1",
        "tags": {"env": "local"},
        "timestamp_ms": int(time.time() * 1000),
    }

    resp = httpx.post(
        f"{endpoint}/v1/ingest",
        json={"events": [event]},
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=10.0,
    )
    print(f"Status: {resp.status_code}")
    print(json.dumps(resp.json(), indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-key", required=True)
    parser.add_argument("--endpoint", default="http://localhost:8000")
    args = parser.parse_args()
    send_event(args.api_key, args.endpoint)
