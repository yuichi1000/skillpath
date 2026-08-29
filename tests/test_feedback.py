"""Feedback Agent のテスト。store (決定的) は DB 統合、extract (LLM) はキー無しでスキップ。"""

import os

import pytest

from app.models.schemas import FeedbackOutput, PerSkillScore
from app.tools.neo4j_tool import run_query
from app.workflow.feedback import store_feedback

# @needs_api_key を付けたテストは実 API を呼ぶ (make test では除外、make test-all で実行)
needs_api_key = pytest.mark.skipif(
    not os.getenv("GOOGLE_API_KEY"), reason="GOOGLE_API_KEY 未設定 (LLM テストはスキップ)"
)


def feedback_of(*scores: tuple[str, int, int], taken_at="2026-08-28T10:00:00") -> FeedbackOutput:
    return FeedbackOutput(
        taken_at=taken_at,
        source="単体テスト模試",
        total_score=0.5,
        per_skill=[
            PerSkillScore(skill_name=name, correct=c, total=t, score=round(c / t, 2))
            for name, c, t in scores
        ],
    )


def test_store_matches_existing_skill_and_creates_new(weakness_graph):
    fb = feedback_of(("test 機械学習", 4, 10), ("test-w 量子コンピューティング", 2, 10))
    assessment_id = store_feedback("test-w-user", fb)

    assert assessment_id == "test-w-user-assess-2026-08-28T10:00:00"
    rows = run_query(
        "MATCH (:Assessment {id: $aid})-[r:ASSESSED]->(s:Skill)"
        " RETURN s.id AS sid, s.name AS name, r.score AS score ORDER BY sid",
        aid=assessment_id,
    )
    assert len(rows) == 2
    # 既存スキルに名寄せされ、新規ノードは作られない
    assert rows[1]["sid"] == "test-w-ml"
    # 未知のスキル名は新規 Skill として作成される
    assert rows[0]["name"] == "test-w 量子コンピューティング"


def test_store_updates_mastery_with_ema(weakness_graph):
    # test-w-user の確率統計は mastery 0.3。スコア 0.9 → 0.4*0.3 + 0.6*0.9 = 0.66
    store_feedback("test-w-user", feedback_of(("test 確率統計", 9, 10)))
    rows = run_query(
        "MATCH (:User {uid: 'test-w-user'})-[c:COMPLETED]->(:Skill {id: 'test-w-stats'})"
        " RETURN c.mastery AS m"
    )
    assert rows[0]["m"] == pytest.approx(0.66)


def test_store_is_idempotent(weakness_graph):
    fb = feedback_of(("test 機械学習", 4, 10))
    first = store_feedback("test-w-user", fb)
    second = store_feedback("test-w-user", fb)

    assert first == second
    rows = run_query("MATCH (a:Assessment {id: $aid}) RETURN count(a) AS c", aid=first)
    assert rows[0]["c"] == 1


MOCK_EXAM_TEXT = """\
模試の結果を報告します。分析お願いします。

G検定 模擬試験 第2回 (2026-08-29 受験)
総合: 31/50

分野別:
- test 機械学習: 7/10
- test データベース: 6/10
"""


@pytest.mark.llm
@needs_api_key
async def test_full_flow_from_pasted_text(weakness_graph):
    from tests.test_graph import run_workflow

    state = await run_workflow(
        {"uid": "test-w-user", "schedule_start": "2026-09-01T00:00:00"},
        with_router=True,
        message=MOCK_EXAM_TEXT,
    )
    assert state["router_output"]["intent"] == "assessment"
    # 抽出 → 名寄せ → 書き込み → weakness まで一気通貫
    assert state["assessment_id"].startswith("test-w-user-assess-")
    per_skill = state["feedback_output"]["per_skill"]
    assert {p["skill_name"] for p in per_skill} == {"test 機械学習", "test データベース"}
    assert state["weakness"] is not None
