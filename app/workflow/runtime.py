"""ワークフロー実行の共通ヘルパ (main.py / demo.py / テストで共用)。"""

from google.adk.runners import InMemoryRunner
from google.genai import types


async def run_workflow_session(
    runner: InMemoryRunner, uid: str, content: types.Content, state: dict
) -> dict:
    """1回分のワークフローを新規セッションで実行し、実行後の state を返す。"""
    session = await runner.session_service.create_session(
        app_name=runner.app_name, user_id=uid, state=state
    )
    async for _event in runner.run_async(
        user_id=uid, session_id=session.id, new_message=content
    ):
        pass
    session = await runner.session_service.get_session(
        app_name=runner.app_name, user_id=uid, session_id=session.id
    )
    return session.state
