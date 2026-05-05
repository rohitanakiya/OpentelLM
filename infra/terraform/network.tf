# Custom VPC. We don't use the auto-created default network because:
#   1. Default subnets exist in every region — wasteful and noisy.
#   2. Cloud SQL/Memorystore both need a private services connection range,
#      which is cleanest when defined explicitly per-VPC.

resource "google_compute_network" "vpc" {
  name                    = "opentelm-vpc"
  auto_create_subnetworks = false
  routing_mode            = "REGIONAL"
  depends_on              = [google_project_service.required]
}

resource "google_compute_subnetwork" "primary" {
  name          = "opentelm-${var.region}"
  region        = var.region
  network       = google_compute_network.vpc.id
  ip_cidr_range = "10.20.0.0/20"

  # GKE Autopilot allocates Pod and Service IPs from secondary ranges.
  secondary_ip_range {
    range_name    = "pods"
    ip_cidr_range = "10.40.0.0/14"
  }
  secondary_ip_range {
    range_name    = "services"
    ip_cidr_range = "10.36.0.0/20"
  }

  # Cheap observability — flow logs at 0.5s aggregation are fine for a project.
  log_config {
    aggregation_interval = "INTERVAL_5_SEC"
    flow_sampling        = 0.5
    metadata             = "INCLUDE_ALL_METADATA"
  }
}

# Reserved range for private services (Cloud SQL, Memorystore).
# Without this, the managed services have no private IPs to attach to.
resource "google_compute_global_address" "private_services" {
  name          = "opentelm-private-services"
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  prefix_length = 16
  network       = google_compute_network.vpc.id
}

resource "google_service_networking_connection" "private_vpc" {
  network                 = google_compute_network.vpc.id
  service                 = "servicenetworking.googleapis.com"
  reserved_peering_ranges = [google_compute_global_address.private_services.name]
}

# Cloud NAT so private GKE nodes can reach the public internet (e.g. to pull
# images from Artifact Registry's regional endpoint, or for outbound HTTPS to
# Confluent Cloud / ClickHouse Cloud).
resource "google_compute_router" "router" {
  name    = "opentelm-router"
  region  = var.region
  network = google_compute_network.vpc.id
}

resource "google_compute_router_nat" "nat" {
  name                               = "opentelm-nat"
  router                             = google_compute_router.router.name
  region                             = var.region
  nat_ip_allocate_option             = "AUTO_ONLY"
  source_subnetwork_ip_ranges_to_nat = "ALL_SUBNETWORKS_ALL_IP_RANGES"

  log_config {
    enable = true
    filter = "ERRORS_ONLY"
  }
}
