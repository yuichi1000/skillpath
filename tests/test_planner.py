from app.workflow.planner import SkillNode, build_plan, estimate_minutes, topological_sort


def node(
    skill_id: str, deps: list[tuple[str, float]] = (), hours: float = 1.0, mastery: float = 0.0
):
    return SkillNode(
        skill_id=skill_id,
        name=skill_id,
        estimated_hours=hours,
        mastery=mastery,
        depends_on=[{"id": d, "strength": s} for d, s in deps],
    )


# ---- topological_sort (純粋関数、DB 不要) ----

def test_linear_chain():
    nodes = [node("ml", [("stats", 0.9)]), node("stats", [("math", 0.9)]), node("math")]
    ordered, warnings = topological_sort(nodes)
    assert ordered == ["math", "stats", "ml"]
    assert warnings == []


def test_diamond_dependency():
    nodes = [
        node("d", [("b", 0.9), ("c", 0.9)]),
        node("b", [("a", 0.9)]),
        node("c", [("a", 0.9)]),
        node("a"),
    ]
    ordered, _ = topological_sort(nodes)
    assert ordered == ["a", "b", "c", "d"]


def test_cycle_resolved_by_cutting_weakest_edge():
    # a と b が相互に前提。strength 0.2 のエッジ (b が a の前提) を切るべき
    nodes = [node("a", [("b", 0.2)]), node("b", [("a", 0.9)])]
    ordered, warnings = topological_sort(nodes)
    assert ordered == ["a", "b"]
    assert len(warnings) == 1
    assert "b -> a" in warnings[0]


def test_empty_input():
    assert topological_sort([]) == ([], [])


def test_dependency_outside_target_set_is_ignored():
    # 対象集合外のスキルへの依存は無視される (設計書: 部分グラフのみでソート)
    nodes = [node("b", [("outside", 0.9)]), node("a")]
    ordered, warnings = topological_sort(nodes)
    assert ordered == ["a", "b"]
    assert warnings == []


# ---- estimate_minutes ----

def test_estimate_minutes_scales_with_mastery():
    assert estimate_minutes(2.0, 0.0) == 120   # 未習熟なら満額
    assert estimate_minutes(2.0, 0.5) == 60    # 半分習熟なら半分
    assert estimate_minutes(2.0, 0.9) == 30    # 12分 → 最低30分に切り上げ


# ---- build_plan (Neo4j 統合) ----

def test_build_plan_orders_prereq_first(weakness_graph):
    out = build_plan("test-w-user", ["test-w-ml", "test-w-stats"])

    assert [p.skill_id for p in out.plan] == ["test-w-stats", "test-w-ml"]
    assert [p.order for p in out.plan] == [1, 2]
    # estimated_hours 未設定 → 1.0h。stats は習熟度 0.3 で 42分→40分、ml は 0.0 で 60分
    assert [p.estimated_minutes for p in out.plan] == [40, 60]
    assert out.warnings == []
    assert all(p.resource_id is None for p in out.plan)  # 教材選定 (LLM) は未実装


def test_transitive_dependency_through_excluded_node(weakness_graph):
    # math→stats→ml のうち stats (習熟済み想定) をクラスタから外しても、
    # ml は math に推移的に依存していることが拾える (可変長パス修正の検証)
    from app.workflow.planner import fetch_subgraph

    nodes = {n.skill_id: n for n in fetch_subgraph("test-w-user", ["test-w-math", "test-w-ml"])}
    assert "test-w-math" in [d["id"] for d in nodes["test-w-ml"].depends_on]
    assert nodes["test-w-math"].depends_on == []
