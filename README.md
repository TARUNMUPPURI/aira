# 🛡️ ARIA — Autonomous Risk-Aware Intelligence Architecture

> An agentic AI system that classifies risk in real time, routes financial actions autonomously, and always defaults to safety when uncertain.

---

## Why It Exists

In financial services, not all AI-driven actions carry the same weight — reading an account balance and wiring funds offshore are orders of magnitude apart in risk. ARIA exists because most AI agents treat every action identically, leaving compliance teams either blocking everything or trusting nothing.

By scoring risk continuously, routing low-risk actions automatically, and escalating high-risk ones to human reviewers with full audit trails, ARIA makes autonomy safe enough to deploy in production financial environments.

---

## Architecture Flow

```
User Request (intent + action_type)
        │
        ▼
┌──────────────────────────────────────────────────────────┐
│  ARIA LangGraph Pipeline                                 │
│                                                          │
│  node_start                                              │
│     │  records start_time_ms                            │
│     ▼                                                    │
│  node_classify_risk  ◄──── ChromaDB RAG (top-3 similar) │
│     │  Gemini Flash → risk_score (0–100)                │
│     │  writes new decision back to ChromaDB             │
│     ▼                                                    │
│  node_route_autonomy                                     │
│     │  score ≤ 35  → AUTONOMOUS                         │
│     │  score ≤ 70  → SUPERVISED                         │
│     │  score >  70 → ESCALATE                           │
│     ▼                                                    │
│  ┌──────────────┬───────────────┬──────────────────┐    │
│  │  AUTONOMOUS  │  SUPERVISED   │  ESCALATE        │    │
│  │  execute()   │  execute() +  │  no execute +    │    │
│  │  EXECUTED    │  webhook POST │  PENDING_APPROVAL │   │
│  └──────┬───────┴───────┬───────┴──────────┬───────┘   │
│         └───────────────┴──────────────────┘            │
│                         │                               │
│                         ▼                               │
│  node_write_audit   →  AuditRecord (immutable)          │
│                         │                               │
└─────────────────────────┼────────────────────────────────┘
                          │
          ┌───────────────┴───────────────┐
          ▼                               ▼
  Kafka topic                      audit_fallback.jsonl
  aria.decisions                   (dead-letter queue)
          │
          ▼
  ARIAConsumer (deque 1000)
          │
          ▼
  Streamlit Dashboard (localhost:8501)
```

**Parallel transports:** REST (`/v1/approve`) and gRPC (`ApprovalService/SubmitApproval`) share the same in-memory `pending_approvals` dict — one source of truth for human review.

---

## Tech Stack

| Technology | Role |
|---|---|
| **Python 3.11** | Core application runtime |
| **LangGraph** | Stateful agent graph: nodes, conditional edges, shared state |
| **Google Gemini Flash** (`gemini-2.0-flash`) | LLM for risk scoring via RAG-augmented prompts |
| **ChromaDB** | Vector store for RAG — stores past decisions, enables calibration retrieval |
| **FastAPI + uvicorn** | REST API — 6 endpoints for requests, approvals, metrics, audit |
| **gRPC + protobuf** | Binary RPC transport for the ApprovalService (shares approval state with REST) |
| **Apache Kafka** | Durable event stream for audit records (`aria.decisions` topic) |
| **Streamlit** | Real-time monitoring dashboard with approve/deny UI |
| **Pydantic v2** | Data validation and serialisation across all layers |
| **Docker Compose / Kubernetes** | Container orchestration — 5-service local stack, 2-replica k8s deploy |

---

## Installation

```bash
# 1. Clone
git clone https://github.com/your-org/aria.git
cd aria

# 2. Configure environment
cp .env.example .env
# Open .env and set:
#   GEMINI_API_KEY=your-key-here

# 3. Start all services
docker compose up --build
```

Services started by `docker compose up`:
- **Zookeeper** → `localhost:2181`
- **Kafka** → `localhost:9092`
- **ARIA API** → `localhost:8000`  (waits for Kafka healthy)
- **ARIA gRPC** → `localhost:50051` (waits for Kafka healthy)
- **ARIA Dashboard** → `localhost:8501` (waits for API healthy)

> **Without Docker:** run `pip install -r requirements.txt` then `uvicorn main:app --reload` and `streamlit run dashboard/app.py` separately.

---

## API Reference

### `POST /v1/request` — Submit a user action for classification

```json
{
  "session_id": "sess-001",
  "user_intent": "transfer funds to external account",
  "action_type": "transfer",
  "context": {}
}
```

**Response** — returns `trace_id` (unique identifier for this decision), `autonomy_mode` (`AUTONOMOUS` / `SUPERVISED` / `ESCALATE`), `outcome`, the raw `result` string from the tool, and `latency_ms`.

---

