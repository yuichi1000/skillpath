resource "google_cloud_run_v2_service" "workflow" {
  name                = "skillpath-workflow"
  location            = var.region
  deletion_protection = false

  template {
    service_account = var.workflow_sa_email
    timeout         = "900s"

    scaling {
      min_instance_count = 0
      max_instance_count = 5
    }

    vpc_access {
      network_interfaces {
        network    = var.network_id
        subnetwork = var.cloudrun_subnetwork_id
      }
      egress = "PRIVATE_RANGES_ONLY"
    }

    containers {
      image = var.container_image

      resources {
        limits = {
          cpu    = "2"
          memory = "2Gi"
        }
      }

      env {
        name  = "NEO4J_URI"
        value = "bolt://${var.neo4j_internal_ip}:7687"
      }
      env {
        name  = "NEO4J_USER"
        value = "neo4j"
      }
      env {
        name = "NEO4J_PASSWORD"
        value_source {
          secret_key_ref {
            secret  = var.neo4j_password_secret_id
            version = "latest"
          }
        }
      }
      env {
        name  = "GOOGLE_CLOUD_PROJECT"
        value = var.project_id
      }
      env {
        name  = "GOOGLE_GENAI_USE_VERTEXAI"
        value = "true"
      }
      # 設計書 §7.7 に無かった差分: global エンドポイント指定と抽出用モデル
      env {
        name  = "GOOGLE_CLOUD_LOCATION"
        value = "global"
      }
      env {
        name  = "GEMINI_MODEL"
        value = var.gemini_model
      }
      env {
        name  = "GEMINI_MODEL_EXTRACT"
        value = var.gemini_model_extract
      }
      env {
        name  = "GCS_UPLOAD_BUCKET"
        value = var.upload_bucket_name
      }
      env {
        name  = "CALENDAR_ENABLED"
        value = "true" # トークン未登録の間は自動でプレースホルダにフォールバックする
      }
    }
  }

  traffic {
    type    = "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST"
    percent = 100
  }
}

# Pub/Sub から Cloud Run を叩くための権限 (設計書 §7.9 から移動)
resource "google_cloud_run_v2_service_iam_member" "pubsub_invoker" {
  name     = google_cloud_run_v2_service.workflow.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "serviceAccount:${var.pubsub_invoker_sa_email}"
}

# 審査員・デモ用の公開アクセス (組織ポリシーで拒否されたら変数で false に)
resource "google_cloud_run_v2_service_iam_member" "public" {
  count = var.allow_unauthenticated ? 1 : 0

  name     = google_cloud_run_v2_service.workflow.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "allUsers"
}
