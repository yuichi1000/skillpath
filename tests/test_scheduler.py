"""Scheduler の空き時間割り当て (純粋ロジック、DB 不要)。"""

from datetime import datetime, timedelta

from app.models.schemas import PlanItem, PlannerOutput
from app.workflow.scheduler import ScheduleConfig, allocate_sessions


def plan_of(*items: tuple[str, int]) -> PlannerOutput:
    return PlannerOutput(
        plan=[
            PlanItem(order=i + 1, skill_id=sid, estimated_minutes=minutes)
            for i, (sid, minutes) in enumerate(items)
        ]
    )


def day_slot(day: int, hour_from: int, hour_to: int):
    base = datetime(2026, 9, 1) + timedelta(days=day)
    return (base.replace(hour=hour_from), base.replace(hour=hour_to))


def test_splits_long_skill_into_blocks():
    # 200分 → 90 + 90 + 20 だが、端数20分は min(30分) に切り上げ
    sessions, warnings = allocate_sessions(
        plan_of(("nn", 200)), [day_slot(0, 9, 18)], plan_key="a1"
    )
    assert [int((s.end - s.start).total_seconds() // 60) for s in sessions] == [90, 90, 30]
    assert warnings == []


def test_spans_multiple_slots_and_keeps_order():
    # 2時間の夜スロット×3日。skill1 の全ブロックが skill2 より先に配置される
    slots = [day_slot(d, 20, 22) for d in range(3)]
    sessions, warnings = allocate_sessions(
        plan_of(("nn", 180), ("dl", 120)), slots, plan_key="a1"
    )
    assert [s.skill_id for s in sessions] == ["nn", "nn", "dl", "dl"]
    assert all(a.end <= b.start for a, b in zip(sessions, sessions[1:], strict=False))
    assert warnings == []


def test_small_leftover_slot_is_skipped():
    # 1つ目のスロット(60分)に90分ブロックは入らず、丸ごと2日目に送られる
    slots = [day_slot(0, 21, 22), day_slot(1, 9, 12)]
    sessions, _ = allocate_sessions(plan_of(("nn", 90)), slots, plan_key="a1")
    assert sessions[0].start == datetime(2026, 9, 2, 9, 0)


def test_warns_when_slots_exhausted():
    sessions, warnings = allocate_sessions(
        plan_of(("nn", 90), ("dl", 90)), [day_slot(0, 9, 10, )], plan_key="a1"
    )
    assert sessions == []
    assert "配置できませんでした" in warnings[0]
    assert "nn, dl" in warnings[0]


def test_deadline_stops_allocation_and_gives_reverse_calc():
    # ゴール逆算: 期限 (9/3) より後のスロットは使わず、入る分だけ配置して逆算警告を出す
    slots = [day_slot(d, 20, 22) for d in range(10)]  # 1日2時間の夜スロット
    sessions, warnings = allocate_sessions(
        plan_of(("nn", 300)),  # 90+90+90+30 の4ブロック
        slots,
        plan_key="a1",
        deadline=datetime(2026, 9, 3),
    )
    # 期限前のスロットは 9/1・9/2 の2枠のみ → 90分×2 だけ配置される
    assert len(sessions) == 2
    assert all(s.end <= datetime(2026, 9, 3) for s in sessions)
    assert any("収まりません" in w for w in warnings)
    assert any("1日あたり" in w for w in warnings)


def test_deadline_with_enough_slots_places_everything():
    slots = [day_slot(d, 20, 22) for d in range(10)]
    sessions, warnings = allocate_sessions(
        plan_of(("nn", 120)), slots, plan_key="a1", deadline=datetime(2026, 9, 5)
    )
    assert len(sessions) == 2
    assert warnings == []


def test_session_ids_are_deterministic():
    args = dict(plan=plan_of(("nn", 180)), free_slots=[day_slot(0, 9, 18)], plan_key="a1")
    first, _ = allocate_sessions(**args)
    second, _ = allocate_sessions(**args)
    assert [s.session_id for s in first] == [s.session_id for s in second]
    assert first[0].session_id == "a1-nn-b1"


def test_respects_max_block_config():
    sessions, _ = allocate_sessions(
        plan_of(("nn", 120)),
        [day_slot(0, 9, 18)],
        ScheduleConfig(max_block_minutes=60),
        plan_key="a1",
    )
    assert [int((s.end - s.start).total_seconds() // 60) for s in sessions] == [60, 60]


def test_compute_free_slots_subtracts_busy():
    from app.workflow.scheduler import compute_free_slots

    start = datetime(2026, 9, 1)
    # 9/2 の 20:30-21:00 に既存予定 → 窓 20-22 が 2 分割される
    busy = [(datetime(2026, 9, 2, 20, 30), datetime(2026, 9, 2, 21, 0))]
    slots = compute_free_slots(start, busy, days=1)
    assert slots == [
        (datetime(2026, 9, 2, 20, 0), datetime(2026, 9, 2, 20, 30)),
        (datetime(2026, 9, 2, 21, 0), datetime(2026, 9, 2, 22, 0)),
    ]


def test_compute_free_slots_busy_covers_whole_window():
    from app.workflow.scheduler import compute_free_slots

    start = datetime(2026, 9, 1)
    busy = [(datetime(2026, 9, 2, 19, 0), datetime(2026, 9, 2, 23, 0))]
    slots = compute_free_slots(start, busy, days=2)
    # 9/2 は丸ごと潰れ、9/3 だけ残る
    assert slots == [(datetime(2026, 9, 3, 20, 0), datetime(2026, 9, 3, 22, 0))]


def test_compute_free_slots_no_busy_equals_placeholder():
    from app.workflow.scheduler import compute_free_slots, placeholder_free_slots

    start = datetime(2026, 9, 1)
    assert compute_free_slots(start, [], days=3) == placeholder_free_slots(start, days=3)
