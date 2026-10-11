// Centralised env access.

function optional(name: string, fallback: string): string {
  return process.env[name] ?? fallback;
}

const IS_PRODUCTION = process.env.NODE_ENV === "production";

export const env = {
  AUTH_SERVICE_URL:     optional("AUTH_SERVICE_URL", "http://localhost:8001"),
  CLICKHOUSE_URL:       optional("CLICKHOUSE_URL", "http://localhost:8123"),
  CLICKHOUSE_DB:        optional("CLICKHOUSE_DB", "opentelm"),
  CLICKHOUSE_USER:      optional("CLICKHOUSE_USER", "default"),
  CLICKHOUSE_PASSWORD:  optional("CLICKHOUSE_PASSWORD", ""),
  REDIS_URL:            optional("REDIS_URL", "redis://localhost:6379"),
  JWT_ALGORITHM:        optional("JWT_ALGORITHM", "HS256"),
  // Internal URL the Next rewrites proxy to. The browser still sees /api/ws/*.
  WS_INTERNAL_URL:      optional("WS_INTERNAL_URL", "http://localhost:3001"),
  IS_PRODUCTION,
  // Secure cookies are only sent over HTTPS. Default on in production builds.
  // The local docker-compose stack serves plain http://localhost and sets
  // COOKIE_SECURE=false so every browser (including Safari) keeps the login.
  COOKIE_SECURE:        optional("COOKIE_SECURE", IS_PRODUCTION ? "true" : "false") !== "false",
};

// Values that shipped as defaults in earlier versions of this repo. They are
// public, so anyone could sign a JWT with them and log in as any tenant.
const PLACEHOLDER_MARKERS = ["change-me", "change_me", "replace_me", "replace-me"];

let _jwtSecret: Uint8Array | null = null;

/**
 * The secret shared with auth-service for verifying access JWTs. Checked on
 * first use rather than at import, so `next build` (which has no secrets)
 * still works; the first request fails loudly if it's missing or weak.
 */
export function jwtSecret(): Uint8Array {
  if (_jwtSecret) return _jwtSecret;
  const v = process.env.JWT_SECRET_KEY ?? "";
  const hint = "Run `bash scripts/dev-up.sh` to create .env with random secrets.";
  if (!v) throw new Error(`JWT_SECRET_KEY is not set. ${hint}`);
  if (PLACEHOLDER_MARKERS.some((m) => v.toLowerCase().includes(m))) {
    throw new Error(`JWT_SECRET_KEY is a published placeholder value. ${hint}`);
  }
  if (v.length < 32) throw new Error(`JWT_SECRET_KEY must be at least 32 characters. ${hint}`);
  _jwtSecret = new TextEncoder().encode(v);
  return _jwtSecret;
}
