"""
tests/test_api.py
──────────────────
FastAPI endpoint tests using TestClient.

aria_graph.invoke is mocked so no Gemini API key is required.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from aria.schemas import (
    AuditRecord,
    AutonomyDecision,
    AutonomyMode,
    DecisionOutcome,
    RiskAssessment,
    RiskLevel,
    UserRequest,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_graph_state(
    request: UserRequest,
    score: int = 8,
    level: RiskLevel = RiskLevel.LOW,
    mode: AutonomyMode = AutonomyMode.AUTONOMOUS,
    outcome: DecisionOutcome = DecisionOutcome.EXECUTED,
) -> dict:
    """Build a fake completed graph state dict."""
    assessment = RiskAssessment(
        trace_id=request.trace_id,
        risk_score=score,
        risk_level=level,
        reasoning="Mocked.",
        confidence=0.90,
        rag_references=[],
    )
    decision = AutonomyDecision(
        trace_id=request.trace_id,
        autonomy_mode=mode,
        risk_assessment=assessment,
        explanation=f"Score {score} → {mode.value}",
    )
    audit = AuditRecord(
        trace_id=request.trace_id,
        session_id=request.session_id,
        user_intent=request.user_intent,
        risk_score=score,
        risk_level=level,
        autonomy_mode=mode,
        action_attempted=request.action_type,
        outcome=outcome,
        reasoning="Mocked.",
        latency_ms=42,
    )
    return {
        "request":          request,
        "risk_assessment":  assessment,
        "autonomy_decision": decision,
        "action_result":    "Mock action result.",
        "audit_record":     audit,
        "error":            None,
        "start_time_ms":    0.0,
    }


# ─────────────────────────────────────────────────────────────────────────────
#  Test 1 — POST /v1/request returns trace_id and autonomy_mode
# ─────────────────────────────────────────────────────────────────────────────

def test_request_endpoint_returns_trace_id_and_mode(test_client):
    """POST /v1/request must return a JSON body with trace_id and autonomy_mode."""
    captured_request: list[UserRequest] = []

    def _fake_invoke(state: dict):
        req: UserRequest = state["request"]
        captured_request.append(req)
        return _make_graph_state(req, score=8, level=RiskLevel.LOW, mode=AutonomyMode.AUTONOMOUS)

    with patch("aria.api.routes.aria_graph") as mock_graph:
        mock_graph.invoke.side_effect = _fake_invoke
        resp = test_client.post("/v1/request", json={
            "session_id": "s1",
            "user_intent": "get account balance",
            "action_type": "read",
        })

    assert resp.status_code == 200
    body = resp.json()
    assert "trace_id" in body, f"Missing trace_id: {body}"
    assert "autonomy_mode" in body, f"Missing autonomy_mode: {body}"
    assert body["trace_id"].startswith("aria-")
    assert body["autonomy_mode"] == "AUTONOMOUS"


# ─────────────────────────────────────────────────────────────────────────────
#  Test 2 — GET /health returns ok
# ─────────────────────────────────────────────────────────────────────────────

def test_health_returns_ok(test_client):
    """GET /health must return HTTP 200 with {status: 'ok'}."""
    resp = test_client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["version"] == "1.0.0"
    assert "timestamp" in body


# ─────────────────────────────────────────────────────────────────────────────
#  Test 3 — GET /v1/audit/{nonexistent} returns 404
# ─────────────────────────────────────────────────────────────────────────────

def test_audit_unknown_trace_id_returns_404(test_client):
    """GET /v1/audit/<unknown-id> must return HTTP 404."""
    resp = test_client.get("/v1/audit/aria-doesnotexist99999")
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()


# ─────────────────────────────────────────────────────────────────────────────
#  Test 4 — /v1/approve updates outcome
# ─────────────────────────────────────────────────────────────────────────────

def test_approve_endpoint_updates_outcome(test_client):
    """
    POST a high-risk request → check escalation registered → POST /v1/approve →
    outcome must change to APPROVED.
    """
    from aria.api.approval import add_pending, pending_approvals
    from aria.agents.audit_agent import audit_agent
    from aria.schemas import ApprovalRequest

    # Build a request and inject it manually into approval + audit stores
    req = UserRequest(session_id="s4", user_intent="close account", action_type="close_account")
    state = _make_graph_state(
        req, score=95, level=RiskLevel.HIGH,
        mode=AutonomyMode.ESCALATE, outcome=DecisionOutcome.PENDING_APPROVAL,
    )

    # Register in audit_agent
    audit_agent.write(state["audit_record"])

    # Register in approval store
    pr = ApprovalRequest(
        trace_id=req.trace_id,
        explanation="High risk action.",
        risk_score=95,
        user_intent=req.user_intent,
        requested_action="close_account",
    )
    add_pending(pr)

    # POST /v1/approve
    resp = test_client.post("/v1/approve", json={
        "trace_id":    req.trace_id,
        "approved":    True,
        "reviewed_by": "test_operator",
        "notes":       "Approved in test",
    })

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "APPROVED"
    assert req.trace_id == body["trace_id"]

    # Verify audit record updated in-memory
    updated = audit_agent.get(req.trace_id)
    assert updated is not None
    assert updated.outcome == DecisionOutcome.APPROVED
    assert updated.human_approved is True

    # Verify removed from pending store
    assert req.trace_id not in pending_approvals


# ─────────────────────────────────────────────────────────────────────────────
#  Test 5 — GET /v1/metrics returns correct shape
# ─────────────────────────────────────────────────────────────────────────────

def test_metrics_returns_correct_shape(test_client):
    """GET /v1/metrics must return a dict with all MetricsSnapshot fields."""
    resp = test_client.get("/v1/metrics")
    assert resp.status_code == 200
    body = resp.json()

    required_fields = {
        "total_decisions",
        "autonomous_count",
        "supervised_count",
        "escalate_count",
        "escalation_rate_pct",
        "avg_risk_score",
        "p95_latency_ms",
        "false_positive_rate_pct",
        "autonomy_drift_7d",
    }
    missing = required_fields - set(body.keys())
    assert not missing, f"Missing MetricsSnapshot fields: {missing}"

    # Sanity ranges
    assert 0 <= body["escalation_rate_pct"] <= 100
    assert body["total_decisions"] >= 0
