variable "project_id" {
  type        = string
  description = "GCP project ID where all resources are created."
}

variable "region" {
  type        = string
  description = "Primary GCP region. asia-south1 (Mumbai) gives the lowest latency for Bangalore-based development."
  default     = "asia-south1"
}

variable "cluster_name" {
  type        = string
  description = "GKE Autopilot cluster name."
  default     = "opentelm-prod"
}

variable "db_tier" {
  type        = string
  description = "Cloud SQL machine type. db-f1-micro is shared-core and cheapest; bump to db-custom-1-3840 for any real workload."
  default     = "db-f1-micro"
}

variable "redis_memory_gb" {
  type        = number
  description = "Memorystore Redis instance size in GB. 1 GB is the smallest available tier."
  default     = 1
}

variable "github_repo" {
  type        = string
  description = "owner/name slug of the GitHub repo allowed to assume the deploy SA via Workload Identity Federation. e.g. \"yourorg/opentelm\"."
}
