from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    postgres_dsn: str = "postgresql://opentelm:opentelm@localhost:5432/opentelm"
    clickhouse_host: str = "localhost"
    clickhouse_port: int = 9000
    clickhouse_db: str = "opentelm"
    kafka_bootstrap_servers: str = "localhost:9092"
    kafka_topic: str = "llm.requests"
    redis_url: str = "redis://localhost:6379/0"
    ingest_api_port: int = 8000

    # Rate limit per API key: requests per minute
    rate_limit_rpm: int = 1000

    # HMAC pepper used to hash raw API keys.
    # MUST match the value configured in auth-service so hashes are interchangeable.
    api_key_pepper: str = "opentelm-key-secret"

    # API-key cache TTL in Redis (24h, matches auth-service)
    apikey_cache_ttl_s: int = 86_400

    class Config:
        env_file = ".env"


settings = Settings()
