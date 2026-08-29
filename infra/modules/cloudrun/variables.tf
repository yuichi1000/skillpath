variable "project_id" {
  type = string
}

variable "region" {
  type = string
}

variable "container_image" {
  type = string
}

variable "workflow_sa_email" {
  type = string
}

variable "pubsub_invoker_sa_email" {
  type = string
}

variable "network_id" {
  type = string
}

variable "cloudrun_subnetwork_id" {
  type = string
}

variable "neo4j_internal_ip" {
  type = string
}

variable "neo4j_password_secret_id" {
  type = string
}

variable "gemini_model" {
  type = string
}

variable "gemini_model_extract" {
  type = string
}

variable "upload_bucket_name" {
  type = string
}

variable "allow_unauthenticated" {
  type = bool
}
