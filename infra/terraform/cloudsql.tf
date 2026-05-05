# Postgres 16 on Cloud SQL.
# - Private IP only — no public endpoint, no firewall holes.
# - Automated backups daily at 03:00 UTC (off-peak for India users).
# - Point-in-time recovery for 7 days.

resource "random_password" "postgres" {
  length  = 32
  special = false # Cloud SQL is fussy about some special chars in DSNs
}

resource "google_sql_database_instance" "postgres" {
  name             = "opentelm-postgres"
  database_version = "POSTGRES_16"
  region           = var.region

  deletion_protection = false # set to true for real prod

  settings {
    tier              = var.db_tier
    availability_type = "ZONAL" # REGIONAL doubles cost; fine for project

    ip_configuration {
      ipv4_enabled    = false
      private_network = google_compute_network.vpc.id
    }

    backup_configuration {
      enabled                        = true
      start_time                     = "03:00"
      point_in_time_recovery_enabled = true
      transaction_log_retention_days = 7
    }

    database_flags {
      name  = "max_connections"
      value = "100"
    }

    insights_config {
      query_insights_enabled = true
    }
  }

  depends_on = [
    google_service_networking_connection.private_vpc,
  ]
}

resource "google_sql_database" "opentelm" {
  name     = "opentelm"
  instance = google_sql_database_instance.postgres.name
}

resource "google_sql_user" "app" {
  name     = "opentelm"
  instance = google_sql_database_instance.postgres.name
  password = random_password.postgres.result
}

# Stash the connection string in Secret Manager. Pods read from there via
# the External Secrets pattern (or a manual kubectl create secret in this
# project's case — see infra/k8s/secrets.yaml.example).
resource "google_secret_manager_secret" "postgres_dsn" {
  secret_id = "opentelm-postgres-dsn"

  replication {
    auto {}
  }

  depends_on = [google_project_service.required]
}

resource "google_secret_manager_secret_version" "postgres_dsn_v1" {
  secret = google_secret_manager_secret.postgres_dsn.id
  secret_data = format(
    "postgresql://%s:%s@%s:5432/%s",
    google_sql_user.app.name,
    random_password.postgres.result,
    google_sql_database_instance.postgres.private_ip_address,
    google_sql_database.opentelm.name,
  )
}
