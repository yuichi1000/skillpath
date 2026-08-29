resource "random_password" "neo4j" {
  length  = 24
  special = false # 設計書からの差分: URL や sed で事故らないよう英数字のみに
}

resource "google_secret_manager_secret" "neo4j_password" {
  secret_id = "skillpath-neo4j-password"

  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_version" "neo4j_password" {
  secret      = google_secret_manager_secret.neo4j_password.id
  secret_data = random_password.neo4j.result
}

# 設計書 §7.9 から移動: VM は Secret Manager からパスワードを読むだけ
resource "google_secret_manager_secret_iam_member" "neo4j_password_reader" {
  secret_id = google_secret_manager_secret.neo4j_password.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${var.sa_email}"
}

resource "google_compute_disk" "neo4j_data" {
  name = "skillpath-neo4j-data"
  type = "pd-ssd"
  zone = var.zone
  size = var.disk_size_gb
}

resource "google_compute_instance" "neo4j" {
  name           = "skillpath-neo4j"
  machine_type   = var.machine_type
  zone           = var.zone
  tags           = ["neo4j"]
  desired_status = var.desired_status

  boot_disk {
    initialize_params {
      image = "debian-cloud/debian-12"
      size  = 20
    }
  }

  attached_disk {
    source      = google_compute_disk.neo4j_data.id
    device_name = "neo4j-data"
  }

  network_interface {
    subnetwork = var.subnetwork_id
    # 外部IPを付与しない (Cloud NAT 経由で外部通信)
  }

  service_account {
    email  = var.sa_email
    scopes = ["cloud-platform"]
  }

  metadata_startup_script = templatefile("${path.module}/startup.sh.tpl", {
    password_secret_id = google_secret_manager_secret.neo4j_password.secret_id
    project_id         = var.project_id
  })

  shielded_instance_config {
    enable_secure_boot          = true
    enable_vtpm                 = true
    enable_integrity_monitoring = true
  }

  # 設計書からの差分: NAT 完成前に起動すると startup script の apt が失敗するため
  depends_on = [terraform_data.nat_ready]
}

resource "terraform_data" "nat_ready" {
  input = var.nat_id
}
