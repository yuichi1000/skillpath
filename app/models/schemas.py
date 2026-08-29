"""ワークフローノード間の入出力スキーマ (設計書 §4.2)。

外部由来テキスト（シラバス・論文・模試）からの LLM 抽出結果は、
Neo4j へ書き込む前に必ずこれらのスキーマで検証する (設計書 §6)。
"""

from typing import Literal

from pydantic import BaseModel, Field

Intent = Literal["register", "assessment", "query"]
Depth = Literal["intro", "standard", "deep"]
SessionKind = Literal["initial", "review"]


class RouterOutput(BaseModel):
    intent: Intent


# ---- Ingestion Agent ----

class SkillIn(BaseModel):
    id: str
    name: str
    description: str = ""
    estimated_hours: float = 0.0
    domain: str = ""
    aliases: list[str] = Field(default_factory=list)


class ResourceIn(BaseModel):
    id: str
    title: str
    type: Literal["book", "paper", "doc", "course"]
    url: str = ""
    pages: int | None = None
    estimated_hours: float = 0.0


class PrerequisiteIn(BaseModel):
    from_: str = Field(alias="from")
    to: str
    strength: float = Field(ge=0.0, le=1.0)


class CoversIn(BaseModel):
    resource_id: str
    skill_id: str
    depth: Depth
    section: str = ""


class IngestionOutput(BaseModel):
    skills: list[SkillIn]
    resources: list[ResourceIn]
    prerequisites: list[PrerequisiteIn]
    covers: list[CoversIn]


# ---- Feedback Agent ----

class PerSkillScore(BaseModel):
    skill_name: str
    skill_id: str | None = None  # 名寄せ後に設定
    correct: int
    total: int
    score: float = Field(ge=0.0, le=1.0)


class FeedbackOutput(BaseModel):
    assessment_id: str
    taken_at: str
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
    resource_id: str | None = None
    estimated_minutes: int
    section: str = ""


class PlannerOutput(BaseModel):
    plan: list[PlanItem]
    warnings: list[str] = Field(default_factory=list)  # 循環解消などの警告


# ---- Scheduler Agent ----

class CreatedEvent(BaseModel):
    event_id: str
    start: str
    end: str
    skill_id: str


class SchedulerOutput(BaseModel):
    created_events: list[CreatedEvent]
    warnings: list[str] = Field(default_factory=list)  # 期限に収まらない場合など
