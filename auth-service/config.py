from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Postgres / Redis
    postgres_dsn: str = "postgresql://opentelm:opentelm@localhost:5432/opentelm"
    redis_url: str = "redis://localhost:6379/0"

    # JWT
    jwt_secret_key: str = "change-me-in-production-must-be-at-least-32-chars"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 7

    # API key HMAC pepper — must match ingest-api so hashes are interchangeable
    api_key_pepper: str = "opentelm-key-secret"

    # API-key cache TTL in Redis (24h per spec)
    apikey_cache_ttl_s: int = 86_400

    # Service port
    auth_service_port: int = 8001

    class Config:
        env_file = ".env"
        case_sensitive = False


settings = Settings()
