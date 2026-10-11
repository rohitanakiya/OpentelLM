# OpenTelLM

Open-source, self-hostable observability for LLM-powered applications.
Drop-in SDKs auto-instrument your OpenAI / Anthropic / Gemini calls; a
multi-tenant pipeline streams them through Kafka into ClickHouse with
real-time aggregation, anomaly detection, and a Next.js dashboard.

## Architecture

```mermaid
flowchart LR
  SDK[Python / Node SDK] -->|HTTPS| Ingest[ingest-api<br/>FastAPI :8000]
  Ingest -->|HMAC validate| Auth[auth-service<br/>FastAPI :8001]
  Auth -->|JWT, API keys| PG[(Postgres<br/>Cloud SQL)]
  Auth -->|cache, refresh| RD[(Redis<br/>Memorystore)]
  Ingest --> KQ{{Kafka<br/>Confluent Cloud}}
  KQ --> Cons[consumer<br/>raw → ClickHouse]
  KQ --> SP[stream-processor<br/>3 consumer groups]
  SP --> CH[(ClickHouse<br/>Cloud)]
  SP --> RD
  SP -->|anomaly events| KA{{llm.anomalies}}
  KA --> AC[anomaly-consumer]
  AC --> CH
  AC --> RD
  Cons --> CH
  Browser((Browser)) -->|HTTPS / WSS| Dash[dashboard<br/>Next.js :3000<br/>ws-server :3001]
  Dash --> Auth
  Dash --> CH
  Dash --> RD
```

Seven services, four managed dependencies, one event bus.

## Tech stack

| Layer            | Tech                       | Why this, not the alternative |
|------------------|----------------------------|-------------------------------|
| SDKs             | Python + Node.js           | Two languages cover ~95% of LLM apps. Python dominates AI/ML; Node dominates web backends. |
| Ingest API       | FastAPI                    | Async, sub-ms overhead, auto-OpenAPI. Flask is sync; uvloop+FastAPI is the modern default. |
| Event bus        | Kafka (Confluent Cloud)    | Replay capability for failed consumers. Redis Pub/Sub can't replay; SQS can't fan out to N consumer groups cheaply. |
| Analytics DB     | ClickHouse Cloud           | Column-store, append-optimised, 100× faster than Postgres on time-series aggregates. |
| Relational DB    | Postgres 16 (Cloud SQL)    | Tenants, projects, API keys are relational and need ACID. ClickHouse is the wrong tool here. |
| Cache + pubsub   | Redis 7 (Memorystore)      | Sub-ms key cache, atomic INCR for rate limits, pub/sub for live dashboard. |
| Dashboard        | Next.js 14 App Router      | SSR + CSR + API routes in one codebase. SPA-only would force a separate BFF. |
| Container orch   | GKE Autopilot              | Production-grade with no node management. Standard GKE is cheaper at idle but demands ops attention. |
| IaC              | Terraform                  | Declarative, diff-able, reviewable. ClickOps doesn't survive a teammate. |
| CI/CD            | GitHub Actions + WIF       | Free for public repos. Workload Identity Federation = no JSON keys. |

## SDKs

Drop-in instrumentation. Five lines and your existing OpenAI / Anthropic
calls show up on the dashboard. Both SDKs auto-instrument, support
streaming, scrub PII before hashing, and never block your application
code path.

### Python

```bash
pip install opentelm
```

```python
import opentelm
opentelm.init(api_key="opentelm_live_...")
# ... existing OpenAI / Anthropic calls are now traced
```

[Full Python SDK docs →](sdk/python/README.md)

### Node.js

```bash
npm install opentelm-node
```

```ts
import opentelm from "opentelm-node";
opentelm.init({ apiKey: process.env.OPENTELM_API_KEY! });
// ... existing openai / @anthropic-ai/sdk calls are now traced
```

[Full Node SDK docs →](sdk/nodejs/README.md)

## Local development

Runs entirely on your machine with Docker, for free: no cloud account, and
no OpenAI or Anthropic calls are needed to try it.

```bash
git clone https://github.com/rohitanakiya/OpentelLM && cd OpentelLM
bash scripts/dev-up.sh       # creates .env with random secrets, builds and starts everything
bash scripts/smoke-test.sh   # sends 200 fake LLM calls and checks every stage (~2 min)
```

