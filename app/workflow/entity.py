"""名寄せ (エンティティ解決) — 模試・シラバス・教材から来る名前を単一ノードへ束ねる。

Feedback / Ingestion の両方が使う共通処理。ここが機能しないと、模試のスコアが
登録済みグラフに接続されず、前提を遡る弱点検出が成立しない。
"""

import re
import unicodedata

from app.tools.neo4j_tool import run_named


def slugify(name: str) -> str:
    s = unicodedata.normalize("NFKC", name).strip().lower()
    return re.sub(r"[^0-9a-zA-Zぁ-んァ-ン一-龥ー]+", "-", s).strip("-")


def resolve_skill_id(name: str) -> str:
    """スキル名を既存 Skill に名寄せし、無ければ新規作成して id を返す (設計書 §4.2)。

    新規作成されたスキルは前提関係が空のノードとして Planner に扱われる。
    """
    rows = run_named("ingestion.cypher", "dedupe_skill", name=name)
    if rows:
        return rows[0]["id"]
    skill_id = f"skill-{slugify(name)}"
    run_named("ingestion.cypher", "create_skill_if_absent", id=skill_id, name=name)
    return skill_id


def ensure_user(uid: str) -> None:
    run_named("ingestion.cypher", "ensure_user", uid=uid)
