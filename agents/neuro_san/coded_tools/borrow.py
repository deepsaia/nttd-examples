"""Raise the loan, with the cost of doing so stated.

A company opens with 100,000. Two airports and the aircraft to fly between them cost several
times that, so a network that never borrows can build almost nothing and will look like it
is refusing to play. Borrowing early is how a run reaches revenue at all.

It is not free, and the cost is not interest. The rating's loan component is
`250,000 - current_loan`, so a loan above 250,000 scores zero out of those 50 points however
well the money is spent. Borrowing the full ceiling is a real trade: it buys earning assets
sooner and forfeits that component. Worth it while the money buys something that earns; not
worth it to sit in the bank.
"""

from __future__ import annotations

from typing import Any

from neuro_san.interfaces.coded_tool import CodedTool

try:
    from agents.neuro_san.coded_tools.nttd_gateway import NttdGateway
except ImportError:
    from nttd_gateway import NttdGateway

# Above this, the rating's loan component is zero.
LOAN_SCORES_NOTHING_ABOVE = 250_000


class Borrow(CodedTool):
    """Set the loan to a chosen amount."""

    async def async_invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> Any:
        gateway = NttdGateway(sly_data)
        wanted = int(args["amount"])

        reply = await gateway.act([gateway.envelope("set_loan", amount=wanted)])
        result = reply[0] if reply else {}
        if result.get("status") != "success":
            return {"borrowed": False, "why": result.get("error") or "refused"}

        company = ((await gateway.observe()).get("companies") or [{}])[0]
        return {
            "borrowed": True,
            "loan": company.get("loan"),
            "money": company.get("money"),
            "costs_rating": int(company.get("loan") or 0) > LOAN_SCORES_NOTHING_ABOVE,
            "note": (
                "cash sitting idle earns nothing and still costs the rating, so borrow "
                "against something you are about to build"
            ),
        }
