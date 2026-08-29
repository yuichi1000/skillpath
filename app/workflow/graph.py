"""ADK Graph Workflow 定義 (設計書 §4.1)。

現状は LLM を使わない assessment ブランチのみをグラフ化している:

    START → weakness_detector ─ has_weakness? ─ True  → planner → scheduler → notifier
                                              └ False → report

Router / Ingestion / Feedback (LLM ノード) は Gemini API キー設定後に追加する。
scheduler の空き時間は Calendar FreeBusy 接続まで placeholder_free_slots で代用。

ADK の約束事:
- ノード関数の引数は Workflow の state_schema (WorkflowState) から名前で束縛される
- `ctx.state[...] = ...` への書き込みが共有状態として永続化される
- 戻り値 Event の route (bool | int | str) がエッジの分岐条件とマッチングされる
"""

from datetime import datetime

from google.adk.events import Event
from google.adk.workflow import START, Workflow, node
from pydantic import BaseModel, Field

from app.models.schemas import PlannerOutput, WeaknessOutput
from app.workflow import notifier as notifier_mod
from app.workflow.planner import build_plan
from app.workflow.scheduler import SessionDraft, allocate_sessions, placeholder_free_slots
from app.workflow.weakness import DEFAULT_THRESHOLD, detect_weakness


class WorkflowState(BaseModel):
    """グラフ全体の共有状態。"""

    uid: str = ""
    assessment_id: str = ""
    threshold: float = DEFAULT_THRESHOLD
    schedule_start: str = ""  # ISO 日時。空なら now()。テストでの固定用
    weakness: WeaknessOutput | None = None
    plan: PlannerOutput | None = None
    sessions: list[SessionDraft] = Field(default_factory=list)
    schedule_warnings: list[str] = Field(default_factory=list)
    summary: str = ""


@node(name="weakness_detector")
def weakness_node(ctx, assessment_id: str, uid: str, threshold: float = DEFAULT_THRESHOLD):
    # 注意: WorkflowState の Pydantic デフォルトは実行時 state に自動注入されない。
    # ADK は「state 辞書 → 関数シグネチャのデフォルト」の順で束縛するため、
    # 省略可能なパラメータはここでデフォルトを持つ必要がある。
    result = detect_weakness(assessment_id, uid, threshold)
    ctx.state["weakness"] = result.model_dump()
    # route=bool が BoolRoute。True なら planner、False なら report へ
    return Event(output=result.model_dump(), route=result.has_weakness)


@node(name="planner")
def planner_node(ctx, uid: str, weakness: WeaknessOutput):
    result = build_plan(uid, weakness.cluster)
    ctx.state["plan"] = result.model_dump()
    return result.model_dump()


@node(name="scheduler")
def scheduler_node(ctx, assessment_id: str, plan: PlannerOutput, schedule_start: str = ""):
    start = datetime.fromisoformat(schedule_start) if schedule_start else datetime.now()
    # TODO(calendar): Calendar 接続後は placeholder を calendar_tool.get_freebusy に差し替え、
    # 作成した SessionDraft を upsert_event + scheduler.cypher (LearningSession) に流す
    slots = placeholder_free_slots(start)
    sessions, warnings = allocate_sessions(
        plan, slots, plan_key=assessment_id, kind="review"
    )
    ctx.state["sessions"] = [s.model_dump(mode="json") for s in sessions]
    ctx.state["schedule_warnings"] = warnings
    return {"created_blocks": len(sessions), "warnings": warnings}


@node(name="notifier")
def notifier_node(
    ctx,
    weakness: WeaknessOutput,
    plan: PlannerOutput,
    sessions: list[SessionDraft],
    schedule_warnings: list[str],
):
    summary = notifier_mod.build_summary(weakness, plan, sessions, schedule_warnings)
    ctx.state["summary"] = summary
    return summary


@node(name="report")
def report_node(ctx, weakness: WeaknessOutput):
    summary = notifier_mod.build_no_weakness_report(weakness)
    ctx.state["summary"] = summary
    return summary


def build_workflow() -> Workflow:
    return Workflow(
        name="skillpath",
        state_schema=WorkflowState,
        edges=[
            (START, weakness_node, {True: planner_node, False: report_node}),
            (planner_node, scheduler_node, notifier_node),
        ],
    )
