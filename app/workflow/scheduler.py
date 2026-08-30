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
from app.models.schemas import PlanItem, PlannerOutput, SessionDraft, SessionKind, WeaknessOutput
from app.tools.neo4j_tool import run_named

logger = logging.getLogger(__name__)

TimeSlot = tuple[datetime, datetime]


@dataclass(frozen=True)
class ScheduleConfig:
    """ユーザーの学習ブロック設定 (設計書 step 2 のフィルタ条件)。"""

    max_block_minutes: int = 90
    min_block_minutes: int = 30

    def __post_init__(self) -> None:
        # 0 や負の値を許すと「1回で1分も進まない」状態になり、割り当てが停止しない。
        # 設定ミスは黙って回り続けるより、その場で落とす。
        if self.min_block_minutes < 1:
            raise ValueError("min_block_minutes は 1 以上である必要があります")
        if self.max_block_minutes < self.min_block_minutes:
            raise ValueError("max_block_minutes は min_block_minutes 以上である必要があります")


def _split_into_blocks(
    plan: PlannerOutput, config: ScheduleConfig
) -> list[tuple[str, int, int, str]]:
    """各スキルの所要時間を (skill_id, 分, ブロック番号) に分割する。

    端数が min_block_minutes 未満になる場合は min まで切り上げる
    (短すぎるブロックはカレンダーに置く価値が薄いため)。
    """
    blocks: list[tuple[str, int, int, str]] = []
    for item in plan.plan:
        remaining = item.estimated_minutes
        block_no = 1
        while remaining > 0:
            duration = max(1, min(config.max_block_minutes, remaining))  # 必ず前進する
            remaining -= duration
            blocks.append(
                (
                    item.skill_id,
                    max(duration, config.min_block_minutes),
                    block_no,
                    item.name or item.skill_id,
                )
            )
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
MAX_HORIZON_DAYS = 120  # 計画を先に伸ばす上限 (遠すぎる試験日で暴走させない)


