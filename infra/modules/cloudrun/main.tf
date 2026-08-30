# /admin/* 保護用トークン (ガードレール)。neo4j パスワードと同じ Secret Manager パターン
resource "random_password" "admin_token" {
  length  = 32
  special = false
}

resource "google_secret_manager_secret" "admin_token" {
  secret_id = "skillpath-admin-token"

  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_version" "admin_token" {
  secret      = google_secret_manager_secret.admin_token.id
  secret_data = random_password.admin_token.result
}

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
      # ---- ガードレール ----
      env {
        name = "ADMIN_TOKEN"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.admin_token.secret_id
            version = "latest"
          }
        }
      }
      env {
        name  = "ALLOWED_UIDS"
        value = var.allowed_uids
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

# 未認証公開。IAP で保護する場合は false のままにする
resource "google_cloud_run_v2_service_iam_member" "public" {
  count = var.allow_unauthenticated ? 1 : 0

  name     = google_cloud_run_v2_service.workflow.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "allUsers"
}

# --- Identity-Aware Proxy ---
# サービスの前段に IAP を置き、指定した Google アカウントだけを通す。
# IAP 自身がサービスを呼ぶため、IAP サービスエージェントに invoker が要る。
#
# 有効化そのものは provider 6.x が未対応 (iap_enabled 引数が無い) のため、
# 一度だけ次のコマンドで行う。Terraform は権限側だけを管理する:
#   gcloud services enable iap.googleapis.com
#   gcloud beta run services update skillpath-workflow --region=<region> --iap
# 解除する場合は --no-iap と allow_unauthenticated=true。
data "google_project" "this" {
  project_id = var.project_id
}

resource "google_cloud_run_v2_service_iam_member" "iap_agent" {
  count = length(var.iap_members) > 0 ? 1 : 0

  name     = google_cloud_run_v2_service.workflow.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "serviceAccount:service-${data.google_project.this.number}@gcp-sa-iap.iam.gserviceaccount.com"
}

resource "google_iap_web_cloud_run_service_iam_member" "accessor" {
  for_each = toset(var.iap_members)

  project                = var.project_id
  location               = var.region
  cloud_run_service_name = google_cloud_run_v2_service.workflow.name
  role                   = "roles/iap.httpsResourceAccessor"
  member                 = each.value
}
