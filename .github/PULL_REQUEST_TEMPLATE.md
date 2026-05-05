## What this PR does

One-paragraph summary. Skip the bullet list — that's what `git log` is
for.

## Why

The motivation. Linked issue if there is one: closes #...

## How

Brief notes on the approach if it's non-obvious. Skip if the code
explains itself.

## Checklist

- [ ] Tests added or updated for the change.
- [ ] If a new model was added, updated all three pricing tables
      (Python SDK, Node SDK, `stream-processor/pricing.py`).
- [ ] If touching `infra/postgres/schema.sql` or the ClickHouse schema,
      verified `docker compose down -v && docker compose up -d --build`
      cleanly applies on a fresh DB.
- [ ] No secrets, API keys, or `.env` values in the diff.
- [ ] No new SDK runtime dependencies added without justification in the
      PR description (the SDKs are intentionally tiny).
- [ ] CI is green.

## Screenshots / output

If this changes the dashboard, paste a before/after screenshot. If it
changes log output or CLI behaviour, paste it.
