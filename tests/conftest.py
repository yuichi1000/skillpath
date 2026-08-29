"""テスト共通フィクスチャ。

Cypher のロジックが本体のため、モックせずローカル Neo4j (make neo4j) に対して
統合テストを行う。テストデータは 'test-w-' プレフィックスで投入し、前後で削除する。
"""

import time

import pytest

from app.tools import neo4j_tool
from app.tools.init_schema import init_schema

CLEANUP = """
MATCH (n) WHERE coalesce(n.id, n.uid, '') CONTAINS 'test-w'
DETACH DELETE n
"""

SEED = """
CREATE (u:User {uid: 'test-w-user', timezone: 'Asia/Tokyo'})
CREATE (u2:User {uid: 'test-w-user2', timezone: 'Asia/Tokyo'})
CREATE (math:Skill {id: 'test-w-math', name: 'test 数学基礎'})
CREATE (stats:Skill {id: 'test-w-stats', name: 'test 確率統計'})
CREATE (ml:Skill {id: 'test-w-ml', name: 'test 機械学習'})
CREATE (db:Skill {id: 'test-w-db', name: 'test データベース'})
CREATE (math)-[:PREREQUISITE_OF {strength: 0.9}]->(stats)
CREATE (stats)-[:PREREQUISITE_OF {strength: 0.9}]->(ml)

// user1: 数学基礎は習熟済み、確率統計は未習熟。模試で機械学習が弱点
CREATE (u)-[:COMPLETED {mastery: 0.9}]->(math)
CREATE (u)-[:COMPLETED {mastery: 0.3}]->(stats)
CREATE (a:Assessment {id: 'test-w-assess1', source: 'test', total_score: 0.65})
CREATE (u)-[:TOOK]->(a)
CREATE (a)-[:ASSESSED {score: 0.4, correct: 4, total: 10}]->(ml)
CREATE (a)-[:ASSESSED {score: 0.9, correct: 9, total: 10}]->(db)

// user2: 前提が全て習熟済みの状態で機械学習が弱点
CREATE (u2)-[:COMPLETED {mastery: 0.9}]->(math)
CREATE (u2)-[:COMPLETED {mastery: 0.9}]->(stats)
CREATE (a2:Assessment {id: 'test-w-assess2', source: 'test', total_score: 0.4})
CREATE (u2)-[:TOOK]->(a2)
CREATE (a2)-[:ASSESSED {score: 0.4, correct: 4, total: 10}]->(ml)
"""


@pytest.fixture(autouse=True)
def _llm_pacing(request):
    """LLM テストの後に間隔を空け、無料枠の RPM 制限 (約10回/分) を回避する。"""
    yield
    if request.node.get_closest_marker("llm"):
        time.sleep(20)


@pytest.fixture(scope="session")
def neo4j():
    try:
        neo4j_tool.get_driver().verify_connectivity()
    except Exception:
        pytest.skip("ローカル Neo4j に接続できない (make neo4j で起動する)")
    init_schema()
    yield
    neo4j_tool.close_driver()


@pytest.fixture()
def weakness_graph(neo4j):
    neo4j_tool.run_query(CLEANUP)
    neo4j_tool.run_query(SEED)
    yield
    neo4j_tool.run_query(CLEANUP)
