resource "google_firestore_database" "main" {
  name        = "(default)"
  location_id = var.region
  type        = "FIRESTORE_NATIVE"

  concurrency_mode = "OPTIMISTIC"
}

resource "google_storage_bucket" "uploads" {
  name                        = "${var.project_id}-skillpath-uploads"
  location                    = var.region
  uniform_bucket_level_access = true
  force_destroy               = false

  lifecycle_rule {
    condition {
      age = 90
    }
    action {
      type = "Delete"
    }
  }
}

resource "google_pubsub_topic" "ingestion" {
  name = "skillpath-ingestion"
}

resource "google_pubsub_topic" "ingestion_dlq" {
  name = "skillpath-ingestion-dlq"
}

# 設計書からの差分: Cloud Run 未作成の phase1 では push 先が無いため条件付き。
# count は plan 時に確定する必要があるため、URL ではなく enable_push (イメージ指定の
# 有無から root が算出) で判定する
resource "google_pubsub_subscription" "ingestion_push" {
  count = var.enable_push ? 1 : 0

  name  = "skillpath-ingestion-push"
  topic = google_pubsub_topic.ingestion.id

  ack_deadline_seconds = 600

  push_config {
    push_endpoint = "${var.workflow_url}/tasks/ingestion"

    oidc_token {
      service_account_email = var.pubsub_invoker_sa_email
    }
  }

  retry_policy {
    minimum_backoff = "10s"
    maximum_backoff = "600s"
  }

  dead_letter_policy {
    dead_letter_topic     = google_pubsub_topic.ingestion_dlq.id
    max_delivery_attempts = 5
  }
}
