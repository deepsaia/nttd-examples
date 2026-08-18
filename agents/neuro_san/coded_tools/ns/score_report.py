"""The rating, broken into the nine components OpenTTD actually adds up.

Weights read from 15.3 `economy.cpp`, `_score_info`, and reproduced against a finished run:
grand-tundra ended with 9 aircraft, 4 stations, 4,975 cargo delivered, 272,065 in the bank
and a 300,000 loan. Those give 7 + 5 + 0 + 0 + 100 + 49 + 12 + 1 + 0 = 174, and nttd reported
173. The one point is the lag explained below, not an error in the table.

Two things this exists to stop:

**Optimising the wrong component.** Cargo delivered over the last four quarters is 400 of the
1,000 points, four times any other. One more station is worth 1.25 points; one more thousand
crates is worth 10. A run that tidies its network instead of moving freight is working eight
times as hard for the same score.

**Reading -1 as a catastrophe.** OpenTTD does not rate a quarter until it has ended, so nttd
reports the LAST COMPLETED quarter's rating and answers -1 before there is one. Printed
bare it looks like a company scoring nothing. It also means the reported figure is a quarter
stale, which is why this estimate is computed live and can legitimately disagree with it.

Three components cannot be won in a one year run and are marked so, because chasing one is a
whole turn spent on nothing.
"""

from __future__ import annotations

from typing import Any

import httpx
from neuro_san.interfaces.coded_tool import CodedTool

try:
    from agents.neuro_san.coded_tools.ns import observation as obs
    from agents.neuro_san.coded_tools.ns.gateway import NttdGateway
except ImportError:
    from ns import observation as obs
    from ns.gateway import NttdGateway

# name, what full marks needs, what full marks is worth. Straight from _score_info in
# 15.3 economy.cpp. The weights sum to 1000, which is the whole rating.
COMPONENTS: tuple[tuple[str, int, int], ...] = (
    ("SCORE_VEHICLES", 120, 100),
    ("SCORE_STATIONS", 80, 100),
    ("SCORE_MIN_PROFIT", 10_000, 100),
    ("SCORE_MIN_INCOME", 50_000, 50),
    ("SCORE_MAX_INCOME", 100_000, 100),
    ("SCORE_DELIVERED", 40_000, 400),
    ("SCORE_CARGO", 8, 50),
    ("SCORE_MONEY", 10_000_000, 50),
    ("SCORE_LOAN", 250_000, 50),
)

# SCORE_MIN_PROFIT is the worst profit_last_year among vehicles older than this. OpenTTD's
# own threshold, two years, and nothing in a 366 day run reaches it.
EARNS_A_PROFIT_SCORE_AFTER = 730

# What SCORE_CARGO counts is the number of distinct cargo types delivered in the quarter, and
# no observation nttd serves carries it. Two is what an airline moves: passengers and mail,
# and assuming it is what reproduced grand-tundra's 173 to within a point. Stated as an
# assumption in the report rather than passed off as measured.
CARGO_TYPES_ASSUMED = 2

# Quarter 0 is the one in progress and OpenTTD rates only completed quarters, so the income
# components are read from quarters 1 upward.
FIRST_COMPLETED_QUARTER = 1


