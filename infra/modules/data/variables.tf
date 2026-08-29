variable "project_id" {
  type = string
}

variable "region" {
  type = string
}

variable "workflow_url" {
  type = string
}

variable "pubsub_invoker_sa_email" {
  type = string
}

variable "enable_push" {
  type    = bool
  default = false
}
