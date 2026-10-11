from pydantic import field_validator
from pydantic_settings import BaseSettings


# Values that shipped as defaults or placeholders in earlier versions of this
# repo. Anyone can read them on GitHub, so a deployment using one is
# forgeable: a known JWT secret lets anyone mint a login for any tenant, and
# a known pepper weakens stored API-key hashes. Refuse to start instead.
_PLACEHOLDER_MARKERS = ("change-me", "change_me", "replace_me", "replace-me")
_KNOWN_WEAK = {"opentelm-key-secret", "secret", "changeme", "password"}


def _check_secret(name: str, value: str, min_len: int) -> str:
    hint = ("Run `bash scripts/dev-up.sh` to create a .env with random secrets, "
            "or generate one with: openssl rand -hex 32")
    if not value:
        raise ValueError(f"{name} is not set. {hint}")
    lowered = value.lower()
    if lowered in _KNOWN_WEAK or any(m in lowered for m in _PLACEHOLDER_MARKERS):
        raise ValueError(f"{name} is a published placeholder value. {hint}")
    if len(value) < min_len:
        raise ValueError(f"{name} must be at least {min_len} characters. {hint}")
    return value


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
    # Required, no default (see _check_secret).
    api_key_pepper: str = ""

    # API-key cache TTL in Redis (24h, matches auth-service)
    apikey_cache_ttl_s: int = 86_400

    @field_validator("api_key_pepper")
    @classmethod
    def _pepper(cls, v: str) -> str:
        return _check_secret("API_KEY_PEPPER", v, 16)

    class Config:
        env_file = ".env"


settings = Settings()
