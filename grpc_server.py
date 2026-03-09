"""
grpc_server.py
───────────────
ARIA gRPC server — ApprovalService implementation.

Shares the same ``pending_approvals`` dict and ``process_approval``
function used by the REST API, so both transports see identical state.

Start the server:
    python grpc_server.py

Or alongside the FastAPI server using docker-compose / supervisord.
"""

from __future__ import annotations

import logging
import sys
import os
from concurrent import futures

import grpc

# ── Make sure the generated stubs can be found ───────────────────────────────
_PROTO_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "proto")
if _PROTO_DIR not in sys.path:
    sys.path.insert(0, _PROTO_DIR)

import approval_pb2        # noqa: E402  (generated)
import approval_pb2_grpc   # noqa: E402  (generated)

from aria.api.approval import get_pending, process_approval
from aria.config import settings
from aria.schemas import ApprovalResponse

logging.basicConfig(
    level=getattr(logging, settings.log_level, logging.INFO),
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger("aria.grpc")


# ─────────────────────────────────────────────────────────────────────────────
#  Servicer implementation
# ─────────────────────────────────────────────────────────────────────────────


class ApprovalServicer(approval_pb2_grpc.ApprovalServiceServicer):
    """
    Implements the ``ApprovalService`` gRPC methods.

    Both ``SubmitApproval`` and ``GetPendingApprovals`` delegate directly
    to the shared ``aria.api.approval`` module so REST and gRPC operate on
    exactly the same in-memory state.
    """

    def SubmitApproval(
        self,
        request: approval_pb2.ApprovalRequest,
        context: grpc.ServicerContext,
    ) -> approval_pb2.ApprovalReply:
        """
        Process a human approval or denial.
        Delegates to the same ``process_approval()`` used by ``POST /v1/approve``.
        """
        logger.info(
            "[%s] gRPC SubmitApproval: approved=%s reviewer=%s",
            request.trace_id, request.approved, request.reviewed_by,
        )

        approval_resp = ApprovalResponse(
            trace_id=request.trace_id,
            approved=request.approved,
            reviewed_by=request.reviewed_by,
            notes=request.notes or None,
        )

        found = process_approval(approval_resp)
        if not found:
            context.set_code(grpc.StatusCode.NOT_FOUND)
            context.set_details(
                f"No pending approval found for trace_id='{request.trace_id}'"
            )
            return approval_pb2.ApprovalReply(
                trace_id=request.trace_id,
                status="NOT_FOUND",
                message=f"trace_id '{request.trace_id}' has no pending approval.",
            )

        status  = "APPROVED" if request.approved else "DENIED"
        message = (
            f"Action approved and queued for execution by {request.reviewed_by}."
            if request.approved
            else f"Action denied by {request.reviewed_by}."
        )
        return approval_pb2.ApprovalReply(
            trace_id=request.trace_id,
            status=status,
            message=message,
        )

    def GetPendingApprovals(
        self,
        request: approval_pb2.Empty,
        context: grpc.ServicerContext,
    ) -> approval_pb2.PendingList:
        """
        Return all currently pending approval requests.
        Reads from the same ``pending_approvals`` dict as the REST API.
        """
        pending = get_pending()
        items = [
            approval_pb2.PendingItem(
                trace_id=p.trace_id,
                user_intent=p.user_intent,
                risk_score=p.risk_score,
                explanation=p.explanation,
            )
            for p in pending
        ]
        logger.info("gRPC GetPendingApprovals: returning %d pending items", len(items))
        return approval_pb2.PendingList(items=items)


# ─────────────────────────────────────────────────────────────────────────────
#  Server factory
# ─────────────────────────────────────────────────────────────────────────────


def create_server(port: int | None = None) -> grpc.Server:
    """
    Build and configure the gRPC server.

    Parameters
    ----------
    port:
        Override the port (defaults to ``settings.grpc_port``).

    Returns
    -------
    grpc.Server
        A configured (but not yet started) server instance.
    """
    target_port = port if port is not None else settings.grpc_port
    server = grpc.server(
        futures.ThreadPoolExecutor(max_workers=10),
        options=[
            ("grpc.max_send_message_length",    50 * 1024 * 1024),
            ("grpc.max_receive_message_length",  50 * 1024 * 1024),
        ],
    )
    approval_pb2_grpc.add_ApprovalServiceServicer_to_server(ApprovalServicer(), server)
    server.add_insecure_port(f"[::]:{target_port}")
    return server


def serve(port: int | None = None) -> None:
    """Start the gRPC server and block until KeyboardInterrupt."""
    target_port = port if port is not None else settings.grpc_port
    server = create_server(target_port)
    server.start()
    logger.info("ARIA gRPC server listening on port %d", target_port)
    try:
        server.wait_for_termination()
    except KeyboardInterrupt:
        logger.info("gRPC server shutting down ...")
        server.stop(grace=5)


# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    serve()
