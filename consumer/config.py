from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    kafka_bootstrap_servers: str = "localhost:9092"
    kafka_topic: str = "llm.requests"
    kafka_group_id: str = "opentelm-consumer-v1"
    clickhouse_host: str = "localhost"
    clickhouse_port: int = 9000
    clickhouse_db: str = "opentelm"
    # Flush to ClickHouse every N messages or every M seconds, whichever comes first
    batch_size: int = 100
    batch_timeout_s: float = 5.0

    class Config:
        env_file = ".env"


settings = Settings()
