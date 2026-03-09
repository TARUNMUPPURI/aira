"""
aria/graph/nodes.py
────────────────────
The 7 LangGraph node functions that make up the ARIA decision pipeline.

Each node:
  • Accepts the full ``ARIAState``
  • Returns a *partial* dict of only the keys it modifies
  • Never raises — errors are captured in ``state["error"]``
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

import httpx

from aria.agents.audit_agent import AuditAgent
from aria.agents.risk_classifier import RiskClassifier
from aria.config import settings
from aria.graph.state import ARIAState
from aria.schemas import (
    AuditRecord,
    AutonomyDecision,
    AutonomyMode,
    DecisionOutcome,
    RiskAssessment,
    RiskLevel,
)
from aria.tools.action_tools import execute_tool

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────


def node_start(state: ARIAState) -> dict:
    """Record the pipeline start time in milliseconds."""
    start = time.time() * 1000
    tid = state["request"].trace_id
    logger.info("[%s] ▶ ARIA graph started", tid)
    return {"start_time_ms": start}


# ─────────────────────────────────────────────────────────────────────────────


def node_classify_risk(state: ARIAState) -> dict:
    """
    Call RiskClassifier to produce a RiskAssessment.
    On any exception, store a HIGH-risk fallback and populate ``error``.
    """
    request = state["request"]
    tid = request.trace_id
    try:
        assessment = RiskClassifier().classify(request)
        logger.info("[%s] Risk classified: score=%d level=%s",
                    tid, assessment.risk_score, assessment.risk_level)
        return {"risk_assessment": assessment}
    except Exception as exc:  # noqa: BLE001
        logger.error("[%s] node_classify_risk failed: %s", tid, exc, exc_info=True)
        fallback = RiskAssessment(
            trace_id=tid,
            risk_score=100,
            risk_level=RiskLevel.HIGH,
            reasoning="Risk classifier failed — defaulting to HIGH for safety.",
            confidence=0.0,
            rag_references=[],
        )
        return {"risk_assessment": fallback, "error": str(exc)}


# ─────────────────────────────────────────────────────────────────────────────


def node_route_autonomy(state: ARIAState) -> dict:
    """
    Map the numeric risk_score to an AutonomyMode using settings thresholds.
    Never hardcodes threshold values — always reads from ``settings``.

    Thresholds (inclusive):
      score ≤ risk_low_threshold   → AUTONOMOUS
      score ≤ risk_high_threshold  → SUPERVISED
      else                         → ESCALATE
    """
    assessment: RiskAssessment = state["risk_assessment"]
    tid = state["request"].trace_id
    score = assessment.risk_score

    if score <= settings.risk_low_threshold:
        mode = AutonomyMode.AUTONOMOUS
    elif score <= settings.risk_high_threshold:
        mode = AutonomyMode.SUPERVISED
    else:
        mode = AutonomyMode.ESCALATE

    decision = AutonomyDecision(
        trace_id=tid,
        autonomy_mode=mode,
        risk_assessment=assessment,
        explanation=(
            f"Risk score {score} → {mode.value} "
            f"(thresholds: LOW≤{settings.risk_low_threshold}, HIGH≥{settings.risk_high_threshold})"
        ),
    )
    logger.info("[%s] Autonomy routed: score=%d → %s", tid, score, mode.value)
    return {"autonomy_decision": decision}


# ─────────────────────────────────────────────────────────────────────────────


def node_execute_autonomous(state: ARIAState) -> dict:
    """
    Execute the action autonomously without human oversight.
    Outcome = EXECUTED.
    """
    request = state["request"]
    tid = request.trace_id
    logger.info("[%s] Executing AUTONOMOUSLY: action_type=%s", tid, request.action_type)
    result = execute_tool(request.action_type, session_id=request.session_id)
    return {
        "action_result": result,
        "autonomy_decision": AutonomyDecision(
            trace_id=tid,
            autonomy_mode=AutonomyMode.AUTONOMOUS,
            risk_assessment=state["risk_assessment"],
            action_to_execute=request.action_type,
            explanation=state["autonomy_decision"].explanation,
        ),
    }


# ─────────────────────────────────────────────────────────────────────────────


def node_execute_supervised(state: ARIAState) -> dict:
    """
    Execute the action under supervision — logs decision and fires a
    non-blocking webhook notification for human review.
    Outcome = FLAGGED.
    """
    request = state["request"]
    tid = request.trace_id
    logger.info("[%s] Executing SUPERVISED: action_type=%s", tid, request.action_type)

    result = execute_tool(request.action_type, session_id=request.session_id)

    # Non-blocking webhook — any failure is silently swallowed
    _fire_webhook(tid, request.user_intent, "SUPERVISED")

    return {
        "action_result": result,
        "autonomy_decision": AutonomyDecision(
            trace_id=tid,
            autonomy_mode=AutonomyMode.SUPERVISED,
            risk_assessment=state["risk_assessment"],
            action_to_execute=request.action_type,
            explanation=state["autonomy_decision"].explanation,
        ),
    }


# ─────────────────────────────────────────────────────────────────────────────


def node_escalate(state: ARIAState) -> dict:
    """
    Halt the action and escalate to a human approver.
    Does NOT call execute_tool. Outcome = PENDING_APPROVAL.
    """
    request = state["request"]
    assessment: RiskAssessment = state["risk_assessment"]
    tid = request.trace_id

    explanation = (
        f"Action '{request.user_intent}' (type={request.action_type}) was ESCALATED to human review.\n"
        f"Risk score: {assessment.risk_score}/100 (level={assessment.risk_level.value}).\n"
        f"Reasoning: {assessment.reasoning}"
    )
    logger.warning("[%s] ESCALATING: %s", tid, explanation)

    # Fire webhook to notify reviewer
    _fire_webhook(tid, request.user_intent, "ESCALATE")

    return {
        "action_result": explanation,
        "autonomy_decision": AutonomyDecision(
            trace_id=tid,
            autonomy_mode=AutonomyMode.ESCALATE,
            risk_assessment=assessment,
            action_to_execute=None,
            explanation=explanation,
        ),
    }


# ─────────────────────────────────────────────────────────────────────────────


def node_write_audit(state: ARIAState) -> dict:
    """
    Build an AuditRecord from the full state, compute latency, and persist it.
    """
    request = state["request"]
    decision: AutonomyDecision = state["autonomy_decision"]
    assessment: RiskAssessment = state["risk_assessment"]
    tid = request.trace_id

    # Map autonomy mode → outcome
    _outcome_map = {
        AutonomyMode.AUTONOMOUS: DecisionOutcome.EXECUTED,
        AutonomyMode.SUPERVISED: DecisionOutcome.FLAGGED,
        AutonomyMode.ESCALATE:   DecisionOutcome.PENDING_APPROVAL,
    }
    outcome = _outcome_map.get(decision.autonomy_mode, DecisionOutcome.PENDING_APPROVAL)

    # Latency
    start = state.get("start_time_ms") or 0.0
    latency_ms = int(time.time() * 1000 - start)

    record = AuditRecord(
        trace_id=tid,
        session_id=request.session_id,
        user_intent=request.user_intent,
        risk_score=assessment.risk_score,
        risk_level=assessment.risk_level,
        autonomy_mode=decision.autonomy_mode,
        action_attempted=request.action_type,
        outcome=outcome,
        reasoning=assessment.reasoning,
        human_approved=None,
        latency_ms=latency_ms,
        timestamp=datetime.now(tz=timezone.utc),
    )

    AuditAgent().write(record)
    logger.info("[%s] AuditRecord written — latency=%dms outcome=%s", tid, latency_ms, outcome)
    return {"audit_record": record}


# ─────────────────────────────────────────────────────────────────────────────
#  Internal helpers
# ─────────────────────────────────────────────────────────────────────────────


def _fire_webhook(trace_id: str, intent: str, mode: str) -> None:
    """
    POST a JSON notification to APPROVAL_WEBHOOK_URL.
    Any network failure is silently swallowed — never blocks the pipeline.
    """
    url = settings.approval_webhook_url
    if not url:
        return
    try:
        with httpx.Client(timeout=3.0) as client:
            client.post(url, json={
                "trace_id": trace_id,
                "intent": intent,
                "mode": mode,
            })
    except Exception as exc:  # noqa: BLE001
        logger.debug("[%s] Webhook delivery failed (non-fatal): %s", trace_id, exc)