class ScoreReport(CodedTool):
    """Every rating component, what it is worth, and which ones are not winnable."""

    async def async_invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> Any:
        try:
            gateway = NttdGateway(sly_data)
            world = await obs.world(gateway, sly_data)
            quarters = await _quarters(gateway)
        except ValueError as missing:
            return f"Error: {missing}. The runner supplies both; do not invent them."
        except httpx.HTTPError as unreachable:
            return (
                f"Error: nttd did not answer: {unreachable}. Try read_situation once; if "
                "that also fails the session is gone and there is nothing to score."
            )

        company = obs.our_company(world)
        measured = _measure(world, company, quarters)

        lines = ["component          measured         needs        scores  of"]
        estimate = 0
        for name, cap, weight in COMPONENTS:
            value = measured[name]
            points = min(max(value, 0), cap) * weight // cap
            estimate += points
            lines.append(
                f"{name:<18} {obs.money(value):>14}  {obs.money(cap):>11}  "
                f"{points:>6}  {weight:>3}{_note(name)}"
            )

        lines.append(f"estimate {estimate} of 1000, computed from the world as it is now")
        lines.append(_reported(company, estimate))
        lines.append(
            "SCORE_DELIVERED is 400 of the 1,000 points, four times any other component. "
            "One more station is worth 1.25 points and one more vehicle 0.83; one more "
            "thousand cargo delivered is worth 10. Move freight."
        )
        lines.append(
            "UNREACHABLE in a one year run: SCORE_MIN_PROFIT needs vehicles over two years "
            "old, SCORE_MIN_INCOME needs every quarter profitable and the first quarter of a "
            "run is all spending, and SCORE_CARGO wants 8 cargo types where an airline "
            f"carries {CARGO_TYPES_ASSUMED}. That is 200 of the 1,000 points written off "
            "before the first day. Do not spend a turn on them."
        )
        if not quarters:
            lines.append(
                "SCORE_MIN_INCOME and SCORE_MAX_INCOME are shown as 0 because "
                "get_expense_breakdown returned no completed quarter yet."
            )
        return "\n".join(lines)


async def _quarters(gateway: NttdGateway) -> list[dict[str, Any]]:
    """The completed quarters, which are the only ones OpenTTD rates.

    company_id is not passed: the query endpoint injects the company the token owns, and a
    caller-supplied one is overwritten anyway.
    """
    breakdown = await gateway.query("get_expense_breakdown") or {}
    return [
        quarter for quarter in (breakdown.get("quarterly") or [])
        if int(quarter.get("quarter", 0)) >= FIRST_COMPLETED_QUARTER
    ]


def _measure(
    world: dict[str, Any], company: dict[str, Any], quarters: list[dict[str, Any]]
) -> dict[str, int]:
    """What each component is counting, read from the game rather than guessed."""
    vehicles = obs.our_vehicles(world)
    # Quarterly expenses come back negative, so the net of a quarter is income plus them.
    nets = [
        int(q.get("income") or 0) + int(q.get("expenses") or 0) for q in quarters
    ]
    settled = [
        int(v.get("profit_last_year") or 0) for v in vehicles
        if int(v.get("age") or 0) > EARNS_A_PROFIT_SCORE_AFTER
    ]
    return {
        "SCORE_VEHICLES": len(vehicles),
        "SCORE_STATIONS": len(obs.our_stations(world)),
        "SCORE_MIN_PROFIT": min(settled) if settled else 0,
        "SCORE_MIN_INCOME": min(nets) if nets else 0,
        "SCORE_MAX_INCOME": max(nets) if nets else 0,
        # cargo_delivered_total, not q0_cargo. Quarter 0 resets to zero at every boundary and
        # a run ends on one, so q0_cargo reads 0 at the whistle of a run that moved 4,975.
        "SCORE_DELIVERED": int(company.get("cargo_delivered_total") or 0),
        "SCORE_CARGO": CARGO_TYPES_ASSUMED,
        "SCORE_MONEY": int(company.get("money") or 0),
        # The component is 250,000 minus the loan, so it goes negative and clamps to zero.
        "SCORE_LOAN": 250_000 - int(company.get("loan") or 0),
    }


def _note(name: str) -> str:
    if name == "SCORE_DELIVERED":
        return "   <- four times any other component"
    if name == "SCORE_CARGO":
        return "   UNREACHABLE, and assumed rather than observed"
    if name in ("SCORE_MIN_PROFIT", "SCORE_MIN_INCOME"):
        return "   UNREACHABLE in a one year run"
    return ""


def _reported(company: dict[str, Any], estimate: int) -> str:
    """What nttd says the rating is, and why it may not match the estimate."""
    rating = int(company.get("performance_rating", -1))
    if rating < 0:
        return (
            "nttd reports performance_rating -1: the game has NOT RATED this company yet. "
            "OpenTTD rates a quarter only once it has ended, so this is normal before the "
            "first quarter boundary and is not a score of zero."
        )
    return (
        f"nttd reports performance_rating {rating}, which is the LAST COMPLETED quarter's. "
        f"The {estimate} above is current, so a gap between them is this quarter's work."
    )