MIN_TAIL_MINUTES = 15  # 端数として置く最小の長さ。これ未満なら次の枠へ送る


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

    枠の端数も使い切る: 90分ブロックが入らない残り時間でも、最小ブロック以上
    あればそこまで進めて続きを翌日に回す。端数を捨てると、圧縮して収めたはずの
    計画がまた溢れてしまう。

    同じスキルが複数グループに現れたら、優先度の高い方にだけ残す
    (資格をまたいで共有される前提スキルを二重に学習しないため)。
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
        # 期限までに入りきらないときは、末尾を捨てるのではなく全体を圧縮する。
        # 範囲を削るより 1 周させる方が試験対策として有効なため。
        items, fit_warnings = _fit_to_deadline(
            items, _capacity(free_slots, slot_i, cursor, group.deadline), group, config
        )
        warnings.extend(fit_warnings)

        unplaced: list[PlanItem] = []
        exhausted = False
        for index, item in enumerate(items):
            if exhausted:
                unplaced.extend(items[index:])
                break
            remaining = item.estimated_minutes
            block_no = 1
            # 端数が MIN_TAIL 未満になったらそのスキルは完了扱い。
            # 置けない端数を抱えたまま回すと、残りの枠を延々と飛ばして
            # 以降のスキルが丸ごと未配置になる。
            while remaining >= MIN_TAIL_MINUTES:
                if slot_i >= len(free_slots):
                    exhausted = True
                    break
                slot_start, slot_end = free_slots[slot_i]
                start = cursor if cursor is not None and cursor > slot_start else slot_start
                if group.deadline is not None and start >= group.deadline:
                    # 期限切れ: カーソルは進めない (残り枠は後続グループが使う)
                    exhausted = True
                    break
                limit = min(slot_end, group.deadline) if group.deadline else slot_end
                space = int((limit - start).total_seconds() // 60)
                take = min(remaining, config.max_block_minutes, space)
                if take <= 0 or take < min(config.min_block_minutes, remaining):
                    # この枠の残りは短すぎる。take<=0 を弾かないと remaining も
                    # slot_i も動かないまま回り続ける
                    slot_i += 1
                    cursor = None
                    continue
                sessions.append(
                    SessionDraft(
                        session_id=f"{group.plan_key}-{item.skill_id}-b{block_no}",
                        skill_id=item.skill_id,
                        skill_name=item.name or item.skill_id,
                        start=start,
                        end=start + timedelta(minutes=take),
                        kind=group.kind,
                        cert_id=group.cert_id,
                    )
                )
                cursor = start + timedelta(minutes=take)
                remaining -= take
                block_no += 1
            if remaining >= MIN_TAIL_MINUTES:
                unplaced.append(item)

        if unplaced:
            warnings.extend(_shortfall_warnings(group, unplaced, free_slots))

    return sessions, warnings


def _capacity(
    free_slots: list[TimeSlot], slot_i: int, cursor: datetime | None, deadline: datetime | None
) -> int:
    """今のカーソル位置から期限までに残っている学習可能時間 (分)。"""
    total = 0
    for i, (start, end) in enumerate(free_slots):
        if i < slot_i:
            continue
        if i == slot_i and cursor is not None and cursor > start:
            start = cursor
        if deadline is not None:
            end = min(end, deadline)
        if end > start:
            total += int((end - start).total_seconds() // 60)
    return total


def _fit_to_deadline(
    items: list[PlanItem], capacity: int, group: PlanGroup, config: ScheduleConfig
) -> tuple[list[PlanItem], list[str]]:
    """期限に収まるよう各スキルの配分時間を圧縮し、その事実を警告として返す。

    配分は Planner が習熟度を加味して出した所要時間の比率を保ったまま
    一律に縮める。どこを削るかを LLM に判断させず、比率だけを機械的に扱う。
    """
    required = sum(i.estimated_minutes for i in items)
    if group.deadline is None or capacity <= 0 or required <= capacity:
        return items, []

    label = f"「{group.label}」" if group.label else ""
    scale = capacity / required
    scaled = [
        i.model_copy(
            update={
                "estimated_minutes": max(
                    config.min_block_minutes, int(i.estimated_minutes * scale)
                )
            }
        )
        for i in items
    ]
    after = sum(i.estimated_minutes for i in scaled)
    days = max((group.deadline - datetime.now()).days, 1)
    warnings = [
        f"{label}全範囲を {group.deadline:%Y-%m-%d} までに一周するには約 {required / 60:.0f} 時間"
        f"必要ですが、空き時間から確保できるのは約 {capacity / 60:.0f} 時間です。"
        f"範囲を削らずに一周させるため、各スキルの配分を {scale:.0%} に圧縮しました"
    ]
    if after > capacity:
        warnings.append(
            f"{label}圧縮しても最短ブロック ({config.min_block_minutes}分) の下限で"
            f"約 {(after - capacity) / 60:.0f} 時間分が残ります。"
            "試験日を延ばすか、1日の学習枠を広げないと全範囲は終わりません"
        )
    else:
        warnings.append(
            f"{label}余裕はありません。1日あたり約 {after / 60 / days:.1f} 時間の学習が前提です"
        )
    return scaled, warnings


def _shortfall_warnings(
    group: PlanGroup, unplaced: list[PlanItem], free_slots: list[TimeSlot]
) -> list[str]:
    """置ききれなかったスキルについて、不足量を具体的に伝える。"""
    label = f"「{group.label}」の" if group.label else ""
    names = ", ".join(i.name or i.skill_id for i in unplaced[:6])
    if len(unplaced) > 6:
        names += f" ほか{len(unplaced) - 6}件"
    short = sum(i.estimated_minutes for i in unplaced)
    out = [
        f"{label}空き時間が足りず {len(unplaced)} スキル分 (約 {short / 60:.0f} 時間) を"
        f"計画に入れられませんでした: {names}"
    ]
    if group.deadline is not None:
        anchor = free_slots[0][0] if free_slots else datetime.now()
        days = max((group.deadline - anchor).days, 1)
        out.append(
            f"{label}期限 {group.deadline:%Y-%m-%d} に全範囲を収めるには"
            f"1日あたり約 {short / 60 / days:.1f} 時間の追加が必要です。"
            "試験日を延ばすか、1日の学習枠を広げてください"
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


def other_pursuit_groups(
    uid: str, exclude_cert_id: str, threshold: float, pursuits: list[dict] | None = None
) -> list[PlanGroup]:
    """今回の実行以外に、ユーザーが目指している資格の未消化分を計画し直す。

    再計画は決定的処理のみ (Cypher + トポロジカルソート) で LLM は呼ばない。
    習熟済みになったスキルは cert_unmastered_targets の時点で落ちるので、
    実行のたびに「今の実力から見た残り」に更新される。
    """
    from app.workflow.planner import build_plan

    groups: list[PlanGroup] = []
    if pursuits is None:
        pursuits = run_named("scheduler.cypher", "user_pursuits", uid=uid)
    for row in pursuits:
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


def ensure_cert_calendar(cert_id: str) -> tuple[str, str]:
    """資格専用カレンダーを冪等に用意し、(calendar_id, 公開用URL) を返す。

    初回登録時にその資格のカレンダーを自動生成する。すでに記録があり実在すれば
    それを使い回す (ユーザーが手で消していた場合だけ作り直す)。
    一般公開の設定はここでは行わない — 公開はカレンダーの持ち主の操作。
    """
    from app.tools import calendar_tool

    rows = run_named("scheduler.cypher", "cert_calendar", cert_id=cert_id)
    if not rows:
        return "", ""
    cert = rows[0]
    if cert["calendar_id"] and calendar_tool.calendar_exists(cert["calendar_id"]):
        return cert["calendar_id"], cert["calendar_url"]

    vendor = f"{cert['vendor']} " if cert["vendor"] else ""
    calendar_id = calendar_tool.create_calendar(
        summary=f"SkillPath — {cert['name']}",
        description=(
            f"{vendor}{cert['name']} の学習ブロック。\n"
            "SkillPath が前提スキルの依存関係と試験日から自動生成し、"
            "模試の結果に応じて組み直します。"
        ),
        color_key=cert_id,
    )
    url = calendar_tool.public_url(calendar_id)
    run_named(
        "scheduler.cypher",
        "set_cert_calendar",
        cert_id=cert_id,
        calendar_id=calendar_id,
        calendar_url=url,
    )
    logger.info("資格カレンダーを新規作成: %s (%s)", cert["name"], calendar_id)
    return calendar_id, url


def _assign_certs_to_reviews(uid: str, sessions: list[SessionDraft]) -> None:
    """資格が未確定のブロック (模試由来の復習) を、対象スキルが属する資格へ寄せる。"""
    pending = [s for s in sessions if not s.cert_id]
    if not pending:
        return
    rows = run_named(
        "scheduler.cypher", "skill_certs", uid=uid,
        skill_ids=sorted({s.skill_id for s in pending}),
    )
    by_skill = {r["skill_id"]: r["cert_ids"] for r in rows}
    for sess in pending:
        certs = by_skill.get(sess.skill_id) or []
        if certs:
            sess.cert_id = sorted(certs)[0]


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
) -> tuple[list[SessionDraft], list[str], bool, dict[str, str]]:
    """空き時間の取得 → 全資格の一括再配置 → カレンダー登録 → LearningSession 記録。

    今回の計画だけを空きに詰めるのではなく、ユーザーが目指している他の資格の
    未消化分もまとめて EDF で組み直す。こうしないと「あとから登録した、より
    期限の近い資格」が先に登録した資格の後ろに回ってしまう。

    CALENDAR_ENABLED のときは実カレンダーを使い、失敗時はプレースホルダに
    フォールバック (ワークフローを止めない)。
    戻り値は (配置, 警告, カレンダー同期済みか, 資格ID→カレンダー公開URL)。
    """
    from app.tools import calendar_tool
    from app.workflow import notifier

    calendar_links: dict[str, str] = {}
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
    pursuits = run_named("scheduler.cypher", "user_pursuits", uid=uid)
    groups += other_pursuit_groups(uid, cert_id, threshold, pursuits=pursuits)

    # 計画期間: 一番遠い試験日まで見る。14日固定だと2つ目の資格が窓からあふれ、
    # 「空き時間不足」に見えてしまう (実際には期間が足りていないだけ)
    deadlines = [g.deadline for g in groups if g.deadline]
    horizon = 14
    if deadlines:
        horizon = max(14, min(MAX_HORIZON_DAYS, (max(deadlines) - start).days + 1))
    window_end = start + timedelta(days=horizon + 1)
    # 空き時間の判定対象: 本人の予定 (primary) + 既存の資格カレンダー。
    # 資格カレンダーを見ないと、他資格の学習ブロックが「無い」ことになってしまう。
    calendar_ids = ("primary", *(p["calendar_id"] for p in pursuits if p["calendar_id"]))
    calendar_ok = False
    warnings_extra: list[str] = []
    if get_settings().calendar_enabled:
        try:
            # own_prefix: この uid の学習ブロックはすべて組み直す対象なので空きとして扱う。
            # 他の予定 (会議・私用) はそのまま busy。
            busy = calendar_tool.get_busy(
                start, window_end, calendar_ids=calendar_ids, own_prefix=f"{uid}-"
            )
            slots = compute_free_slots(start, busy, days=horizon)
            calendar_ok = True
        except calendar_tool.CalendarUnavailable as e:
            logger.warning("Calendar 未接続のためプレースホルダで続行: %s", e)
            warnings_extra.append("カレンダー未接続のため仮の空き時間で計画しています")
            slots = placeholder_free_slots(start, days=horizon)
    else:
        slots = placeholder_free_slots(start, days=horizon)

    sessions, warnings = allocate_groups(groups, slots)
    if len(groups) > 1:
        others = "・".join(g.label or g.cert_id for g in groups[1:])
        warnings_extra.append(f"他に進行中の資格 ({others}) の予定と重ならないよう調整しました")

    if calendar_ok and sessions:
        # イベント作成 → LearningSession を Neo4j に記録 (設計書 §4.2 step 6 / §5 冪等)
        _assign_certs_to_reviews(uid, sessions)
        # 資格ごとのカレンダーを必要な分だけ用意する (初回登録時に自動生成)
        cert_calendars: dict[str, str] = {}
        for cid in sorted({s.cert_id for s in sessions if s.cert_id}):
            cal_id, url = ensure_cert_calendar(cid)
            if cal_id:
                cert_calendars[cid] = cal_id
                calendar_links.setdefault(cid, url)
        calendar_ids = ("primary", *sorted(set(cert_calendars.values())))

        names = {i.skill_id: i.name or i.skill_id for g in groups for i in g.plan.plan}
        session_rows = []
        for sess in sessions:
            summary, description = notifier.build_event_texts(
                sess.skill_id, names, sess.kind, weakness
            )
            event_id = calendar_tool.upsert_event(
                sess.session_id, summary, description, sess.start, sess.end,
                calendar_id=cert_calendars.get(sess.cert_id, "primary"),
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
            removed = calendar_tool.delete_orphan_events(
                f"{uid}-", keep, start, window_end, calendar_ids=calendar_ids
            )
            if removed:
                logger.info("再配置で不要になった学習ブロックを %d 件削除", removed)
        except Exception:  # noqa: BLE001 - 掃除の失敗で計画自体を落とさない
            logger.exception("不要ブロックの削除に失敗 (続行)")
        run_named(
            "scheduler.cypher", "delete_orphan_sessions", uid=uid, keep_ids=sorted(keep)
        )

    return sessions, warnings + warnings_extra, calendar_ok, calendar_links
