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
