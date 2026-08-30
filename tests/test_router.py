"""Router (LLM) の分類テスト — 実 API を呼ぶため GOOGLE_API_KEY が無ければスキップ。

課金・レイテンシを抑えるため最小限の2ケースのみ。決定的な経路の検証は
test_graph.py (with_router=False) が担う。
"""

import os

import pytest

from tests.helpers import run_workflow

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
        message="2026年11月15日に本試験を受けます。模試を受けたので結果を分析してください",
    )
    assert state["router_output"]["intent"] == "assessment"
    assert state["deadline"] == "2026-11-15"  # 期限が抽出され state に入る
    assert state["user_input"].startswith("2026年")


@pytest.mark.llm
@needs_api_key
async def test_query_intent_goes_to_stub(weakness_graph):
    # register 経路は Ingestion 実装済みのため test_ingestion.py が担う。
    # ここでは3つ目の経路 (query) の分類とスタブ到達を検証する
    state = await run_workflow(
        {"uid": "test-w-user"},
        with_router=True,
        message="今週の学習予定を教えて",
    )
    assert state["router_output"]["intent"] == "query"
    assert "登録も採点も行いませんでした" in state["summary"]


def test_output_schemas_have_no_empty_enum_values():
    """LLM に渡す JSON スキーマに空文字の enum を入れない。

    Gemini は response_schema の enum に空文字があると 400 を返す
    (本番で refusal="" がこれに当たった)。全 LLM ノードの出力スキーマを見張る。
    """
    from app.models.schemas import (
        CertProfile,
        FeedbackOutput,
        IngestionOutput,
        RouterOutput,
    )

    def enums(schema: dict) -> list[list]:
        found = []
        if isinstance(schema, dict):
            if "enum" in schema:
                found.append(schema["enum"])
            for value in schema.values():
                found += enums(value)
        elif isinstance(schema, list):
            for item in schema:
                found += enums(item)
        return found

    for model in (RouterOutput, CertProfile, FeedbackOutput, IngestionOutput):
        for values in enums(model.model_json_schema()):
            assert "" not in values, f"{model.__name__} の enum に空文字がある: {values}"
