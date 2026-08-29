locals {
  # 設計書 §7.4 + 差分: cloudbuild / iam を追加 (イメージビルドと SA 作成に必要)。
  # vpcaccess は Direct VPC Egress では不要のため削除
  required_apis = [
    "run.googleapis.com",
    "compute.googleapis.com",
    "aiplatform.googleapis.com",
    "firestore.googleapis.com",
    "pubsub.googleapis.com",
    "secretmanager.googleapis.com",
    "storage.googleapis.com",
    "calendar-json.googleapis.com",
    "artifactregistry.googleapis.com",
    "cloudtrace.googleapis.com",
    "cloudbuild.googleapis.com",
    "iam.googleapis.com",
  ]
}

resource "google_project_service" "apis" {
  for_each = toset(local.required_apis)

  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

# API 有効化の伝播待ち (有効化直後にリソース作成すると 403 SERVICE_DISABLED になるため)
resource "time_sleep" "api_propagation" {
  depends_on      = [google_project_service.apis]
  create_duration = "60s"
}

# 設計書に無かった差分: gcloud builds submit の push 先リポジトリ
resource "google_artifact_registry_repository" "workflow" {
  location      = var.region
  repository_id = "skillpath"
  format        = "DOCKER"

  depends_on = [time_sleep.api_propagation]
}

module "network" {
  source = "./modules/network"
  region = var.region

  depends_on = [time_sleep.api_propagation]
}

module "iam" {
  source     = "./modules/iam"
  project_id = var.project_id

  depends_on = [time_sleep.api_propagation]
}

module "neo4j" {
  source = "./modules/neo4j"

  project_id     = var.project_id
  zone           = var.zone
  machine_type   = var.neo4j_machine_type
  disk_size_gb   = var.neo4j_disk_size_gb
  subnetwork_id  = module.network.subnetwork_id
  sa_email       = module.iam.neo4j_sa_email
  desired_status = var.neo4j_desired_status
  nat_id         = module.network.nat_id
}

module "data" {
  source = "./modules/data"

  project_id              = var.project_id
  region                  = var.region
  workflow_url            = var.container_image != "" ? module.cloudrun[0].url : ""
  pubsub_invoker_sa_email = module.iam.pubsub_invoker_sa_email
}

# phase1 (container_image 未指定) では Cloud Run を作らない。
# イメージ push 後に container_image を渡して再 apply する (3段デプロイ)
module "cloudrun" {
  source = "./modules/cloudrun"
  count  = var.container_image != "" ? 1 : 0

  project_id               = var.project_id
  region                   = var.region
  container_image          = var.container_image
  workflow_sa_email        = module.iam.workflow_sa_email
  pubsub_invoker_sa_email  = module.iam.pubsub_invoker_sa_email
  network_id               = module.network.network_id
  cloudrun_subnetwork_id   = module.network.cloudrun_subnetwork_id
  neo4j_internal_ip        = module.neo4j.internal_ip
  neo4j_password_secret_id = module.neo4j.password_secret_id
  gemini_model             = var.gemini_model
  gemini_model_extract     = var.gemini_model_extract
  upload_bucket_name       = module.data.upload_bucket_name
  allow_unauthenticated    = var.allow_unauthenticated
}
