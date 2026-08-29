from app.workflow.weakness import detect_weakness


def test_detects_weak_skill_and_traces_unmastered_prereqs(weakness_graph):
    out = detect_weakness("test-w-assess1", "test-w-user")

    assert out.has_weakness
    # 弱点は機械学習のみ (データベースは 0.9 で閾値以上)
    assert [w.weak_skill_id for w in out.weak_skills] == ["test-w-ml"]
    assert out.weak_skills[0].score == 0.4
    # 前提のうち未習熟の確率統計は入り、習熟済みの数学基礎は入らない
    prereq_ids = {p.id for p in out.weak_skills[0].unmastered_prerequisites}
    assert prereq_ids == {"test-w-stats"}
    # クラスタ = 弱点 + 未習熟前提
    assert out.cluster == ["test-w-ml", "test-w-stats"]


def test_no_weakness_when_all_scores_above_threshold(weakness_graph):
    out = detect_weakness("test-w-assess1", "test-w-user", threshold=0.3)

    assert not out.has_weakness
    assert out.weak_skills == []
    assert out.cluster == []


def test_weak_skill_reported_even_if_all_prereqs_mastered(weakness_graph):
    # 設計書クエリのバグ修正の検証: 前提が全て習熟済みでも弱点スキル自体は返る
    out = detect_weakness("test-w-assess2", "test-w-user2")

    assert out.has_weakness
    assert [w.weak_skill_id for w in out.weak_skills] == ["test-w-ml"]
    assert out.weak_skills[0].unmastered_prerequisites == []
    assert out.cluster == ["test-w-ml"]


def test_uid_isolation_blocks_other_users_assessment(weakness_graph):
    # 他人の assessment_id を渡しても何も返らない (§6 ユーザーデータ分離)
    out = detect_weakness("test-w-assess1", "test-w-user2")

    assert not out.has_weakness
