"""Scheduler — 学習ブロックの空き時間割り当て (設計書 §4.2)。

決定的部分のみ実装: Planner の計画を空き時間帯へ貪欲法で配置する。
空き時間の取得 (Calendar FreeBusy) とイベント作成 (upsert_event)、
LearningSession の Neo4j 書き込みは calendar_tool 実装後に接続する。

設計書との差分: 配置アルゴリズム step 4「前提が先の日時になるよう検証し、
違反したら再割り当て」は省略した。plan の順序どおりにカーソルを前進させる
貪欲法では、後のスキルが先の時刻に置かれることが構造的に起き得ないため。
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from pydantic import BaseModel

from app.models.schemas import PlannerOutput, SessionKind

TimeSlot = tuple[datetime, datetime]


@dataclass(frozen=True)
class ScheduleConfig:
    """ユーザーの学習ブロック設定 (設計書 step 2 のフィルタ条件)。"""

    max_block_minutes: int = 90
    min_block_minutes: int = 30


class SessionDraft(BaseModel):
    """カレンダー登録前の学習ブロック案。

    session_id はここで確定し、Calendar の extendedProperties
    (skillpath_session_id) と Neo4j の LearningSession.id になる冪等性の鍵。
    同じ入力からは常に同じ id が生成される。
    """

    session_id: str
    skill_id: str
    start: datetime
    end: datetime
    kind: SessionKind


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

    if deadline is not None and free_slots:
        overruns = [s for s in sessions if s.end > deadline]
        if overruns or placed_count < len(blocks):
            total_minutes = sum(b[1] for b in blocks)
            days = max((deadline - free_slots[0][0]).days, 1)
            hours_per_day = total_minutes / 60 / days
            warnings.append(
                f"期限 {deadline:%Y-%m-%d} に収まりません。"
                f"完了には1日あたり約 {hours_per_day:.1f} 時間の学習が必要です"
            )

    return sessions, warnings


def placeholder_free_slots(
    start: datetime, days: int = 14, hour_from: int = 20, hour_to: int = 22
) -> list[TimeSlot]:
    """Calendar FreeBusy 接続までの仮の空き時間 (翌日から days 日間、毎晩 hour_from-hour_to)。

    Calendar 接続時は、graph.py の scheduler ノードでこの呼び出しを
    calendar_tool.get_freebusy に差し替える。
    """
    base = (start + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return [
        (day.replace(hour=hour_from), day.replace(hour=hour_to))
        for day in (base + timedelta(days=i) for i in range(days))
    ]
