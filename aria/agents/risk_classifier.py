"""
aria/agents/risk_classifier.py
────────────────────────────────
RiskClassifier agent — Gemini Flash + RAG-augmented prompting.

Classify any UserRequest into a RiskAssessment with no hard-coded thresholds;
all band boundaries come from ``settings``.

Usage::

    from aria.agents.risk_classifier import RiskClassifier
    from aria.schemas import UserRequest

    clf = RiskClassifier()
    assessment = clf.classify(request)
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone

import google.generativeai as genai

from aria.config import settings
from aria.rag.retriever import retrieve_similar_decisions
from aria.rag.vectorstore import get_vectorstore
from aria.schemas import RiskAssessment, RiskLevel, UserRequest

logger = logging.getLogger(__name__)

# ── Gemini model name ─────────────────────────────────────────────────────────
_MODEL = "gemini-2.0-flash"


def _risk_level_from_score(score: int) -> RiskLevel:
    """
    Derive categorical RiskLevel from a numeric score using the configured
    thresholds — never from hardcoded values.
    """
    if score <= settings.risk_low_threshold:
        return RiskLevel.LOW
    if score >= settings.risk_high_threshold:
        return RiskLevel.HIGH
    return RiskLevel.MEDIUM


def _format_rag_block(refs: list[dict]) -> str:
    """Convert RAG metadata dicts into a numbered calibration block for the prompt."""
    if not refs:
        return "  (no historical examples available)"
    lines = []
    for i, r in enumerate(refs, 1):
        lines.append(
            f"  {i}. intent=\"{r.get('intent', '?')}\" | "
            f"action={r.get('action_type', '?')} | "
            f"risk={r.get('risk_score', '?')} | "
            f"outcome={r.get('outcome', '?')}"
        )
    return "\n".join(lines)


def _build_prompt(request: UserRequest, rag_block: str) -> str:
    low  = settings.risk_low_threshold
    high = settings.risk_high_threshold

    return f"""You are a financial-services risk classifier for an autonomous AI agent system.

RISK BANDS (use configured thresholds — do not deviate):
  • LOW    (0–{low}):    Safe to auto-execute. Routine read/summarise operations.
  • MEDIUM ({low+1}–{high-1}): Flag for supervised execution. Moderate-impact changes.
  • HIGH   ({high}–100): Halt and escalate to human. Irreversible or high-value actions.

CALIBRATION EXAMPLES from historical decisions:
{rag_block}

ACTION TO CLASSIFY:
  intent      : {request.user_intent}
  action_type : {request.action_type}
  session_id  : {request.session_id}
  context     : {json.dumps(request.context)}

TASK:
Analyse the action using the calibration examples and the risk bands above.
Return ONLY a valid JSON object — no markdown fences, no prose, no explanation.

