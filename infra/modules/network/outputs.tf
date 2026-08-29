output "network_id" {
  value = google_compute_network.vpc.id
}

output "subnetwork_id" {
  value = google_compute_subnetwork.main.id
}

output "cloudrun_subnetwork_id" {
  value = google_compute_subnetwork.cloudrun.id
}

output "nat_id" {
  value = google_compute_router_nat.nat.id
}
