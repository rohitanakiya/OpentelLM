from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    kafka_bootstrap_servers: str = "localhost:9092"
    kafka_topic: str = "llm.requests"
    anomalies_topic: str = "llm.anomalies"

    clickhouse_host: str = "localhost"
    clickhouse_port: int = 9000
    clickhouse_db: str = "opentelm"

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

    # Prompt regression thresholds
    regression_min_samples: int = 50
    regression_compare_every_n: int = 100
    regression_error_rate_delta: float = 0.10   # 10 percentage points
    regression_latency_delta_pct: float = 0.40  # 40%

    class Config:
        env_file = ".env"
        case_sensitive = False


settings = Settings()
