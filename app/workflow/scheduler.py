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


@dataclass(frozen=True)
class PlanGroup:
    """1本のタイムラインに載せる計画単位 (資格ごとの初期計画 / 模試由来の復習計画)。"""

    plan_key: str  # session_id の接頭辞。冪等性の鍵
    plan: PlannerOutput
    kind: SessionKind = "review"
    deadline: datetime | None = None
    cert_id: str = ""
    label: str = ""  # 警告文に出す表示名 (資格名など)


FAR_FUTURE = datetime(9999, 12, 31)


def allocate_groups(
    groups: list[PlanGroup],
    free_slots: list[TimeSlot],
    config: ScheduleConfig | None = None,
) -> tuple[list[SessionDraft], list[str]]:
    """複数の計画を1本の空き時間タイムラインへ EDF (最早期限優先) で配置する。

    期限が近いグループから順に手前の枠を取る。単一の資源 (ユーザーの時間) に
    対する EDF は「間に合う割り当てが存在するなら必ず間に合わせる」ことが
    保証されているため、資格間の優先順位はこれで決める。期限が同じ場合は
    復習 (弱点の穴埋め) を新規学習より先に置く。

    同じスキルが複数グループに現れたら、優先度の高い方にだけ残す
    (資格をまたいで共有される前提スキルを二重に学習しないため)。

    あるグループが自分の期限までに置ききれなくなっても、そこで消費しなかった
    枠は後続グループがそのまま使える (カーソルを進めずに次のグループへ移る)。
    """
    config = config or ScheduleConfig()
    ordered = sorted(
        groups, key=lambda g: (g.deadline or FAR_FUTURE, 0 if g.kind == "review" else 1)
    )
    sessions: list[SessionDraft] = []
    warnings: list[str] = []
    seen_skills: set[str] = set()

    slot_i = 0
    cursor: datetime | None = None  # 現在のスロット内での次の空き開始時刻
    for group in ordered:
        items = [i for i in group.plan.plan if i.skill_id not in seen_skills]
        seen_skills.update(i.skill_id for i in items)
        warnings.extend(group.plan.warnings)
        blocks = _split_into_blocks(PlannerOutput(plan=items), config)
        placed_count = 0
        for skill_id, duration, block_no in blocks:
            placed = False
            while slot_i < len(free_slots):
                slot_start, slot_end = free_slots[slot_i]
                start = cursor if cursor is not None and cursor > slot_start else slot_start
                if group.deadline is not None and start >= group.deadline:
                    break  # 期限切れ: カーソルは進めない (残り枠は後続グループが使う)
                limit = min(slot_end, group.deadline) if group.deadline else slot_end
                if limit - start >= timedelta(minutes=duration):
                    sessions.append(
                        SessionDraft(
                            session_id=f"{group.plan_key}-{skill_id}-b{block_no}",
                            skill_id=skill_id,
                            start=start,
                            end=start + timedelta(minutes=duration),
                            kind=group.kind,
                            cert_id=group.cert_id,
                        )
                    )
                    cursor = start + timedelta(minutes=duration)
                    placed = True
                    break
                # このスロットには収まらない → 次のスロットへ (残り時間は捨てる)
                slot_i += 1
                cursor = None
            if not placed:
                break
            placed_count += 1

        if placed_count < len(blocks):
            warnings.extend(_shortfall_warnings(group, blocks, placed_count, free_slots))

    return sessions, warnings


def _shortfall_warnings(
    group: PlanGroup,
    blocks: list[tuple[str, int, int]],
    placed_count: int,
    free_slots: list[TimeSlot],
) -> list[str]:
    """置ききれなかったブロックについての警告文を作る。"""
    label = f"「{group.label}」の" if group.label else ""
    unplaced = list(dict.fromkeys(b[0] for b in blocks[placed_count:]))
    out = [
        f"{label}空き時間が不足し {len(blocks) - placed_count} ブロックを配置できませんでした"
        f" (未配置スキル: {', '.join(unplaced)})"
    ]
    if group.deadline is not None:
        total_minutes = sum(b[1] for b in blocks)
        anchor = free_slots[0][0] if free_slots else None
        days = max((group.deadline - anchor).days, 1) if anchor else 1
        out.append(
            f"現在の空き時間では{label}期限 {group.deadline:%Y-%m-%d} に収まりません。"
            f"全てを終えるには1日あたり約 {total_minutes / 60 / days:.1f} 時間の学習時間が必要です"
        )
    return out


def allocate_sessions(
    plan: PlannerOutput,
    free_slots: list[TimeSlot],
    config: ScheduleConfig | None = None,
    *,
    plan_key: str,
    kind: SessionKind = "review",
    deadline: datetime | None = None,
) -> tuple[list[SessionDraft], list[str]]:
    """単一の計画を配置する (allocate_groups の1グループ版)。"""
    return allocate_groups(
        [PlanGroup(plan_key=plan_key, plan=plan, kind=kind, deadline=deadline)],
        free_slots,
        config,
    )


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


def _parse_date(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value) if value else None
    except ValueError:
        return None


