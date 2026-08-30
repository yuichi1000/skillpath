"""Scheduler の空き時間割り当て (純粋ロジック、DB 不要)。"""

from datetime import datetime, timedelta

from app.models.schemas import PlanItem, PlannerOutput
from app.workflow.scheduler import PlanGroup, ScheduleConfig, allocate_groups, allocate_sessions


def _minutes(sessions, skill_id: str) -> int:
    return sum(
        int((s.end - s.start).total_seconds() // 60) for s in sessions if s.skill_id == skill_id
    )


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
    # 200分 → 90 + 90 + 20。所要時間はそのまま使い、切り上げで水増ししない
    sessions, warnings = allocate_sessions(
        plan_of(("nn", 200)), [day_slot(0, 9, 18)], plan_key="a1"
    )
    assert [int((s.end - s.start).total_seconds() // 60) for s in sessions] == [90, 90, 20]
    assert warnings == []


def test_spans_multiple_slots_and_keeps_order():
    # 2時間の夜スロット×3日。skill1 の全ブロックが skill2 より先に配置される。
    # 枠の端数 (90分ブロックを置いた残りの30分) も学習に使う
    slots = [day_slot(d, 20, 22) for d in range(3)]
    sessions, warnings = allocate_sessions(
        plan_of(("nn", 180), ("dl", 120)), slots, plan_key="a1"
    )
    assert [s.skill_id for s in sessions] == ["nn", "nn", "nn", "dl", "dl"]
    assert _minutes(sessions, "nn") == 180
    assert _minutes(sessions, "dl") == 120
    assert all(a.end <= b.start for a, b in zip(sessions, sessions[1:], strict=False))
    assert warnings == []


def test_slot_shorter_than_the_minimum_block_is_skipped():
    # 20分しかない枠は学習ブロックとして短すぎるので使わない
    slots = [
        (datetime(2026, 9, 1, 21, 40), datetime(2026, 9, 1, 22, 0)),
        day_slot(1, 9, 12),
    ]
    sessions, _ = allocate_sessions(plan_of(("nn", 90)), slots, plan_key="a1")
    assert sessions[0].start == datetime(2026, 9, 2, 9, 0)


def test_partial_slot_is_used_rather_than_wasted():
    # 60分の枠には90分ブロックは入らないが、60分ぶんは進めて残りを翌日に回す
    slots = [day_slot(0, 21, 22), day_slot(1, 9, 12)]
    sessions, _ = allocate_sessions(plan_of(("nn", 90)), slots, plan_key="a1")
    assert [int((s.end - s.start).total_seconds() // 60) for s in sessions] == [60, 30]
    assert sessions[0].start == datetime(2026, 9, 1, 21, 0)


def test_warns_when_slots_exhausted():
    sessions, warnings = allocate_sessions(
        plan_of(("nn", 90), ("dl", 90)), [day_slot(0, 9, 10)], plan_key="a1"
    )
    assert _minutes(sessions, "nn") == 60  # 入る分だけ進める
    assert _minutes(sessions, "dl") == 0
    assert "計画に入れられませんでした" in warnings[0]
    assert "nn, dl" in warnings[0]


def test_deadline_compresses_the_plan_instead_of_cutting_it():
    # ゴール逆算: 期限 (9/3) より後の枠は使わない。300分の計画を240分に圧縮して収める
    slots = [day_slot(d, 20, 22) for d in range(10)]  # 1日2時間の夜スロット
    sessions, warnings = allocate_sessions(
        plan_of(("nn", 300)), slots, plan_key="a1", deadline=datetime(2026, 9, 3)
    )
    assert all(s.end <= datetime(2026, 9, 3) for s in sessions)
    assert _minutes(sessions, "nn") == 240  # 期限前の枠を使い切る
    assert any("圧縮しました" in w for w in warnings)
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


# ---- 複数資格の同時進行 (EDF) ----


def _plan(*skills: tuple[str, int]) -> PlannerOutput:
    return PlannerOutput(
        plan=[
            PlanItem(order=i + 1, skill_id=sid, name=sid, estimated_minutes=m)
            for i, (sid, m) in enumerate(skills)
        ]
    )


def _slots(day_count: int = 6) -> list[tuple[datetime, datetime]]:
    base = datetime(2026, 9, 1, 20, 0)
    return [
        (base + timedelta(days=i), base + timedelta(days=i, hours=2)) for i in range(day_count)
    ]


def test_earlier_deadline_gets_earlier_slots_regardless_of_registration_order():
    """あとから登録した資格でも、期限が近ければ手前の枠を取る (EDF)。"""
    late = PlanGroup(  # 先に登録された、期限の遠い資格
        plan_key="u-cert-a-initial", plan=_plan(("a1", 90)), kind="initial",
        deadline=datetime(2026, 10, 5), cert_id="cert-a", label="資格A",
    )
    soon = PlanGroup(  # あとから登録された、期限の近い資格
        plan_key="u-cert-b-initial", plan=_plan(("b1", 90)), kind="initial",
        deadline=datetime(2026, 9, 20), cert_id="cert-b", label="資格B",
    )
    sessions, _ = allocate_groups([late, soon], _slots())
    by_skill = {s.skill_id: s.start for s in sessions}
    assert by_skill["b1"] < by_skill["a1"]


def test_groups_never_overlap_in_time():
    """複数資格のブロックが同じ時間帯に二重登録されない。"""
    groups = [
        PlanGroup(plan_key="u-cert-a-initial", plan=_plan(("a1", 90), ("a2", 60)),
                  kind="initial", deadline=datetime(2026, 10, 5), cert_id="cert-a"),
        PlanGroup(plan_key="u-cert-b-initial", plan=_plan(("b1", 90), ("b2", 60)),
                  kind="initial", deadline=datetime(2026, 10, 20), cert_id="cert-b"),
    ]
    sessions, _ = allocate_groups(groups, _slots())
    assert {s.skill_id for s in sessions} == {"a1", "a2", "b1", "b2"}
    ordered = sorted(sessions, key=lambda s: s.start)
    for prev, nxt in zip(ordered, ordered[1:], strict=False):
        assert prev.end <= nxt.start


def test_shared_skill_is_scheduled_once_for_the_more_urgent_cert():
    """資格をまたいで共有される前提スキルは、期限が近い側に1回だけ置かれる。"""
    groups = [
        PlanGroup(plan_key="u-cert-a-initial", plan=_plan(("shared", 60)),
                  kind="initial", deadline=datetime(2026, 10, 5), cert_id="cert-a"),
        PlanGroup(plan_key="u-cert-b-initial", plan=_plan(("shared", 60)),
                  kind="initial", deadline=datetime(2026, 9, 20), cert_id="cert-b"),
    ]
    sessions, _ = allocate_groups(groups, _slots())
    assert [s.cert_id for s in sessions] == ["cert-b"]


def test_group_past_its_deadline_leaves_slots_for_the_others():
    """圧縮しても期限に収まらないグループが残した枠を、後続の資格が使える。"""
    groups = [
        PlanGroup(  # 10スキル×90分 を 3時間の枠に詰め込むのは最小ブロックの下限で不可能
            plan_key="u-cert-a-initial",
            plan=_plan(*[(f"a{i}", 90) for i in range(10)]),
            kind="initial",
            deadline=datetime(2026, 9, 2, 21),
            cert_id="cert-a",
            label="資格A",
        ),
        PlanGroup(
            plan_key="u-cert-b-initial", plan=_plan(("b1", 90)),
            kind="initial", deadline=datetime(2026, 12, 1), cert_id="cert-b",
        ),
    ]
    sessions, warnings = allocate_groups(groups, _slots())
    assert "b1" in {s.skill_id for s in sessions}  # 資格Bは配置されている
    assert all(s.end <= datetime(2026, 9, 2, 21) for s in sessions if s.cert_id == "cert-a")
    assert any("計画に入れられませんでした" in w for w in warnings)
    assert any("試験日を延ばすか" in w for w in warnings)


def test_review_comes_before_new_material_on_the_same_deadline():
    """期限が同じなら、弱点の復習を新規学習より先に置く。"""
    deadline = datetime(2026, 10, 5)
    groups = [
        PlanGroup(plan_key="u-cert-a-initial", plan=_plan(("new1", 90)),
                  kind="initial", deadline=deadline, cert_id="cert-a"),
        PlanGroup(plan_key="u-assess-1", plan=_plan(("weak1", 90)),
                  kind="review", deadline=deadline, cert_id="cert-a"),
    ]
    sessions, _ = allocate_groups(groups, _slots())
    assert sessions[0].skill_id == "weak1"


def test_blocks_carry_display_names():
    """所見やカレンダーに skill_id が露出しないよう、ブロックが表示名を持つ。"""
    g = PlanGroup(plan_key="u-cert-a-initial", plan=_plan(("a1", 60)), kind="initial")
    g.plan.plan[0].name = "ディープラーニング手法"
    sessions, _ = allocate_groups([g], _slots())
    assert sessions[0].skill_name == "ディープラーニング手法"


# ---- 期限に収まらないときの扱い ----


def test_plan_is_compressed_rather_than_truncated_at_the_deadline():
    """期限に入りきらなくても、末尾のスキルを捨てず全体を圧縮して1周させる。"""
    # 1日2時間 × 6日 = 12時間の枠に、10スキル × 120分 = 20時間の計画
    group = PlanGroup(
        plan_key="u-cert-a-initial",
        plan=_plan(*[(f"s{i}", 120) for i in range(10)]),
        kind="initial",
        deadline=datetime(2026, 9, 7),
        label="資格A",
    )
    sessions, warnings = allocate_groups([group], _slots(6))
    # 圧縮前なら 6 スキルで枠が尽きる。圧縮によりほぼ全範囲が計画に入る
    assert len({s.skill_id for s in sessions}) >= 9
    assert any("圧縮しました" in w for w in warnings)
    # 入りきらなかった分は、どのスキルが何時間足りないかまで伝える
    if len({s.skill_id for s in sessions}) < 10:
        assert any("計画に入れられませんでした" in w for w in warnings)
        assert any("試験日を延ばすか" in w for w in warnings)


def test_compression_warning_states_the_gap_honestly():
    """どれだけ足りないかを具体的な時間で伝える。"""
    group = PlanGroup(
        plan_key="u-cert-a-initial",
        plan=_plan(*[(f"s{i}", 120) for i in range(10)]),
        kind="initial",
        deadline=datetime(2026, 9, 7),
        label="資格A",
    )
    _, warnings = allocate_groups([group], _slots(6))
    joined = "\n".join(warnings)
    assert "時間必要ですが" in joined
    assert "確保できるのは" in joined
    assert "1日あたり" in joined or "試験日を延ばすか" in joined


def test_plan_within_the_deadline_is_left_alone():
    """収まっている計画は圧縮しない。"""
    group = PlanGroup(
        plan_key="u-cert-a-initial", plan=_plan(("s1", 60), ("s2", 60)),
        kind="initial", deadline=datetime(2026, 9, 30),
    )
    sessions, warnings = allocate_groups([group], _slots(10))
    assert sum(int((s.end - s.start).total_seconds() // 60) for s in sessions) == 120
    assert not any("圧縮" in w for w in warnings)


def test_tiny_leftover_does_not_burn_the_remaining_slots():
    """15分未満の端数を抱えたまま枠を飛ばし続け、以降が全滅しないこと。

    本番で 19 スキル中 14 スキルが未配置になった原因。端数は切り捨てて
    次のスキルへ進む。
    """
    slots = [day_slot(d, 20, 22) for d in range(27)]
    plan = plan_of(*[(f"s{i}", 165) for i in range(19)])  # 165分は端数が出る長さ
    sessions, warnings = allocate_sessions(
        plan, slots, plan_key="a1", deadline=datetime(2026, 9, 27)
    )
    assert len({s.skill_id for s in sessions}) == 19  # 全スキルに時間が割り当たる
    used = sum(int((s.end - s.start).total_seconds() // 60) for s in sessions)
    assert used > 0.93 * 27 * 120  # 空き枠をほぼ使い切る (端数の切り捨てぶんのみ残る)
    assert max(s.start for s in sessions).day >= 25  # 期限直前まで計画が伸びる
