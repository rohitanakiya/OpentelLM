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
    # Postgres / Redis
    postgres_dsn: str = "postgresql://opentelm:opentelm@localhost:5432/opentelm"
    redis_url: str = "redis://localhost:6379/0"

    # JWT
    # Required, no default (see _check_secret). Shared with the dashboard.
    jwt_secret_key: str = ""
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 7

    # API key HMAC pepper — must match ingest-api so hashes are interchangeable
    api_key_pepper: str = ""

    # API-key cache TTL in Redis (24h per spec)
    apikey_cache_ttl_s: int = 86_400

    # Service port
    auth_service_port: int = 8001

    @field_validator("jwt_secret_key")
    @classmethod
    def _jwt_secret(cls, v: str) -> str:
        return _check_secret("JWT_SECRET_KEY", v, 32)

    @field_validator("api_key_pepper")
    @classmethod
    def _pepper(cls, v: str) -> str:
        return _check_secret("API_KEY_PEPPER", v, 16)

    class Config:
        env_file = ".env"
        case_sensitive = False


settings = Settings()
