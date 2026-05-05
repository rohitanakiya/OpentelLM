# OpenTelLM — GCP infrastructure root.
#
# Single-environment setup (no workspaces) — see README "Engineering decisions".
# Apply order is implicit via Terraform's resource graph; nothing here forces
# explicit depends_on except where referenced by attribute.
#
# State backend: GCS bucket. The bucket itself is NOT created here — chicken
# and egg. Run scripts/bootstrap-tfstate.sh once per project to create it.

terraform {
  required_version = ">= 1.6.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.30"
    }
    google-beta = {
      source  = "hashicorp/google-beta"
      version = "~> 5.30"
    }
  }

  backend "gcs" {
    # Configured per environment via `terraform init -backend-config=...`
    # bucket = "opentelm-tfstate-<project_id>"
    # prefix = "infra"
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

provider "google-beta" {
  project = var.project_id
  region  = var.region
}

# Enable every API we use, exactly once. Without this terraform apply fails
# with "API not enabled" and no clear remediation hint.
locals {
  required_apis = [
    "compute.googleapis.com",
    "container.googleapis.com",
    "sqladmin.googleapis.com",
    "redis.googleapis.com",
    "artifactregistry.googleapis.com",
    "secretmanager.googleapis.com",
    "servicenetworking.googleapis.com",
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "cloudresourcemanager.googleapis.com",
  ]
}

resource "google_project_service" "required" {
  for_each = toset(local.required_apis)
  project  = var.project_id
  service  = each.value

  disable_on_destroy = false # keep APIs on if state is wiped
}
