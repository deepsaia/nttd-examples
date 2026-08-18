"""Where the company stands, in one screen.

The first tool of every turn, and short on purpose. The report it replaces returned the raw
observation, and a model handed four hundred lines of stations and vehicles spends its
attention on reading rather than deciding, then answers about the wrong one.

What is on it and why:

- **The engine's problems list, verbatim.** Computed by nttd rather than here, which is the
  whole point: its list never reads `idle_reason`, and a hand-rolled version that did
  reported an aircraft loading at a gate and one sitting in its hangar as faults, turning a
  healthy fleet into a wall of them. Its `_SETTLING_DAYS` is 400, so in a 366 day run it
  cannot call a young vehicle a failure at all.
- **Days remaining**, because a vehicle bought too late cannot pay for itself. Aircraft take
  roughly 190 days to return their price, so one bought with 60 days to go is cash converted
  into a depreciating asset.
- **The loan, against 250,000.** The rating's loan component is `250,000 - current_loan`, so
  a run borrowed to a 300,000 ceiling forfeits all 50 of its points. Measured: a finished run
  held 300,000 at the whistle and scored 0 there while sitting on 272,065 in cash.
- **Refusals held and actions staged**, because both are invisible state. A network that
  cannot see its own ledger repeats itself: one run submitted the same purchase 35 times.

The snapshot is cached so fleet_report and route_report in the same turn read it rather than
fetching the world again.
"""

from __future__ import annotations

from typing import Any

import httpx
from neuro_san.interfaces.coded_tool import CodedTool

try:
    from agents.neuro_san.coded_tools.ns import constants as key
    from agents.neuro_san.coded_tools.ns import observation as obs
    from agents.neuro_san.coded_tools.ns.gateway import NttdGateway
    from agents.neuro_san.coded_tools.ns.plan import Plan
except ImportError:
    from ns import constants as key
    from ns import observation as obs
    from ns.gateway import NttdGateway
    from ns.plan import Plan

# The rating's loan component is max(0, 250,000 - loan), so every pound above this scores
# nothing and costs interest as well.
LOAN_SCORES_NOTHING_ABOVE = 250_000

# How many problems to print. The engine emits one per orderless vehicle and one per piled
# up station, so a thirty vehicle fleet can produce thirty lines, and a report nobody
# finishes reading is a report that did not happen. The rest are counted, not hidden.
PROBLEMS_SHOWN = 8

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


class ReadSituation(CodedTool):
    """Money, horizon, what is built, and what the engine says is wrong."""

    async def async_invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> Any:
        try:
            gateway = NttdGateway(sly_data)
            situation = await gateway.situation()
            # Always fresh: this is the tool that opens a turn, and every other report in
            # the turn then reads the snapshot it leaves behind.
            world = await obs.world(gateway, sly_data, refresh=True)
        except ValueError as missing:
            return f"Error: {missing}. The runner supplies both; do not invent them."
        except httpx.HTTPError as unreachable:
            return (
                f"Error: nttd did not answer: {unreachable}. The session may have ended. "
                "Do not retry more than once; report that the run is unreachable."
            )

        company = obs.our_company(world)
        game = world.get("game") or {}
        purse = situation.get("money") or {}
        built = situation.get("built") or {}
        earning = situation.get("earning") or {}
        problems = situation.get("problems") or []

        lines = [
            _when(game),
            f"money {obs.money(purse.get('balance'))}, loan {obs.money(purse.get('loan'))} "
            f"of {obs.money(purse.get('max_loan'))}, borrowable "
            f"{obs.money(purse.get('headroom'))}, company value "
            f"{obs.money(purse.get('company_value'))}",
        ]
        lines.extend(_loan_lines(int(purse.get("loan") or 0)))
        lines.append(
            f"built: {built.get('stations', 0)} stations, {built.get('vehicles', 0)} vehicles "
            f"{_by_type(built)}, {built.get('routes', 0)} routes the engine can see"
        )
        lines.append(
            f"earning: income {obs.money(earning.get('income'))}, "
            f"{earning.get('vehicles_earning', 0)} vehicles in profit, "
            f"{earning.get('vehicles_losing', 0)} losing, fleet profit this year "
            f"{obs.money(earning.get('fleet_profit_this_year'))}"
        )
        lines.append(
            f"cargo delivered {obs.money(company.get('cargo_delivered_total'))} so far. "
            "That is SCORE_DELIVERED, 400 of the 1,000 rating points and four times any "
            "other component: the decision that moves more cargo wins."
        )
        lines.append(_rating(company))
        lines.extend(_problem_lines(problems))
        lines.append(
            f"memory: {len(sly_data.get(key.REFUSALS) or [])} refusals in the ledger, "
            f"{len(Plan(sly_data).actions)} actions staged in the plan"
        )
        return "\n".join(lines)


