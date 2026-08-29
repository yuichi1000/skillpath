"""app/cypher/schema.cypher の制約・インデックスを適用する。

ローカル: `make schema`
本番:     POST /admin/init-schema (設計書 §7.10)
"""

from app.tools.neo4j_tool import CYPHER_DIR, run_query


def _statements(text: str) -> list[str]:
    out = []
    for chunk in text.split(";"):
        lines = [ln for ln in chunk.splitlines() if not ln.strip().startswith("//")]
        stmt = "\n".join(lines).strip()
        if stmt:
            out.append(stmt)
    return out


def init_schema() -> int:
    stmts = _statements((CYPHER_DIR / "schema.cypher").read_text(encoding="utf-8"))
    for stmt in stmts:
        run_query(stmt)
    return len(stmts)


if __name__ == "__main__":
    n = init_schema()
    print(f"applied {n} schema statements")
