resource "google_service_account" "workflow" {
  account_id   = "skillpath-workflow"
  display_name = "SkillPath ADK Workflow"
}

resource "google_service_account" "neo4j" {
  account_id   = "skillpath-neo4j"
  display_name = "SkillPath Neo4j VM"
}

locals {
  workflow_roles = [
    "roles/aiplatform.user",
    "roles/secretmanager.secretAccessor",
    "roles/cloudtrace.agent",
    "roles/logging.logWriter",
  ]
}

resource "google_project_iam_member" "workflow" {
  for_each = toset(local.workflow_roles)

  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.workflow.email}"
}

# 設計書 §7.9 からの差分:
# - run.invoker の付与は cloudrun モジュール側 (サービス名への依存を切るため)
# - Neo4j パスワードの secretAccessor は neo4j モジュール側 (シークレットIDへの依存を切るため)

# 設計書に無かった差分: 新規プロジェクトではデフォルト Compute SA に権限が自動付与
# されないため、Cloud Build (gcloud builds submit) 用の標準ロールを明示付与する
data "google_project" "current" {
  project_id = var.project_id
}

resource "google_project_iam_member" "cloudbuild_default_sa" {
  project = var.project_id
  role    = "roles/cloudbuild.builds.builder"
  member  = "serviceAccount:${data.google_project.current.number}-compute@developer.gserviceaccount.com"
}
