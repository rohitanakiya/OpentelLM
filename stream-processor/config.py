from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    kafka_bootstrap_servers: str = "localhost:9092"
    kafka_topic: str = "llm.requests"
    anomalies_topic: str = "llm.anomalies"

    clickhouse_host: str = "localhost"
    clickhouse_port: int = 9000
    clickhouse_db: str = "opentelm"
    clickhouse_user: str = "default"
    clickhouse_password: str = ""
    clickhouse_secure: bool = False   # TLS; required by ClickHouse Cloud (port 9440)

    redis_url: str = "redis://localhost:6379/0"

    # Tumbling window length for the metrics aggregator
    metrics_window_s: int = 60
    metrics_redis_ttl_s: int = 90

    # Cost-spike anomaly (see consumers/anomaly.py for the full rule):
    # fire when this hour's spend > multiplier x the average spend of the
    # active hours in the previous 24.
    cost_spike_multiplier: float = 3.0
    # Need at least this many hours with traffic in the last 24 before a
    # baseline is trusted; brand-new projects never alert.
    cost_spike_min_baseline_hours: int = 3
    # Never alert while this hour's spend is below this many USD, so tiny
    # projects don't get paged over cents.
    cost_spike_min_usd: float = 1.0
    anomaly_dedup_ttl_s: int = 3600

    # Prompt regression (see consumers/regression.py). A regression needs BOTH
    # statistical significance (p < alpha) AND a practically meaningful size.
    regression_min_samples: int = 50            # per version; looks at 50, 100, 200, 400, ...
    regression_alpha: float = 0.01              # total false-alarm budget per version
    regression_error_rate_delta: float = 0.02   # min error-rate increase (2 percentage points)
    regression_latency_delta_pct: float = 0.20  # min median-latency increase (20%)
    regression_latency_samples: int = 1000      # latency samples kept per version
    regression_state_ttl_s: int = 14 * 24 * 3600

    class Config:
        env_file = ".env"
        case_sensitive = False


settings = Settings()
