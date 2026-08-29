"""デモ実行 CLI — シード済みデータでワークフローを実行し、結果を表示する。

事前準備: make neo4j && make schema && make seed
実行:     make demo  (= uv run python -m app.demo)
"""

import asyncio

from google.adk.runners import InMemoryRunner
from google.genai import types

from app.workflow.graph import build_workflow

UID = "demo-user"

# デモ入力: 模試結果の貼り付けテキスト。Router が assessment に分類し、
# Feedback Agent が抽出 → 名寄せ → DB 書き込みまで行う (seed 済み assessment には依存しない)
DEMO_MESSAGE = """\
模試の結果を報告します。復習計画を立ててください。

G検定 模擬試験 第2回 (2026-08-29 受験)
総合: 62/100

分野別:
- 機械学習基礎: 8/10
- ディープラーニング手法: 12/20
- CNN(画像認識): 9/20
- 自然言語処理: 10/20
- AI倫理・法律: 9/10
"""


async def main() -> None:
    runner = InMemoryRunner(node=build_workflow())
    session = await runner.session_service.create_session(
        app_name=runner.app_name,
        user_id=UID,
        state={"uid": UID},
    )
    print(f"=== SkillPath ワークフロー実行 (uid={UID}) ===")
    print("--- 入力 (模試結果の貼り付け) ---")
    print(DEMO_MESSAGE)
    async for event in runner.run_async(
        user_id=UID,
        session_id=session.id,
        new_message=types.Content(role="user", parts=[types.Part(text=DEMO_MESSAGE)]),
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
