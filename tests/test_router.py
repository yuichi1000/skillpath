"""Router (LLM) の分類テスト — 実 API を呼ぶため GOOGLE_API_KEY が無ければスキップ。

課金・レイテンシを抑えるため最小限の2ケースのみ。決定的な経路の検証は
test_graph.py (with_router=False) が担う。
"""

import os

import pytest

from tests.test_graph import run_workflow

# @needs_api_key を付けたテストは実 API を呼ぶ (make test では除外、make test-all で実行)
needs_api_key = pytest.mark.skipif(
    not os.getenv("GOOGLE_API_KEY"), reason="GOOGLE_API_KEY 未設定 (LLM テストはスキップ)"
)


@pytest.mark.llm
@needs_api_key
async def test_assessment_intent_is_classified(weakness_graph):
    # 意図分類のみを検証する。スコア付きテキストでの一気通貫は
    # test_feedback.test_full_flow_from_pasted_text が担う
    state = await run_workflow(
        {"uid": "test-w-user"},
        with_router=True,
        message="模試を受けたので結果を分析してください",
    )
    assert state["router_output"]["intent"] == "assessment"
    assert state["user_input"].startswith("模試を受けた")


@pytest.mark.llm
@needs_api_key
async def test_register_intent_goes_to_stub(weakness_graph):
    state = await run_workflow(
        {"uid": "test-w-user", "assessment_id": "test-w-assess1"},
        with_router=True,
        message="G検定を受験したいので教材を登録してください",
    )
    assert state["router_output"]["intent"] == "register"
    assert "未実装" in state["summary"]
