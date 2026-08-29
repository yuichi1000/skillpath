"""Cypher 実行の共通 function tool。

- クエリ本文は app/cypher/*.cypher に置き、`// -- name: xxx --` マーカーで分割して読み込む
- 書き込みは MERGE ベースで冪等に (設計書 §5)
- ユーザーデータ分離のため、ユーザー起点のクエリは必ず uid をパラメータに含めること (設計書 §6)
"""

import re
from functools import lru_cache
from pathlib import Path

from neo4j import Driver, GraphDatabase

from app.config import get_settings

CYPHER_DIR = Path(__file__).resolve().parent.parent / "cypher"

_driver: Driver | None = None


def get_driver() -> Driver:
    global _driver
    if _driver is None:
        s = get_settings()
        _driver = GraphDatabase.driver(
            s.neo4j_uri,
            auth=(s.neo4j_user, s.neo4j_password),
            # 「aliases プロパティが未使用」等のサーバ通知でログが埋まるのを抑制
            notifications_min_severity="OFF",
        )
    return _driver


def close_driver() -> None:
    global _driver
    if _driver is not None:
        _driver.close()
        _driver = None


@lru_cache
def load_queries(filename: str) -> dict[str, str]:
    """`// -- name: xxx --` マーカーで区切られた .cypher ファイルを {name: query} で返す。"""
    text = (CYPHER_DIR / filename).read_text(encoding="utf-8")
    parts = re.split(r"//\s*--\s*name:\s*(\w+)\s*--", text)
    # parts = [先頭コメント, name1, body1, name2, body2, ...]
    return {parts[i]: parts[i + 1].strip() for i in range(1, len(parts) - 1, 2)}


def run_query(query: str, **params) -> list[dict]:
    with get_driver().session() as session:
        return [r.data() for r in session.run(query, **params)]


def run_named(filename: str, query_name: str, /, **params) -> list[dict]:
    # 位置専用 (/) にして、クエリパラメータ名 (例: $name) と衝突しないようにする
    return run_query(load_queries(filename)[query_name], **params)
