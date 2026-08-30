"""ループの停止保証。

割り当て・トポロジカルソート・カレンダーのページングは、いずれも
外部入力 (LLM 出力・設定値・API 応答) 次第で回り続けうる場所なので、
異常系でも必ず有限回で終わることを固定する。
"""

from datetime import datetime, timedelta

import pytest

from app.models.schemas import PlanItem, PlannerOutput
from app.workflow.planner import SkillNode, topological_sort
from app.workflow.scheduler import PlanGroup, ScheduleConfig, allocate_groups


def _slots(n: int = 5):
    base = datetime(2026, 9, 1, 20, 0)
    return [(base + timedelta(days=i), base + timedelta(days=i, hours=2)) for i in range(n)]


def _group(*items: tuple[str, int], **kw) -> PlanGroup:
    return PlanGroup(
        plan_key="k",
        plan=PlannerOutput(
            plan=[
                PlanItem(order=i + 1, skill_id=s, name=s, estimated_minutes=m)
                for i, (s, m) in enumerate(items)
            ]
        ),
        **kw,
    )


@pytest.mark.parametrize(
    ("max_block", "min_block"),
    [(0, 30), (90, 0), (90, -5), (10, 30)],  # 0分ブロック / 逆転した上下限
)
def test_broken_block_config_is_rejected_instead_of_hanging(max_block, min_block):
    with pytest.raises(ValueError):
        ScheduleConfig(max_block_minutes=max_block, min_block_minutes=min_block)


def test_allocation_terminates_when_no_slot_fits():
    """どの枠にも入らないブロックを渡しても、枠を舐めきって終わる。"""
    tiny = [(datetime(2026, 9, 1, 21, 55), datetime(2026, 9, 1, 22, 0))]  # 5分だけ
    sessions, warnings = allocate_groups([_group(("a", 90), ("b", 90))], tiny)
    assert sessions == []
    assert warnings


def test_allocation_terminates_with_no_slots_at_all():
    sessions, _ = allocate_groups([_group(("a", 90))], [])
    assert sessions == []


def test_allocation_terminates_on_a_deadline_before_every_slot():
    group = _group(("a", 90), deadline=datetime(2026, 8, 1), kind="initial")
    sessions, _ = allocate_groups([group], _slots())
    assert sessions == []


def test_topological_sort_resolves_a_cycle_and_stops():
    """相互依存だけのグラフでも、エッジを切って必ず全件返す。"""
    nodes = [
        SkillNode("a", "a", 1.0, 0.0, [{"id": "b", "strength": 0.5}]),
        SkillNode("b", "b", 1.0, 0.0, [{"id": "c", "strength": 0.4}]),
        SkillNode("c", "c", 1.0, 0.0, [{"id": "a", "strength": 0.3}]),
    ]
    ordered, warnings = topological_sort(nodes)
    assert sorted(ordered) == ["a", "b", "c"]
    assert any("循環参照" in w for w in warnings)


def test_topological_sort_handles_a_self_loop():
    nodes = [SkillNode("a", "a", 1.0, 0.0, [{"id": "a", "strength": 0.9}])]
    ordered, _ = topological_sort(nodes)
    assert ordered == ["a"]


def test_calendar_paging_has_a_page_ceiling():
    """API が同じトークンを返し続けても、列挙は有限回で止まる。"""
    from app.tools import calendar_tool

    class _Loop:
        def events(self):
            return self

        def list(self, **kwargs):
            return self

        def execute(self):
            return {"items": [{"id": "x"}], "nextPageToken": "same-token"}

    original = calendar_tool.get_service
    calendar_tool.get_service = lambda: _Loop()
    try:
        got = list(
            calendar_tool._iter_events("primary", datetime(2026, 9, 1), datetime(2026, 9, 30))
        )
    finally:
        calendar_tool.get_service = original
    assert len(got) <= calendar_tool.MAX_EVENT_PAGES
