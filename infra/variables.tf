variable "project_id" {
  description = "GCP プロジェクトID"
  type        = string
}

variable "region" {
  description = "デプロイ先リージョン"
  type        = string
  default     = "asia-northeast1"
}

variable "zone" {
  description = "Neo4j VM のゾーン"
  type        = string
  default     = "asia-northeast1-a"
}

variable "neo4j_machine_type" {
  description = "Neo4j VM のマシンタイプ"
  type        = string
  default     = "e2-standard-2"
}

variable "neo4j_disk_size_gb" {
  description = "Neo4j データディスクのサイズ"
  type        = number
  default     = 50
}

variable "neo4j_desired_status" {
  description = "Neo4j VM の稼働状態 (RUNNING / TERMINATED)。デモ後の停止用 (設計書 §7.11)"
  type        = string
  default     = "RUNNING"
}

variable "gemini_model" {
  description = "Router 用 Gemini モデル (ハッカソン規定: 3.5 以降)"
  type        = string
  default     = "gemini-3.5-flash"
}

variable "gemini_model_extract" {
  description = "構造抽出 (Ingestion/Feedback) 用 Gemini モデル"
  type        = string
  default     = "gemini-3.5-flash"
}

variable "container_image" {
  description = "ADK Workflow のコンテナイメージ (空なら Cloud Run を作らない = phase1)"
  type        = string
  default     = ""
}

variable "allow_unauthenticated" {
  description = "Cloud Run を未認証公開するか (審査員アクセス用)"
  type        = bool
  default     = true
}
