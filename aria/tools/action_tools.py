"""
aria/tools/action_tools.py
───────────────────────────
LangChain-compatible tool definitions for ARIA.

Each function is decorated with ``@tool`` so it can be bound directly to a
LangChain agent or called imperatively via ``execute_tool``.

Usage::

    from aria.tools.action_tools import execute_tool

    result = execute_tool("read", session_id="sess-001")
"""

from __future__ import annotations

import logging
import random
import string
import uuid
from datetime import date, timedelta

from langchain_core.tools import tool

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _ticket_id() -> str:
    return "TKT-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=8))


def _schedule_id() -> str:
    return "SCH-" + uuid.uuid4().hex[:10].upper()


def _date_n_days_ago(n: int) -> str:
    return (date.today() - timedelta(days=n)).strftime("%Y-%m-%d")


# ─────────────────────────────────────────────────────────────────────────────
#  Tools
# ─────────────────────────────────────────────────────────────────────────────


@tool
def get_account_summary(session_id: str) -> str:
    """
    Retrieve a summary of the user's account, including balance,
    account type, status, and when data was last refreshed.

    Args:
        session_id: The active session identifier for the user.

    Returns:
        A formatted string with account balance, type, status, and last-updated timestamp.
    """
    return (
        f"[Account Summary | session={session_id}]\n"
        f"  Account Type   : Checking (Primary)\n"
        f"  Account Number : ****4821\n"
        f"  Balance        : $12,450.78\n"
        f"  Available      : $11,900.00\n"
        f"  Status         : Active / Good Standing\n"
        f"  Last Updated   : {_date_n_days_ago(0)} 09:15 UTC"
    )


@tool
def summarize_transactions(session_id: str, limit: int = 10) -> str:
    """
    Return the N most recent transactions for the user's session.

    Args:
        session_id: The active session identifier for the user.
        limit: Maximum number of transactions to return (default 10).

    Returns:
        A formatted ledger of up to ``limit`` transactions with date,
        description, and amount.
    """
    _mock_txns = [
        ("Amazon Prime",        "-$14.99"),
        ("Starbucks",           "-$6.45"),
        ("Salary Deposit",      "+$4,200.00"),
        ("Netflix",             "-$15.49"),
        ("Grocery Store",       "-$87.32"),
        ("Electricity Bill",    "-$110.00"),
        ("ATM Withdrawal",      "-$200.00"),
        ("Freelance Payment",   "+$750.00"),
        ("Gym Membership",      "-$29.99"),
        ("Restaurant",          "-$43.20"),
        ("Online Transfer",     "-$500.00"),
        ("Interest Credit",     "+$3.21"),
    ]
    rows = _mock_txns[:max(1, min(limit, len(_mock_txns)))]
    lines = [f"[Recent Transactions | session={session_id} | showing {len(rows)} of {limit} requested]"]
    for i, (desc, amt) in enumerate(rows, 1):
        lines.append(f"  {i:>2}. {_date_n_days_ago(i-1):<12}  {desc:<25}  {amt}")
    return "\n".join(lines)


@tool
def flag_transaction(transaction_id: str, reason: str) -> str:
    """
    Flag a specific transaction as suspicious or incorrect.

    Args:
        transaction_id: The unique ID of the transaction to flag.
        reason: A human-readable reason for flagging the transaction.

    Returns:
        A confirmation string containing a generated support ticket ID and
        the details of the flag.
    """
    ticket = _ticket_id()
    return (
        f"[Transaction Flagged]\n"
        f"  Transaction ID : {transaction_id}\n"
        f"  Reason         : {reason}\n"
        f"  Ticket ID      : {ticket}\n"
        f"  Status         : Under Review\n"
        f"  ETA            : 2–3 business days\n"
        f"  Contact        : disputes@aria-bank.example.com"
    )


