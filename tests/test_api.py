"""FastAPI エンドポイントのテスト。/run は LLM を呼ぶため @llm マーク。"""

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app

async def client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_healthz():
    async with await client() as c:
        res = await c.get("/healthz")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}


async def test_init_schema(neo4j):
    async with await client() as c:
        res = await c.post("/admin/init-schema")
    assert res.status_code == 200
    assert res.json()["applied"] == 6


async def test_seed_demo(neo4j):
    async with await client() as c:
        res = await c.post("/admin/seed-demo")
    assert res.status_code == 200
    assert res.json()["applied"] > 0


async def test_ingestion_task_stub():
    async with await client() as c:
        res = await c.post("/tasks/ingestion")
    assert res.status_code == 202


@pytest.mark.llm
async def test_run_assessment_flow(weakness_graph):
    async with await client() as c:
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
