output "workflow_url" {
  description = "Cloud Run の URL (phase1 では空)"
  value       = var.container_image != "" ? module.cloudrun[0].url : ""
}

output "neo4j_internal_ip" {
  value = module.neo4j.internal_ip
}

output "upload_bucket" {
  value = module.data.upload_bucket_name
}

output "artifact_repo" {
  description = "イメージの push 先"
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.workflow.repository_id}/workflow"
}

output "admin_token_hint" {
  description = "管理エンドポイント用トークンの取得方法"
  value       = "gcloud secrets versions access latest --secret=skillpath-admin-token --project=${var.project_id}"
}
