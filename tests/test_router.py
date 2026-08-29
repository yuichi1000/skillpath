"""Router (LLM) の分類テスト — 実 API を呼ぶため GOOGLE_API_KEY が無ければスキップ。

課金・レイテンシを抑えるため最小限の2ケースのみ。決定的な経路の検証は
test_graph.py (with_router=False) が担う。
"""

import os

import pytest

from tests.test_graph import run_workflow

needs_api_key = pytest.mark.skipif(
    not os.getenv("GOOGLE_API_KEY"), reason="GOOGLE_API_KEY 未設定 (LLM テストはスキップ)"
)


@needs_api_key
async def test_assessment_intent_reaches_weakness_branch(weakness_graph):
    state = await run_workflow(
        {
            "uid": "test-w-user",
            "assessment_id": "test-w-assess1",
            "schedule_start": "2026-09-01T00:00:00",
        },
        with_router=True,
        message="模試を受けたので結果を分析してください",
    )
    assert state["router_output"]["intent"] == "assessment"
    assert state["weakness"]["has_weakness"] is True
    assert "📅 学習スケジュール" in state["summary"]


@needs_api_key
async def test_register_intent_goes_to_stub(weakness_graph):
    state = await run_workflow(
        {"uid": "test-w-user", "assessment_id": "test-w-assess1"},
        with_router=True,
        message="G検定を受験したいので教材を登録してください",
    )
    assert state["router_output"]["intent"] == "register"
    assert "未実装" in state["summary"]
