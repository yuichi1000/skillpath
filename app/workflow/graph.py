"""ADK Graph Workflow 定義 (設計書 §4.1)。

グラフ構成:

    START → intake → router(LLM) → dispatch ─ StringRoute
        ├ "assessment" → feedback_extract(LLM) → feedback_store ─┐
        ├ "register"   → ingestion_extract(LLM) → ingestion_store ─→ planner
        └ "query"      → query_stub                              │      │
    feedback_store → weakness_detector ─ has_weakness? ─ True ───┘      ▼
                                       └ False → report          scheduler → notifier

対象スキル集合 (設計書 §4.2「新規登録 or 弱点クラスタ」) は state の
target_skill_ids に統一: weakness_detector が弱点クラスタを、
ingestion_store が未習熟の新規スキルを設定し、planner はそれだけを見る。

ADK の約束事:
- ノード関数の引数は Workflow の state_schema (WorkflowState) から名前で束縛される
- `ctx.state[...] = ...` への書き込みが共有状態として永続化される
- 戻り値 Event の route (bool | int | str) がエッジの分岐条件とマッチングされる
- LLM ノードは single_turn で「直前ノードの出力」しか見ない → intake が原文を
  state に保存し、dispatch が原文を出力として後段 LLM へ渡す
"""

import logging
from datetime import datetime

from google.adk.events import Event
from google.adk.workflow import START, RetryConfig, Workflow, node
from google.genai import types
from pydantic import BaseModel, Field

from app.config import get_settings
from app.models.schemas import (
    CertProfile,
    FeedbackOutput,
    IngestionOutput,
    PlannerOutput,
    RouterOutput,
    WeaknessOutput,
)
from app.workflow import notifier as notifier_mod
from app.workflow.feedback import build_feedback_extractor, store_feedback
from app.workflow.ingestion import (
    build_cert_profiler,
    build_cert_researcher,
    build_cert_specialist,
    store_ingestion,
)
from app.workflow.planner import build_plan
from app.workflow.router import build_router
from app.workflow.scheduler import (
    SessionDraft,
    allocate_sessions,
    compute_free_slots,
    placeholder_free_slots,
)
from app.workflow.weakness import DEFAULT_THRESHOLD, detect_weakness

logger = logging.getLogger(__name__)


class WorkflowState(BaseModel):
    """グラフ全体の共有状態。"""

    uid: str = ""
    user_input: str = ""  # ユーザー入力の原文 (intake が保存し、後段 LLM へ渡す)
    attachment_b64: str = ""  # 添付 (模試の写真・PDF 等)。base64
    attachment_mime: str = ""
    assessment_id: str = ""
    threshold: float = DEFAULT_THRESHOLD
    deadline: str = ""  # 目標期限 (ISO 日付)。router が入力から抽出 or 直接指定
    schedule_start: str = ""  # ISO 日時。空なら now()。テストでの固定用
    router_output: RouterOutput | None = None
    feedback_output: FeedbackOutput | None = None
    cert_profile: CertProfile | None = None
    specialist: str = ""  # 動的生成されたスペシャリスト・エージェント名
    research_notes: str = ""  # リサーチャーによる公式情報の裏どり (出典URL含む)
    ingestion_output: IngestionOutput | None = None
    ingestion_counts: dict = Field(default_factory=dict)
    weakness: WeaknessOutput | None = None
    target_skill_ids: list[str] = Field(default_factory=list)
    plan_kind: str = "review"  # "initial" (新規登録) | "review" (復習)
    plan: PlannerOutput | None = None
    sessions: list[SessionDraft] = Field(default_factory=list)
    schedule_warnings: list[str] = Field(default_factory=list)
    calendar_synced: bool = False
    summary: str = ""


@node(name="intake")
def intake_node(ctx, node_input: str):
    ctx.state["user_input"] = node_input
    return node_input


