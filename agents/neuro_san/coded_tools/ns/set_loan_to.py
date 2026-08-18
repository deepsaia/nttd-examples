"""Set the loan to an exact amount, with what it costs stated.

A company opens with 100,000. One air corridor is two airports and the aircraft to fly between
them, several times that, so a network that never borrows can build almost nothing and looks
like it is refusing to play.

Borrowing is not free, and the cost is not the interest. The rating's loan component is
`250,000 - current_loan`, so a loan at or above 250,000 scores zero out of those points however
well the money is spent. That is a real trade rather than a rule: the money buys earning assets
sooner and forfeits the component. Worth it while it buys something that earns, not worth it to
sit in the bank.

**This is the one planning tool that steps.** It borrows immediately instead of staging into the
plan, because the money has to be in the bank before the same turn's builds can be checked for
affordability, and a `commit_plan` that had to guess whether a staged `set_loan` would clear
would be guessing about the only number it can actually check. That costs one game day. Where a
build is already staged and its cost is known, staging `set_loan` into the same batch is cheaper
and `commit_plan` accounts for it; use this tool when the cash is needed before the plan exists.
"""

from __future__ import annotations

from typing import Any

import httpx
from neuro_san.interfaces.coded_tool import CodedTool

try:
    from agents.neuro_san.coded_tools.ns import constants as key
    from agents.neuro_san.coded_tools.ns import counting, envelope
    from agents.neuro_san.coded_tools.ns.gateway import NttdGateway
    from agents.neuro_san.coded_tools.ns.observation import our_company
except ImportError:
    # Loaded by neuro-san from AGENT_TOOL_PATH, where `ns` is a package beside the flat tools
    # and the repository above it is not on the path. Both spellings are needed because
    # AGENT_TOOL_PATH_ONLY=true deliberately stops a tool resolving from anywhere on PYTHONPATH.
    from ns import constants as key
    from ns import counting, envelope
    from ns.gateway import NttdGateway
    from ns.observation import our_company

# At or above this the rating's loan component is zero: SCORE_LOAN is max(0, 250000 - loan).
LOAN_SCORES_NOTHING_AT = 250_000


class SetLoanTo(CodedTool):
    """Hold the loan at a chosen amount."""

    async def async_invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> Any:
        try:
            gateway = NttdGateway(sly_data)
        except ValueError as problem:
            return f"Error: {problem}. The runner supplies these; nothing here can invent them."

        amount = counting.whole(args.get("amount"))
        if amount is None or amount < 0:
            return (
                "Error: amount must be a whole number of pounds, and set_loan sets the loan to "
                "that figure rather than adding to it. Pass the total loan you want to hold."
            )

        try:
            result = await gateway.step([envelope.action("set_loan", amount=amount)])
        except httpx.HTTPError as problem:
            return f"Error: the loan could not be set ({problem}). Nothing was borrowed."

        outcome = (result.get("action_results") or [{}])[0] or {}
        if outcome.get("status") != "success":
            return {
                "borrowed": False,
                "why": outcome.get("error") or "refused",
                "error_name": outcome.get("error_name") or "",
            }

        snapshot = result.get("snapshot") or {}
        if snapshot:
            # This step moved the world, so the turn's cached snapshot is now a day out of
            # date and a whole loan behind. Later tools in this turn read that cache.
            sly_data[key.SNAPSHOT] = snapshot
        company = our_company(snapshot)
        loan = int(company.get("loan") or 0)
        report: dict[str, Any] = {
            "borrowed": True,
            "loan": loan,
            "money": int(company.get("money") or 0),
            "headroom": max(0, int(company.get("max_loan") or 0) - loan),
            "days_spent": int(result.get("days_advanced") or 0),
        }
        if loan != amount:
            # The game rounds to its own loan interval and caps at max_loan, so the figure asked
            # for and the figure held are not always the same number.
            report["asked_for"] = amount
            report["note_on_amount"] = (
                "the game rounds to its loan interval and stops at the maximum loan, so this is "
                "what is actually held"
            )
        if loan >= LOAN_SCORES_NOTHING_AT:
            report["costs_rating"] = (
                f"the loan is {loan} and the rating's loan component is "
                f"max(0, {LOAN_SCORES_NOTHING_AT} - loan), so it now scores zero. Worth it only "
                "while this money is buying something that earns."
            )
        if result.get("terminated"):
            report["session_ended"] = True
            report["end_reason"] = result.get("end_reason") or "the run reached its day budget"
        return report
