"""ADK Graph Workflow 定義 (設計書 §4.1)。

グラフ構成:

    START → intake → router(LLM) → dispatch ─ StringRoute
        ├ "assessment" → feedback_extract(LLM) → feedback_store ──────┐
        ├ "register"   → ingestion_orchestrator(LLM) → ingestion_store │
        │                    has_scores? ─ True → score_handoff ───────┤ (原文を再送)
        │                                └ False ──────────────┐       │
        └ "query"      → query_stub                            │       ▼
                                                               │  weakness_detector
                                    計画対象あり ────────────────┴──── ┤
                                                                      ▼
                                    計画対象なし → report          planner → scheduler → notifier

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

from app.models.schemas import (
    CertProfile,
    FeedbackOutput,
    IngestionOutput,
    PlannerOutput,
    RouterOutput,
    SessionDraft,
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
from app.workflow.scheduler import schedule_sessions
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
    cert_id: str = ""  # 今回登録した資格 (複数資格の再配置で「自分」を識別する)
    cert_name: str = ""
    has_scores: bool = False  # 登録と同じ文面に模試の得点が含まれていた
    weakness: WeaknessOutput | None = None
    target_skill_ids: list[str] = Field(default_factory=list)
    plan_kind: str = "review"  # "initial" (新規登録) | "review" (復習)
    plan: PlannerOutput | None = None
    sessions: list[SessionDraft] = Field(default_factory=list)
    schedule_warnings: list[str] = Field(default_factory=list)
    calendar_synced: bool = False
    calendar_links: dict = Field(default_factory=dict)  # 資格ID → カレンダー公開URL
    summary: str = ""


@node(name="intake")
def intake_node(ctx, node_input: str, attachment_b64: str = "", attachment_mime: str = ""):
    """原文を state に保存し、添付を付け直して Router へ渡す。

    ADK の自動変換は str 引数に対して inline_data を落とすため、ここで
    復元しないと Router が添付を見られない。模試の写真だけを添えて
    「この資格を登録して」と言われたとき、得点の存在を判定できなくなる。
    """
    ctx.state["user_input"] = node_input
    return _content_with_attachment(node_input, attachment_b64, attachment_mime)


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
    # 明示指定 (API 引数) > 本文からの抽出 の優先順位。空なら既存の値を保持する
    if router_output.deadline and not ctx.state.get("deadline"):
        ctx.state["deadline"] = router_output.deadline
    if router_output.start_date and not ctx.state.get("schedule_start"):
        ctx.state["schedule_start"] = router_output.start_date
    ctx.state["has_scores"] = router_output.has_scores
    logger.info(
        "router: intent=%s deadline=%r start=%r has_scores=%s attachment=%s",
        router_output.intent, router_output.deadline, router_output.start_date,
        router_output.has_scores, bool(attachment_b64),
    )
    content = _content_with_attachment(user_input, attachment_b64, attachment_mime)
    return Event(output=content, route=router_output.intent)


@node(name="query_stub")
def query_node(ctx):
    msg = (
        "質問応答としては受け取りましたが、この入力からは登録も採点も行いませんでした。\n\n"
        "・資格を登録するなら「〇〇を受験します。試験日は〇年〇月〇日です」\n"
        "・模試を反映するなら、分野ごとの得点が読み取れるテキストか画像"
    )
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
    deadline: str = "",
    schedule_start: str = "",
    has_scores: bool = False,
):
    # 試験日は (User)-[:PURSUES]->(Certification) に永続化する。
    # これが無いと次回以降の実行で資格間の期限を比較できない (EDF の前提)。
    counts, targets, cert_id = store_ingestion(
        uid,
        ingestion_output,
        threshold,
        cert=cert_profile,
        deadline=deadline,
        start_date=schedule_start[:10],
    )
    ctx.state["ingestion_counts"] = counts
    ctx.state["target_skill_ids"] = targets
    ctx.state["plan_kind"] = "initial"
    ctx.state["cert_id"] = cert_id
    ctx.state["cert_name"] = cert_profile.name if cert_profile else ""
    if not counts["skills"]:
        logger.warning("登録経路だがスキルを1件も抽出できなかった (cert=%r)", cert_id)
    # 登録の文面に模試の得点が混ざっていたら、そのまま採点処理へ回す。
    # (シラバスと最新の模試を一度に貼るのは自然な使い方で、
    #  登録だけ処理して得点を捨てると「反映されない」ように見える)
    # 添付があるときは Router の判定に関わらず模試抽出を通す。
    # 得点が無ければ feedback_store が何も書かずに素通りする (決定的な保険)。
    return Event(
        output={"counts": counts, "targets": targets, "cert_id": cert_id},
        route=bool(has_scores or ctx.state.get("attachment_b64")),
    )


@node(name="feedback_store")
def feedback_store_node(ctx, uid: str, feedback_output: FeedbackOutput):
    # 名寄せ + 決定的な assessment_id 採番 + Neo4j 書き込み (Assessment/ASSESSED/習熟度EMA)。
    # 得点が1件も無いなら空の Assessment を作らずに素通りする
    # (登録に添付が付いていただけ、というケース)。
    if not feedback_output.per_skill:
        ctx.state["assessment_id"] = ""
        return {"assessment_id": "", "skills": 0}
    assessment_id = store_feedback(uid, feedback_output)
    ctx.state["assessment_id"] = assessment_id
    return {"assessment_id": assessment_id, "skills": len(feedback_output.per_skill)}


@node(name="decline")
def decline_node(ctx, router_output: RouterOutput):
    """このアプリの範囲外・応じるべきでない依頼を、何も書き込まずに断る終端。

    判定自体は LLM だが、この経路は Neo4j にもカレンダーにも一切触れないので、
    誤判定した場合の影響は「断ってしまう」側にしか出ない (fail-safe)。
    """
    ctx.state["summary"] = notifier_mod.build_decline_report(
        router_output.refusal, router_output.reason
    )
    logger.info("依頼を辞退: refusal=%s reason=%r", router_output.refusal, router_output.reason)
    return ctx.state["summary"]


@node(name="score_handoff")
def score_handoff_node(ctx, user_input: str, attachment_b64: str = "", attachment_mime: str = ""):
    """登録経路から模試抽出へ渡すための原文再送 (LLM ノードは直前の出力しか見ない)。"""
    return _content_with_attachment(user_input, attachment_b64, attachment_mime)


@node(name="weakness_detector")
def weakness_node(ctx, uid: str, assessment_id: str = "", threshold: float = DEFAULT_THRESHOLD):
    # 注意: WorkflowState の Pydantic デフォルトは実行時 state に自動注入されない。
    # ADK は「state 辞書 → 関数シグネチャのデフォルト」の順で束縛するため、
    # 省略可能なパラメータはここでデフォルトを持つ必要がある。
    result = (
        detect_weakness(assessment_id, uid, threshold)
        if assessment_id
        else WeaknessOutput(weak_skills=[], cluster=[], has_weakness=False)
    )
    ctx.state["weakness"] = result.model_dump()
    # 登録と同時に模試が来た場合、新規登録スキルと弱点クラスタの両方を計画対象にする
    targets = sorted(set(ctx.state.get("target_skill_ids") or []) | set(result.cluster))
    ctx.state["target_skill_ids"] = targets
    if result.has_weakness:
        ctx.state["plan_kind"] = "review"
    # route=bool が BoolRoute。計画対象があれば planner、無ければ report へ
    return Event(output=result.model_dump(), route=bool(targets))


@node(name="planner")
def planner_node(ctx, uid: str, target_skill_ids: list[str]):
    result = build_plan(uid, target_skill_ids)
    ctx.state["plan"] = result.model_dump()
    return result.model_dump()


def _start_from(schedule_start: str) -> datetime:
    """計画の起点を決める: 指定があればその日、無ければ今日 (= 翌日から配置)。

    過去日の指定は今日に丸める (already-past な開始日で枠を無駄にしないため)。
    """
    now = datetime.now()
    if not schedule_start:
        return now
    try:
        start = datetime.fromisoformat(schedule_start)
    except ValueError:
        return now
    return max(start, now)


@node(name="scheduler")
def scheduler_node(
    ctx, uid: str, plan: PlannerOutput, plan_kind: str,
    schedule_start: str = "", deadline: str = "",
    cert_id: str = "", cert_name: str = "", threshold: float = DEFAULT_THRESHOLD,
):
    weakness = ctx.state.get("weakness")
    # plan_key に資格を含める: 含めないと資格をまたぐ共有スキルの session_id が
    # 衝突し、先に立てた予定が上書きされてしまう
    plan_key = ctx.state.get("assessment_id") or f"{uid}-{cert_id or plan_kind}-{plan_kind}"
    sessions, warnings, calendar_ok, links = schedule_sessions(
        uid=uid,
        plan=plan,
        kind=plan_kind,
        start=_start_from(schedule_start),
        plan_key=plan_key,
        deadline=datetime.fromisoformat(deadline) if deadline else None,
        weakness=WeaknessOutput.model_validate(weakness) if weakness else None,
        cert_id=cert_id,
        label=cert_name,
        threshold=threshold,
    )
    ctx.state["calendar_synced"] = calendar_ok
    ctx.state["calendar_links"] = links
    ctx.state["sessions"] = [s.model_dump(mode="json") for s in sessions]
    ctx.state["schedule_warnings"] = warnings
    return {"created_blocks": len(sessions), "calendar_synced": calendar_ok, "warnings": warnings}


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
        plan,
        sessions,
        schedule_warnings,
        weakness=weakness,
        kind=plan_kind,
        deadline=deadline,
        calendar_links=ctx.state.get("calendar_links") or {},
        coverage=(ctx.state.get("ingestion_counts") or {}).get("by_domain"),
    )
    ctx.state["summary"] = summary
    return summary


@node(name="report")
def report_node(ctx, weakness: WeaknessOutput, assessment_id: str = ""):
    summary = notifier_mod.build_no_weakness_report(weakness, assessment_id)
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
                    "declined": decline_node,
                },
            ),
            (feedback_agent, feedback_store_node, weakness_node),
            (ingestion_orchestrator_node, ingestion_store_node),
            # 登録の文面に得点があれば模試処理へ合流、無ければそのまま計画へ
            (ingestion_store_node, {True: score_handoff_node, False: planner_node}),
            (score_handoff_node, feedback_agent),
            *branch,
        ]
    else:
        edges = [(START, weakness_node), *branch]
    return Workflow(name="skillpath", state_schema=WorkflowState, edges=edges)
