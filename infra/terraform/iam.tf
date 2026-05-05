# IAM has three sets of identities:
#
# 1. opentelm-runtime  — GCP SA for pods at runtime. Bound to the K8s SA
#    "opentelm-app" in namespace "opentelm" via Workload Identity. Has read
#    access to Secret Manager only — never write, never admin.
#
# 2. opentelm-deployer — GCP SA assumed by GitHub Actions via Workload
#    Identity Federation (no JSON keys). Pushes images, applies manifests.
#
# 3. The pool + provider that lets a specific GitHub repo impersonate (2).

# ---------------------------------------------------------------------------
# Runtime SA
# ---------------------------------------------------------------------------
resource "google_service_account" "runtime" {
  account_id   = "opentelm-runtime"
  display_name = "OpenTelLM pod runtime"
}

resource "google_project_iam_member" "runtime_secrets" {
  project = var.project_id
  role    = "roles/secretmanager.secretAccessor"
  member  = "serviceAccount:${google_service_account.runtime.email}"
}

# Allow the K8s SA "opentelm-app" in namespace "opentelm" to impersonate
# the runtime SA. The K8s manifest annotates its SA with this email.
resource "google_service_account_iam_member" "runtime_workload_identity" {
  service_account_id = google_service_account.runtime.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "serviceAccount:${var.project_id}.svc.id.goog[opentelm/opentelm-app]"
}

# ---------------------------------------------------------------------------
# Deployer SA + Workload Identity Federation for GitHub Actions
# ---------------------------------------------------------------------------
resource "google_service_account" "deployer" {
  account_id   = "opentelm-deployer"
  display_name = "GitHub Actions deployer"
}

# Deployer needs to push images and call the K8s API.
resource "google_project_iam_member" "deployer_artifact_writer" {
  project = var.project_id
  role    = "roles/artifactregistry.writer"
  member  = "serviceAccount:${google_service_account.deployer.email}"
}

resource "google_project_iam_member" "deployer_gke_developer" {
  project = var.project_id
  role    = "roles/container.developer"
  member  = "serviceAccount:${google_service_account.deployer.email}"
}

# Required to fetch the kubeconfig
resource "google_project_iam_member" "deployer_token_creator" {
  project = var.project_id
  role    = "roles/iam.serviceAccountTokenCreator"
  member  = "serviceAccount:${google_service_account.deployer.email}"
}

resource "google_iam_workload_identity_pool" "github" {
  workload_identity_pool_id = "github-actions-pool"
  display_name              = "GitHub Actions"
}

resource "google_iam_workload_identity_pool_provider" "github" {
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github-provider"
  display_name                       = "GitHub OIDC"

  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.repository" = "assertion.repository"
    "attribute.actor"      = "assertion.actor"
    "attribute.ref"        = "assertion.ref"
  }

  # CRITICAL: condition restricts which repo can mint tokens. Without this,
  # any GitHub repo on the planet could impersonate the deployer.
  attribute_condition = "assertion.repository == \"${var.github_repo}\""

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

# Bind the GitHub repo to the deployer SA.
resource "google_service_account_iam_member" "deployer_wif" {
  service_account_id = google_service_account.deployer.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository/${var.github_repo}"
}