### `GET /v1/audit/{trace_id}` — Retrieve an audit record

Returns the full immutable `AuditRecord` for a given `trace_id`: intent, risk score, reasoning, outcome, latency, and whether a human approved it.
Returns **HTTP 404** if the `trace_id` is unknown.

---

### `POST /v1/approve` — Submit a human approval or denial

```json
{
  "trace_id": "aria-abc123def456",
  "approved": true,
  "reviewed_by": "compliance-team",
  "notes": "Verified counterparty details"
}
```

**Response** — returns `trace_id`, `status` (`APPROVED` or `DENIED`), and a human-readable `message`. If `approved=true`, the deferred tool is also executed.
Returns **HTTP 404** if the `trace_id` is not in the pending queue.

---

### `GET /v1/metrics` — Live metrics snapshot

Returns all `MetricsSnapshot` fields: `total_decisions`, `autonomous_count`, `supervised_count`, `escalate_count`, `escalation_rate_pct`, `avg_risk_score`, `p95_latency_ms`, `false_positive_rate_pct`, `autonomy_drift_7d`.

---

### `GET /v1/pending` — List pending human approvals

Returns a list of all escalated decisions awaiting human review, each with `trace_id`, `user_intent`, `risk_score`, and `explanation`.

---

### `GET /health` — Liveness probe

```json
{ "status": "ok", "version": "1.0.0", "timestamp": "2026-03-07T00:00:00Z" }
```

---

## Running Tests

```bash
# All 15 tests (no GEMINI_API_KEY required — LLM is mocked)
pytest tests/ -v

# Individual suites
pytest tests/test_risk_classifier.py -v
pytest tests/test_graph.py -v
pytest tests/test_api.py -v
```

All LLM calls are mocked with `unittest.mock.patch` — the test suite runs fully offline.

---

## Dashboard

Open **http://localhost:8501** after `docker compose up`.

| Section | What it shows |
|---|---|
| **Live Metrics** | 4 KPI cards + bar chart of mode distribution; auto-refreshes every 10s |
| **Recent Decisions** | Last 20 audit records with colour-coded rows (🟢 AUTO / 🟠 SUP / 🔴 ESC) |
| **Autonomy Drift Chart** | Hourly avg risk score over time; warning banner if 7-day drift exceeds ±5% |
| **Pending Approvals** | One card per escalated action with ✅ Approve and ❌ Deny buttons |

---

## Design Decisions

### Why does failure default to HIGH risk (score 100)?

A misconfigured LLM, a network timeout, or a malformed response are all indistinguishable from an adversarial prompt. Defaulting to `risk_score=100 / HIGH` means the **worst case is a delayed action** reviewed by a human — not an unauthorised wire transfer executed silently. Safety must be the zero-configuration state.

### Why Kafka instead of a database for audit events?

`AuditRecord` events are immutable facts, not mutable rows. Kafka models this naturally: records are append-only, replayable, partitioned by `trace_id`, and can fan-out to downstream consumers (compliance archiving, anomaly detection, dashboards) without coupling them to the ARIA service. A write-ahead database would require schema migrations every time a downstream consumer evolves.

### Why both REST and gRPC?

REST is consumed by the Streamlit dashboard, external webhooks, and human operators via `curl`. gRPC is for service-to-service calls where binary framing, strong typing from protobuf, and streaming (future) matter. Both share the same `pending_approvals` dict, so there is exactly one source of truth — the transport is irrelevant to the business logic.

### Why are thresholds config, not code?

Hardcoding `if score > 70: ESCALATE` means a regulatory change or a risk-policy update requires a code deployment, a PR review, and a retest cycle. Loading `RISK_LOW_THRESHOLD` and `RISK_HIGH_THRESHOLD` from environment variables means a compliance officer can tune them via a ConfigMap diff and a pod restart — no code change, no release.

---

## Metrics Glossary

### `escalation_rate_pct`

The percentage of all processed decisions that were escalated to a human reviewer. A value of 0% means the agent is fully autonomous (potentially under-conservative); a value of 100% means nothing is being executed autonomously (over-cautious or misconfigured). A healthy production system typically targets 5–15% depending on the risk appetite of the organisation.

**Formula:** `(escalate_count / total_decisions) × 100`

---

### `autonomy_drift_7d`

Measures how much the agent's autonomous execution rate has shifted over the past 7 days compared to the first half of the observation window. A **negative** value means the agent is escalating more than it used to (becoming more conservative — could indicate prompt degradation, new action types, or deliberately tightened thresholds). A **positive** value means it's approving more autonomously (could indicate calibration improvement, or an erosion of safety controls that warrants investigation).

**Trigger:** A warning banner appears in the dashboard if `|autonomy_drift_7d| > 5%`.

**Formula:** `(recent_autonomous% − baseline_autonomous%) × 100`
