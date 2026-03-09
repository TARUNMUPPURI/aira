"""
aria/graph/graph.py
────────────────────
Compiled LangGraph decision pipeline for ARIA.

Topology:
  node_start
    └─► node_classify_risk
          └─► node_route_autonomy
                ├─► node_execute_autonomous ─┐
                ├─► node_execute_supervised  ─┤─► node_write_audit ─► END
                └─► node_escalate ───────────┘

Usage::

    from aria.graph.graph import aria_graph
    from aria.schemas import UserRequest

    result = aria_graph.invoke({
        "request": UserRequest(
            session_id="sess-001",
            user_intent="get account balance",
            action_type="read",
        )
    })
    print(result["autonomy_decision"].autonomy_mode)
    print(result["audit_record"])
"""

from __future__ import annotations

import logging

from langgraph.graph import END, StateGraph

from aria.graph.nodes import (
    node_classify_risk,
    node_escalate,
    node_execute_autonomous,
    node_execute_supervised,
    node_route_autonomy,
    node_start,
    node_write_audit,
)
from aria.graph.state import ARIAState
from aria.schemas import AutonomyMode

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
#  Conditional edge router
# ─────────────────────────────────────────────────────────────────────────────


def _route(state: ARIAState) -> str:
    """
    Read the autonomy_mode from the routed decision and return the name of
    the next node to execute.
    """
    mode = state["autonomy_decision"].autonomy_mode
    mapping = {
        AutonomyMode.AUTONOMOUS: "node_execute_autonomous",
        AutonomyMode.SUPERVISED: "node_execute_supervised",
        AutonomyMode.ESCALATE:   "node_escalate",
    }
    next_node = mapping[mode]
    logger.debug("Conditional edge: mode=%s → %s", mode.value, next_node)
    return next_node


# ─────────────────────────────────────────────────────────────────────────────
#  Graph construction
# ─────────────────────────────────────────────────────────────────────────────


def _build_graph() -> StateGraph:
    g = StateGraph(ARIAState)

    # Register nodes
    g.add_node("node_start",               node_start)
    g.add_node("node_classify_risk",        node_classify_risk)
    g.add_node("node_route_autonomy",       node_route_autonomy)
    g.add_node("node_execute_autonomous",   node_execute_autonomous)
    g.add_node("node_execute_supervised",   node_execute_supervised)
    g.add_node("node_escalate",             node_escalate)
    g.add_node("node_write_audit",          node_write_audit)

    # Linear edges
    g.set_entry_point("node_start")
    g.add_edge("node_start",         "node_classify_risk")
    g.add_edge("node_classify_risk", "node_route_autonomy")

    # Conditional edge: autonomy mode → execution branch
    g.add_conditional_edges(
        "node_route_autonomy",
        _route,
        {
            "node_execute_autonomous": "node_execute_autonomous",
            "node_execute_supervised": "node_execute_supervised",
            "node_escalate":           "node_escalate",
        },
    )

    # All execution branches converge at audit
    g.add_edge("node_execute_autonomous", "node_write_audit")
    g.add_edge("node_execute_supervised", "node_write_audit")
    g.add_edge("node_escalate",           "node_write_audit")

    g.add_edge("node_write_audit", END)

    return g


# ─────────────────────────────────────────────────────────────────────────────
#  Module-level compiled singleton
# ─────────────────────────────────────────────────────────────────────────────

aria_graph = _build_graph().compile()
logger.info("aria_graph compiled successfully")
