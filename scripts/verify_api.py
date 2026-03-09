"""
scripts/verify_api.py
──────────────────────
Live verification: starts the ARIA FastAPI server, exercises every endpoint.
Run with: python scripts/verify_api.py
"""

import os
import sys
import time
import subprocess
import signal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

import ast
import logging
logging.basicConfig(level=logging.WARNING)

# ── 1. Structural checks (no API key needed) ─────────────────────────────
for f in ["aria/api/approval.py", "aria/api/routes.py", "main.py"]:
    ast.parse(open(f, encoding="utf-8").read())
    print(f"AST OK: {f}")

from main import app
route_paths = [r.path for r in app.routes]
required = {"/v1/request", "/v1/audit/{trace_id}", "/v1/approve",
            "/v1/metrics", "/v1/pending", "/health"}
assert required.issubset(set(route_paths)), f"Missing routes: {required - set(route_paths)}"
print(f"Routes OK: {sorted(required)}")

# ── 2. Unit-level checks (no server needed) ────────────────────────────
from aria.api.approval import add_pending, get_pending, process_approval, pending_approvals
from aria.schemas import ApprovalRequest, ApprovalResponse, RiskLevel

# add_pending / get_pending
req = ApprovalRequest(
    trace_id="aria-apitest001",
    explanation="High risk transfer detected",
    risk_score=95,
    user_intent="transfer",
    requested_action="transfer",
)
add_pending(req)
pending = get_pending()
assert any(p.trace_id == "aria-apitest001" for p in pending), "Not in pending"
print(f"add_pending / get_pending OK: {len(pending)} pending")

# process_approval (no audit_agent record exists, so update_outcome returns None — that's ok)
result = process_approval(ApprovalResponse(
    trace_id="aria-apitest001",
    approved=True,
    reviewed_by="test-reviewer",
))
assert result is True, "process_approval should return True"
assert "aria-apitest001" not in pending_approvals, "Should be removed after processing"
print("process_approval OK: removed from pending after processing")

# ── 3. Skip live server test if no API key ─────────────────────────────
key = os.getenv("GEMINI_API_KEY", "")
if not key or key == "your-gemini-api-key-here":
    print("SKIP_LIVE: No real GEMINI_API_KEY — skipping server spin-up test")
    print("ALL STRUCTURAL CHECKS PASSED")
    sys.exit(0)

# ── 4. Live server test ────────────────────────────────────────────────
import httpx

proc = subprocess.Popen(
    [sys.executable, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", "18765"],
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
)
time.sleep(4)  # give server time to start

BASE = "http://127.0.0.1:18765"
try:
    # Health
    r = httpx.get(f"{BASE}/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"
    print("GET /health OK")

    # POST /v1/request — transfer (expect ESCALATE)
    r = httpx.post(f"{BASE}/v1/request", json={
        "session_id": "sess-live",
        "user_intent": "transfer funds to external account",
        "action_type": "transfer",
    }, timeout=30)
    assert r.status_code == 200, f"Got {r.status_code}: {r.text}"
    body = r.json()
    print(f"POST /v1/request OK: trace_id={body['trace_id']} mode={body['autonomy_mode']}")
    assert body["autonomy_mode"] == "ESCALATE", f"Expected ESCALATE, got {body['autonomy_mode']}"
    tid = body["trace_id"]

    # GET /v1/pending — must contain our trace_id
    r = httpx.get(f"{BASE}/v1/pending")
    assert r.status_code == 200
    trace_ids = [p["trace_id"] for p in r.json()]
    assert tid in trace_ids, f"trace_id {tid} not found in pending: {trace_ids}"
    print(f"GET /v1/pending OK: trace_id={tid} found in pending list")

    # GET /v1/audit
    r = httpx.get(f"{BASE}/v1/audit/{tid}")
    assert r.status_code == 200
    assert r.json()["trace_id"] == tid
    print(f"GET /v1/audit OK: outcome={r.json()['outcome']}")

    # GET /v1/metrics
    r = httpx.get(f"{BASE}/v1/metrics")
    assert r.status_code == 200 and "total_decisions" in r.json()
    print(f"GET /v1/metrics OK: total={r.json()['total_decisions']}")

    # GET /v1/audit 404
    r = httpx.get(f"{BASE}/v1/audit/aria-doesnotexist")
    assert r.status_code == 404
    print("GET /v1/audit 404 OK")

    print("ALL LIVE CHECKS PASSED")
finally:
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except Exception:
        proc.kill()