def _when(game: dict[str, Any]) -> str:
    """The date, and how much run is left.

    Zero days total means the run is not bounded by days at all, which is a different thing
    from having none left. Testing the remaining count for truthiness conflated them, so the
    last day of a run reported "no horizon" at exactly the moment the horizon mattered most.
    """
    date = _readable(int(game.get("game_date") or 0))
    total = int(game.get("game_days_total") or 0)
    if not total:
        return f"{date}, this run is not bounded by days"
    left = int(game.get("game_days_remaining") or 0)
    return f"{date}, {left} of {total} days left"


def _readable(game_date: int) -> str:
    """An OpenTTD date, which is days since year 0, as something a reader can use.

    Left raw it is 738156, and two dates twelve days apart look like two arbitrary large
    numbers. A model cannot tell December from March from that.
    """
    year, remaining = 0, game_date
    blocks = remaining // 146097
    year, remaining = blocks * 400, remaining - blocks * 146097
    while True:
        length = 366 if _is_leap(year) else 365
        if remaining < length:
            break
        remaining -= length
        year += 1
    months = [31, 29 if _is_leap(year) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    month = 0
    for days in months:
        if remaining < days:
            break
        remaining -= days
        month += 1
    return f"{remaining + 1} {_MONTHS[month]} {year}"


def _is_leap(year: int) -> bool:
    return (year % 4 == 0 and year % 100 != 0) or year % 400 == 0


def _loan_lines(loan: int) -> list[str]:
    """Said only when it is true, so the line means something when it appears."""
    if loan <= LOAN_SCORES_NOTHING_ABOVE:
        return []
    return [
        f"LOAN: {obs.money(loan)} is above {obs.money(LOAN_SCORES_NOTHING_ABOVE)}, so "
        f"SCORE_LOAN scores 0 of its 50 points. Repaying to "
        f"{obs.money(LOAN_SCORES_NOTHING_ABOVE)} buys all 50 back and stops the interest."
    ]


def _by_type(built: dict[str, Any]) -> str:
    by_type = built.get("vehicles_by_type") or {}
    if not by_type:
        return "(none)"
    return "(" + ", ".join(f"{count} {name}" for name, count in sorted(by_type.items())) + ")"


def _rating(company: dict[str, Any]) -> str:
    """The rating, and what -1 means.

    OpenTTD does not rate a quarter until it ends, so nttd reports the last COMPLETED
    quarter's rating and answers -1 before there is one. Printed as a number it reads as a
    catastrophic zero and invites a panic that nothing is working.
    """
    rating = int(company.get("performance_rating", -1))
    if rating < 0:
        return (
            "rating: not yet computed. OpenTTD rates a quarter only once it has ended, so "
            "this is absent for the first quarter of a run rather than bad."
        )
    return f"rating {rating} of 1000, as at the end of the last completed quarter"


def _problem_lines(problems: list[dict[str, Any]]) -> list[str]:
    """The engine's own list, unedited.

    Not rewritten and not re-derived. Each entry already carries what is wrong, the detail
    that locates it and why it matters, and every one of them is actionable.
    """
    if not problems:
        return ["problems: none. Grow."]
    lines = [f"problems ({len(problems)}), from the engine, verbatim:"]
    for entry in problems[:PROBLEMS_SHOWN]:
        lines.append(f"  - {entry.get('problem')}: {entry.get('detail')}")
        lines.append(f"    why it matters: {entry.get('why_it_matters')}")
    hidden = len(problems) - PROBLEMS_SHOWN
    if hidden > 0:
        lines.append(
            f"  ...and {hidden} more of the same kinds. fleet_report and route_report list "
            "every vehicle and every route."
        )
    return lines
