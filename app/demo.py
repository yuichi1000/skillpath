"""デモ実行 CLI — 2幕構成でワークフローを実行する。

第1幕: シラバスの貼り付け → スキルグラフ構築 + 初期学習計画
第2幕: 模試結果の貼り付け → 弱点クラスタ検出 + 復習計画

事前準備: make neo4j && make schema && make seed
実行:     make demo  (= uv run python -m app.demo)
"""

import asyncio

from google.adk.runners import InMemoryRunner
from google.genai import types

from app.workflow.graph import build_workflow

UID = "demo-user"

REGISTER_MESSAGE = """\
学習対象を登録してください。

【資格】JDLA E資格 (デモ)
シラバス抜粋:
1. 応用数学
  - 情報理論 (学習目安 4時間)
  - ベイズ則 (学習目安 4時間)
2. 深層学習の実装
  - 最適化手法 SGD/Adam (学習目安 6時間)
  - 正則化 ドロップアウト/バッチ正規化 (学習目安 5時間)
※「最適化手法 SGD/Adam」と「正則化 ドロップアウト/バッチ正規化」の理解には
  「情報理論」「ベイズ則」の内容が前提となる。

教材: 「ゼロから作るDeep Learning(デモ)」の4-6章が最適化手法と正則化をカバーする。
"""

EXAM_MESSAGE = """\
模試の結果を報告します。本試験は 2026年11月15日 です。それまでに間に合う復習計画を立ててください。

G検定 模擬試験 第2回 (2026-08-29 受験)
総合: 62/100

分野別:
- 機械学習基礎: 8/10
- ディープラーニング手法: 12/20
- CNN(画像認識): 9/20
- 自然言語処理: 10/20
- AI倫理・法律: 9/10
"""


async def run_act(runner: InMemoryRunner, title: str, message: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"■ {title}")
    print("=" * 60)
    print(message)
    session = await runner.session_service.create_session(
        app_name=runner.app_name, user_id=UID, state={"uid": UID}
    )
    async for event in runner.run_async(
        user_id=UID,
        session_id=session.id,
        new_message=types.Content(role="user", parts=[types.Part(text=message)]),
    ):
        if event.author and event.author not in ("user", "skillpath"):
            print(f"▶ ノード実行: {event.author}")
    session = await runner.session_service.get_session(
        app_name=runner.app_name, user_id=UID, session_id=session.id
    )
    print("\n--- 結果サマリ ---")
    print(session.state.get("summary", "(summary なし)"))


async def main() -> None:
    runner = InMemoryRunner(node=build_workflow())
    await run_act(runner, "第1幕: 学習対象の登録 (Ingestion)", REGISTER_MESSAGE)
    await run_act(runner, "第2幕: 模試結果の分析 (Feedback)", EXAM_MESSAGE)


if __name__ == "__main__":
    asyncio.run(main())
