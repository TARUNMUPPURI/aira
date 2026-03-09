"""
tests/test_graph.py
────────────────────
Integration tests for the ARIA LangGraph pipeline.

All tests mock ``RiskClassifier.classify`` so a Gemini API key is not required.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from aria.schemas import (
    AutonomyMode,
    DecisionOutcome,
    RiskAssessment,
    RiskLevel,
    UserRequest,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_assessment(request: UserRequest, score: int, level: RiskLevel) -> RiskAssessment:
    return RiskAssessment(
        trace_id=request.trace_id,
        risk_score=score,
        risk_level=level,
        reasoning="Mocked assessment.",
        confidence=0.90,
        rag_references=[],
    )


def _invoke_graph(request: UserRequest, mock_assessment: RiskAssessment) -> dict:
    """Run aria_graph with RiskClassifier.classify patched to return *mock_assessment*."""
    with patch(
        "aria.graph.nodes.RiskClassifier"
    ) as MockClf:
        MockClf.return_value.classify.return_value = mock_assessment
        from aria.graph.graph import aria_graph
        return aria_graph.invoke({"request": request})


# ─────────────────────────────────────────────────────────────────────────────
#  Test 1 — low risk → AUTONOMOUS / EXECUTED
# ─────────────────────────────────────────────────────────────────────────────

def test_low_risk_flows_to_autonomous():
    """A LOW-risk request must route to AUTONOMOUS and produce outcome=EXECUTED."""
    request = UserRequest(session_id="g1", user_intent="get account balance", action_type="read")
    assessment = _make_assessment(request, score=8, level=RiskLevel.LOW)

    state = _invoke_graph(request, assessment)

    assert state["autonomy_decision"].autonomy_mode == AutonomyMode.AUTONOMOUS
    assert state["audit_record"].outcome == DecisionOutcome.EXECUTED
    assert state["action_result"] is not None


# ─────────────────────────────────────────────────────────────────────────────
#  Test 2 — high risk → ESCALATE / PENDING_APPROVAL
# ─────────────────────────────────────────────────────────────────────────────

def test_high_risk_flows_to_escalate():
    """A HIGH-risk request must route to ESCALATE and produce outcome=PENDING_APPROVAL."""
    request = UserRequest(
        session_id="g2",
        user_intent="transfer funds to external account",
        action_type="transfer",
    )
    assessment = _make_assessment(request, score=92, level=RiskLevel.HIGH)

    state = _invoke_graph(request, assessment)

    assert state["autonomy_decision"].autonomy_mode == AutonomyMode.ESCALATE
    assert state["audit_record"].outcome == DecisionOutcome.PENDING_APPROVAL
    # No tool called — action_result is the explanation string
    assert "ESCALATED" in state["action_result"] or "risk" in state["action_result"].lower()


# ─────────────────────────────────────────────────────────────────────────────
#  Test 3 — audit_record always written
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("score,level", [
    (10,  RiskLevel.LOW),
    (50,  RiskLevel.MEDIUM),
    (95,  RiskLevel.HIGH),
])
def test_audit_record_always_written(score, level):
    """
    Regardless of risk level, node_write_audit must produce a non-None AuditRecord
    with a matching trace_id.
    """
    request = UserRequest(session_id="g3", user_intent="some action", action_type="read")
    assessment = _make_assessment(request, score=score, level=level)

    state = _invoke_graph(request, assessment)

    assert state.get("audit_record") is not None, "audit_record should never be None"
    assert state["audit_record"].trace_id == request.trace_id


# ─────────────────────────────────────────────────────────────────────────────
#  Test 4 — trace_id consistent throughout state
# ─────────────────────────────────────────────────────────────────────────────

def test_trace_id_consistent_throughout():
    """
    trace_id in request == trace_id in risk_assessment == trace_id in audit_record.
    """
    request = UserRequest(session_id="g4", user_intent="view transactions", action_type="read")
    assessment = _make_assessment(request, score=12, level=RiskLevel.LOW)

    state = _invoke_graph(request, assessment)

    tid = request.trace_id
    assert state["risk_assessment"].trace_id == tid
    assert state["autonomy_decision"].trace_id == tid
    assert state["audit_record"].trace_id == tid


# ─────────────────────────────────────────────────────────────────────────────
#  Test 5 — graph survives classifier failure
# ─────────────────────────────────────────────────────────────────────────────

def test_graph_survives_classifier_failure():
    """
    If RiskClassifier.classify raises, node_classify_risk must catch it,
    return a HIGH-risk fallback, and the graph must still complete with
    audit_record written.
    """
    request = UserRequest(session_id="g5", user_intent="some action", action_type="read")

    with patch("aria.graph.nodes.RiskClassifier") as MockClf:
        MockClf.return_value.classify.side_effect = RuntimeError("LLM exploded")
        from aria.graph.graph import aria_graph
        state = aria_graph.invoke({"request": request})

    # error field must be set
    assert state.get("error") is not None

    # fallback assessment must be HIGH
    ra = state["risk_assessment"]
    assert ra.risk_score == 100
    assert ra.risk_level == RiskLevel.HIGH
    assert ra.confidence == 0.0

    # graph must still have routed and written an audit record
    assert state.get("audit_record") is not None
    assert state["audit_record"].trace_id == request.trace_id
