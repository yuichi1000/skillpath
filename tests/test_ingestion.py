"""Ingestion Agent のテスト。store (決定的) は DB 統合、extract (LLM) はキー無しでスキップ。"""

import os

import pytest

from app.models.schemas import CoversIn, IngestionOutput, PrerequisiteIn, ResourceIn, SkillIn
from app.tools.neo4j_tool import run_query
from app.workflow.ingestion import store_ingestion

# @needs_api_key を付けたテストは実 API を呼ぶ (make test では除外、make test-all で実行)
needs_api_key = pytest.mark.skipif(
    not os.getenv("GOOGLE_API_KEY"), reason="GOOGLE_API_KEY 未設定 (LLM テストはスキップ)"
)


def ingestion_of() -> IngestionOutput:
    return IngestionOutput(
        skills=[
            SkillIn(name="test 機械学習"),  # 既存 (test-w-ml) に名寄せされるべき
            SkillIn(name="test-w ベイズ統計", estimated_hours=5.0),  # 新規
        ],
        resources=[ResourceIn(title="test-w 統計教科書", type="book", estimated_hours=20.0)],
        prerequisites=[
            PrerequisiteIn.model_validate(
                {"from": "test-w ベイズ統計", "to": "test 機械学習", "strength": 0.7}
            )
        ],
        covers=[
            CoversIn(
                resource_id="test-w 統計教科書",
                skill_id="test-w ベイズ統計",
                depth="standard",
                section="3章",
            )
        ],
    )


def test_store_resolves_names_and_writes_graph(weakness_graph):
    counts, targets, _cert_id = store_ingestion("test-w-user", ingestion_of(), threshold=0.6)

    assert {k: v for k, v in counts.items() if k != "by_domain"} == {
        "skills": 2, "resources": 1, "prerequisites": 1, "covers": 1
    }
    # 既存スキルは名寄せされ、同名の新ノードは作られない
    rows = run_query("MATCH (s:Skill) WHERE s.name = 'test 機械学習' RETURN s.id AS id")
    assert [r["id"] for r in rows] == ["test-w-ml"]
    # 前提エッジは実IDで張られる
    rows = run_query(
        "MATCH (:Skill {name: 'test-w ベイズ統計'})-[r:PREREQUISITE_OF]->(:Skill {id: 'test-w-ml'})"
        " RETURN r.strength AS s"
    )
    assert rows[0]["s"] == 0.7
    # 教材と COVERS
    rows = run_query(
        "MATCH (:Resource {title: 'test-w 統計教科書'})-[c:COVERS]->(s:Skill)"
        " RETURN s.name AS name, c.section AS sec"
    )
    assert rows[0]["name"] == "test-w ベイズ統計"
    # 両スキルとも未習熟 (ml は COMPLETED なし、ベイズは新規) なので初期計画の対象
    assert len(targets) == 2


def test_targets_exclude_mastered_skills(weakness_graph):
    out = IngestionOutput(
        skills=[SkillIn(name="test 数学基礎"), SkillIn(name="test-w ベイズ統計")],
        resources=[],
        prerequisites=[],
        covers=[],
    )
    _, targets, _ = store_ingestion("test-w-user", out, threshold=0.6)
    # 数学基礎は mastery 0.9 で除外され、新規のベイズ統計だけが対象
    assert targets == ["skill-test-w-ベイズ統計"]


def test_store_creates_certification_with_requires(weakness_graph):
    from app.models.schemas import CertProfile

    out = IngestionOutput(skills=[SkillIn(name="test-w ベイズ統計")])
    store_ingestion(
        "test-w-user", out, threshold=0.6,
        cert=CertProfile(name="test-w 統計検定", vendor="テスト協会"),
    )
    rows = run_query(
        "MATCH (c:Certification {name: 'test-w 統計検定'})-[r:REQUIRES]->(s:Skill)"
        " RETURN c.vendor AS vendor, r.weight AS w, s.name AS skill"
    )
    assert rows[0]["vendor"] == "テスト協会"
    assert rows[0]["w"] == 1.0
    assert rows[0]["skill"] == "test-w ベイズ統計"


def test_specialist_agent_is_synthesized_per_cert():
    from app.models.schemas import CertProfile
    from app.workflow.ingestion import build_cert_specialist, specialist_name

    profile = CertProfile(name="Professional Data Engineer", vendor="Google Cloud")
    agent = build_cert_specialist(profile)
    assert agent.name == "specialist_professional_data_engineer"
    assert "Google Cloud Professional Data Engineer" in agent.instruction
    # 資格を特定できない場合は汎用スペシャリストにフォールバック
    generic = build_cert_specialist(CertProfile())
    assert generic.name == "specialist_generic"
    assert specialist_name(CertProfile(name="G検定")) == "specialist_g検定"


def test_store_is_idempotent(weakness_graph):
    first = store_ingestion("test-w-user", ingestion_of(), threshold=0.6)
    second = store_ingestion("test-w-user", ingestion_of(), threshold=0.6)

    assert first == second
    rows = run_query("MATCH (s:Skill {name: 'test-w ベイズ統計'}) RETURN count(s) AS c")
    assert rows[0]["c"] == 1
    rows = run_query(
        "MATCH (:Skill {name: 'test-w ベイズ統計'})-[r:PREREQUISITE_OF]->() RETURN count(r) AS c"
    )
    assert rows[0]["c"] == 1


SYLLABUS_TEXT = """\
学習対象を登録してください。

【資格】test-w クラウド基礎検定
シラバス:
1. test-w ネットワーク入門 (学習目安 4時間)
2. test-w 仮想化技術 (学習目安 6時間)
※「test-w 仮想化技術」の理解には「test-w ネットワーク入門」が前提となる。

教材: 「test-w クラウド教科書」の1章がネットワーク入門、2章が仮想化技術をカバーする。
"""


@pytest.mark.llm
@needs_api_key
async def test_full_flow_from_pasted_syllabus(weakness_graph):
    from tests.helpers import run_workflow

    state = await run_workflow(
        {"uid": "test-w-user", "schedule_start": "2026-09-01T00:00:00"},
        with_router=True,
        message=SYLLABUS_TEXT,
    )
    assert state["router_output"]["intent"] == "register"
    # 資格が特定され、その資格専用スペシャリストが動的生成されている
    assert "クラウド基礎検定" in state["cert_profile"]["name"]
    assert state["specialist"].startswith("specialist_")
    assert len(state["ingestion_output"]["skills"]) >= 2
    assert state["plan_kind"] == "initial"
    assert len(state["target_skill_ids"]) >= 2
    # 初期計画がスケジュールまで到達している
    assert "初期学習計画" in state["summary"]
    assert "📅 学習スケジュール" in state["summary"]


def test_domain_is_stored_even_when_the_node_already_exists(weakness_graph):
    """公式セクション名 (domain) がノードに残る。

    名寄せが先にノードを作るため MERGE の ON CREATE は発火しない。
    ここが漏れると出題範囲の網羅性をグラフから検証できなくなる。
    """
    from app.models.schemas import IngestionOutput, SkillIn
    from app.tools.neo4j_tool import run_query

    out = IngestionOutput(
        skills=[SkillIn(name="test-w 負荷分散", domain="セクション3: マネージドサービス")]
    )
    store_ingestion("test-w-user", out, threshold=0.6)
    rows = run_query(
        "MATCH (s:Skill) WHERE s.match_key = $k RETURN s.domain AS d",
        k="testw負荷分散",
    )
    assert rows[0]["d"] == "セクション3: マネージドサービス"
