"""ADK Graph Workflow 定義 (設計書 §4.1)。

グラフ構成 (設計書 §4.1 のうち Ingestion 以外):

    START → router(LLM) → dispatch ─ StringRoute ┬ "assessment" → feedback_extract(LLM)
                                                 ├ "register" → register_stub    │
                                                 └ "query"    → query_stub       ▼
              ┌──────────────────────────────────────── feedback_store → weakness_detector
              └ has_weakness? ─ True  → planner → scheduler → notifier
                              └ False → report

Ingestion (LLM ノード) は未実装。Feedback の PDF/画像入力も未対応 (テキストのみ)。
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

from app.models.schemas import FeedbackOutput, PlannerOutput, RouterOutput, WeaknessOutput
from app.workflow import notifier as notifier_mod
from app.workflow.feedback import build_feedback_extractor, store_feedback
from app.workflow.planner import build_plan
from app.workflow.router import build_router
from app.workflow.scheduler import SessionDraft, allocate_sessions, placeholder_free_slots
from app.workflow.weakness import DEFAULT_THRESHOLD, detect_weakness


class WorkflowState(BaseModel):
    """グラフ全体の共有状態。"""

    uid: str = ""
    user_input: str = ""  # ユーザー入力の原文 (intake が保存し、feedback_extract 等へ渡す)
    assessment_id: str = ""
    threshold: float = DEFAULT_THRESHOLD
    schedule_start: str = ""  # ISO 日時。空なら now()。テストでの固定用
    router_output: RouterOutput | None = None
    feedback_output: FeedbackOutput | None = None
    weakness: WeaknessOutput | None = None
    plan: PlannerOutput | None = None
    sessions: list[SessionDraft] = Field(default_factory=list)
    schedule_warnings: list[str] = Field(default_factory=list)
    summary: str = ""


@node(name="intake")
def intake_node(ctx, node_input: str):
    # ADK の LLM ノードは single_turn で「直前ノードの出力」しか見ないため、
    # 入口で原文を state に保存しておく (後段の feedback_extract が使う)
    ctx.state["user_input"] = node_input
    return node_input


@node(name="dispatch")
def dispatch_node(ctx, router_output: RouterOutput, user_input: str):
    # LLM の分類結果 (構造化出力で検証済み) を StringRoute に変換する決定的ノード。
    # 出力は原文にする — 次の LLM ノード (feedback_extract) の入力になるため
    return Event(output=user_input, route=router_output.intent)


@node(name="register_stub")
def register_node(ctx):
    msg = "教材・資格の登録 (Ingestion Agent) は未実装です。"
    ctx.state["summary"] = msg
    return msg


@node(name="query_stub")
def query_node(ctx):
    msg = "質問応答 (query) は未実装です。"
    ctx.state["summary"] = msg
    return msg


@node(name="feedback_store")
def feedback_store_node(ctx, uid: str, feedback_output: FeedbackOutput):
    # 名寄せ + 決定的な assessment_id 採番 + Neo4j 書き込み (Assessment/ASSESSED/習熟度EMA)
    assessment_id = store_feedback(uid, feedback_output)
    ctx.state["assessment_id"] = assessment_id
    return {"assessment_id": assessment_id, "skills": len(feedback_output.per_skill)}


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


def build_workflow(with_router: bool = True) -> Workflow:
    """with_router=False は LLM を呼ばない決定的テスト用 (assessment ブランチ直行)。"""
    branch = [
        (weakness_node, {True: planner_node, False: report_node}),
        (planner_node, scheduler_node, notifier_node),
    ]
    if with_router:
        feedback_agent = build_feedback_extractor()
        edges = [
            (
                START,
                intake_node,
                build_router(),
                dispatch_node,
                {
                    "assessment": feedback_agent,
                    "register": register_node,
                    "query": query_node,
                },
            ),
            (feedback_agent, feedback_store_node, weakness_node),
            *branch,
        ]
    else:
        edges = [(START, weakness_node), *branch]
    return Workflow(name="skillpath", state_schema=WorkflowState, edges=edges)
