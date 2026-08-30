"""名寄せ (エンティティ解決) — 模試・シラバス・教材から来る名前を単一ノードへ束ねる。

Feedback / Ingestion の両方が使う共通処理。ここが機能しないと、模試のスコアが
登録済みグラフに接続されず、前提を遡る弱点検出が成立しない。
"""

import logging
import re
import unicodedata

from app.tools.neo4j_tool import run_named

logger = logging.getLogger(__name__)


def slugify(name: str) -> str:
    """id 用の読めるスラグ。区切りは残す (人が見る文字列なので)。"""
    s = unicodedata.normalize("NFKC", name).strip().lower()
    return re.sub(r"[^0-9a-zA-Zぁ-んァ-ン一-龥ー]+", "-", s).strip("-")


def match_key(name: str) -> str:
    """名寄せ専用の正規化キー。区切りを **すべて落として** 比較する。

    シラバスと模試で同じスキルの表記が揺れるのが常態のため
    (「ロードバランシング」と「ロード バランシング」、
     「VPC設計・IP管理」と「VPC 設計・IP 管理」)、
    空白・記号・括弧・中黒は同一視する。NFKC で全角/半角も吸収する。
    """
    s = unicodedata.normalize("NFKC", name).lower()
    return re.sub(r"[^0-9a-zぁ-んァ-ン一-龥ー]+", "", s)


def resolve_skill_id(name: str, aliases: list[str] | None = None) -> str:
    """スキル名を既存 Skill に名寄せし、無ければ新規作成して id を返す (設計書 §4.2)。

    3段階で寄せる:
      1. 正規化キーの完全一致 (表記ゆれを吸収)
      2. 包含関係にあり、長さ比 0.6 以上のもの (「GCPのロードバランシング」↔「ロードバランシング」)
      3. どれにも当たらなければ新規作成
    ここが漏れると模試のスコアが登録済みグラフに接続されず、弱点検出が成立しない。
    """
    key = match_key(name)
    if not key:
        key = slugify(name) or name.strip().lower()
    rows = run_named("ingestion.cypher", "dedupe_skill", key=key, name=name.strip().lower())
    if rows:
        return rows[0]["id"]
    rows = run_named("ingestion.cypher", "fuzzy_skill", key=key)
    if rows:
        logger.info(
            "スキルを近似一致で名寄せ: %r → %s (%.2f)", name, rows[0]["id"], rows[0]["ratio"]
        )
        return rows[0]["id"]
    skill_id = f"skill-{slugify(name)}"
    run_named(
        "ingestion.cypher",
        "create_skill_if_absent",
        id=skill_id,
        name=name,
        key=key,
        alias_keys=sorted({match_key(a) for a in aliases or [] if match_key(a)}),
    )
    return skill_id


def ensure_user(uid: str) -> None:
    run_named("ingestion.cypher", "ensure_user", uid=uid)
