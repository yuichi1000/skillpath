"""デモ実行 CLI — シード済みデータでワークフローを実行し、結果を表示する。

事前準備: make neo4j && make schema && make seed
実行:     make demo  (= uv run python -m app.demo)
"""

import asyncio

from google.adk.runners import InMemoryRunner
from google.genai import types

from app.workflow.graph import build_workflow

UID = "demo-user"
ASSESSMENT_ID = "demo-assess-1"


async def main() -> None:
    runner = InMemoryRunner(node=build_workflow())
    session = await runner.session_service.create_session(
        app_name=runner.app_name,
        user_id=UID,
        state={"uid": UID, "assessment_id": ASSESSMENT_ID},
    )
    print(f"=== SkillPath ワークフロー実行 (uid={UID}, assessment={ASSESSMENT_ID}) ===\n")
    async for event in runner.run_async(
        user_id=UID,
        session_id=session.id,
        new_message=types.Content(role="user", parts=[types.Part(text="模試の結果を分析して")]),
    ):
        if event.author and event.author != "user":
            print(f"▶ ノード実行: {event.author}")

    session = await runner.session_service.get_session(
        app_name=runner.app_name, user_id=UID, session_id=session.id
    )
    print("\n=== 結果サマリ (notifier の出力) ===\n")
    print(session.state.get("summary", "(summary なし)"))


if __name__ == "__main__":
    asyncio.run(main())
