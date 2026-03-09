"""
aria/rag/vectorstore.py
────────────────────────
ChromaDB persistent vector store for ARIA.

Collection : ``aria_decisions``
Seeded on  : first startup if doc count < 20

Usage::

    from aria.rag.vectorstore import get_vectorstore
    col = get_vectorstore()
    print(col.count())
"""

import logging
from functools import lru_cache

import chromadb
from chromadb.config import Settings as ChromaSettings

from aria.config import settings

logger = logging.getLogger(__name__)

COLLECTION_NAME = "aria_decisions"

# ─────────────────────────────────────────────────────────────────────────────
#  Seed data
# ─────────────────────────────────────────────────────────────────────────────

_SEED: list[dict] = [
    {"intent": "get account balance",               "action_type": "read",          "risk_score": 8,  "outcome": "EXECUTED"},
    {"intent": "show last 5 transactions",           "action_type": "read",          "risk_score": 12, "outcome": "EXECUTED"},
    {"intent": "summarize spending this month",      "action_type": "summarize",     "risk_score": 18, "outcome": "EXECUTED"},
    {"intent": "generate quarterly report",          "action_type": "report",        "risk_score": 22, "outcome": "EXECUTED"},
    {"intent": "show account statement",             "action_type": "read",          "risk_score": 15, "outcome": "EXECUTED"},
    {"intent": "set spending alert threshold",       "action_type": "update",        "risk_score": 30, "outcome": "EXECUTED"},
    {"intent": "view transaction categories",        "action_type": "read",          "risk_score": 10, "outcome": "EXECUTED"},
    {"intent": "flag transaction as suspicious",     "action_type": "flag",          "risk_score": 55, "outcome": "FLAGGED"},
    {"intent": "update contact email address",       "action_type": "update",        "risk_score": 48, "outcome": "FLAGGED"},
    {"intent": "cancel recurring payment",           "action_type": "cancel",        "risk_score": 62, "outcome": "FLAGGED"},
    {"intent": "change notification settings",       "action_type": "update",        "risk_score": 40, "outcome": "FLAGGED"},
    {"intent": "dispute a transaction charge",       "action_type": "dispute",       "risk_score": 58, "outcome": "FLAGGED"},
    {"intent": "schedule future payment",            "action_type": "schedule",      "risk_score": 65, "outcome": "FLAGGED"},
    {"intent": "transfer to external bank account",  "action_type": "transfer",      "risk_score": 92, "outcome": "PENDING_APPROVAL"},
    {"intent": "export full transaction history",    "action_type": "export",        "risk_score": 75, "outcome": "PENDING_APPROVAL"},
    {"intent": "add new payee",                      "action_type": "add_payee",     "risk_score": 88, "outcome": "PENDING_APPROVAL"},
    {"intent": "increase daily transfer limit",      "action_type": "limit_change",  "risk_score": 95, "outcome": "PENDING_APPROVAL"},
    {"intent": "close bank account permanently",     "action_type": "close_account", "risk_score": 98, "outcome": "PENDING_APPROVAL"},
    {"intent": "reset account password",             "action_type": "security",      "risk_score": 82, "outcome": "PENDING_APPROVAL"},
    {"intent": "download all personal data",         "action_type": "export",        "risk_score": 78, "outcome": "PENDING_APPROVAL"},
]


def _doc_text(row: dict) -> str:
    """Canonical text representation stored as the ChromaDB document."""
    return (
        f"{row['intent']} | {row['action_type']} "
        f"| risk:{row['risk_score']} | outcome:{row['outcome']}"
    )


def _seed_collection(collection: chromadb.Collection) -> None:
    """Insert all 20 seed records into the collection."""
    logger.info("Seeding '%s' with %d synthetic decisions …", COLLECTION_NAME, len(_SEED))
    collection.add(
        ids=[f"seed-{i:03d}" for i in range(len(_SEED))],
        documents=[_doc_text(row) for row in _SEED],
        metadatas=[
            {
                "intent":      row["intent"],
                "action_type": row["action_type"],
                "risk_score":  row["risk_score"],
                "outcome":     row["outcome"],
            }
            for row in _SEED
        ],
    )
    logger.info("Seeding complete — %d documents inserted.", collection.count())


# ─────────────────────────────────────────────────────────────────────────────
#  Public API
# ─────────────────────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def get_vectorstore() -> chromadb.Collection:
    """
    Return the persistent ChromaDB collection, seeding it on first access
    if the document count is fewer than 20.

    The client and collection are cached — repeated calls return the same object.
    """
    persist_dir = settings.chroma_persist_dir
    logger.info("Connecting to ChromaDB at '%s' …", persist_dir)

    client = chromadb.PersistentClient(
        path=persist_dir,
        settings=ChromaSettings(anonymized_telemetry=False),
    )

    collection = client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"hnsw:space": "cosine"},
    )

    if collection.count() < 20:
        _seed_collection(collection)
    else:
        logger.info(
            "Collection '%s' already has %d docs — skipping seed.",
            COLLECTION_NAME,
            collection.count(),
        )

    return collection
