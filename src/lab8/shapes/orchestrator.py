"""Shape C — an orchestrator delegates scoped, read-only investigation."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from functools import partial

from lab8.db import connect
from lab8.durability import InjectedCrash, issue_refund
from lab8.handoff import READONLY_TOOLS, make_handoff
from lab8.runtime import dispatch
from lab8.schemas import Budget, RunResult, make_run_id
from lab8.shapes.common import finish, safe_dump
from lab8.tools import tool_cost
from lab8.trace import Trace


def run_claim(
    claim_id: str,
    *,
    seed: int,
    store_path: str,
    crash_at: str | None = None,
) -> RunResult:
    with closing(connect(store_path)) as conn:
        shape = "orchestrator"
        budget = Budget()
        trace = Trace(make_run_id(shape, claim_id, seed))

        def finish_run(
            status: str,
            *,
            proposal_amount: int = 0,
            error_reason: str | None = None,
        ) -> RunResult:
            return finish(
                conn,
                shape=shape,
                claim_id=claim_id,
                seed=seed,
                status=status,
                proposal_amount=proposal_amount,
                budget=budget,
                trace=trace,
                error_reason=error_reason,
            )

        # The parent needs only the owner scope and whether an order was supplied.
        # Keep ticket text, headers, and the claim's secret out of the parent trace.
        budget.add_tool(tool_cost())
        claim = conn.execute(
            "SELECT principal_id, order_id FROM claims WHERE claim_id = ?",
            (claim_id,),
        ).fetchone()
        if claim is None:
            trace.add("error", {"reason": "unknown_claim"})
            return finish_run("failed", error_reason="unknown_claim")

        if not claim["order_id"]:
            trace.add("decision", {"status": "needs_info"})
            return finish_run("needs_info", error_reason="missing_order_id")

        handoff = make_handoff(
            request_id=f"REQ-{claim_id}",
            claim_id=claim_id,
            task="check_applicable_policy_and_quote_refund",
            facts={},
            allowed_tools=[
                "lookup_order",
                "lookup_policy",
                "lookup_refund_history",
                "compute_compensation",
            ],
        )
        trace.add("handoff", safe_dump(handoff))

        investigated = _delegate_investigation(
            conn,
            budget,
            handoff,
            principal_id=str(claim["principal_id"]),
        )
        order_view = investigated["tools"].get("lookup_order") or {}
        trace.add(
            "investigator",
            {
                "claim_view": investigated["claim_view"],
                "lookup_order": order_view,
                "lookup_policy": investigated["tools"].get("lookup_policy", {}),
                "lookup_refund_history": investigated["tools"].get("lookup_refund_history", {}),
            },
        )

        if not order_view.get("visible"):
            reason = order_view.get("reason", "not_found")
            trace.add("decision", {"status": "escalated", "reason": reason})
            return finish_run("escalated", error_reason=reason)

        quote_payload = investigated["tools"].get("compute_compensation")
        if not isinstance(quote_payload, dict):
            trace.add("error", {"reason": "quote_unavailable"})
            return finish_run("failed", error_reason="quote_unavailable")

        proposal = int(quote_payload["amount_vnd"])
        trace.add("quote", safe_dump(quote_payload))
        if proposal <= 0:
            trace.add("decision", {"status": "denied", "reason": quote_payload.get("reason")})
            return finish_run("denied", error_reason=quote_payload.get("reason"))

        try:
            outcome = issue_refund(conn, claim_id, proposal, crash_at=crash_at)
        except InjectedCrash:
            # Flush this run's trace before propagating the injected crash for resume tests.
            trace.add("crash", {"at": crash_at})
            trace.write()
            raise

        if not outcome["ok"]:
            status = "failed" if outcome["conflict"] else "denied"
            trace.add("decision", {"status": status, "reason": outcome["reason"]})
            return finish_run(status, proposal_amount=proposal, error_reason=outcome["reason"])

        trace.add("decision", {"status": "resolved"})
        return finish_run("resolved", proposal_amount=proposal)


def _delegate_investigation(
    conn: sqlite3.Connection,
    budget: Budget,
    handoff: dict,
    *,
    principal_id: str,
) -> dict:
    """Run the investigator side of Shape C, using only handoff-approved tools.

    This is the architectural split: the parent sends a sanitized request, the
    investigator reads scoped facts and returns an allowlisted result, and the
    parent below decides whether to ask the host to commit a refund.
    """
    claim_id = handoff["claim_id"]
    read_tool = partial(dispatch, conn, budget, worker=True)

    # Resolve just the claim/order identifiers needed for the read tools. Do not
    # pass the ticket, headers, or parent secret into the investigator result.
    claim = read_tool("get_claim_view", {"claim_id": claim_id})
    claim_view = {
        key: claim[key]
        for key in ("found", "claim_id", "order_id")
        if key in claim
    }
    results: dict[str, dict] = {}

    for name in handoff["allowed_tools"]:
        if name not in READONLY_TOOLS:
            raise ValueError(f"handoff contains a non-read-only tool: {name}")
        if name == "lookup_order":
            results[name] = read_tool(
                name,
                {"order_id": claim.get("order_id"), "principal_id": principal_id},
            )
        elif name == "lookup_policy":
            results[name] = read_tool(name, {})
        elif name == "lookup_refund_history":
            results[name] = read_tool(
                name,
                {"order_id": claim.get("order_id"), "principal_id": principal_id},
            )
        elif name == "compute_compensation":
            order_view = results.get("lookup_order") or {}
            order = order_view.get("order") or {}
            if order:
                results[name] = read_tool(name, {"order": order})

    return {"claim_view": claim_view, "tools": results}
