"""ワークフローノード間の入出力スキーマ (設計書 §4.2)。

外部由来テキスト（シラバス・論文・模試）からの LLM 抽出結果は、
Neo4j へ書き込む前に必ずこれらのスキーマで検証する (設計書 §6)。
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Intent = Literal["register", "assessment", "query"]
Depth = Literal["intro", "standard", "deep"]
SessionKind = Literal["initial", "review"]


class RouterOutput(BaseModel):
    intent: Intent
    deadline: str = ""  # 入力に試験日・目標日があれば ISO 日付 (YYYY-MM-DD)、無ければ空
    start_date: str = ""  # 「来週から」等の学習開始希望日。無ければ空 (= 最速で開始)

    @field_validator("deadline", "start_date", mode="before")
    @classmethod
    def _none_to_empty(cls, v):
        return v or ""


# ---- Ingestion Agent ----

# LLM 抽出の入力スキーマ群は「出力は必ず揺れる」前提で寛容に定義する:
# null は既定値へ、未知の列挙値は安全なデフォルトへ正規化し、範囲外はクランプする。
# 厳格に守らせるのは構造 (フィールド構成) と、名寄せに使う name/title の存在のみ。


class SkillIn(BaseModel):
    id: str = ""  # LLM には発明させず、空なら store 側で名前から名寄せ・採番する
    name: str
    description: str = ""
    estimated_hours: float | None = None
    domain: str = ""
    aliases: list[str] = Field(default_factory=list)

    @field_validator("id", "description", "domain", mode="before")
    @classmethod
    def _none_to_empty(cls, v):
        return v or ""


class ResourceIn(BaseModel):
    id: str = ""  # 同上。store 側でタイトルから採番
    title: str
    type: str = "doc"
    url: str = ""
    pages: int | None = None
    estimated_hours: float | None = None

    @field_validator("id", "url", mode="before")
    @classmethod
    def _none_to_empty(cls, v):
        return v or ""

    @field_validator("type", mode="before")
    @classmethod
    def _normalize_type(cls, v):
        v = (v or "").lower()
        return v if v in ("book", "paper", "doc", "course") else "doc"


class PrerequisiteIn(BaseModel):
    # LLM への JSON スキーマはフィールド名 (from_) で生成されるため、
    # alias の "from" とフィールド名の "from_" の両方を受け付ける
    model_config = ConfigDict(populate_by_name=True)

    from_: str = Field(alias="from")
    to: str
    strength: float = 0.5

    @field_validator("strength", mode="before")
    @classmethod
    def _clamp(cls, v):
        try:
            return min(1.0, max(0.0, float(v)))
        except (TypeError, ValueError):
            return 0.5


class CoversIn(BaseModel):
    resource_id: str
    skill_id: str
    depth: str = "standard"
    section: str = ""

    @field_validator("depth", mode="before")
    @classmethod
    def _normalize_depth(cls, v):
        v = (v or "").lower()
        return v if v in ("intro", "standard", "deep") else "standard"

    @field_validator("section", mode="before")
    @classmethod
    def _none_to_empty(cls, v):
        return v or ""


class IngestionOutput(BaseModel):
    # LLM が「該当なし」のリストを省略したり null を返しても受ける
    skills: list[SkillIn] = Field(default_factory=list)
    resources: list[ResourceIn] = Field(default_factory=list)
    prerequisites: list[PrerequisiteIn] = Field(default_factory=list)
    covers: list[CoversIn] = Field(default_factory=list)

    @field_validator("skills", "resources", "prerequisites", "covers", mode="before")
    @classmethod
    def _none_to_list(cls, v):
        return v or []


class CertProfile(BaseModel):
    """Cert Profiler の出力: 登録対象の資格の特定結果。"""

    name: str = ""  # 例: "Professional Data Engineer"。特定できなければ空
    vendor: str = ""  # 例: "Google Cloud"

    @field_validator("name", "vendor", mode="before")
    @classmethod
    def _none_to_empty(cls, v):
        return v or ""


# ---- Feedback Agent ----

class PerSkillScore(BaseModel):
    skill_name: str
    skill_id: str | None = None  # 名寄せ後に設定
    correct: int
    total: int
    score: float = Field(ge=0.0, le=1.0)

    @field_validator("correct", "total", mode="before")
    @classmethod
    def _non_negative(cls, v):
        try:
            return max(0, int(v))
        except (TypeError, ValueError):
            return 0

    @field_validator("score", mode="before")
    @classmethod
    def _clamp_score(cls, v):
        try:
            return min(1.0, max(0.0, float(v)))
        except (TypeError, ValueError):
            return 0.0


class FeedbackOutput(BaseModel):
    assessment_id: str = ""  # LLM には決めさせない。feedback_store が uid+taken_at から決定的に生成
    taken_at: str = ""
    source: str = ""
    total_score: float
    per_skill: list[PerSkillScore]


# ---- Weakness Detector ----

class UnmasteredPrerequisite(BaseModel):
    id: str
    name: str
    mastery: float


class WeakSkill(BaseModel):
    weak_skill_id: str
    weak_skill_name: str
    score: float
    unmastered_prerequisites: list[UnmasteredPrerequisite]


class WeaknessOutput(BaseModel):
    weak_skills: list[WeakSkill]
    cluster: list[str]  # 弱点＋未習熟前提の skill_id 集合
    has_weakness: bool


# ---- Planner Agent ----

class PlanItem(BaseModel):
    order: int
    skill_id: str
    name: str = ""  # 表示用スキル名 (Planner が部分グラフから設定)
    resource_id: str | None = None
    estimated_minutes: int
    section: str = ""


class PlannerOutput(BaseModel):
    plan: list[PlanItem]
    warnings: list[str] = Field(default_factory=list)  # 循環解消などの警告


# ---- Scheduler Agent ----

class SessionDraft(BaseModel):
    """カレンダー登録前の学習ブロック案 (Scheduler の出力単位)。

    session_id は Calendar の extendedProperties (skillpath_session_id) と
    Neo4j の LearningSession.id になる冪等性の鍵。同じ入力からは常に同じ id。
    """

    session_id: str
    skill_id: str
    skill_name: str = ""  # 表示用。所見・イベント名で skill_id が露出しないように
    start: datetime
    end: datetime
    kind: SessionKind
    cert_id: str = ""  # このブロックが属する資格 (カレンダーの振り分けに使う)


class CreatedEvent(BaseModel):
    event_id: str
    start: str
    end: str
    skill_id: str


class SchedulerOutput(BaseModel):
    created_events: list[CreatedEvent]
    warnings: list[str] = Field(default_factory=list)  # 期限に収まらない場合など
