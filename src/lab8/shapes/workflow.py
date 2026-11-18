"""Shape A — deterministic workflow.

Money is calculated by code through ``compute_compensation``.
The model does not choose or commit the refund amount.
"""

from __future__ import annotations

import sqlite3

from lab8.db import connect
from lab8.durability import issue_refund
from lab8.schemas import Budget, RunResult, make_run_id
from lab8.shapes.common import finish
from lab8.tools import (
    compute_compensation,
    get_claim_view,
    lookup_order,
    tool_cost,
)
from lab8.trace import Trace


def run_claim(
    claim_id: str,
    *,
    seed: int,
    store_path: str,
    crash_at: str | None = None,
) -> RunResult:
    """Run one claim through the deterministic workflow."""

    conn = connect(store_path)
    try:
        return _run(
            conn,
            claim_id,
            seed=seed,
            crash_at=crash_at,
        )
    finally:
        conn.close()


def _run(
    conn: sqlite3.Connection,
    claim_id: str,
    *,
    seed: int,
    crash_at: str | None,
) -> RunResult:
    budget = Budget()
    run_id = make_run_id("workflow", claim_id, seed)
    trace = Trace(run_id)

    # Step 1: Read the safe claim view.
    budget.add_tool(tool_cost())
    claim = get_claim_view(conn, claim_id)

    if not claim["found"]:
        trace.add(
            "decision",
            {
                "status": "failed",
                "reason": "unknown_claim",
            },
        )
        return finish(
            conn,
            shape="workflow",
            claim_id=claim_id,
            seed=seed,
            status="failed",
            proposal_amount=0,
            budget=budget,
            trace=trace,
            error_reason="unknown_claim",
        )

    # Step 2: An order ID must come from the claim record,
    # never from the user-written ticket.
    if not claim.get("order_id"):
        trace.add(
            "decision",
            {
                "status": "needs_info",
                "reason": "missing_order_id",
            },
        )
        return finish(
            conn,
            shape="workflow",
            claim_id=claim_id,
            seed=seed,
            status="needs_info",
            proposal_amount=0,
            budget=budget,
            trace=trace,
            error_reason="missing_order_id",
        )

    # Step 3: Look up the order through the scoped read tool.
    budget.add_tool(tool_cost())
    order_view = lookup_order(
        conn,
        claim["order_id"],
        claim["principal_id"],
    )

    if not order_view["visible"]:
        reason = order_view.get("reason", "order_not_visible")
        trace.add(
            "decision",
            {
                "status": "escalated",
                "reason": reason,
            },
        )
        return finish(
            conn,
            shape="workflow",
            claim_id=claim_id,
            seed=seed,
            status="escalated",
            proposal_amount=0,
            budget=budget,
            trace=trace,
            error_reason=reason,
        )

    # Step 4: Calculate the full entitlement from trusted order data.
    budget.add_tool(tool_cost())
    quote = compute_compensation(
        conn,
        order_view["order"],
    )
    proposal = int(quote["amount_vnd"])

    trace.add(
        "quote",
        {
            "amount_vnd": proposal,
            "reason": quote["reason"],
            "clause_ids": quote["clause_ids"],
        },
    )

    # Step 5: A zero quote is denied without writing to the ledger.
    if proposal <= 0:
        trace.add(
            "decision",
            {
                "status": "denied",
                "reason": quote["reason"],
            },
        )
        return finish(
            conn,
            shape="workflow",
            claim_id=claim_id,
            seed=seed,
            status="denied",
            proposal_amount=0,
            budget=budget,
            trace=trace,
            error_reason=quote["reason"],
        )

    # Step 6: Send the full quote to the host.
    # The host applies approval, paid amount, prior-refund caps,
    # idempotency, and crash safety.
    trace.add(
        "refund_request",
        {
            "proposal_amount": proposal,
        },
    )

    outcome = issue_refund(
        conn,
        claim_id,
        proposal,
        crash_at=crash_at,
    )

    if not outcome["ok"]:
        status = "failed" if outcome["conflict"] else "denied"

        trace.add(
            "decision",
            {
                "status": status,
                "reason": outcome["reason"],
            },
        )
        return finish(
            conn,
            shape="workflow",
            claim_id=claim_id,
            seed=seed,
            status=status,
            proposal_amount=proposal,
            budget=budget,
            trace=trace,
            error_reason=outcome["reason"],
        )

    # Step 7: Refund committed or replayed idempotently.
    trace.add(
        "decision",
        {
            "status": "resolved",
            "reason": outcome["reason"],
        },
    )
    return finish(
        conn,
        shape="workflow",
        claim_id=claim_id,
        seed=seed,
        status="resolved",
        proposal_amount=proposal,
        budget=budget,
        trace=trace,
        error_reason=None,
    )