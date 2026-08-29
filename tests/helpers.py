"""テスト共通ヘルパ (API クライアント / ワークフロー実行 / LLM テストの skip 条件)。"""

import os

import pytest
from google.adk.runners import InMemoryRunner
from google.genai import types
from httpx import ASGITransport, AsyncClient

from app.workflow.graph import build_workflow
from app.workflow.runtime import run_workflow_session

# @needs_api_key を付けたテストは実 API を呼ぶ (make test では除外、make test-all で実行)
needs_api_key = pytest.mark.skipif(
    not os.getenv("GOOGLE_API_KEY"), reason="GOOGLE_API_KEY 未設定 (LLM テストはスキップ)"
)


def client() -> AsyncClient:
    from app.main import app

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def run_workflow(
    initial_state: dict, *, with_router: bool = False, message: str = "模試結果を分析して"
) -> dict:
    """ワークフローを1回実行し、実行後のセッション状態を返す。"""
    runner = InMemoryRunner(node=build_workflow(with_router=with_router))
    content = types.Content(role="user", parts=[types.Part(text=message)])
    return await run_workflow_session(runner, initial_state["uid"], content, initial_state)
