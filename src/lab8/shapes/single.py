"""Shape B — one ReAct agent. Student work.

Use `lab8.runtime.run_react` with max_steps=8. `decide` may pick a read tool.
It must not call `issue_refund`. After the loop, the host validates the quote
and is the only writer.

A decide() that trusts ticket text over the order row will fail hidden claims
where the customer overstates the delay or cites a superseded clause.

Per-order cap: an order may already have been refunded by an earlier claim.
The read tool `lookup_refund_history` shows how much. The host still enforces
the cap, so pass the full quote to `durability.issue_refund`, not a net amount.

Caps: stop at 8 steps. Count model calls in the Budget so cost stays comparable.

Trace contract v1 (docs/contracts.md): return a trace_path to a JSON file written in this run,
{"run_id": <same run_id>, "events": [...]}, on every return path including early
ones. `lab8.trace.Trace` does this; your own tracer is fine if it matches.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from lab8 import durability
from lab8.db import connect
from lab8.mock_llm import complete
from lab8.runtime import run_react
from lab8.schemas import Budget, RunResult, make_run_id
from lab8.shapes.common import finish, safe_dump
from lab8.tools import get_claim_view, tool_cost
from lab8.trace import Trace

SHAPE = "single"
MAX_STEPS = 8


def run_claim(
    claim_id: str,
    *,
    seed: int,
    store_path: str,
    crash_at: str | None = None,
) -> RunResult:
    conn = connect(store_path)
    try:
        return _run(conn, claim_id, seed=seed, crash_at=crash_at)
    finally:
        conn.close()


def _run(conn: sqlite3.Connection, claim_id: str, *, seed: int, crash_at: str | None) -> RunResult:
    budget = Budget()
    trace = Trace(make_run_id(SHAPE, claim_id, seed))

    def done(status: str, proposal: int = 0, reason: str | None = None) -> RunResult:
        # Every return path goes through here, so a trace is always written and RunResult stays valid.
        trace.add("decision", {"status": status, "proposal_amount": proposal, "reason": reason})
        return finish(
            conn,
            shape=SHAPE,
            claim_id=claim_id,
            seed=seed,
            status=status,
            proposal_amount=proposal,
            budget=budget,
            trace=trace,
            error_reason=reason,
        )

    # 1. Read the claim through the filtered view (no parent_secret, no raw_headers).
    budget.add_tool(tool_cost())
    view = get_claim_view(conn, claim_id)
    if not view["found"]:
        return done("failed", reason="unknown_claim")
    trace.add("claim", {"claim_id": claim_id, "has_order_id": bool(view["order_id"])})

    # 2. ReAct loop. The agent only gets read tools; its choices live in decide().
    decide = _make_decide(budget, seed=seed, principal_id=view["principal_id"])
    outcome = run_react(
        conn,
        budget,
        observation={"claim": view},
        decide=decide,
        max_steps=MAX_STEPS,
    )
    final = outcome["final"]
    trace.add("react", {"stopped": outcome["stopped"], "final": safe_dump(final)})

    status = final.get("status")
    if status in ("needs_info", "escalated", "failed"):
        return done(status, reason=final.get("error_reason"))

    # 3. The quote comes from compute_compensation (code); the agent only passes it along.
    proposal = int(final.get("amount_vnd") or 0)
    if proposal <= 0:
        # Zero quote: deny locally and do NOT call issue_refund.
        return done("denied", reason=final.get("quote_reason"))

    # 4. The host is the only writer. Pass the FULL quote and crash_at.
    try:
        result = durability.issue_refund(conn, claim_id, proposal, crash_at=crash_at)
    except durability.InjectedCrash:
        trace.add("crash", {"at": crash_at})
        trace.write()
        raise  # never swallow the crash: T12 must observe it

    if not result["ok"]:
        status = "failed" if result["conflict"] else "denied"
        return done(status, proposal, result["reason"])
    return done("resolved", proposal)


def _make_decide(budget: Budget, *, seed: int, principal_id: str):
    """Build the agent's decide() function.

    run_react only hands back the result of the tool that was JUST called,
    so the agent keeps its own `memory` to carry facts across steps.
    """
    memory: dict[str, Any] = {}

    def decide(seen: dict[str, Any]) -> dict[str, Any]:
        # Each reasoning step counts as one model call, so cost is comparable across shapes.
        last_tool = seen.get("last_tool")
        complete(budget, seed=seed, purpose="plan", prompt=f"step after {last_tool or 'start'}")

        # First step: only the claim view is available.
        if last_tool is None:
            claim = seen["claim"]
            memory["order_id"] = claim.get("order_id")
            if not memory["order_id"]:
                # Never pull an order id out of the ticket text.
                return {"final": {"status": "needs_info", "error_reason": "missing_order_id"}}
            return {
                "tool": "lookup_order",
                "args": {"order_id": memory["order_id"], "principal_id": principal_id},
            }

        result = seen["last_result"]

        if last_tool == "lookup_order":
            if not result.get("visible"):
                # Not owned or not found -> escalate without exposing order data.
                return {"final": {"status": "escalated", "error_reason": result.get("reason")}}
            # Use the order row from the DB, NOT the delay the customer wrote in the ticket.
            memory["order"] = result["order"]
            return {"tool": "compute_compensation", "args": {"order": memory["order"]}}

        if last_tool == "compute_compensation":
            return {
                "final": {
                    "status": "quoted",
                    "amount_vnd": int(result["amount_vnd"]),
                    "quote_reason": result.get("reason"),
                    "clause_ids": result.get("clause_ids"),
                }
            }

        # Unexpected tool -> stop safely.
        return {"final": {"status": "failed", "error_reason": f"unexpected_tool:{last_tool}"}}

    return decide
