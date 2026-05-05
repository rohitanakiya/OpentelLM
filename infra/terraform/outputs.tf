output "cluster_name" {
  value = google_container_cluster.primary.name
}

output "cluster_endpoint" {
  value     = google_container_cluster.primary.endpoint
  sensitive = true
}

output "cluster_location" {
  value = google_container_cluster.primary.location
}

output "artifact_registry" {
  description = "Docker push target prefix. Concat \":/${service}:${sha}\" for full image refs."
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/opentelm"
}

output "postgres_private_ip" {
  value     = google_sql_database_instance.postgres.private_ip_address
  sensitive = true
}

output "redis_host" {
  value     = google_redis_instance.primary.host
  sensitive = true
}

output "deployer_service_account" {
  description = "Email of the SA that GitHub Actions assumes via WIF."
  value       = google_service_account.deployer.email
}

output "workload_identity_provider" {
  description = "Full resource name of the WIF provider — paste into GitHub Secrets as GCP_WORKLOAD_IDENTITY_PROVIDER."
  value       = google_iam_workload_identity_pool_provider.github.name
}

output "runtime_service_account" {
  description = "Email of the SA bound to the K8s opentelm-app SA — used in namespace SA annotation."
  value       = google_service_account.runtime.email
}
