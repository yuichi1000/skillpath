.PHONY: dev neo4j test lint fmt schema seed demo

dev:            ## FastAPI をローカル起動
	uv run uvicorn app.main:app --reload --port 8080

neo4j:          ## ローカル Neo4j 起動
	docker compose up -d neo4j

schema:         ## ローカル Neo4j に制約・インデックスを適用
	uv run python -m app.tools.init_schema

seed:            ## デモデータ投入 (冪等)
	uv run python -m app.tools.seed_demo

demo:            ## デモ実行 (weakness→planner→notifier)
	uv run python -m app.demo

test:
	uv run pytest

lint:
	uv run ruff check .

fmt:
	uv run ruff format . && uv run ruff check --fix .
