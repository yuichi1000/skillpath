"""FastAPI エンドポイントのテスト。/run は LLM を呼ぶため @llm マーク。"""

import pytest

from tests.helpers import client


async def test_index_serves_ui():
    async with client() as c:
        res = await c.get("/")
    assert res.status_code == 200
    assert "SkillPath" in res.text
    assert "知識グラフ" in res.text


async def test_health():
    async with client() as c:
        res = await c.get("/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}


async def test_init_schema(neo4j, monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin")
    async with client() as c:
        res = await c.post("/admin/init-schema", headers={"X-Admin-Token": "test-admin"})
    assert res.status_code == 200
    assert res.json()["applied"] == 7


async def test_seed_demo(neo4j, monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin")
    async with client() as c:
        res = await c.post("/admin/seed-demo", headers={"X-Admin-Token": "test-admin"})
    assert res.status_code == 200
    assert res.json()["applied"] > 0


async def test_ingestion_task_stub():
    async with client() as c:
        res = await c.post("/tasks/ingestion")
    assert res.status_code == 202


@pytest.mark.llm
async def test_run_assessment_flow(weakness_graph):
    async with client() as c:
        res = await c.post(
            "/run",
            json={
                "uid": "test-w-user",
                "message": "模試の結果です。分析してください。\n- test 機械学習: 4/10",
                "schedule_start": "2026-09-01T00:00:00",
            },
        )
    assert res.status_code == 200
    body = res.json()
    assert body["intent"] == "assessment"
    assert body["assessment_id"].startswith("test-w-user-assess-")
    assert body["summary"]


async def test_graph_view(neo4j, weakness_graph):
    async with client() as c:
        res = await c.get("/graph", params={"uid": "test-w-user"})
    assert res.status_code == 200
    body = res.json()
    ids = {n["id"] for n in body["nodes"]}
    assert {"test-w-ml", "test-w-stats", "test-w-math"} <= ids
    assert any(
        e["source"] == "test-w-math" and e["target"] == "test-w-stats" for e in body["edges"]
    )
    # 習熟度と直近スコアがノードに載る
    ml = next(n for n in body["nodes"] if n["id"] == "test-w-ml")
    assert ml["latest_score"] == 0.4
    assert len(body["assessments"]) >= 1


async def test_graph_view_respects_allowlist(monkeypatch, neo4j):
    monkeypatch.setenv("ALLOWED_UIDS", "demo-user")
    async with client() as c:
        res = await c.get("/graph", params={"uid": "test-w-user"})
    assert res.status_code == 403


async def test_graph_shows_every_skill_the_exam_requires(weakness_graph):
    """学習ブロックがまだ無いスキルも、受験予定の資格の範囲なら UI に出る。

    ここが漏れると「エージェントが調べた出題範囲が勝手に減った」ように見える。
    """
    from app.tools.neo4j_tool import run_query

    run_query(
        """
        MATCH (u:User {uid: 'test-w-user'})
        CREATE (c:Certification {id: 'test-w-cert', name: 'test 資格'})
        CREATE (orphan:Skill {id: 'test-w-orphan', name: 'test 未計画スキル',
                              match_key: 'test未計画スキル'})
        CREATE (u)-[:PURSUES {deadline: date('2026-12-01')}]->(c)
        CREATE (c)-[:REQUIRES]->(orphan)
        """
    )
    async with client() as c:
        res = await c.get("/graph", params={"uid": "test-w-user"})
    ids = {n["id"] for n in res.json()["nodes"]}
    assert "test-w-orphan" in ids