def other_pursuit_groups(uid: str, exclude_cert_id: str, threshold: float) -> list[PlanGroup]:
    """今回の実行以外に、ユーザーが目指している資格の未消化分を計画し直す。

    再計画は決定的処理のみ (Cypher + トポロジカルソート) で LLM は呼ばない。
    習熟済みになったスキルは cert_unmastered_targets の時点で落ちるので、
    実行のたびに「今の実力から見た残り」に更新される。
    """
    from app.workflow.planner import build_plan

    groups: list[PlanGroup] = []
    for row in run_named("scheduler.cypher", "user_pursuits", uid=uid):
        if row["cert_id"] == exclude_cert_id:
            continue
        rows = run_named(
            "scheduler.cypher",
            "cert_unmastered_targets",
            uid=uid,
            cert_id=row["cert_id"],
            threshold=threshold,
        )
        targets = rows[0]["ids"] if rows else []
        if not targets:
            continue
        plan = build_plan(uid, targets)
        if not plan.plan:
            continue
        groups.append(
            PlanGroup(
                plan_key=f"{uid}-{row['cert_id']}-initial",
                plan=plan,
                kind="initial",
                deadline=_parse_date(row["deadline"]),
                cert_id=row["cert_id"],
                label=row["name"],
            )
        )
    return groups


def schedule_sessions(
    uid: str,
    plan: PlannerOutput,
    kind: SessionKind,
    start: datetime,
    plan_key: str,
    deadline: datetime | None = None,
    weakness: WeaknessOutput | None = None,
    cert_id: str = "",
    label: str = "",
    threshold: float = 0.7,
) -> tuple[list[SessionDraft], list[str], bool]:
    """空き時間の取得 → 全資格の一括再配置 → カレンダー登録 → LearningSession 記録。

    今回の計画だけを空きに詰めるのではなく、ユーザーが目指している他の資格の
    未消化分もまとめて EDF で組み直す。こうしないと「あとから登録した、より
    期限の近い資格」が先に登録した資格の後ろに回ってしまう。

    CALENDAR_ENABLED のときは実カレンダーを使い、失敗時はプレースホルダに
    フォールバック (ワークフローを止めない)。戻り値は (配置, 警告, カレンダー同期済みか)。
    """
    from app.tools import calendar_tool
    from app.workflow import notifier

    groups = [
        PlanGroup(
            plan_key=plan_key,
            plan=plan,
            kind=kind,
            deadline=deadline,
            cert_id=cert_id,
            label=label,
        )
    ]
    groups += other_pursuit_groups(uid, exclude_cert_id=cert_id, threshold=threshold)

    window_end = start + timedelta(days=15)
    calendar_ok = False
    warnings_extra: list[str] = []
    if get_settings().calendar_enabled:
        try:
            # own_prefix: この uid の学習ブロックはすべて組み直す対象なので空きとして扱う。
            # 他の予定 (会議・私用) はそのまま busy。
            busy = calendar_tool.get_busy(start, window_end, own_prefix=f"{uid}-")
            slots = compute_free_slots(start, busy)
            calendar_ok = True
        except calendar_tool.CalendarUnavailable as e:
            logger.warning("Calendar 未接続のためプレースホルダで続行: %s", e)
            warnings_extra.append("カレンダー未接続のため仮の空き時間で計画しています")
            slots = placeholder_free_slots(start)
    else:
        slots = placeholder_free_slots(start)

    sessions, warnings = allocate_groups(groups, slots)
    if len(groups) > 1:
        others = "・".join(g.label or g.cert_id for g in groups[1:])
        warnings_extra.append(f"他に進行中の資格 ({others}) の予定と重ならないよう調整しました")

    if calendar_ok and sessions:
        # イベント作成 → LearningSession を Neo4j に記録 (設計書 §4.2 step 6 / §5 冪等)
        names = {i.skill_id: i.name or i.skill_id for g in groups for i in g.plan.plan}
        session_rows = []
        for sess in sessions:
            summary, description = notifier.build_event_texts(
                sess.skill_id, names, sess.kind, weakness
            )
            event_id = calendar_tool.upsert_event(
                sess.session_id, summary, description, sess.start, sess.end
            )
            session_rows.append(
                {
                    "id": sess.session_id,
                    "skill_id": sess.skill_id,
                    "start": sess.start.isoformat(),
                    "duration_min": int((sess.end - sess.start).total_seconds() // 60),
                    "event_id": event_id,
                    "kind": sess.kind,
                    "resource_id": None,
                    "from_page": None,
                    "to_page": None,
                }
            )
        run_named("scheduler.cypher", "create_sessions", uid=uid, sessions=session_rows)

        # 組み直しで不要になった過去のブロックを片付ける (空リストでは絶対に呼ばない)
        keep = {sess.session_id for sess in sessions}
        try:
            removed = calendar_tool.delete_orphan_events(f"{uid}-", keep, start, window_end)
            if removed:
                logger.info("再配置で不要になった学習ブロックを %d 件削除", removed)
        except Exception:  # noqa: BLE001 - 掃除の失敗で計画自体を落とさない
            logger.exception("不要ブロックの削除に失敗 (続行)")
        run_named(
            "scheduler.cypher", "delete_orphan_sessions", uid=uid, keep_ids=sorted(keep)
        )

    return sessions, warnings + warnings_extra, calendar_ok
