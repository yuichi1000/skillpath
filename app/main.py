"""FastAPI エントリポイント (Cloud Run) — 設計書 §8。

エンドポイント:
- GET  /health          死活監視
- POST /run             ワークフロー実行 (Web UI / デモが使用)
- POST /admin/init-schema  Neo4j スキーマ初期化 (設計書 §7.10)
- POST /admin/seed-demo    デモデータ投入
- POST /tasks/ingestion    Pub/Sub push 受け口 (現状スタブ)

ガードレール:
- /admin/* は X-Admin-Token 必須 (ADMIN_TOKEN 未設定なら閉鎖 = safe by default)
- /run は入力サイズ・uid 形式を検証し、ALLOWED_UIDS 設定時は許可リスト制、
  プロセス内レートリミット (RATE_LIMIT_PER_MIN)、LLM 失敗は 502 の JSON で返す
"""

import logging
import time
from collections import deque
from datetime import datetime
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse
from google.adk.runners import InMemoryRunner
from google.genai import types
from pydantic import BaseModel, Field, field_validator

from app.config import get_settings
from app.tools.init_schema import init_schema
from app.tools.neo4j_tool import run_named
from app.tools.seed_demo import seed
from app.workflow.graph import build_workflow
from app.workflow.runtime import run_workflow_session

logger = logging.getLogger(__name__)

app = FastAPI(title="SkillPath", version="0.1.0")

# プロセス内シングルトン。Cloud Run のインスタンス単位で使い回す
_runner: InMemoryRunner | None = None

# /run のレートリミット (プロセス内。分散環境での限界は README に明記)
_run_timestamps: deque[float] = deque()


def get_runner() -> InMemoryRunner:
    global _runner
    if _runner is None:
        _runner = InMemoryRunner(node=build_workflow())
    return _runner


def require_admin(x_admin_token: str = Header(default="")) -> None:
    settings = get_settings()
    if not settings.admin_token:
        raise HTTPException(
            status_code=403, detail="admin endpoints are disabled (ADMIN_TOKEN not set)"
        )
    if x_admin_token != settings.admin_token:
        raise HTTPException(status_code=403, detail="invalid admin token")


def check_rate_limit() -> None:
    limit = get_settings().rate_limit_per_min
    now = time.monotonic()
    while _run_timestamps and now - _run_timestamps[0] > 60:
        _run_timestamps.popleft()
    if len(_run_timestamps) >= limit:
        raise HTTPException(
            status_code=429,
            detail=f"rate limit exceeded ({limit}/min)",
            headers={"Retry-After": "60"},
        )
    _run_timestamps.append(now)


def _validate_iso(value: str) -> str:
    if value:
        try:
            datetime.fromisoformat(value)
        except ValueError as e:
            raise ValueError("must be an ISO 8601 date/datetime") from e
    return value


ALLOWED_ATTACHMENT_MIMES = {"image/png", "image/jpeg", "image/webp", "application/pdf"}
MAX_ATTACHMENT_B64 = 7_200_000  # base64 で約 5.4MB (バイナリ 4MB 相当)


class RunRequest(BaseModel):
    uid: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    message: str = Field(min_length=1, max_length=8000)  # トークン浪費の上限
    deadline: str = ""  # ISO 日付。message からも抽出されるが直接指定も可
    schedule_start: str = ""  # テスト・検証用
    attachment_b64: str = Field(default="", max_length=MAX_ATTACHMENT_B64)  # 模試の写真/PDF
    attachment_mime: str = ""

    @field_validator("deadline", "schedule_start")
    @classmethod
    def _iso(cls, v: str) -> str:
        return _validate_iso(v)

    @field_validator("attachment_mime")
    @classmethod
    def _mime(cls, v: str) -> str:
        if v and v not in ALLOWED_ATTACHMENT_MIMES:
            raise ValueError(f"unsupported attachment type: {v}")
        return v


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
    calendar_links: dict[str, str] = {}
    research_notes: str = ""
    specialist: str = ""


WEB_INDEX = Path(__file__).resolve().parent / "web" / "index.html"


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(WEB_INDEX, media_type="text/html")


@app.get("/graph")
async def graph(uid: str = Query(pattern=r"^[A-Za-z0-9_-]{1,64}$")) -> dict:
    """UI のグラフ可視化用データ (uid 分離)。"""
    allowed = get_settings().allowed_uids
    if allowed and uid not in allowed:
        raise HTTPException(status_code=403, detail="uid is not allowed on this deployment")
    rows = run_named("graph_view.cypher", "user_skill_ids", uid=uid)
    skill_ids = rows[0]["ids"] if rows else []
    return {
        "nodes": run_named("graph_view.cypher", "graph_nodes", uid=uid, skill_ids=skill_ids),
        "edges": run_named("graph_view.cypher", "graph_edges", skill_ids=skill_ids),
        "certifications": run_named("graph_view.cypher", "graph_certs", skill_ids=skill_ids),
        "assessments": run_named("graph_view.cypher", "graph_assessments", uid=uid),
    }


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.post("/run", response_model=RunResponse)
async def run(req: RunRequest) -> RunResponse:
    check_rate_limit()
    allowed = get_settings().allowed_uids
    if allowed and req.uid not in allowed:
        raise HTTPException(status_code=403, detail="uid is not allowed on this deployment")

    if req.attachment_b64 and not req.attachment_mime:
        raise HTTPException(status_code=422, detail="attachment_mime is required with attachment")

    runner = get_runner()
    state: dict = {"uid": req.uid}
    if req.deadline:
        state["deadline"] = req.deadline
    if req.schedule_start:
        state["schedule_start"] = req.schedule_start
    if req.attachment_b64:
        state["attachment_b64"] = req.attachment_b64
        state["attachment_mime"] = req.attachment_mime
    parts = [types.Part(text=req.message)]
    if req.attachment_b64:
        import base64

        try:
            raw = base64.b64decode(req.attachment_b64, validate=True)
        except Exception as e:
            raise HTTPException(status_code=422, detail="attachment is not valid base64") from e
        parts.append(types.Part(inline_data=types.Blob(mime_type=req.attachment_mime, data=raw)))
    try:
        s = await run_workflow_session(
            runner, req.uid, types.Content(role="user", parts=parts), state
        )
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001 - 生のスタックトレース 500 を外に出さない
        logger.exception("workflow execution failed")
        raise HTTPException(
            status_code=502,
            detail={
                "error": "workflow execution failed",
                "hint": str(e)[:300],
            },
        ) from e

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
        calendar_links=s.get("calendar_links") or {},
        research_notes=s.get("research_notes", ""),
        specialist=s.get("specialist", ""),
    )


@app.post("/admin/init-schema", dependencies=[Depends(require_admin)])
async def admin_init_schema() -> dict:
    applied = init_schema()
    return {"applied": applied}


@app.post("/admin/seed-demo", dependencies=[Depends(require_admin)])
async def admin_seed_demo() -> dict:
    # クラウド上の Neo4j にデモデータを投入する (冪等)。デモ準備用
    statements = seed()
    return {"applied": statements}


@app.post("/tasks/ingestion", status_code=202)
async def tasks_ingestion() -> dict:
    # TODO(pubsub): Pub/Sub push の本実装 (設計書 §5 の長時間処理の非同期化)
    return {"status": "accepted (stub)"}
