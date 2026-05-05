# Memorystore Redis 7. Used for:
#   - API key cache (apikey:{hash})
#   - Refresh token store (refresh:{hash})
#   - Anomaly EMA state (ema:{tenant}:{project}) — survives restarts
#   - Live metrics pub/sub (live:{tenant})
#   - Alert list (alerts:{tenant})
# Persistence is RDB-only at this tier. AOF requires Standard tier.

resource "google_redis_instance" "primary" {
  name           = "opentelm-redis"
  tier           = "BASIC"  # STANDARD_HA doubles cost; BASIC is fine for project
  memory_size_gb = var.redis_memory_gb
  region         = var.region
  redis_version  = "REDIS_7_2"

  authorized_network = google_compute_network.vpc.id
  connect_mode       = "PRIVATE_SERVICE_ACCESS"

  # AUTH on the wire. Pods use the password via env var REDIS_AUTH.
  auth_enabled = true

  # No transit_encryption_mode at BASIC tier — bumps require STANDARD_HA.

  depends_on = [
    google_service_networking_connection.private_vpc,
  ]
}

resource "google_secret_manager_secret" "redis_url" {
  secret_id = "opentelm-redis-url"
  replication {
    auto {}
  }
  depends_on = [google_project_service.required]
}

resource "google_secret_manager_secret_version" "redis_url_v1" {
  secret = google_secret_manager_secret.redis_url.id
  secret_data = format(
    "redis://:%s@%s:%d",
    google_redis_instance.primary.auth_string,
    google_redis_instance.primary.host,
    google_redis_instance.primary.port,
  )
}