Then open http://localhost:3000 and log in with the account the smoke test
prints (the dashboard has no sign-up page yet; create other accounts with
`POST /auth/register` at http://localhost:8001/docs). Create an API key under
Settings and point an SDK at `http://localhost:8000`. `docker compose down` stops everything
(`down -v` also deletes the data).

Only ports 3000, 8000 and 8001 are opened on your machine. Postgres, Redis,
Kafka and ClickHouse stay inside Docker, so they never clash with a database
you already have installed (uncomment their `ports:` in docker-compose.yml if
you need to reach them).

The services refuse to start without real secrets: `JWT_SECRET_KEY` (32+
characters) and `API_KEY_PEPPER` (16+). `dev-up.sh` generates them; the old
placeholder values from `.env.example` are rejected.

Run the test suite:

```bash
pip install -r tests/requirements.txt
pytest tests/test_isolation.py -v
pytest tests/test_stream_processor.py -m "not slow" -v   # ~30s
pytest tests/test_dashboard.py -v
```

## Production deployment

### One-time setup

```bash
# 1. Create the GCP project, then bootstrap the TF state bucket
gsutil mb -l asia-south1 gs://opentelm-tfstate-$PROJECT_ID

# 2. Provision infrastructure
cd infra/terraform
cp terraform.tfvars.example terraform.tfvars  # fill in project_id, github_repo
terraform init -backend-config="bucket=opentelm-tfstate-$PROJECT_ID" -backend-config="prefix=infra"
terraform apply

# 3. Configure GitHub Secrets (see "Required GitHub Secrets" below) using the
#    values from `terraform output`.

# 4. Apply the K8s manifests (namespace + configmap + secrets first)
gcloud container clusters get-credentials opentelm-prod --region asia-south1
kubectl apply -f infra/k8s/namespace.yaml

# Materialise opentelm-secrets — see infra/k8s/secrets.yaml.example
cp infra/k8s/secrets.yaml.example infra/k8s/secrets.yaml
# fill in values from `terraform output` and your Confluent / ClickHouse Cloud consoles
kubectl apply -f infra/k8s/secrets.yaml
rm infra/k8s/secrets.yaml      # never commit

kubectl apply -f infra/k8s/configmap.yaml
kubectl apply -R -f infra/k8s/
```

### Subsequent deploys

Push to `main`. The `deploy.yml` workflow:
1. Waits for `ci.yml` to pass on the same SHA.
2. Builds and pushes 5 Docker images to Artifact Registry, tagged with the short SHA.
3. `kubectl set image` for each Deployment + `kubectl rollout status --timeout=300s`.
4. On any failure: `kubectl rollout undo` for every deployment.

### Required GitHub Secrets

| Secret                            | Where it comes from |
|-----------------------------------|---------------------|
| `GCP_PROJECT_ID`                  | Your GCP project |
| `GCP_WORKLOAD_IDENTITY_PROVIDER`  | `terraform output workload_identity_provider` |
| `GCP_SERVICE_ACCOUNT`             | `terraform output deployer_service_account` |
| `JWT_SECRET_KEY`                  | `openssl rand -hex 32` |
| `API_KEY_PEPPER`                  | `openssl rand -hex 16` |
| `CLICKHOUSE_URL`                  | ClickHouse Cloud console |
| `CLICKHOUSE_PASSWORD`             | ClickHouse Cloud console |
| `CONFLUENT_BOOTSTRAP_SERVERS`     | Confluent Cloud cluster page |
| `CONFLUENT_API_KEY`               | Confluent Cloud → API Keys |
| `CONFLUENT_API_SECRET`            | Confluent Cloud → API Keys |

## Engineering decisions

The non-obvious calls. Every one of these is an interview talking point.

- **ClickHouse over Postgres for analytics.** A 24-hour cost-per-tenant query
  against `llm_metrics_1m` returns in single-digit milliseconds at hundreds
  of millions of rows. The same query on Postgres needs custom indexes,
  table partitioning, and still loses by 100×. The price is no `JOIN`s on
  raw events — we precompute aggregates in the stream-processor instead.

- **Kafka over Redis Pub/Sub.** Replay matters. If the metrics-aggregator
  crashes mid-window, it restarts and re-reads from its committed offset;
  no events are lost. Pub/Sub is fire-and-forget — if the subscriber is
  down, the message is gone.

- **Hash prompts and responses, never store raw text by default.** SHA-256
  hashes let us group identical prompts (for prompt regression detection)
  without storing PII. Tenants who explicitly opt in get a separate blob
  store path; the default is privacy-preserving.

- **Auth as a separate service, not a module of ingest-api.** Blast radius:
  an auth bug shouldn't disable telemetry, and a telemetry bug shouldn't
  let unauthenticated users in. Each owns its own Postgres pool and Redis
  client.

- **HMAC pepper for API keys, not just hashing.** A salt would protect
  against rainbow tables, but a pepper (server-side secret) means even a
  full Postgres dump doesn't yield usable keys without also exfiltrating
  the pepper from a different system.

- **WebSocket on a separate Node process, not a Next.js route handler.**
  App Router cannot cleanly upgrade HTTP → WS in route.ts. The Next config
  rewrites `/api/ws/*` to `:3001`, which runs `ws-server.mjs`. Browsers
  see one origin; Kubernetes sees two ports on the same pod.

- **Cost spikes compare an hour with other hours.** An alert fires when a
  project's spend so far this hour is more than 3x its usual hour. "Usual"
  means the average of the hours that had traffic among the previous 24, not
  a plain 24-hour average. A project busy only during office hours would
  otherwise have a baseline of about a third of a normal hour, and every
  working hour would look like a spike. Two floors stop noise: at least
  3 hours of history (so new projects don't alert), and at least $1 spent
  this hour. Hourly totals live in Redis (`hourcost:{tenant}:{project}:{hour}`,
  26h TTL), so restarts and extra replicas need no warm-up. The first
  version compared a whole hour's spend with the cost of a single call and
  fired every hour for every active project. Unit tests now cover the "must
  stay quiet" cases, not only the "must fire" ones.

- **Prompt regressions need significance AND size, with peeking controlled.**
  Each tagged version (`opentelm.set_version("v2")`) is compared with the
  version first seen before it. Error rate uses a one-sided two-proportion
  z-test, falling back to Fisher's exact test when counts are too small
  for the normal approximation. Latency uses a one-sided Mann-Whitney U
  test on successful calls, which is rank-based, so a few timeouts can't
  dominate. An alert needs p below the threshold AND a meaningful change
  (+2 points of error rate, or +20% median latency). Re-testing as data
  arrives inflates false alarms: a fixed p < 0.01 checked every 50
  requests alerted on ~4% of simulated no-change deploys. So tests run at
  50, 100, 200, ... requests, and look k gets alpha / 2^(k+1). The total
  stays under 1% however long a version runs (~1% measured), while 2%→6%
  error rates and +50% latency are still caught in 100/100 simulations.
  The statistics are ~100 lines of pure Python, checked against scipy in
  the tests, rather than a ~100 MB scipy dependency.

- **T-Digest for percentiles, and digests (not percentiles) in storage.**
  Keeping every latency grows memory with traffic; a t-digest
  (`stream-processor/tdigest.py`, ~100 lines, no dependencies) keeps ~60
  centroids per window whatever the traffic, and is most accurate at the
  tails. Measured: estimates within 0.2% in rank of the exact value, exact
  for windows under ~50 calls. Percentiles can't be averaged: on 30
  minutes of test traffic with one slow minute, the true p99 was 1,902 ms;
  "average of per-minute p99s" said 1,682 ms and "max of per-minute p99s"
  said 4,078 ms. So each minute row stores its centroids, and ClickHouse
  merges them over any range with
  `quantileTDigestWeightedArray(0.99)(latency_means, latency_weights)`
  (1,926 ms in that test).

- **Watermark + allowed lateness, at-least-once offsets.** A minute is
  written 10 s after it ends, so SDK batching delays still count in the
  right minute; anything later becomes an extra row for that minute, so
  every reader sums and merges rather than trusting one row per minute.
  Kafka offsets are committed only up to the oldest event in a minute not
  yet written, so a crash replays exactly the unwritten events.

- **Cursor pagination, not offset.** Tuple compare on `(timestamp, trace_id)`
  is O(1) at any depth. Offset pagination skews under concurrent inserts
  and gets slower the deeper you page.

- **GKE Autopilot, not Standard.** Auto-scaling, auto-patching, and
  pod-security defaults that pass a CIS audit. Standard is cheaper at idle
  but the operational burden isn't worth it for a project of this size.

## Roadmap

What's planned next, ordered by what users will feel first:

- **OpenTelemetry export (OTLP).** Emit OpenTelLM events as OTLP spans so
  tenants can pipe them into Honeycomb / Tempo / Datadog without
  duplicating instrumentation.
- **Go SDK.** Same zero-config philosophy, third runtime. Probably via
  `httptrace` rather than monkey-patching since Go discourages that.
- **Prompt playground UI.** Run the same prompt across GPT-4o / Claude
  3.5 / Gemini 1.5 in the dashboard, side-by-side cost + latency +
  output diff.
- **Team / org multi-user support.** Tenants today are single-user.
  Add roles (`owner`, `admin`, `member`), invitations, and a real
  org→user→project hierarchy.
- **Slack and PagerDuty alert destinations.** Anomaly events already
  flow through Kafka — wire a fan-out service to deliver them.
- **Cost budget alerts with hard stop.** Set `budget_usd_per_day` per
  project; when crossed, ingest-api returns 429 until next UTC day.
- **OpenTelemetry instrumentation INSIDE the platform.** Yes, it's
  ironic that the observability platform isn't yet observable. Phase
  6+1.
- **External Secrets operator.** Sync GCP Secret Manager → K8s Secret
  automatically rather than the manual `kubectl apply` step today.
- **Schema migrations.** `infra/postgres/schema.sql` is idempotent for
  the current shape; production needs Alembic or sqitch.
- **SAML / OIDC SSO** for enterprise tenants.

If one of these matters to you, [open an issue](https://github.com/rohitanakiya/OpentelLM/issues/new/choose)
or send a PR — see [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT.
