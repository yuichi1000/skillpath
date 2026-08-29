resource "google_service_account" "workflow" {
  account_id   = "skillpath-workflow"
  display_name = "SkillPath ADK Workflow"
}

resource "google_service_account" "neo4j" {
  account_id   = "skillpath-neo4j"
  display_name = "SkillPath Neo4j VM"
}

resource "google_service_account" "pubsub_invoker" {
  account_id   = "skillpath-pubsub-invoker"
  display_name = "SkillPath Pub/Sub Invoker"
}

locals {
  workflow_roles = [
    "roles/aiplatform.user",
    "roles/datastore.user",
    "roles/storage.objectAdmin",
    "roles/pubsub.publisher",
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