def _content_with_attachment(text: str, attachment_b64: str, attachment_mime: str) -> types.Content:
    """テキスト + 添付 (画像/PDF) を1つの Content にまとめる。"""
    import base64

    parts = [types.Part(text=text)]
    if attachment_b64 and attachment_mime:
        parts.append(
            types.Part(
                inline_data=types.Blob(
                    mime_type=attachment_mime, data=base64.b64decode(attachment_b64)
                )
            )
        )
    return types.Content(role="user", parts=parts)


@node(name="dispatch")
def dispatch_node(
    ctx,
    router_output: RouterOutput,
    user_input: str,
    attachment_b64: str = "",
    attachment_mime: str = "",
):
    # LLM の分類結果 (構造化出力で検証済み) を StringRoute に変換する決定的ノード。
    # 出力は「原文 + 添付」— 次の LLM ノード (feedback/ingestion) の入力になるため
    if router_output.deadline:  # 空なら既存の設定 (state 直指定) を保持
        ctx.state["deadline"] = router_output.deadline
    content = _content_with_attachment(user_input, attachment_b64, attachment_mime)
    return Event(output=content, route=router_output.intent)


@node(name="query_stub")
def query_node(ctx):
    msg = "質問応答 (query) は未実装です。"
    ctx.state["summary"] = msg
    return msg


@node(name="ingestion_orchestrator", rerun_on_resume=True)
async def ingestion_orchestrator_node(
    ctx, user_input: str, attachment_b64: str = "", attachment_mime: str = ""
):
    """資格スペシャリストの動的生成 (設計の核)。

    ① Cert Profiler で登録対象の資格を特定 → ② リサーチャーが Google 検索で
    公式シラバスを裏どり → ③ その資格専用の抽出エージェントを実行時に合成し、
    原文 + 添付 + 調査メモを入力として動的実行する。
    """
    await ctx.run_node(build_cert_profiler(), user_input)
    profile = CertProfile.model_validate(ctx.state.get("cert_profile") or {})

    research = ""
    if profile.name:
        researcher = build_cert_researcher(profile)
        logger.info("リサーチャー起動: %s", researcher.name)
        try:
            await ctx.run_node(researcher, user_input)
            research = str(ctx.state.get("research_notes") or "")
        except Exception:  # noqa: BLE001 - 裏どり失敗は致命ではない
            logger.exception("公式情報の裏どりに失敗 (続行)")
            ctx.state["research_notes"] = "(公式情報の検索に失敗したため、貼り付け内容のみで構築)"

    specialist = build_cert_specialist(profile)
    ctx.state["specialist"] = specialist.name
    logger.info("動的スペシャリスト生成: %s (%s %s)", specialist.name, profile.vendor, profile.name)
    text = user_input
    if research:
        text += "\n\n[公式情報の調査メモ]\n" + research
    await ctx.run_node(
        specialist, _content_with_attachment(text, attachment_b64, attachment_mime)
    )
    return {"specialist": specialist.name, "cert": profile.name, "researched": bool(research)}


@node(name="ingestion_store")
def ingestion_store_node(
    ctx,
    uid: str,
    ingestion_output: IngestionOutput,
    threshold: float = DEFAULT_THRESHOLD,
    cert_profile: CertProfile | None = None,
):
    counts, targets = store_ingestion(uid, ingestion_output, threshold, cert=cert_profile)
    ctx.state["ingestion_counts"] = counts
    ctx.state["target_skill_ids"] = targets
    ctx.state["plan_kind"] = "initial"
    return {"counts": counts, "targets": targets}


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
    ctx.state["target_skill_ids"] = result.cluster
    ctx.state["plan_kind"] = "review"
    # route=bool が BoolRoute。True なら planner、False なら report へ
    return Event(output=result.model_dump(), route=result.has_weakness)


@node(name="planner")
def planner_node(ctx, uid: str, target_skill_ids: list[str]):
    result = build_plan(uid, target_skill_ids)
    ctx.state["plan"] = result.model_dump()
    return result.model_dump()


