"""名寄せ (エンティティ解決)。

模試とシラバスで同じスキルの表記が揺れるのが常態で、ここが漏れると
スコアが登録済みグラフに接続されず、弱点検出そのものが成立しない。
実データで実際に起きた分裂を再現ケースとして固定する。
"""

import pytest

from app.tools.neo4j_tool import run_query
from app.workflow.entity import match_key, resolve_skill_id


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("ロードバランシング", "ロード バランシング"),          # 本番で分裂した実例
        ("VPC設計・IP管理", "VPC 設計・IP 管理"),
        ("GKEネットワーキング", "GKE ネットワーキング"),
        ("ネットワークセキュリティ (Cloud Armor)", "ネットワーク セキュリティ（Cloud Armor）"),
        ("Cloud Router・BGP", "cloud router bgp"),
        ("Ｃｌｏｕｄ　ＤＮＳ", "Cloud DNS"),                    # 全角
    ],
)
def test_notation_differences_collapse_to_one_key(a, b):
    assert match_key(a) == match_key(b)


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("ロードバランシング", "ロードバランサ"),
        ("VPC設計", "VPC Service Controls"),
        ("Cloud DNS", "Cloud CDN"),
    ],
)
def test_different_skills_keep_different_keys(a, b):
    assert match_key(a) != match_key(b)


def test_spacing_variant_resolves_to_the_existing_skill(weakness_graph):
    """シラバス側の表記で作られたノードに、模試側の表記が寄る。"""
    first = resolve_skill_id("test-w ロードバランシング")
    second = resolve_skill_id("test-w ロード バランシング")
    assert first == second
    rows = run_query(
        "MATCH (s:Skill) WHERE s.match_key = $k RETURN count(s) AS n",
        k=match_key("test-w ロードバランシング"),
    )
    assert rows[0]["n"] == 1  # ノードは増えていない


def test_wordy_variant_resolves_by_containment(weakness_graph):
    """語尾に説明が付いただけの表記も、長さが近ければ同じスキルに寄る。"""
    base = resolve_skill_id("test-w ロードバランシング")
    assert resolve_skill_id("test-w ロードバランシングの設計") == base


def test_short_substring_does_not_over_merge(weakness_graph):
    """短い語が長い名前に埋もれているだけの場合は、別スキルのまま。"""
    long_id = resolve_skill_id("test-w VPC設計とIPアドレス管理の実務")
    assert resolve_skill_id("test-w VPC") != long_id


def test_alias_lets_a_translated_name_resolve(weakness_graph):
    """英語名で登録されたスキルに、日本語の模試分野名が寄る (別表記の索引経由)。"""
    from app.tools.neo4j_tool import run_named
    from app.workflow.entity import slugify

    sid = f"skill-{slugify('test-w Cloud Load Balancing')}"
    run_named(
        "ingestion.cypher", "create_skill_if_absent",
        id=sid, name="test-w Cloud Load Balancing",
        key=match_key("test-w Cloud Load Balancing"),
        alias_keys=[match_key("test-w ロードバランシング")],
    )
    assert resolve_skill_id("test-w ロード バランシング") == sid


# ---- 模試の分野名がシラバスより粗い場合 ----


def test_question_count_is_not_part_of_the_skill_name():
    """「Cloud DNS (4問)」はシラバスの「Cloud DNS」と同じスキル。"""
    from app.workflow.sanitize import clean_name

    variants = [
        "Cloud DNS (4問)", "Cloud DNS（4問）", "Cloud DNS (4 questions)", "Cloud DNS(12題)",
    ]
    for raw in variants:
        assert clean_name(raw) == "Cloud DNS"
    assert clean_name("Cloud Run (v2)") == "Cloud Run (v2)"  # 出題数でない括弧は残す


def test_coarse_domain_scores_every_service_it_lists(weakness_graph):
    """模試の1分野が複数サービスを束ねている場合、その全部に配点する。"""
    from app.workflow.entity import resolve_assessment_skills

    nat = resolve_skill_id("test-w Cloud NAT")
    proxy = resolve_skill_id("test-w Secure Web Proxy")
    mirror = resolve_skill_id("test-w Packet Mirroring")

    got = resolve_assessment_skills(
        "test-w ネットワーク運用（test-w Cloud NAT・test-w Secure Web Proxy・"
        "test-w Packet Mirroring）"
    )
    assert set(got) == {nat, proxy, mirror}


def test_domain_matching_an_existing_skill_is_not_expanded(weakness_graph):
    """分野名そのものが既存スキルなら、括弧の中身へは展開しない。"""
    from app.workflow.entity import resolve_assessment_skills

    sid = resolve_skill_id("test-w Cloud DNS")
    assert resolve_assessment_skills("test-w Cloud DNS") == [sid]


def test_unknown_domain_still_creates_one_skill(weakness_graph):
    """どの既存スキルにも当たらない分野は、これまでどおり1件だけ作る。"""
    from app.workflow.entity import resolve_assessment_skills

    got = resolve_assessment_skills("test-w まったく新しい分野（未知A・未知B）")
    assert len(got) == 1


def test_parenthetical_inside_a_syllabus_name_is_indexed_as_an_alias(weakness_graph):
    """シラバス側が冗長な名前でも、模試側の短い分野名が当たる。

    「負荷分散（ロード バランシング）の構成」と「ロード バランシング」は
    長さ比 0.56 で包含判定を通らないため、括弧の中身を別表記として索引する。
    """
    from app.models.schemas import IngestionOutput, SkillIn
    from app.workflow.entity import resolve_assessment_skills
    from app.workflow.ingestion import store_ingestion

    store_ingestion(
        "test-w-user",
        IngestionOutput(skills=[SkillIn(name="test-w 負荷分散（ロード バランシング）の構成")]),
        threshold=0.6,
    )
    from app.workflow.entity import slugify

    expected = f"skill-{slugify('test-w 負荷分散（ロード バランシング）の構成')}"
    assert resolve_assessment_skills("ロード バランシング") == [expected]
