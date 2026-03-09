"""
tests/test_risk_classifier.py
──────────────────────────────
Unit tests for RiskClassifier — mocks Gemini and optionally uses real ChromaDB.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from aria.agents.risk_classifier import RiskClassifier
from aria.schemas import RiskAssessment, RiskLevel, UserRequest


# ── Helper ────────────────────────────────────────────────────────────────────

def _make_genai_response(payload: dict) -> MagicMock:
    resp = MagicMock()
    resp.text = json.dumps(payload)
    return resp


def _mock_model(payload: dict) -> MagicMock:
    """Return a mock genai model whose generate_content returns *payload* as JSON."""
    m = MagicMock()
    m.generate_content.return_value = _make_genai_response(payload)
    return m


def _patch_genai(model_mock):
    """Context manager: patch genai.configure and genai.GenerativeModel."""
    return patch.multiple(
        "aria.agents.risk_classifier.genai",
        configure=MagicMock(),
        GenerativeModel=MagicMock(return_value=model_mock),
    )


# ─────────────────────────────────────────────────────────────────────────────
#  Test 1 — low risk score
# ─────────────────────────────────────────────────────────────────────────────

def test_low_risk_returns_score_below_35():
    """intent='get account balance' must yield risk_score ≤ 35 and RiskLevel.LOW."""
    request = UserRequest(
        session_id="t1", user_intent="get account balance", action_type="read"
    )
    low_payload = {
        "trace_id": request.trace_id,
        "risk_score": 8,
        "risk_level": "LOW",
        "reasoning": "Routine read — matches low-risk calibration examples.",
        "confidence": 0.95,
        "rag_references": ["get account balance"],
    }
    with _patch_genai(_mock_model(low_payload)):
        result = RiskClassifier().classify(request)

    assert isinstance(result, RiskAssessment)
    assert result.risk_score <= 35, f"Expected ≤35, got {result.risk_score}"
    assert result.risk_level == RiskLevel.LOW


# ─────────────────────────────────────────────────────────────────────────────
#  Test 2 — high risk score
# ─────────────────────────────────────────────────────────────────────────────

def test_high_risk_returns_score_above_71():
    """intent='transfer funds to external account' must yield risk_score ≥ 71 and RiskLevel.HIGH."""
    request = UserRequest(
        session_id="t2",
        user_intent="transfer funds to external account",
        action_type="transfer",
    )
    high_payload = {
        "trace_id": request.trace_id,
        "risk_score": 92,
        "risk_level": "HIGH",
        "reasoning": "Irreversible external transfer — HIGH risk.",
        "confidence": 0.97,
        "rag_references": [],
    }
    with _patch_genai(_mock_model(high_payload)):
        result = RiskClassifier().classify(request)

    assert isinstance(result, RiskAssessment)
    assert result.risk_score >= 71, f"Expected ≥71, got {result.risk_score}"
    assert result.risk_level == RiskLevel.HIGH


# ─────────────────────────────────────────────────────────────────────────────
#  Test 3 — safe fallback on LLM failure
# ─────────────────────────────────────────────────────────────────────────────

def test_fallback_on_llm_failure():
    """When Gemini raises, classify() must return risk_score=100, HIGH, confidence=0.0."""
    request = UserRequest(
        session_id="t3", user_intent="something", action_type="read"
    )
    failing_model = MagicMock()
    failing_model.generate_content.side_effect = RuntimeError("Simulated LLM failure")

    with _patch_genai(failing_model):
        result = RiskClassifier().classify(request)

    assert isinstance(result, RiskAssessment)
    assert result.risk_score == 100
    assert result.risk_level == RiskLevel.HIGH
    assert result.confidence == 0.0
    assert "Classifier failed" in result.reasoning or "failed" in result.reasoning.lower()


# ─────────────────────────────────────────────────────────────────────────────
#  Test 4 — RAG returns results for seeded intents
# ─────────────────────────────────────────────────────────────────────────────

def test_rag_returns_results_for_known_intents():
    """
    retrieve_similar_decisions with a seeded intent should return ≥1 reference.
    ChromaDB is seeded with 20 decisions on first call.
    """
    from aria.rag.retriever import retrieve_similar_decisions

    refs = retrieve_similar_decisions(
        user_intent="get account balance",
        action_type="read",
        n_results=3,
    )
    assert isinstance(refs, list)
    assert len(refs) >= 1, "Expected at least 1 RAG reference for a seeded intent"
    # Each reference should have the expected metadata keys
    assert "intent" in refs[0]
    assert "risk_score" in refs[0]


# ─────────────────────────────────────────────────────────────────────────────
#  Test 5 — output always a valid Pydantic model, never raises
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("intent,action", [
    ("get account balance",               "read"),
    ("transfer funds to external account","transfer"),
    ("x",                                 "unknown"),   # single-char minimum valid intent
    ("xss <script>alert(1)</script>",     "inject"),
    ("summarize spending",                "summarize"),
])
def test_output_always_valid_pydantic_model(intent, action):
    """
    For any input, classify() must return a valid RiskAssessment without raising.
    Uses a mock LLM that returns a valid payload to keep the test deterministic.
    """
    request = UserRequest(session_id="t5", user_intent=intent, action_type=action)
    mock_payload = {
        "trace_id": request.trace_id,
        "risk_score": 50,
        "risk_level": "MEDIUM",
        "reasoning": "Mock classification.",
        "confidence": 0.80,
        "rag_references": [],
    }
    model_mock = _mock_model(mock_payload)
    with _patch_genai(model_mock):
        result = RiskClassifier().classify(request)

    assert isinstance(result, RiskAssessment)
    assert 0 <= result.risk_score <= 100
    assert 0.0 <= result.confidence <= 1.0
    assert result.trace_id == request.trace_id
