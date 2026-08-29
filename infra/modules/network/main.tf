resource "google_compute_network" "vpc" {
  name                    = "skillpath-vpc"
  auto_create_subnetworks = false
}

resource "google_compute_subnetwork" "main" {
  name          = "skillpath-subnet"
  ip_cidr_range = "10.10.0.0/24"
  region        = var.region
  network       = google_compute_network.vpc.id

  private_ip_google_access = true
}

# Cloud Run Direct VPC Egress 用のサブネット
resource "google_compute_subnetwork" "cloudrun" {
  name          = "skillpath-cloudrun-subnet"
  ip_cidr_range = "10.10.1.0/24"
  region        = var.region
  network       = google_compute_network.vpc.id
}

# Neo4j には VPC 内からのみ到達可能
resource "google_compute_firewall" "neo4j_internal" {
  name    = "skillpath-allow-neo4j-internal"
  network = google_compute_network.vpc.name

  allow {
    protocol = "tcp"
    ports    = ["7687", "7474"] # Bolt, HTTP
  }

  source_ranges = ["10.10.0.0/16"]
  target_tags   = ["neo4j"]
}

# 設計書に無かった差分: IAP トンネル経由の到達を許可 (デモで Neo4j Browser を
# localhost から見るため。VM は外部IP無しのまま)
resource "google_compute_firewall" "neo4j_iap" {
  name    = "skillpath-allow-neo4j-iap"
  network = google_compute_network.vpc.name

  allow {
    protocol = "tcp"
    ports    = ["22", "7474", "7687"]
  }

  source_ranges = ["35.235.240.0/20"] # IAP の送信元レンジ
  target_tags   = ["neo4j"]
}

# Neo4j VM から外部への通信 (パッケージ取得等)
resource "google_compute_router" "router" {
  name    = "skillpath-router"
  region  = var.region
  network = google_compute_network.vpc.id
}

resource "google_compute_router_nat" "nat" {
  name                               = "skillpath-nat"
  router                             = google_compute_router.router.name
  region                             = var.region
  nat_ip_allocate_option             = "AUTO_ONLY"
  source_subnetwork_ip_ranges_to_nat = "ALL_SUBNETWORKS_ALL_IP_RANGES"
}
