"""複数資格の同時進行 (Cypher 込みの統合テスト)。

PURSUES による試験日の永続化 → 他資格の再計画 → EDF 配置 までを
ローカル Neo4j に対して検証する。LLM もカレンダー API も使わない。
"""

from datetime import datetime

from app.models.schemas import PlanItem, PlannerOutput
from app.tools.neo4j_tool import run_query
from app.workflow.scheduler import other_pursuit_groups, schedule_sessions

TWO_CERTS = """
MATCH (u:User {uid: 'test-w-user'})
CREATE (ca:Certification {id: 'test-w-cert-a', name: 'test 資格A'})
CREATE (cb:Certification {id: 'test-w-cert-b', name: 'test 資格B'})
WITH u, ca, cb
MATCH (stats:Skill {id: 'test-w-stats'}), (ml:Skill {id: 'test-w-ml'}),
      (db:Skill {id: 'test-w-db'})
CREATE (ca)-[:REQUIRES]->(stats)
CREATE (ca)-[:REQUIRES]->(ml)
CREATE (cb)-[:REQUIRES]->(db)
CREATE (u)-[:PURSUES {deadline: date('2026-11-30')}]->(ca)
CREATE (u)-[:PURSUES {deadline: date('2026-09-20')}]->(cb)
"""


def test_pursuits_are_replanned_from_current_mastery(weakness_graph):
    run_query(TWO_CERTS)
    groups = other_pursuit_groups("test-w-user", exclude_cert_id="", threshold=0.7)
    by_cert = {g.cert_id: g for g in groups}

    assert set(by_cert) == {"test-w-cert-a", "test-w-cert-b"}
    # 試験日が PURSUES から復元される
    assert by_cert["test-w-cert-b"].deadline == datetime(2026, 9, 20)
    # 習熟済み (mastery 0.9) の数学基礎は対象から落ちる。未習熟の確率統計は残る
    a_skills = {i.skill_id for i in by_cert["test-w-cert-a"].plan.plan}
    assert "test-w-stats" in a_skills
    assert "test-w-math" not in a_skills


def test_later_registered_cert_with_nearer_deadline_wins_the_early_slots(weakness_graph):
    """期限の近い資格Bが、先に登録された資格Aより手前の枠を取る。"""
    run_query(TWO_CERTS)
    groups = other_pursuit_groups("test-w-user", exclude_cert_id="", threshold=0.7)
    cert_a = next(g for g in groups if g.cert_id == "test-w-cert-a")

    # 資格A の計画を「今回の実行」として渡す (資格B は他の進行中資格として合流する)
    sessions, warnings, _ok, _links = schedule_sessions(
        uid="test-w-user",
        plan=cert_a.plan,
        kind="initial",
        start=datetime(2026, 9, 1),
        plan_key="test-w-user-test-w-cert-a-initial",
        deadline=datetime(2026, 11, 30),
        cert_id="test-w-cert-a",
        label="test 資格A",
    )
    starts = {s.cert_id: s.start for s in sorted(sessions, key=lambda s: s.start, reverse=True)}
    assert starts["test-w-cert-b"] < starts["test-w-cert-a"]

    # 二重ブッキングが無い
    ordered = sorted(sessions, key=lambda s: s.start)
    for prev, nxt in zip(ordered, ordered[1:], strict=False):
        assert prev.end <= nxt.start
    assert any("他に進行中の資格" in w for w in warnings)


def test_planning_horizon_reaches_the_furthest_exam_date(weakness_graph):
    """計画期間は一番遠い試験日まで伸びる (14日固定だと遠い試験の分が入りきらない)。

    1日の学習枠は 20-22時の2時間。90分ブロックは1日1個しか入らないので、
    30個を置くには30日ぶんの枠が要る (14日固定なら14個で頭打ちになる)。
    """
    plan = PlannerOutput(
        plan=[
            PlanItem(order=i + 1, skill_id=f"test-w-s{i}", name=f"skill{i}", estimated_minutes=90)
            for i in range(30)
        ]
    )
    sessions, _warnings, _ok, _links = schedule_sessions(
        uid="test-w-user",
        plan=plan,
        kind="initial",
        start=datetime(2026, 9, 1),
        plan_key="test-w-user-horizon-initial",
        deadline=datetime(2026, 11, 30),
        cert_id="test-w-cert-none",
    )
    covered = {s.skill_id for s in sessions}
    assert len(covered) == 30  # 14日窓なら14スキルで頭打ちになる
    total = sum(int((s.end - s.start).total_seconds() // 60) for s in sessions)
    assert total == 30 * 90
    assert max(s.start for s in sessions) > datetime(2026, 9, 20)
    assert all(s.start < datetime(2026, 11, 30) for s in sessions)
