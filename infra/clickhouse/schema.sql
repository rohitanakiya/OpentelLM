CREATE DATABASE IF NOT EXISTS opentelm;

-- Raw LLM call events. Prompt/response stored as SHA-256 hash for privacy.
-- Full text stored separately in a blob store if the tenant opts in.
CREATE TABLE IF NOT EXISTS opentelm.llm_events (
    tenant_id          String,
    project_id         String,
    trace_id           String,
    span_id            String,
    model              String,
    provider           LowCardinality(String),   -- openai, anthropic, google
    prompt_tokens      UInt32,
    completion_tokens  UInt32,
    total_tokens       UInt32,
    latency_ms         UInt32,
    cost_usd           Float64,
    status             Enum8('success' = 1, 'error' = 2, 'timeout' = 3),
    error_code         Nullable(String),
    error_message      Nullable(String),
    prompt_hash        FixedString(64),           -- SHA-256 hex
    response_hash      FixedString(64),
    sdk_version        String,
    deployment_version Nullable(String),          -- for prompt regression detection
    tags               Map(String, String),       -- arbitrary user-defined metadata
    timestamp          DateTime64(3, 'UTC')
) ENGINE = MergeTree()
PARTITION BY toYYYYMM(timestamp)
ORDER BY (tenant_id, project_id, timestamp, trace_id)
TTL toDateTime(timestamp) + INTERVAL 90 DAY
SETTINGS index_granularity = 8192;

-- Pre-aggregated 1-minute metrics windows, written by the stream-processor's
-- metrics-aggregator consumer group. Phase 3 schema (replaces a placeholder
-- definition added in Phase 1 with a different column set — fresh dev DBs
-- get this shape; upgrading from Phase 1 requires `docker compose down -v`).
CREATE TABLE IF NOT EXISTS opentelm.llm_metrics_1m (
    tenant_id         String,
    project_id        String,
    model             String,
    window_start      DateTime64(3),
    request_count     UInt32,
    error_count       UInt32,
    prompt_tokens     UInt64,
    completion_tokens UInt64,
    cost_usd          Float32,
    p50_latency_ms    Float32,
    p95_latency_ms    Float32,
    p99_latency_ms    Float32,
    -- t-digest centroids of this row's latencies (means + weights). Percentiles
    -- can't be added up, but digests can: merge any set of rows with
    --   quantileTDigestWeightedArray(0.99)(latency_means, latency_weights)
    -- A minute can have more than one row (late events), so always aggregate.
    latency_means     Array(Float32),
    latency_weights   Array(UInt32)
) ENGINE = MergeTree()
PARTITION BY toYYYYMM(window_start)
ORDER BY (tenant_id, project_id, model, window_start);

-- Anomaly events written by the anomaly-consumer service. The `details`
-- column is a JSON blob whose shape varies by anomaly_type.
CREATE TABLE IF NOT EXISTS opentelm.llm_anomalies (
    tenant_id    String,
    project_id   String,
    anomaly_type Enum8('cost_spike' = 1, 'prompt_regression' = 2),
    severity     Enum8('warning' = 1, 'critical' = 2),
    details      String,
    detected_at  DateTime64(3)
) ENGINE = MergeTree()
PARTITION BY toYYYYMM(detected_at)
ORDER BY (tenant_id, project_id, detected_at);

-- Dead letter queue: events that failed validation or enrichment.
CREATE TABLE IF NOT EXISTS opentelm.dlq_events (
    raw_payload   String,
    failure_reason String,
    kafka_offset  Int64,
    arrived_at    DateTime64(3, 'UTC') DEFAULT now64()
) ENGINE = MergeTree()
ORDER BY arrived_at
TTL toDateTime(arrived_at) + INTERVAL 7 DAY;
