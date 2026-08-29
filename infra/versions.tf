terraform {
  required_version = ">= 1.9.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
    time = {
      source  = "hashicorp/time"
      version = "~> 0.12"
    }
  }

  # 設計書 §7.2 は GCS backend だが、ハッカソンでは bootstrap を減らすためローカル state。
  # チーム開発に移行する際は以下を有効化する (バケット名はプロジェクト固有にすること)。
  # backend "gcs" {
  #   bucket = "skillpath-design-tfstate"
  #   prefix = "infra"
  # }
}

provider "google" {
  project = var.project_id
  region  = var.region
}
