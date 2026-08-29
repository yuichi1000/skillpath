"""ADK ワークフロー全体の統合テスト (weakness → planner → notifier)。"""

from google.adk.runners import InMemoryRunner
from google.genai import types

from app.workflow.graph import build_workflow


async def run_workflow(
    initial_state: dict, *, with_router: bool = False, message: str = "模試結果を分析して"
) -> dict:
    """ワークフローを1回実行し、実行後のセッション状態を返す。"""
    runner = InMemoryRunner(node=build_workflow(with_router=with_router))
    uid = initial_state["uid"]
    session = await runner.session_service.create_session(
        app_name=runner.app_name, user_id=uid, state=initial_state
    )
    async for _event in runner.run_async(
        user_id=uid,
        session_id=session.id,
        new_message=types.Content(role="user", parts=[types.Part(text=message)]),
    ):
        pass
    session = await runner.session_service.get_session(
        app_name=runner.app_name, user_id=uid, session_id=session.id
    )
    return session.state


async def test_weakness_branch_runs_planner_scheduler_notifier(weakness_graph):
    state = await run_workflow(
        {
            "uid": "test-w-user",
            "assessment_id": "test-w-assess1",
            "schedule_start": "2026-09-01T00:00:00",  # 固定してスケジュールを決定的に
        }
    )

    assert state["weakness"]["has_weakness"] is True
    # planner まで到達し、前提が先の順序で計画が入る
    assert [p["skill_id"] for p in state["plan"]["plan"]] == ["test-w-stats", "test-w-ml"]
    # scheduler が空き時間 (毎晩20-22時のプレースホルダ) に配置している
    sessions = state["sessions"]
    assert [s["skill_id"] for s in sessions] == ["test-w-stats", "test-w-ml"]
    assert sessions[0]["start"].startswith("2026-09-02T20:00")
    assert sessions[0]["session_id"] == "test-w-assess1-test-w-stats-b1"
    # notifier のサマリにスケジュールが含まれる
    assert "弱点クラスタ" in state["summary"]
    assert "test 確率統計" in state["summary"]  # 計画はスキル名で表示される
    assert "📅 学習スケジュール" in state["summary"]


async def test_no_weakness_branch_goes_to_report(weakness_graph):
    # 閾値 0.3 なら弱点なし → report へ分岐し、planner は実行されない
    state = await run_workflow(
        {"uid": "test-w-user", "assessment_id": "test-w-assess1", "threshold": 0.3}
    )

    assert state["weakness"]["has_weakness"] is False
    assert state.get("plan") is None
    assert "弱点スキルはありませんでした" in state["summary"]


async def test_deadline_from_state_limits_schedule(weakness_graph):
    # deadline を state 直指定 (Web UI 想定)。期限内に収まる場合は ✅ が出る
    state = await run_workflow(
        {
            "uid": "test-w-user",
            "assessment_id": "test-w-assess1",
            "schedule_start": "2026-09-01T00:00:00",
            "deadline": "2026-09-03",
        }
    )
    sessions = state["sessions"]
    assert sessions  # stats 40分 + ml 60分 は 9/2 夜の1スロットに収まる
    assert all(s["end"] < "2026-09-03" for s in sessions)
    assert "🎯 目標期限: 2026-09-03" in state["summary"]
    assert "✅ 計画は期限内に収まっています" in state["summary"]
