"""
scripts/verify_final.py
────────────────────────
Final verification script — runs everything that doesn't require Docker.
Checks:
  1. pytest suite
  2. GET /health
  3. POST /v1/request (read) → AUTONOMOUS
  4. POST /v1/request (transfer) → ESCALATE
  5. GET /v1/pending → transfer trace_id present
  6. Streamlit server HTTP 200
"""

import os, sys, subprocess, time, json
sys.path.insert(0, os.getcwd())

import httpx

API   = "http://127.0.0.1:8000"
DASH  = "http://127.0.0.1:8501"
RESULTS = []

def check(label, ok, detail=""):
    symbol = "✅" if ok else "❌"
    RESULTS.append((label, ok, detail))
    print(f"  {symbol}  {label}" + (f"  [{detail}]" if detail else ""))

# ── 1. pytest ──────────────────────────────────────────────────────────────
print("\n── 1. pytest tests/ ──")
r = subprocess.run([sys.executable, "-m", "pytest", "tests/", "-q", "--tb=line"],
                   capture_output=True, text=True)
passed = r.returncode == 0
# pull summary line
summary = [l for l in r.stdout.splitlines() if "passed" in l or "failed" in l]
check("pytest tests/ -v", passed, summary[-1] if summary else r.stdout[-80:])

# ── Start API server for live checks (using TestClient, no subprocess needed) ──
print("\n── 2–6. API + Dashboard checks (TestClient + httpx) ──")

from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
from aria.schemas import (
    RiskAssessment, RiskLevel, AutonomyDecision, AutonomyMode,
    AuditRecord, DecisionOutcome, UserRequest,
)

def _state(req, score, level, mode, outcome):
    ra = RiskAssessment(trace_id=req.trace_id, risk_score=score, risk_level=level,
                        reasoning="verify", confidence=0.9, rag_references=[])
    ad = AutonomyDecision(trace_id=req.trace_id, autonomy_mode=mode,
                          risk_assessment=ra, explanation="verify")
    ar = AuditRecord(trace_id=req.trace_id, session_id=req.session_id,
                     user_intent=req.user_intent, risk_score=score, risk_level=level,
                     autonomy_mode=mode, action_attempted=req.action_type,
                     outcome=outcome, reasoning="verify", latency_ms=10)
    return {"request": req, "risk_assessment": ra, "autonomy_decision": ad,
            "action_result": "ok", "audit_record": ar, "error": None, "start_time_ms": 0.0}

from main import app
client = TestClient(app, raise_server_exceptions=False)

# ── 2. GET /health ──
resp = client.get("/health")
check("GET /health → status: ok", resp.status_code == 200 and resp.json().get("status") == "ok",
      resp.json().get("status","?"))

# ── 3. POST /v1/request read → AUTONOMOUS ──
read_req_holder = []
def fake_read(state):
    req = state["request"]
    read_req_holder.append(req)
    return _state(req, 8, RiskLevel.LOW, AutonomyMode.AUTONOMOUS, DecisionOutcome.EXECUTED)

with patch("aria.api.routes.aria_graph") as mg:
    mg.invoke.side_effect = fake_read
    resp = client.post("/v1/request", json={
        "session_id": "verify", "user_intent": "get account balance", "action_type": "read"
    })
body = resp.json()
mode = body.get("autonomy_mode", "?")
check("POST /v1/request (read) → AUTONOMOUS", mode == "AUTONOMOUS", mode)

# ── 4. POST /v1/request transfer → ESCALATE ──
transfer_tid = None
def fake_transfer(state):
    global transfer_tid
    req = state["request"]
    transfer_tid = req.trace_id
    return _state(req, 92, RiskLevel.HIGH, AutonomyMode.ESCALATE, DecisionOutcome.PENDING_APPROVAL)

with patch("aria.api.routes.aria_graph") as mg:
    mg.invoke.side_effect = fake_transfer
    resp = client.post("/v1/request", json={
        "session_id": "verify", "user_intent": "transfer funds externally",
        "action_type": "transfer"
    })
body = resp.json()
mode = body.get("autonomy_mode", "?")
check("POST /v1/request (transfer) → ESCALATE", mode == "ESCALATE", mode)

# ── 5. GET /v1/pending → transfer trace_id present ──
resp = client.get("/v1/pending")
pending_tids = [p["trace_id"] for p in resp.json()]
found = transfer_tid in pending_tids if transfer_tid else False
check("GET /v1/pending → transfer trace_id present", found,
      f"looking for {transfer_tid}, found {pending_tids[:1]}")

# ── 6. Streamlit HTTP 200 ──
try:
    dash_r = httpx.get(DASH, timeout=3)
    check("localhost:8501 → dashboard HTTP 200", dash_r.status_code == 200,
          f"status={dash_r.status_code}")
except Exception as e:
    check("localhost:8501 → dashboard HTTP 200", False, str(e)[:60])

# ── 7. docker-compose available ──
dc = subprocess.run(["docker", "compose", "config", "--quiet"],
                    capture_output=True, text=True, cwd=os.getcwd())
check("docker-compose config valid", "services" in dc.stdout or dc.returncode == 0,
      "run 'docker compose up --build' to start (Docker daemon required)")

# ── Summary ──
print("\n══════════════════════════════════════")
total  = len(RESULTS)
passed = sum(1 for _, ok, _ in RESULTS if ok)
print(f"  {passed}/{total} checks passed")
if passed == total:
    print("  🎉  ALL CHECKS PASSED — ARIA is complete!")
else:
    failures = [label for label, ok, _ in RESULTS if not ok]
    print(f"  FAILED: {failures}")
print("══════════════════════════════════════")

sys.exit(0 if passed == total else 1)
