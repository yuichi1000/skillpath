"""Weakness Detector — LLM を使わない決定的ノード (設計書 §4.2)。

低スコアスキルと、その前提を最大4階層遡った「弱点クラスタ」を特定する。
ロジックの本体は app/cypher/weakness.cypher にある。
"""

from app.models.schemas import UnmasteredPrerequisite, WeaknessOutput, WeakSkill
from app.tools.neo4j_tool import run_named

DEFAULT_THRESHOLD = 0.6


def detect_weakness(
    assessment_id: str, uid: str, threshold: float = DEFAULT_THRESHOLD
) -> WeaknessOutput:
    rows = run_named(
        "weakness.cypher",
        "weakness_cluster",
        assessment_id=assessment_id,
        uid=uid,
        threshold=threshold,
    )
    weak_skills: list[WeakSkill] = []
    cluster: set[str] = set()
    for row in rows:
        ws = WeakSkill(
            weak_skill_id=row["weak_skill_id"],
            weak_skill_name=row["weak_skill_name"],
            score=row["score"],
            unmastered_prerequisites=[
                UnmasteredPrerequisite(**p) for p in row["unmastered_prerequisites"]
            ],
        )
        weak_skills.append(ws)
        cluster.add(ws.weak_skill_id)
        cluster.update(p.id for p in ws.unmastered_prerequisites)
    return WeaknessOutput(
        weak_skills=weak_skills,
        cluster=sorted(cluster),
        has_weakness=bool(weak_skills),
    )
