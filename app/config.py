"""環境変数からの設定読み込み。Cloud Run では Terraform (modules/cloudrun) が注入する。"""

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Settings:
    neo4j_uri: str = field(default_factory=lambda: os.environ.get("NEO4J_URI", "bolt://localhost:7687"))
    neo4j_user: str = field(default_factory=lambda: os.environ.get("NEO4J_USER", "neo4j"))
    neo4j_password: str = field(default_factory=lambda: os.environ.get("NEO4J_PASSWORD", ""))
    project_id: str = field(default_factory=lambda: os.environ.get("GOOGLE_CLOUD_PROJECT", ""))
    gemini_model: str = field(default_factory=lambda: os.environ.get("GEMINI_MODEL", "gemini-3.5-flash"))
    gemini_model_pro: str = field(default_factory=lambda: os.environ.get("GEMINI_MODEL_PRO", "gemini-3.5-pro"))
    upload_bucket: str = field(default_factory=lambda: os.environ.get("GCS_UPLOAD_BUCKET", ""))
    weakness_threshold: float = 0.6


def get_settings() -> Settings:
    return Settings()
