// Centralised env access. Throws loudly at module load if a required var is
// missing — better than silently using `undefined` somewhere deep in a query.

function required(name: string): string {
  const v = process.env[name];
  if (!v) throw new Error(`Missing required env var: ${name}`);
  return v;
}

function optional(name: string, fallback: string): string {
  return process.env[name] ?? fallback;
}

export const env = {
  AUTH_SERVICE_URL:     optional("AUTH_SERVICE_URL", "http://localhost:8001"),
  CLICKHOUSE_URL:       optional("CLICKHOUSE_URL", "http://localhost:8123"),
  CLICKHOUSE_DB:        optional("CLICKHOUSE_DB", "opentelm"),
  CLICKHOUSE_USER:      optional("CLICKHOUSE_USER", "default"),
  CLICKHOUSE_PASSWORD:  optional("CLICKHOUSE_PASSWORD", ""),
  REDIS_URL:            optional("REDIS_URL", "redis://localhost:6379"),
  // The dashboard verifies JWTs locally rather than calling auth-service on
  // every request — must share the same secret.
  JWT_SECRET_KEY:       optional("JWT_SECRET_KEY", "change-me-in-production-must-be-at-least-32-chars"),
  JWT_ALGORITHM:        optional("JWT_ALGORITHM", "HS256"),
  COOKIE_SECRET:        optional("COOKIE_SECRET", "change-me-32-chars-of-random-data"),
  // Internal URL the Next rewrites proxy to. The browser still sees /api/ws/*.
  WS_INTERNAL_URL:      optional("WS_INTERNAL_URL", "http://localhost:3001"),
  IS_PRODUCTION:        process.env.NODE_ENV === "production",
};

// Used by the WS server (separate Node process) — kept here so config
// surface is one file.
export function requiredEnv(name: string): string {
  return required(name);
}
