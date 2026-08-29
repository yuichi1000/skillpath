output "internal_ip" {
  value = google_compute_instance.neo4j.network_interface[0].network_ip
}

output "password_secret_id" {
  value = google_secret_manager_secret.neo4j_password.secret_id
}