@node(name="scheduler")
def scheduler_node(
    ctx, uid: str, plan: PlannerOutput, plan_kind: str,
    schedule_start: str = "", deadline: str = "",
):
    from datetime import timedelta

    from app.tools import calendar_tool
    from app.tools.neo4j_tool import run_named
    from app.workflow import notifier as nmod

    start = datetime.fromisoformat(schedule_start) if schedule_start else datetime.now()
    settings = get_settings()
    calendar_ok = False
    warnings_extra: list[str] = []
    if settings.calendar_enabled:
        # 実カレンダーの busy を差し引いた空き時間 (設計書 §4.2 step 1-2)
        try:
            busy = calendar_tool.get_busy(start, start + timedelta(days=15))
            slots = compute_free_slots(start, busy)
            calendar_ok = True
        except calendar_tool.CalendarUnavailable as e:
            logger.warning("Calendar 未接続のためプレースホルダで続行: %s", e)
            warnings_extra.append("カレンダー未接続のため仮の空き時間で計画しています")
            slots = placeholder_free_slots(start)
    else:
        slots = placeholder_free_slots(start)

    plan_key = ctx.state.get("assessment_id") or f"{uid}-{plan_kind}"
    sessions, warnings = allocate_sessions(
        plan, slots, plan_key=plan_key, kind=plan_kind,
        deadline=datetime.fromisoformat(deadline) if deadline else None,
    )

    if calendar_ok and sessions:
        # イベント作成 → LearningSession を Neo4j に記録 (設計書 §4.2 step 6 / §5 冪等)
        names = {item.skill_id: item.name or item.skill_id for item in plan.plan}
        weakness = ctx.state.get("weakness")
        weakness_model = WeaknessOutput.model_validate(weakness) if weakness else None
        session_rows = []
        for s in sessions:
            summary, description = nmod.build_event_texts(
                s.skill_id, names, plan_kind, weakness_model
            )
            event_id = calendar_tool.upsert_event(
                s.session_id, summary, description, s.start, s.end
            )
            session_rows.append(
                {
                    "id": s.session_id,
                    "skill_id": s.skill_id,
                    "start": s.start.isoformat(),
                    "duration_min": int((s.end - s.start).total_seconds() // 60),
                    "event_id": event_id,
                    "kind": s.kind,
                    "resource_id": None,
                    "from_page": None,
                    "to_page": None,
                }
            )
        run_named("scheduler.cypher", "create_sessions", uid=uid, sessions=session_rows)
        ctx.state["calendar_synced"] = True

    ctx.state["sessions"] = [s.model_dump(mode="json") for s in sessions]
    ctx.state["schedule_warnings"] = warnings + warnings_extra
    return {
        "created_blocks": len(sessions),
        "calendar_synced": calendar_ok,
        "warnings": warnings + warnings_extra,
    }


@node(name="notifier")
def notifier_node(
    ctx,
    plan: PlannerOutput,
    sessions: list[SessionDraft],
    schedule_warnings: list[str],
    plan_kind: str,
    weakness: WeaknessOutput | None = None,
    deadline: str = "",
):
    summary = notifier_mod.build_summary(
        plan, sessions, schedule_warnings, weakness=weakness, kind=plan_kind, deadline=deadline
    )
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
        # LLM ノードは一時的な 429/5xx で即死させず、指数バックオフで最大3回試行する
        llm_retry = RetryConfig(max_attempts=3, initial_delay=2.0, backoff_factor=2.0)
        feedback_agent = node(build_feedback_extractor(), retry_config=llm_retry)
        router_agent = node(build_router(), retry_config=llm_retry)
        edges = [
            (
                START,
                intake_node,
                router_agent,
                dispatch_node,
                {
                    "assessment": feedback_agent,
                    "register": ingestion_orchestrator_node,
                    "query": query_node,
                },
            ),
            (feedback_agent, feedback_store_node, weakness_node),
            (ingestion_orchestrator_node, ingestion_store_node, planner_node),
            *branch,
        ]
    else:
        edges = [(START, weakness_node), *branch]
    return Workflow(name="skillpath", state_schema=WorkflowState, edges=edges)