@tool
def generate_report(session_id: str, report_type: str) -> str:
    """
    Generate a financial report of the specified type for the user.

    Args:
        session_id: The active session identifier for the user.
        report_type: Type of report to generate (e.g., "monthly", "quarterly", "annual").

    Returns:
        A mock report string including period totals and top spending categories.
    """
    return (
        f"[Financial Report | session={session_id} | type={report_type}]\n"
        f"  Period         : {_date_n_days_ago(90)} → {_date_n_days_ago(0)}\n"
        f"  Total Income   : $13,350.00\n"
        f"  Total Expenses : $4,872.44\n"
        f"  Net Savings    : $8,477.56\n\n"
        f"  Top Categories:\n"
        f"    1. Housing & Utilities  — $1,420.00 (29.1%)\n"
        f"    2. Groceries & Dining   — $  893.50 (18.3%)\n"
        f"    3. Subscriptions        — $  178.40  (3.7%)\n"
        f"    4. Transport            — $  345.00  (7.1%)\n"
        f"    5. Entertainment        — $  210.00  (4.3%)\n"
        f"  Report ID : RPT-{uuid.uuid4().hex[:8].upper()}"
    )


@tool
def assess_anomaly(session_id: str, action: str) -> str:
    """
    Assess whether an action on the account exhibits anomalous behaviour.

    Args:
        session_id: The active session identifier for the user.
        action: A plain-English description of the action to assess.

    Returns:
        A structured string indicating whether an anomaly was detected,
        the confidence score, and the reason for the determination.
    """
    # Mock: flag "transfer" and "export" actions as anomalous
    keywords = {"transfer", "export", "delete", "close", "limit", "reset"}
    detected = any(kw in action.lower() for kw in keywords)
    confidence = 0.87 if detected else 0.12
    reason = (
        "Action pattern matches high-risk historical transactions in this session."
        if detected else
        "Action is consistent with normal user behaviour for this session."
    )
    return (
        f"[Anomaly Assessment | session={session_id}]\n"
        f"  Action          : {action}\n"
        f"  Anomaly Detected: {'YES ⚠️' if detected else 'NO ✅'}\n"
        f"  Confidence      : {confidence:.0%}\n"
        f"  Reason          : {reason}"
    )


@tool
def schedule_payment(
    session_id: str,
    payee: str,
    amount: float,
    date: str,
) -> str:
    """
    Schedule a future payment to a named payee.

    Args:
        session_id: The active session identifier for the user.
        payee: Name of the payee or beneficiary.
        amount: Payment amount in USD.
        date: Scheduled payment date in YYYY-MM-DD format.

    Returns:
        A confirmation string with the generated schedule ID, payee, amount,
        and scheduled date.
    """
    sid = _schedule_id()
    return (
        f"[Payment Scheduled | session={session_id}]\n"
        f"  Schedule ID    : {sid}\n"
        f"  Payee          : {payee}\n"
        f"  Amount         : ${amount:,.2f}\n"
        f"  Scheduled Date : {date}\n"
        f"  Status         : Confirmed — pending processing\n"
        f"  Reference      : REF-{uuid.uuid4().hex[:6].upper()}"
    )


# ─────────────────────────────────────────────────────────────────────────────
#  Tool Registry & Dispatcher
# ─────────────────────────────────────────────────────────────────────────────

TOOL_REGISTRY: dict[str, object] = {
    "read":      get_account_summary,
    "summarize": summarize_transactions,
    "flag":      flag_transaction,
    "report":    generate_report,
    "anomaly":   assess_anomaly,
    "schedule":  schedule_payment,
}


def execute_tool(action_type: str, session_id: str, **kwargs) -> str:
    """
    Look up ``action_type`` in :data:`TOOL_REGISTRY` and invoke the
    corresponding tool.

    Parameters
    ----------
    action_type:
        Key from ``TOOL_REGISTRY`` (e.g. ``"read"``, ``"transfer"``).
    session_id:
        The active session identifier passed as the first argument to every tool.
    **kwargs:
        Additional keyword arguments forwarded to the tool (e.g.
        ``limit=5`` for ``summarize_transactions``).

    Returns
    -------
    str
        Tool output, or a clear "no tool found" message if *action_type*
        is not registered.
    """
    tool_fn = TOOL_REGISTRY.get(action_type)

    if tool_fn is None:
        logger.warning("execute_tool: unknown action_type=%r", action_type)
        return (
            f"[ARIA Tool Dispatcher] No tool registered for action_type='{action_type}'. "
            f"Available actions: {sorted(TOOL_REGISTRY.keys())}"
        )

    logger.debug("execute_tool: invoking %s(session_id=%r, **%s)", action_type, session_id, kwargs)

    # LangChain @tool functions are callable directly; pass session_id + kwargs
    return tool_fn.invoke({"session_id": session_id, **kwargs})
