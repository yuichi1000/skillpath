output "workflow_sa_email" {
  value = google_service_account.workflow.email
}

output "neo4j_sa_email" {
  value = google_service_account.neo4j.email
}

