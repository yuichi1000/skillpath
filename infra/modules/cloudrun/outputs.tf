output "url" {
  value = google_cloud_run_v2_service.workflow.uri
}

output "service_name" {
  value = google_cloud_run_v2_service.workflow.name
}
