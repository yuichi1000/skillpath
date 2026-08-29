"""ADK Graph Workflow 定義 (設計書 §4.1)。

現状は LLM を使わない assessment ブランチのみをグラフ化している:

    START → weakness_detector ─ has_weakness? ─ True  → planner → notifier
                                              └ False → report

Router / Ingestion / Feedback (LLM ノード) は Gemini API キー設定後に追加する。

ADK の約束事:
- ノード関数の引数は Workflow の state_schema (WorkflowState) から名前で束縛される
- `ctx.state[...] = ...` への書き込みが共有状態として永続化される
- 戻り値 Event の route (bool | int | str) がエッジの分岐条件とマッチングされる
"""

from google.adk.events import Event
from google.adk.workflow import START, Workflow, node
from pydantic import BaseModel

from app.models.schemas import PlannerOutput, WeaknessOutput
from app.workflow import notifier as notifier_mod
from app.workflow.planner import build_plan
from app.workflow.weakness import DEFAULT_THRESHOLD, detect_weakness


class WorkflowState(BaseModel):
    """グラフ全体の共有状態。"""

    uid: str = ""
    assessment_id: str = ""
    threshold: float = DEFAULT_THRESHOLD
    weakness: WeaknessOutput | None = None
    plan: PlannerOutput | None = None
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


@node(name="notifier")
def notifier_node(ctx, weakness: WeaknessOutput, plan: PlannerOutput):
    summary = notifier_mod.build_summary(weakness, plan)
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
            (planner_node, notifier_node),
        ],
    )
