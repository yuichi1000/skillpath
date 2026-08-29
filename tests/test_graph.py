"""ADK ワークフロー全体の統合テスト (weakness → planner → notifier)。"""

from google.adk.runners import InMemoryRunner
from google.genai import types

from app.workflow.graph import build_workflow


async def run_workflow(initial_state: dict) -> dict:
    """ワークフローを1回実行し、実行後のセッション状態を返す。"""
    runner = InMemoryRunner(node=build_workflow())
    uid = initial_state["uid"]
    session = await runner.session_service.create_session(
        app_name=runner.app_name, user_id=uid, state=initial_state
    )
    async for _event in runner.run_async(
        user_id=uid,
        session_id=session.id,
        new_message=types.Content(role="user", parts=[types.Part(text="模試結果を分析して")]),
    ):
        pass
    session = await runner.session_service.get_session(
        app_name=runner.app_name, user_id=uid, session_id=session.id
    )
    return session.state


async def test_weakness_branch_runs_planner_and_notifier(weakness_graph):
    state = await run_workflow({"uid": "test-w-user", "assessment_id": "test-w-assess1"})

    assert state["weakness"]["has_weakness"] is True
    # planner まで到達し、前提が先の順序で計画が入る
    assert [p["skill_id"] for p in state["plan"]["plan"]] == ["test-w-stats", "test-w-ml"]
    # notifier のサマリが生成されている
    assert "弱点クラスタ" in state["summary"]
    assert "test-w-stats" in state["summary"]


async def test_no_weakness_branch_goes_to_report(weakness_graph):
    # 閾値 0.3 なら弱点なし → report へ分岐し、planner は実行されない
    state = await run_workflow(
        {"uid": "test-w-user", "assessment_id": "test-w-assess1", "threshold": 0.3}
    )

    assert state["weakness"]["has_weakness"] is False
    assert state.get("plan") is None
    assert "弱点スキルはありませんでした" in state["summary"]
