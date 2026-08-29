"""FastAPI エントリポイント (Cloud Run) — 設計書 §8。

エンドポイント:
- GET  /healthz         死活監視
- POST /run             ワークフロー実行 (Web UI / デモが使用)
- POST /admin/init-schema  Neo4j スキーマ初期化 (設計書 §7.10)
- POST /tasks/ingestion Pub/Sub push 受け口 (現状スタブ)

認証は Cloud Run の IAM に委ねる (アプリ層では uid を受けるのみ)。
"""

from fastapi import FastAPI
from google.adk.runners import InMemoryRunner
from google.genai import types
from pydantic import BaseModel

from app.tools.init_schema import init_schema
from app.tools.seed_demo import seed
from app.workflow.graph import build_workflow

app = FastAPI(title="SkillPath", version="0.1.0")

# プロセス内シングルトン。Cloud Run のインスタンス単位で使い回す
_runner: InMemoryRunner | None = None


def get_runner() -> InMemoryRunner:
    global _runner
    if _runner is None:
        _runner = InMemoryRunner(node=build_workflow())
    return _runner


class RunRequest(BaseModel):
    uid: str
    message: str
    deadline: str = ""  # ISO 日付。message からも抽出されるが直接指定も可
    schedule_start: str = ""  # テスト・検証用


class RunResponse(BaseModel):
    summary: str
    intent: str = ""
    assessment_id: str = ""
    plan_kind: str = ""
    deadline: str = ""
    weakness: dict | None = None
    plan: dict | None = None
    sessions: list[dict] = []
    schedule_warnings: list[str] = []


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}


@app.post("/run", response_model=RunResponse)
async def run(req: RunRequest) -> RunResponse:
    runner = get_runner()
    state: dict = {"uid": req.uid}
    if req.deadline:
        state["deadline"] = req.deadline
    if req.schedule_start:
        state["schedule_start"] = req.schedule_start
    session = await runner.session_service.create_session(
        app_name=runner.app_name, user_id=req.uid, state=state
    )
    async for _event in runner.run_async(
        user_id=req.uid,
        session_id=session.id,
        new_message=types.Content(role="user", parts=[types.Part(text=req.message)]),
    ):
        pass
    session = await runner.session_service.get_session(
        app_name=runner.app_name, user_id=req.uid, session_id=session.id
    )
    s = session.state
    return RunResponse(
        summary=s.get("summary", ""),
        intent=(s.get("router_output") or {}).get("intent", ""),
        assessment_id=s.get("assessment_id", ""),
        plan_kind=s.get("plan_kind", ""),
        deadline=s.get("deadline", ""),
        weakness=s.get("weakness"),
        plan=s.get("plan"),
        sessions=s.get("sessions") or [],
        schedule_warnings=s.get("schedule_warnings") or [],
    )


@app.post("/admin/init-schema")
async def admin_init_schema() -> dict:
    applied = init_schema()
    return {"applied": applied}


@app.post("/admin/seed-demo")
async def admin_seed_demo() -> dict:
    # クラウド上の Neo4j にデモデータを投入する (冪等)。デモ準備用
    statements = seed()
    return {"applied": statements}


@app.post("/tasks/ingestion", status_code=202)
async def tasks_ingestion() -> dict:
    # TODO(pubsub): Pub/Sub push の本実装 (設計書 §5 の長時間処理の非同期化)
    return {"status": "accepted (stub)"}
