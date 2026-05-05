"""
Security primitives for the auth-service.

Three independent concerns live here:

1. Password hashing (bcrypt, 12 rounds)
2. JWT issue / verify (python-jose, HS256)
3. API key generation + HMAC-SHA256 hashing (interchangeable with ingest-api)

The HMAC pepper is read from settings.api_key_pepper. The same value MUST be
configured in ingest-api/config.py (API_KEY_PEPPER env var) — otherwise the
ingest-api will fail to validate keys minted here.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
from jose import JWTError, jwt

from config import settings

# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------
_BCRYPT_ROUNDS = 12


def hash_password(plain: str) -> str:
    salt = bcrypt.gensalt(rounds=_BCRYPT_ROUNDS)
    return bcrypt.hashpw(plain.encode("utf-8"), salt).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------
ACCESS_TOKEN_TYPE = "access"
REFRESH_TOKEN_TYPE = "refresh"


def _encode(payload: dict[str, Any]) -> str:
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def create_access_token(tenant_id: str, email: str, role: str = "owner") -> str:
    now = datetime.now(timezone.utc)
    return _encode({
        "sub": tenant_id,
        "tenant_id": tenant_id,
        "email": email,
        "role": role,
        "type": ACCESS_TOKEN_TYPE,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=settings.access_token_expire_minutes)).timestamp()),
    })


def create_refresh_token(tenant_id: str) -> tuple[str, str]:
    """Return (raw_refresh_token, sha256_of_token). Caller stores the hash in Redis."""
    now = datetime.now(timezone.utc)
    raw = _encode({
        "sub": tenant_id,
        "tenant_id": tenant_id,
        "type": REFRESH_TOKEN_TYPE,
        "jti": secrets.token_urlsafe(16),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(days=settings.refresh_token_expire_days)).timestamp()),
    })
    return raw, hash_refresh_token(raw)


def hash_refresh_token(token: str) -> str:
    """Plain SHA-256 — the token is already cryptographically random; HMAC adds nothing."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def decode_token(token: str) -> dict[str, Any]:
    """Raises JWTError on invalid / expired token."""
    return jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])


def decode_token_safe(token: str) -> dict[str, Any] | None:
    try:
        return decode_token(token)
    except JWTError:
        return None


# ---------------------------------------------------------------------------
# API keys
# ---------------------------------------------------------------------------
API_KEY_PREFIX = "opentelm_live_"


def generate_api_key() -> tuple[str, str, str]:
    """
    Mint a new raw key.

    Returns (raw_key, key_hash, display_prefix):
      raw_key       — opentelm_live_<32 hex>. Shown to the user exactly once.
      key_hash      — HMAC-SHA256 hex digest. The only thing stored.
      display_prefix — first 8 chars of raw_key, for showing "opentelm…" in lists.
    """
    raw = API_KEY_PREFIX + secrets.token_hex(16)  # 16 bytes = 32 hex chars
    return raw, hash_api_key(raw), raw[:8]


def hash_api_key(raw_key: str) -> str:
    """HMAC-SHA256 with the per-deployment pepper."""
    return hmac.new(
        settings.api_key_pepper.encode("utf-8"),
        raw_key.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
