"""環境変数からの設定読み込み。Cloud Run では Terraform (modules/cloudrun) が注入する。"""

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

# ローカル開発用: .env を読み込む。override=True で常に .env の値が正
# (シェルに残った古い export に上書きされる事故を防ぐ)。
# 本番 (Cloud Run) には .env が無いので no-op となり、Terraform 注入の環境変数が使われる
load_dotenv(override=True)


@dataclass(frozen=True)
class Settings:
    neo4j_uri: str = field(default_factory=lambda: os.environ.get("NEO4J_URI", "bolt://localhost:7687"))
    neo4j_user: str = field(default_factory=lambda: os.environ.get("NEO4J_USER", "neo4j"))
    neo4j_password: str = field(default_factory=lambda: os.environ.get("NEO4J_PASSWORD", ""))
    project_id: str = field(default_factory=lambda: os.environ.get("GOOGLE_CLOUD_PROJECT", ""))
    gemini_model: str = field(
        default_factory=lambda: os.environ.get("GEMINI_MODEL", "gemini-3.5-flash")
    )
    gemini_model_extract: str = field(
        default_factory=lambda: os.environ.get("GEMINI_MODEL_EXTRACT", "gemini-3.5-flash")
    )
    upload_bucket: str = field(default_factory=lambda: os.environ.get("GCS_UPLOAD_BUCKET", ""))
    calendar_enabled: bool = field(
        default_factory=lambda: os.environ.get("CALENDAR_ENABLED", "").lower() == "true"
    )
    schedule_tz: str = field(default_factory=lambda: os.environ.get("SCHEDULE_TZ", "Asia/Tokyo"))
    weakness_threshold: float = 0.6


def get_settings() -> Settings:
    return Settings()
