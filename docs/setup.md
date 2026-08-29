# SkillPath ローカル開発セットアップ手順

ローカルで開発基盤（Python 環境 + Neo4j）を動かすまでの手順。
プロジェクトルート (`~/skillpath-design`) で実行する。

## Step 0: 前提ツールの確認

```bash
uv --version
docker info --format '{{.ServerVersion}}'
```

- `uv` が無い場合: `curl -LsSf https://astral.sh/uv/install.sh | sh`
- `docker info` が失敗する場合: Docker Desktop を起動する

## Step 1: Python 依存関係のインストール

```bash
uv sync
```

`pyproject.toml` の依存 (google-adk, neo4j, fastapi など) が `.venv/` に入る。

> よくある失敗: `google-adk` のバージョン解決エラー。出たらエラー全文を確認する。

## Step 2: ローカル Neo4j の起動

```bash
make neo4j        # 実体: docker compose up -d neo4j
docker compose ps # STATUS が running になっていること
```

- 初回はイメージ pull で 1〜2 分かかる
- Neo4j Browser: http://localhost:7474 （user: `neo4j` / pass: `localdevpassword`）
- データは `./neo4j-data/` に永続化される（gitignore 済み）

## Step 3: スキーマ適用

```bash
cp .env.example .env   # 初回のみ (.env はアプリ起動時に自動読み込みされる)
make schema                   # app/cypher/schema.cypher の制約・インデックスを適用
```

`applied 6 schema statements` と出れば成功。
ここが通れば Python → Bolt(7687) → Neo4j の経路がすべて動いている。

## 日常の操作

| やること | コマンド |
|---|---|
| Neo4j 起動 / 停止 | `make neo4j` / `docker compose down` |
| テスト | `make test`（単一: `uv run pytest tests/test_xxx.py::test_yyy`） |
| リント / フォーマット | `make lint` / `make fmt` |
| FastAPI ローカル起動 | `make dev`（port 8080） |

## トラブルシューティング

- **Bolt 接続エラー (`Connection refused`)**: Neo4j の起動完了前の可能性。`docker compose logs neo4j` で `Started.` が出ているか確認
- **認証エラー**: `.env` の `NEO4J_PASSWORD` と docker-compose.yml の `NEO4J_AUTH` が一致しているか確認（既定はどちらも `localdevpassword`）
- **環境変数が効かない**: `.env` は `app/config.py` が自動読み込みする。シェルで直接使いたい場合のみ `set -a; source .env; set +a` を実行する
