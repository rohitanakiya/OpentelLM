# Artifact Registry — one repo for all 5 service images.
# Format: ${region}-docker.pkg.dev/${project}/opentelm/${service}:${sha}

resource "google_artifact_registry_repository" "images" {
  location      = var.region
  repository_id = "opentelm"
  format        = "DOCKER"
  description   = "OpenTelLM container images"

  depends_on = [google_project_service.required]
}
