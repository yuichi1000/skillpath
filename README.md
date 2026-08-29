# SkillPath — Autonomous Learning-Roadmap Agent

**All Things Agentic Hackathon entry / Track: The Taskmaster**

SkillPath turns "what should I study, in what order, and when?" into an autonomous
workflow. Paste a certification syllabus and it builds a **prerequisite skill graph**
in Neo4j. Paste your mock-exam results and it detects not just your weak areas but the
**unmastered prerequisite skills behind them** (a graph traversal no plain LLM chat can
replicate), plans a study order via topological sort, back-calculates against your exam
date, and **writes the study blocks into your real Google Calendar** around your
existing appointments.

Live service: `https://skillpath-workflow-924686405565.asia-northeast1.run.app` (see `/docs`)

## Try it

```bash
curl -s -X POST $URL/run -H "Content-Type: application/json" -d '{
  "uid": "demo-user",
  "message": "模試の結果です。試験日は2026年11月15日。CNN(画像認識): 9/20、自然言語処理: 10/20"
}'
```

The response contains the detected weakness cluster (weak skills + their unmastered
prerequisites), an ordered study plan, and the scheduled calendar blocks with a
deadline-fit verdict.

## Architecture

```
User input ─▶ Router (Gemini, intent+deadline) ─▶ dispatch (deterministic route)
   ├─ register:   Ingestion (Gemini extract) ─▶ store (entity-resolve, MERGE) ─┐
   ├─ assessment: Feedback (Gemini extract) ─▶ store (mastery EMA) ─▶ Weakness │
   │                                            Detector (pure Cypher) ────────┤
   └─ query: stub                                                              ▼
              Planner (topological sort, cycle cut) ─▶ Scheduler (free-slot fit,
              Google Calendar upsert, LearningSession nodes) ─▶ Notifier
```

- **LLM nodes and deterministic nodes are strictly separated** (ADK graph workflow).
  LLMs only classify intent and extract structure; ordering, weakness traversal,
  slot allocation and all graph writes are deterministic and unit-tested.
- **Everything is idempotent**: Neo4j writes are MERGE-based; calendar events are
  keyed by `extendedProperties.private.skillpath_session_id` so re-runs update
  instead of duplicate.
- **Per-user isolation**: every user-scoped Cypher query filters by `uid`.
- LLM output is validated through lenient-but-structured Pydantic schemas before
  any database write (untrusted text is data, never instructions).

| Layer | Technology |
|---|---|
| LLM | Gemini 3.5 Flash on Vertex AI |
| Agent framework | Google ADK 2.8 (graph workflow, `LlmAgent` + `FunctionNode`) |
| Graph DB | Neo4j 5 LTS on GCE (private VPC, no external IP) |
| Runtime | Cloud Run (Direct VPC egress to Neo4j) |
| Async / storage | Pub/Sub (+DLQ), Firestore, Cloud Storage, Secret Manager |
| External action | Google Calendar API (OAuth, token in Secret Manager) |
| IaC | Terraform (everything in `infra/`) |

## Spin-up

### Local development

Prereqs: Python 3.12+, [uv](https://docs.astral.sh/uv/), Docker.

```bash
uv sync
cp .env.example .env            # defaults work for local Neo4j
make neo4j                      # Neo4j in docker compose (browser: localhost:7474)
make schema                     # constraints & indexes
make seed                       # demo skill graph (idempotent)
make test                       # fast tests (no LLM calls)
```

For LLM features locally, authenticate to Vertex AI and set your project in `.env`
(`GOOGLE_CLOUD_PROJECT`, `GOOGLE_GENAI_USE_VERTEXAI=true`):

```bash
gcloud auth application-default login
make test-all                   # full suite incl. live Gemini calls
uv run python -m app.demo       # two-act demo: syllabus ingestion + exam feedback
```

Google Calendar (optional locally): create an OAuth desktop client (internal consent
screen), save it as `client_secret.json`, then:

```bash
uv run python -m app.tools.calendar_auth   # one-time browser consent
CALENDAR_ENABLED=true uv run python -m app.demo
```

### Deploy to Google Cloud (Terraform, 3 phases)

```bash
cd infra
terraform init
terraform apply -var project_id=$PROJECT_ID          # phase 1: everything except Cloud Run

cd ..
gcloud builds submit --tag $REGION-docker.pkg.dev/$PROJECT_ID/skillpath/workflow:latest

cd infra
terraform apply -var project_id=$PROJECT_ID \
  -var container_image=$REGION-docker.pkg.dev/$PROJECT_ID/skillpath/workflow:latest   # phase 2+3

URL=$(terraform output -raw workflow_url)
curl -X POST $URL/admin/init-schema
curl -X POST $URL/admin/seed-demo
```

Calendar on Cloud Run: run the local `calendar_auth` flow once, then
`gcloud secrets create skillpath-calendar-token --data-file=calendar-token.json`.
Without the token the scheduler degrades gracefully to placeholder time slots.

To view the (private) Neo4j browser during a demo:

```bash
gcloud compute start-iap-tunnel skillpath-neo4j 7474 --local-host-port=localhost:7474 --zone=$ZONE
gcloud compute start-iap-tunnel skillpath-neo4j 7687 --local-host-port=localhost:7687 --zone=$ZONE
```

### Cost controls

Cloud Run scales to zero; after the demo the Neo4j VM can be stopped with
`terraform apply -var neo4j_desired_status=TERMINATED` (data disk survives).

## Hackathon requirement mapping

- **Gemini 3.5+** — `gemini-3.5-flash` via Vertex AI (model ids externalized as env vars)
- **Google agent framework** — Google ADK 2.8 graph workflow
- **Google Cloud infrastructure** — Cloud Run, Compute Engine, Pub/Sub, Firestore,
  Cloud Storage, Secret Manager, Cloud Build, Artifact Registry; all Terraform-managed

## Repository layout

```
app/
  main.py         FastAPI entrypoint (Cloud Run)
  workflow/       ADK graph: router, ingestion, feedback, weakness, planner,
                  scheduler, notifier
  tools/          neo4j, calendar, seed/demo utilities
  cypher/         all Cypher lives here (named queries)
  models/         Pydantic I/O schemas
infra/            Terraform (VPC, Neo4j VM, Cloud Run, Pub/Sub, IAM, ...)
tests/            36 fast tests + live-LLM tests (pytest -m llm)
```