Required JSON schema:
{{
  "trace_id":       "<copy the trace_id exactly: {request.trace_id}>",
  "risk_score":     <integer 0–100>,
  "risk_level":     "<LOW | MEDIUM | HIGH>",
  "reasoning":      "<one-sentence explanation referencing the calibration examples>",
  "confidence":     <float 0.0–1.0>,
  "rag_references": [<list of intent strings from calibration examples that influenced the score>]
}}""".strip()


class RiskClassifier:
    """
    Classifies a :class:`~aria.schemas.UserRequest` into a
    :class:`~aria.schemas.RiskAssessment` using Gemini Flash and
    RAG-augmented prompting.

    The classifier:
    1. Retrieves similar past decisions from ChromaDB.
    2. Builds a structured prompt injecting RAG context + thresholds from settings.
    3. Calls Gemini Flash and parses the JSON response into a RiskAssessment.
    4. Writes the new decision back to ChromaDB (online learning).
    5. On any failure → safe fallback with risk_score=100, risk_level=HIGH.
    """

    def __init__(self) -> None:
        genai.configure(api_key=settings.gemini_api_key)
        self._model = genai.GenerativeModel(model_name=_MODEL)
        logger.info("RiskClassifier initialised with model=%s", _MODEL)

    # ── Public API ────────────────────────────────────────────────────────────

    def classify(self, request: UserRequest) -> RiskAssessment:
        """
        Classify *request* and return a :class:`RiskAssessment`.

        Never raises — all errors produce the HIGH-risk safe-fallback.
        """
        tid = request.trace_id
        try:
            return self._classify_inner(request)
        except Exception as exc:  # noqa: BLE001
            logger.error("[%s] RiskClassifier failed: %s", tid, exc, exc_info=True)
            return self._safe_fallback(tid)

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _classify_inner(self, request: UserRequest) -> RiskAssessment:
        tid = request.trace_id

        # Step 1 — RAG retrieval
        refs = retrieve_similar_decisions(
            user_intent=request.user_intent,
            action_type=request.action_type,
            n_results=3,
            trace_id=tid,
        )
        logger.debug("[%s] RAG returned %d references", tid, len(refs))

        # Step 2 — Format RAG context block
        rag_block = _format_rag_block(refs)

        # Step 3 — Build prompt
        prompt = _build_prompt(request, rag_block)

        # Step 4 — Call Gemini Flash
        logger.info("[%s] Calling %s …", tid, _MODEL)
        response = self._model.generate_content(
            prompt,
            generation_config=genai.types.GenerationConfig(
                temperature=0.1,       # low temp for deterministic scoring
                max_output_tokens=512,
            ),
        )
        raw = response.text.strip()
        logger.debug("[%s] Raw Gemini response: %s", tid, raw)

        # Step 5 — Strip optional markdown fences and parse JSON
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
        raw = re.sub(r"\s*```$", "", raw, flags=re.MULTILINE)
        payload = json.loads(raw)

        # Enforce trace_id consistency and risk_level alignment
        payload["trace_id"] = tid
        score = int(payload["risk_score"])
        payload["risk_score"] = score
        payload["risk_level"] = _risk_level_from_score(score).value

        assessment = RiskAssessment.model_validate(payload)
        logger.info(
            "[%s] Classified: score=%d level=%s confidence=%.2f",
            tid, assessment.risk_score, assessment.risk_level, assessment.confidence,
        )

        # Step 7 — Write result back to ChromaDB (online learning)
        self._persist_to_vectorstore(request, assessment)

        return assessment

    def _persist_to_vectorstore(
        self, request: UserRequest, assessment: RiskAssessment
    ) -> None:
        """
        Add the newly classified decision to ChromaDB so future requests
        can benefit from it as a calibration example.
        """
        try:
            col = get_vectorstore()
            doc_id = f"live-{request.trace_id}"
            doc_text = (
                f"{request.user_intent} | {request.action_type} "
                f"| risk:{assessment.risk_score} | outcome:{assessment.risk_level.value}"
            )
            col.add(
                ids=[doc_id],
                documents=[doc_text],
                metadatas=[{
                    "intent":      request.user_intent,
                    "action_type": request.action_type,
                    "risk_score":  assessment.risk_score,
                    "outcome":     assessment.risk_level.value,
                    "trace_id":    request.trace_id,
                    "source":      "live",
                    "timestamp":   datetime.now(tz=timezone.utc).isoformat(),
                }],
            )
            logger.debug("[%s] Persisted decision to ChromaDB (id=%s)", request.trace_id, doc_id)
        except Exception as exc:  # noqa: BLE001
            # Non-fatal — a failed write to ChromaDB must never block the assessment
            logger.warning(
                "[%s] Failed to persist decision to ChromaDB: %s",
                request.trace_id, exc,
            )

    @staticmethod
    def _safe_fallback(trace_id: str) -> RiskAssessment:
        """Return a maximally defensive HIGH-risk assessment on any classifier error."""
        return RiskAssessment(
            trace_id=trace_id,
            risk_score=100,
            risk_level=RiskLevel.HIGH,
            reasoning="Classifier failed — defaulting to HIGH risk for safety.",
            confidence=0.0,
            rag_references=[],
        )
