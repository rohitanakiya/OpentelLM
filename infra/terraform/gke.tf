# GKE Autopilot — Google manages nodes, scaling, upgrades, security patches.
# Standard GKE would be cheaper at idle but requires baby-sitting node pools;
# Autopilot is the right tradeoff for "production-grade but not yet at scale".

resource "google_container_cluster" "primary" {
  provider = google-beta

  name     = var.cluster_name
  location = var.region
  project  = var.project_id

  enable_autopilot = true

  network    = google_compute_network.vpc.id
  subnetwork = google_compute_subnetwork.primary.id

  ip_allocation_policy {
    cluster_secondary_range_name  = "pods"
    services_secondary_range_name = "services"
  }

  # Workload Identity: pods authenticate to GCP APIs as a Kubernetes Service
  # Account that's bound to a GCP Service Account. No JSON keys ever land
  # in a pod or container image. Mandatory in Autopilot anyway.
  workload_identity_config {
    workload_pool = "${var.project_id}.svc.id.goog"
  }

  # Private cluster: control plane and nodes have private IPs only.
  # Autopilot still needs a public endpoint for kubectl by default; we
  # accept that here for simplicity (interview talking point: in real prod
  # we'd front it with an authorized network range).
  private_cluster_config {
    enable_private_nodes    = true
    enable_private_endpoint = false
    master_ipv4_cidr_block  = "172.16.0.0/28"
  }

  # Release channel REGULAR balances "new features" vs "stability".
  release_channel {
    channel = "REGULAR"
  }

  # Tear down the default node pool that gets created and immediately deleted
  # — this is the recommended pattern for autopilot.
  deletion_protection = false

  depends_on = [
    google_project_service.required,
    google_service_networking_connection.private_vpc,
  ]
}
