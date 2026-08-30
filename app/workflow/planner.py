"""Planner — 学習順序の決定 (設計書 §4.2)。

決定的部分のみ実装:
- 依存部分グラフの取得 (planner.cypher)
- トポロジカルソート (Kahn 法)。循環は strength 最小のエッジを切って解消し警告に記録
- 習熟度を加味した所要時間の算出

教材選定 (LLM 判断) は未実装で、resource_id は None のまま返す。
"""

from dataclasses import dataclass

from app.models.schemas import PlanItem, PlannerOutput
from app.tools.neo4j_tool import run_named

MIN_BLOCK_MINUTES = 30


@dataclass
class SkillNode:
    skill_id: str
    name: str
    estimated_hours: float
    mastery: float
    depends_on: list[dict]  # [{"id": str, "strength": float}]


def fetch_subgraph(uid: str, target_skill_ids: list[str]) -> list[SkillNode]:
    rows = run_named(
        "planner.cypher", "dependency_subgraph", uid=uid, target_skill_ids=target_skill_ids
    )
    return [
        SkillNode(
            skill_id=r["skill_id"],
            name=r["name"],
            estimated_hours=r["estimated_hours"],
            mastery=r["mastery"],
            depends_on=r["depends_on"],
        )
        for r in rows
    ]


def topological_sort(nodes: list[SkillNode]) -> tuple[list[str], list[str]]:
    """前提が先に来る順序を返す (Kahn 法)。

    循環を検出したら、循環に関与する残ノード間のエッジのうち strength が最小の
    ものを1本無視して続行し、警告として返す (設計書 §4.2)。
    順序を決定的にするため、同時に取り出せるノードは skill_id 順で並べる。
    """
    ids = {n.skill_id for n in nodes}
    # deps[to] = {from: strength} (対象スキル集合内のエッジのみ)
    deps: dict[str, dict[str, float]] = {n.skill_id: {} for n in nodes}
    for n in nodes:
        for d in n.depends_on:
            if d["id"] in ids:
                deps[n.skill_id][d["id"]] = d["strength"]

    ordered: list[str] = []
    warnings: list[str] = []
    remaining = set(ids)
    # 1周ごとに「ノードが減る」か「エッジが1本減る」ので、その総数が反復の上限。
    # 不変条件が崩れても回り続けないよう明示的に打ち切る。
    budget = len(ids) + sum(len(d) for d in deps.values()) + 1
    while remaining:
        budget -= 1
        if budget < 0:
            warnings.append(
                f"依存関係の解消が収束しなかったため、残り {len(remaining)} スキルを"
                "ID 順で並べました"
            )
            ordered.extend(sorted(remaining))
            break
        ready = sorted(i for i in remaining if not (deps[i].keys() & remaining))
        if not ready:
            # 循環: strength 最小のエッジを切る (同率は id 順で決定的に)
            frm, to, strength = min(
                ((f, t, s) for t in remaining for f, s in deps[t].items() if f in remaining),
                key=lambda e: (e[2], e[0], e[1]),
            )
            del deps[to][frm]
            warnings.append(
                f"循環参照を検出: エッジ {frm} -> {to} (strength={strength}) を無視して解消"
            )
            continue
        for i in ready:
            ordered.append(i)
            remaining.discard(i)
    return ordered, warnings


def estimate_minutes(estimated_hours: float, mastery: float) -> int:
    """習熟度が高いほど短く。10分単位に丸め、最低 30 分。"""
    return max(MIN_BLOCK_MINUTES, int(round(estimated_hours * 60 * (1.0 - mastery), -1)))


def build_plan(uid: str, target_skill_ids: list[str]) -> PlannerOutput:
    nodes = fetch_subgraph(uid, target_skill_ids)
    ordered, warnings = topological_sort(nodes)
    by_id = {n.skill_id: n for n in nodes}
    plan = [
        PlanItem(
            order=i + 1,
            skill_id=sid,
            name=by_id[sid].name,
            estimated_minutes=estimate_minutes(by_id[sid].estimated_hours, by_id[sid].mastery),
        )
        for i, sid in enumerate(ordered)
    ]
    return PlannerOutput(plan=plan, warnings=warnings)
