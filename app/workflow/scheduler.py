"""Scheduler — 学習ブロックの空き時間割り当て (設計書 §4.2)。

決定的部分のみ実装: Planner の計画を空き時間帯へ貪欲法で配置する。
空き時間の取得 (Calendar FreeBusy) とイベント作成 (upsert_event)、
LearningSession の Neo4j 書き込みは calendar_tool 実装後に接続する。

設計書との差分: 配置アルゴリズム step 4「前提が先の日時になるよう検証し、
違反したら再割り当て」は省略した。plan の順序どおりにカーソルを前進させる
貪欲法では、後のスキルが先の時刻に置かれることが構造的に起き得ないため。
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.config import get_settings
from app.models.schemas import PlannerOutput, SessionDraft, SessionKind, WeaknessOutput
from app.tools.neo4j_tool import run_named

logger = logging.getLogger(__name__)

TimeSlot = tuple[datetime, datetime]


@dataclass(frozen=True)
class ScheduleConfig:
    """ユーザーの学習ブロック設定 (設計書 step 2 のフィルタ条件)。"""

    max_block_minutes: int = 90
    min_block_minutes: int = 30


def _split_into_blocks(
    plan: PlannerOutput, config: ScheduleConfig
) -> list[tuple[str, int, int]]:
    """各スキルの所要時間を (skill_id, 分, ブロック番号) に分割する。

    端数が min_block_minutes 未満になる場合は min まで切り上げる
    (短すぎるブロックはカレンダーに置く価値が薄いため)。
    """
    blocks: list[tuple[str, int, int]] = []
    for item in plan.plan:
        remaining = item.estimated_minutes
        block_no = 1
        while remaining > 0:
            duration = min(config.max_block_minutes, remaining)
            remaining -= duration
            blocks.append((item.skill_id, max(duration, config.min_block_minutes), block_no))
            block_no += 1
    return blocks


def allocate_sessions(
    plan: PlannerOutput,
    free_slots: list[TimeSlot],
    config: ScheduleConfig | None = None,
    *,
    plan_key: str,
    kind: SessionKind = "review",
    deadline: datetime | None = None,
) -> tuple[list[SessionDraft], list[str]]:
    """plan を空き時間帯へ前から貪欲に割り当てる。

    Args:
        plan: Planner の出力 (前提順ソート済み)
        free_slots: 空き時間帯のリスト (開始時刻順であること)
        plan_key: session_id の接頭辞 (例: assessment_id)。冪等性の鍵
        deadline: 期限 (資格試験日など)。超過時は警告を返す
    Returns:
        (配置済みブロック, 警告リスト)
    """
    config = config or ScheduleConfig()
    if deadline is not None:
        # ゴール逆算: 期限より後の空き時間は使わない (期限をまたぐスロットは切り詰める)
        free_slots = [
            (start, min(end, deadline)) for start, end in free_slots if start < deadline
        ]
    blocks = _split_into_blocks(plan, config)
    warnings: list[str] = []
    sessions: list[SessionDraft] = []

    slot_i = 0
    cursor: datetime | None = None  # 現在のスロット内での次の空き開始時刻
    placed_count = 0
    for skill_id, duration, block_no in blocks:
        placed = False
        while slot_i < len(free_slots):
            slot_start, slot_end = free_slots[slot_i]
            start = cursor if cursor is not None and cursor > slot_start else slot_start
            if slot_end - start >= timedelta(minutes=duration):
                end = start + timedelta(minutes=duration)
                sessions.append(
                    SessionDraft(
                        session_id=f"{plan_key}-{skill_id}-b{block_no}",
                        skill_id=skill_id,
                        start=start,
                        end=end,
                        kind=kind,
                    )
                )
                cursor = end
                placed = True
                break
            # このスロットには収まらない → 次のスロットへ (残り時間は捨てる)
            slot_i += 1
            cursor = None
        if not placed:
            break
        placed_count += 1

    if placed_count < len(blocks):
        unplaced = list(dict.fromkeys(b[0] for b in blocks[placed_count:]))
        warnings.append(
            f"空き時間が不足し {len(blocks) - placed_count} ブロックを配置できませんでした"
            f" (未配置スキル: {', '.join(unplaced)})"
        )

    if deadline is not None and placed_count < len(blocks):
        total_minutes = sum(b[1] for b in blocks)
        anchor = free_slots[0][0] if free_slots else None
        days = max((deadline - anchor).days, 1) if anchor else 1
        hours_per_day = total_minutes / 60 / days
        warnings.append(
            f"現在の空き時間では期限 {deadline:%Y-%m-%d} に収まりません。"
            f"全てを終えるには1日あたり約 {hours_per_day:.1f} 時間の学習時間が必要です"
        )

    return sessions, warnings


def placeholder_free_slots(
    start: datetime, days: int = 14, hour_from: int = 20, hour_to: int = 22
) -> list[TimeSlot]:
    """Calendar FreeBusy 接続までの仮の空き時間 (翌日から days 日間、毎晩 hour_from-hour_to)。

    Calendar 接続時は、graph.py の scheduler ノードでこの呼び出しを
    calendar_tool.get_busy に差し替える。
    """
    base = (start + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return [
        (day.replace(hour=hour_from), day.replace(hour=hour_to))
        for day in (base + timedelta(days=i) for i in range(days))
    ]


def compute_free_slots(
    start: datetime,
    busy: list[TimeSlot],
    days: int = 14,
    hour_from: int = 20,
    hour_to: int = 22,
) -> list[TimeSlot]:
    """毎日の学習可能ウィンドウから busy 区間を差し引いた空きスロットを返す (純粋関数)。

    Calendar FreeBusy の結果 (busy) と組み合わせて実際の空き時間を作る。
    設計書 §4.2 配置アルゴリズム step 1-2 に相当。
    """
    free: list[TimeSlot] = []
    for window_start, window_end in placeholder_free_slots(start, days, hour_from, hour_to):
        cursor = window_start
        for b_start, b_end in sorted(busy):
            if b_end <= cursor or b_start >= window_end:
                continue
            if b_start > cursor:
                free.append((cursor, min(b_start, window_end)))
            cursor = max(cursor, b_end)
            if cursor >= window_end:
                break
        if cursor < window_end:
            free.append((cursor, window_end))
    return free


def schedule_sessions(
    uid: str,
    plan: PlannerOutput,
    kind: SessionKind,
    start: datetime,
    plan_key: str,
    deadline: datetime | None = None,
    weakness: WeaknessOutput | None = None,
) -> tuple[list[SessionDraft], list[str], bool]:
    """空き時間の取得 → 割り当て → カレンダー登録 → LearningSession 記録までの一連。

    CALENDAR_ENABLED のときは実カレンダーを使い、失敗時はプレースホルダに
    フォールバック (ワークフローを止めない)。戻り値は (配置, 警告, カレンダー同期済みか)。
    """
    from app.tools import calendar_tool
    from app.workflow import notifier

    calendar_ok = False
    warnings_extra: list[str] = []
    if get_settings().calendar_enabled:
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

    sessions, warnings = allocate_sessions(
        plan, slots, plan_key=plan_key, kind=kind, deadline=deadline
    )

    if calendar_ok and sessions:
        # イベント作成 → LearningSession を Neo4j に記録 (設計書 §4.2 step 6 / §5 冪等)
        names = {item.skill_id: item.name or item.skill_id for item in plan.plan}
        session_rows = []
        for s in sessions:
            summary, description = notifier.build_event_texts(s.skill_id, names, kind, weakness)
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

    return sessions, warnings + warnings_extra, calendar_ok
