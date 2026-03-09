"""
tests/conftest.py
──────────────────
Shared pytest fixtures for the ARIA test suite.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

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

def make_mock_genai_response(payload: dict):
    """Return a fake google.generativeai response with .text set to JSON."""
    mock_resp = MagicMock()
    mock_resp.text = json.dumps(payload)
    return mock_resp


def _low_risk_payload(trace_id: str) -> dict:
    return {
        "trace_id":       trace_id,
        "risk_score":     8,
        "risk_level":     "LOW",
        "reasoning":      "Routine read operation matching low-risk calibration examples.",
        "confidence":     0.95,
        "rag_references": ["get account balance"],
    }


def _high_risk_payload(trace_id: str) -> dict:
    return {
        "trace_id":       trace_id,
        "risk_score":     92,
        "risk_level":     "HIGH",
        "reasoning":      "External fund transfer — irreversible high-value action.",
        "confidence":     0.97,
        "rag_references": ["transfer funds to external account"],
    }


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def isolate_audit_fallback_file(tmp_path, monkeypatch):
    """Redirect the DLQ JSONL to a temp file so tests don't pollute each other."""
    import aria.kafka.producer as prod
    import aria.kafka.consumer as cons
    dlq = tmp_path / "audit_fallback.jsonl"
    monkeypatch.setattr(prod, "_DLQ_PATH", dlq)
    monkeypatch.setattr(cons, "_DLQ_PATH", dlq)
    yield


@pytest.fixture()
def low_risk_request() -> UserRequest:
    return UserRequest(
        session_id="test-sess",
        user_intent="get account balance",
        action_type="read",
    )


@pytest.fixture()
def high_risk_request() -> UserRequest:
    return UserRequest(
        session_id="test-sess",
        user_intent="transfer funds to external account",
        action_type="transfer",
    )


@pytest.fixture()
def mock_low_risk_assessment(low_risk_request) -> RiskAssessment:
    return RiskAssessment(
        trace_id=low_risk_request.trace_id,
        risk_score=8,
        risk_level=RiskLevel.LOW,
        reasoning="Routine read.",
        confidence=0.95,
        rag_references=["get account balance"],
    )


@pytest.fixture()
def mock_high_risk_assessment(high_risk_request) -> RiskAssessment:
    return RiskAssessment(
        trace_id=high_risk_request.trace_id,
        risk_score=92,
        risk_level=RiskLevel.HIGH,
        reasoning="High risk transfer.",
        confidence=0.97,
        rag_references=[],
    )


@pytest.fixture()
def test_client():
    """FastAPI TestClient with mocked aria_graph."""
    from fastapi.testclient import TestClient
    from main import app
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client
