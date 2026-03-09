"""
aria/rag/retriever.py
──────────────────────
RAG retrieval layer for ARIA.

Usage::

    from aria.rag.retriever import retrieve_similar_decisions

    refs = retrieve_similar_decisions(
        user_intent="transfer money abroad",
        action_type="transfer",
        n_results=3,
    )
    # refs → list of metadata dicts, e.g.:
    # [{"intent": "...", "action_type": "...", "risk_score": 92, "outcome": "PENDING_APPROVAL"}, ...]
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def retrieve_similar_decisions(
    user_intent: str,
    action_type: str,
    n_results: int = 3,
    trace_id: Optional[str] = None,
) -> list[dict]:
    """
    Query the ChromaDB collection for the ``n_results`` most similar past decisions.

    Query text is built as ``"{user_intent} | {action_type}"`` to mirror the
    format used when seeding documents.

    Parameters
    ----------
    user_intent:
        The user's stated intent (e.g. ``"transfer to external bank account"``).
    action_type:
        The machine-readable action category (e.g. ``"transfer"``).
    n_results:
        Number of similar decisions to retrieve (default 3).
    trace_id:
        Optional trace ID included in warning logs for end-to-end traceability.

    Returns
    -------
    list[dict]
        Each dict contains: ``intent``, ``action_type``, ``risk_score``, ``outcome``.
        Returns an empty list if ChromaDB is unavailable — **never raises**.
    """
    try:
        # Import here to keep the module importable even if chromadb isn't installed
        # in a test environment that mocks it out.
        from aria.rag.vectorstore import get_vectorstore

        collection = get_vectorstore()
        doc_count = collection.count()

        # Cap n_results to however many docs actually exist
        effective_n = min(n_results, doc_count)
        if effective_n == 0:
            logger.warning(
                "[%s] ChromaDB collection is empty — returning no references.",
                trace_id or "no-trace",
            )
            return []

        query_text = f"{user_intent} | {action_type}"
        results = collection.query(
            query_texts=[query_text],
            n_results=effective_n,
            include=["metadatas", "distances"],
        )

        metadatas: list[dict] = results.get("metadatas", [[]])[0]
        logger.debug(
            "[%s] RAG query '%s' → %d results",
            trace_id or "no-trace",
            query_text,
            len(metadatas),
        )
        return metadatas

    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[%s] ChromaDB unavailable — returning empty RAG references. Error: %s",
            trace_id or "no-trace",
            exc,
        )
        return []
