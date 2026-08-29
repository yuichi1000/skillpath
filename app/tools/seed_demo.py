"""デモ用シードデータの投入 (app/cypher/seed_demo.cypher)。

  make seed                                # 投入 (冪等: 何度実行しても増えない)
  uv run python -m app.tools.seed_demo --clean   # demo- データを全削除
"""

import sys

from app.tools.init_schema import _statements
from app.tools.neo4j_tool import CYPHER_DIR, run_query

CLEAN = """
MATCH (n) WHERE coalesce(n.id, n.uid, '') STARTS WITH 'demo-'
DETACH DELETE n
"""


def seed() -> int:
    stmts = _statements((CYPHER_DIR / "seed_demo.cypher").read_text(encoding="utf-8"))
    for stmt in stmts:
        run_query(stmt)
    counts = run_query(
        "MATCH (n) WHERE coalesce(n.id, n.uid, '') STARTS WITH 'demo-' "
        "RETURN labels(n)[0] AS label, count(*) AS c ORDER BY label"
    )
    for row in counts:
        print(f"  {row['label']}: {row['c']}")
    return len(stmts)


if __name__ == "__main__":
    if "--clean" in sys.argv:
        run_query(CLEAN)
        print("demo データを削除しました")
    else:
        n = seed()
        print(f"seed 完了 ({n} statements)。Neo4j Browser: http://localhost:7474")
