# Contributing to OpenTelLM

Thanks for considering a contribution. This doc covers the bits that
aren't obvious from reading the code.

## Run the platform locally

```bash
git clone https://github.com/yourorg/opentelm && cd opentelm
cp .env.example .env                  # set JWT_SECRET_KEY + COOKIE_SECRET
docker compose up -d --build
python infra/postgres/seed_dev.py     # creates a tenant + project + write key
open http://localhost:3000
```

Seven services come up:

| Service           | Port    | What it does                                        |
|-------------------|---------|-----------------------------------------------------|
| ingest-api        | 8000    | accepts SDK events, validates API keys              |
| auth-service      | 8001    | tenants, projects, JWTs                             |
| dashboard         | 3000    | Next.js UI                                          |
| ws-server         | 3001    | live metrics WS (proxied behind dashboard)          |
| consumer          | —       | raw events → ClickHouse                             |
| stream-processor  | —       | metrics + anomaly + regression                      |
| anomaly-consumer  | —       | anomaly events → ClickHouse + Redis list            |

## Run the tests

```bash
pip install -r tests/requirements.txt

# Auth + isolation, all green in ~5s
pytest tests/test_isolation.py -v

# Stream processor (fast tests, ~30s)
pytest tests/test_stream_processor.py -m "not slow" -v

# Dashboard BFF
pytest tests/test_dashboard.py -v

# Node SDK
cd sdk/nodejs && npm ci && npm test
```

Slow tests with `@pytest.mark.slow` involve 75-second window flushes.
Run them with `pytest tests/test_stream_processor.py` (no marker filter)
when you need full coverage.

## Adding a new LLM provider patch

The two existing patches (OpenAI + Anthropic) live in:

- Python: `sdk/python/opentelm/monkey_patch.py`
- Node:   `sdk/nodejs/src/patch.ts`

To add (say) Cohere:

1. **Pick the entry point.** For `cohere-ai`, that's
   `CohereClient.prototype.chat` in Node, or `cohere.chat()` in Python.
2. **Wrap it.** Mirror the existing OpenAI/Anthropic patches: capture
   `started_at`, call the original, hash prompt + response, enqueue an
   `LLMEvent`.
3. **Stream support.** If the provider offers a streaming variant, wrap
   the async iterable rather than the immediate return value (see
   `wrapOpenAIStream` in `sdk/nodejs/src/patch.ts` for the pattern).
4. **Tests.**
   - Add unit tests under `sdk/{python,nodejs}/test/` that mock the
     provider's package (we never depend on it at runtime).
   - Add the model's pricing to all THREE pricing tables — Python SDK,
     Node SDK, and `stream-processor/pricing.py`. They drift if you
     forget, and aggregated cost goes wrong.
5. **PR template fields.** Tick the "added pricing entries" and "tests
   pass against mocked provider" boxes.

## Code style

- **Python**: `ruff check .` + `ruff format`. Prefer raw asyncpg over an
  ORM for the same reasons spelled out in the auth-service code.
- **TypeScript**: strict mode on, no `any` unless you explain it in a
  comment. The SDK has zero hard runtime deps — be careful adding
  packages.
- **YAML**: 2-space indent.
- **SQL**: parametrise everything (`{name:Type}` for ClickHouse,
  `$1, $2` for Postgres). String interpolation in SQL is a PR blocker.

## PR checklist (also in PULL_REQUEST_TEMPLATE)

- [ ] Tests added or updated.
- [ ] If adding a model, updated all three pricing tables.
- [ ] If touching the schema, updated `infra/postgres/schema.sql` AND
      verified Phase 1 idempotency (`docker compose down -v && up -d`).
- [ ] No secrets in commits. Check `git diff` before pushing.
- [ ] No new runtime dependencies in the SDKs without a strong reason.
- [ ] CI is green.

## Reporting security issues

Don't open a public issue. Email security@opentelm.dev (or the
maintainer listed in package.json). We'll respond within 48 hours.

## License

By contributing you agree that your contributions are licensed under the
MIT license, the same as the rest of the project.
