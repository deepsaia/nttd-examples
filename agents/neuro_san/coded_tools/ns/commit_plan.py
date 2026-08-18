"""Turn the staged plan into game days, in as few of them as it can be done in.

This is where the batching win lives. A step executes a whole batch and then advances one day,
so two airports, four aircraft and their orders are one day of world time or eight, depending
only on how they were submitted. The best hand-played air run spent 15 game days on an opening
that needs 3, and every one of those days was paperwork rather than a decision.

Four checks run before anything is sent, and each of them is a game day that a measured run
spent learning the answer:

**Nothing staged.** An empty batch is a legal step. It spends a day and changes nothing, so it
is the most expensive way to say "I had no plan".

**Already refused.** The refusal ledger is compared against what is about to be sent. Without
this check one run submitted the same purchase 35 times, with the same error every time, at a
day apiece.

**Parameter problems.** `envelope.check` asks the engine's own manifest whether these
parameters exist. A misspelled parameter name otherwise costs a day to discover, and
`build_bridge` wanting `start_x` where every other spatial action wants `from_x` is exactly the
kind of thing nobody guesses right.

**Affordability.** Aircraft prices are knowable for free from `get_engines`, so a batch that
buys more planes than the bank holds is refused here rather than by the game. Where a cost is
not knowable, and airport prices are not knowable before the first one is built, this says so
instead of inventing a number.
"""

from __future__ import annotations

from typing import Any

import httpx
from neuro_san.interfaces.coded_tool import CodedTool

try:
    # Loaded as part of this repository, which is how the tests import it.
    from agents.neuro_san.coded_tools.ns import constants as key
    from agents.neuro_san.coded_tools.ns import counting, envelope
    from agents.neuro_san.coded_tools.ns.gateway import NttdGateway
    from agents.neuro_san.coded_tools.ns.plan import Plan
except ImportError:
    # Loaded by neuro-san from AGENT_TOOL_PATH, where `ns` is a package beside the flat tools
    # and the repository above it is not on the path. Both spellings are needed because
    # AGENT_TOOL_PATH_ONLY=true deliberately stops a tool resolving from anywhere on PYTHONPATH.
    from ns import constants as key
    from ns import counting, envelope
    from ns.gateway import NttdGateway
    from ns.plan import Plan

# Cash a commit will not spend below. The best air run's floor was 38,441 and it survived;
# blithe-harbor bottomed at 7,707 and nearly went bankrupt, which ends the run and scores
# nothing. The rating also pays for a balance, so money kept is not money wasted.
CASH_RESERVE = 40_000

# Actions that take money out of the bank. Orders, starts, group changes and renames do not, so
# a plan made only of those commits at any balance.
SPENDS_PREFIXES = ("build_", "buy_", "clone_", "connect_", "convert_", "level_", "plant_")
SPENDS_EXACTLY = frozenset({
    "demolish_tile", "lower_tile", "perform_town_action", "raise_tile", "refit_vehicle",
})

# get_engines answers per vehicle type, and a staged buy_vehicle carries an engine id with no
# type attached. OpenTTD numbers engines from one global pool, so merging the four answers gives
# an unambiguous id to price map. All four are asked because this tool is mode-agnostic, and the
# queries are free: they cost no game day and work while the world is paused.
VEHICLE_TYPES = ("aircraft", "train", "road", "ship")


class CommitPlan(CodedTool):
    """Submit the staged plan and report what the game made of it."""

    async def async_invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> Any:
        try:
            gateway = NttdGateway(sly_data)
        except ValueError as problem:
            return f"Error: {problem}. The runner supplies these; nothing here can invent them."

        plan = Plan(sly_data)
        if not plan.actions:
            return {
                "committed": False,
                "days_spent": 0,
                "why": (
                    "nothing is staged, so there is nothing to commit. Plan something first: "
                    "an empty batch still spends a game day and changes nothing."
                ),
            }

        dropped = plan.already_refused()
        if dropped:
            refusals = sly_data.get(key.REFUSALS) or []
            keep = [entry for entry in plan.actions if not _is_repeat(entry, refusals)]
            plan.clear()
            plan.add(*keep)
        if not plan.actions:
            return {
                "committed": False,
                "days_spent": 0,
                "already_refused_and_dropped": dropped,
                "why": (
                    "everything staged had already been refused, so none of it was sent. "
                    "Read the errors and plan something different; the same call gets the "
                    "same answer."
                ),
            }

        batch = list(plan.actions)
        problems = envelope.check(batch)
        if problems:
            return {
                "committed": False,
                "days_spent": 0,
                "parameter_problems": problems,
                "already_refused_and_dropped": dropped,
                "still_staged": plan.describe(),
                "why": (
                    "the batch was not sent, so no game day was spent. Fix these parameters "
                    "and commit again."
                ),
            }

        try:
            money = (await gateway.situation()).get("money") or {}
        except httpx.HTTPError as problem:
            return f"Error: could not read the company's money ({problem}). Nothing was committed."

        balance = int(money.get("balance") or 0)
        loan = int(money.get("loan") or 0)

        # A staged set_loan is credited against this batch's cost, and that credit is only true if
        # the borrowing runs BEFORE the spending, because a step executes its actions in the order
        # given. Reordering was chosen over refusing to credit a late set_loan: the order a batch
        # was staged in is an accident of which agent contributed first, and a corridor that funds
        # itself is worth committing whichever way round it arrived.
        plan.clear()
        plan.add(*_loan_first(batch, loan))
        batch = list(plan.actions)

        try:
            costed, unpriced = await _cost(gateway, batch)
        except httpx.HTTPError as problem:
            return f"Error: could not price the batch ({problem}). Nothing was committed."
        spendable = balance + _borrowing(batch, loan) - CASH_RESERVE

        if costed > spendable:
            return {
                "committed": False,
                "days_spent": 0,
                "balance": balance,
                "known_cost": costed,
                "spendable_above_reserve": spendable,
                "already_refused_and_dropped": dropped,
                "still_staged": plan.describe(),
                "why": (
                    f"this batch costs at least {costed} and only {spendable} can be spent "
                    f"without going below the {CASH_RESERVE} reserve. Borrow with set_loan_to, "
                    "or stage fewer vehicles."
                ),
            }
        if spendable <= 0 and (costed or unpriced):
            return {
                "committed": False,
                "days_spent": 0,
                "balance": balance,
                "already_refused_and_dropped": dropped,
                "still_staged": plan.describe(),
                "why": (
                    f"the balance of {balance} is already at or below the {CASH_RESERVE} "
                    "reserve, and this batch spends. Call set_loan_to before committing "
                    "anything that costs money."
                ),
            }

        verdicts: list[dict[str, Any]] = []
        unsent: list[str] = []
        days = 0
        steps = 0
        terminated = False
        end_reason = ""
        groups = plan.split()
        for index, group in enumerate(groups):
            try:
                result = await gateway.step(group)
            except httpx.HTTPError as problem:
                unsent = [str(entry.get("action")) for later in groups[index:] for entry in later]
                plan.clear()
                return {
                    "committed": bool(verdicts),
                    "days_spent": days,
                    "steps_taken": steps,
                    "actions": verdicts,
                    "not_submitted": unsent,
                    "why": f"the step was refused by the server: {problem}",
                }
            steps += 1
            days += int(result.get("days_advanced") or 0)
            # The step already observed the world after it moved, so the turn's cached
            # snapshot is now the world as it was before these builds existed. Refreshing it
            # here is what stops a later tool in the same turn reporting the station this
            # commit just built as missing.
            if result.get("snapshot"):
                sly_data[key.SNAPSHOT] = result["snapshot"]
            verdicts.extend(_verdicts(group, result.get("action_results") or []))
            if result.get("terminated"):
                terminated = True
                end_reason = result.get("end_reason") or "the session reached its day budget"
                unsent = [
                    str(entry.get("action")) for later in groups[index + 1:] for entry in later
                ]
                break

        plan.clear()
        report: dict[str, Any] = {
            "committed": True,
            "days_spent": days,
            "steps_taken": steps,
            "actions": verdicts,
            "terminated": terminated,
        }
        if dropped:
            report["already_refused_and_dropped"] = dropped
        if unpriced:
            report["cost_not_known_for"] = sorted(set(unpriced))
        if unsent:
            report["not_submitted"] = unsent
        if end_reason:
            report["end_reason"] = end_reason
        return report


def _is_repeat(entry: dict[str, Any], refusals: list[dict[str, Any]]) -> bool:
    """Whether the game has already refused exactly this call.

    Asked through Plan rather than by comparing parameters here, so there is one definition of
    "the same attempt" in the codebase and this cannot drift away from it.
    """
    probe = Plan({key.PLAN: [entry], key.REFUSALS: refusals})
    return bool(probe.already_refused())


def _spends(name: str) -> bool:
    """Whether this action takes money out of the bank."""
    return name.startswith(SPENDS_PREFIXES) or name in SPENDS_EXACTLY


async def _cost(gateway: NttdGateway, batch: list[dict[str, Any]]) -> tuple[int, list[str]]:
    """What this batch is known to cost, and what could not be priced.

    Only vehicle purchases are priceable in advance: get_engines carries a price per engine and
    the staged action carries the engine id. Airports, track and road are not knowable until one
    has been built and the cash delta measured, so they are named rather than guessed at. A
    guessed cost that is too low commits an unaffordable batch and a guessed cost that is too
    high refuses an affordable one, and both are worse than saying which figure is missing.
    """
    prices = await _prices(gateway) if any(e.get("action") == "buy_vehicle" for e in batch) else {}
    known = 0
    unpriced: list[str] = []
    for entry in batch:
        name = str(entry.get("action") or "")
        if not _spends(name):
            continue
        price = None
        if name == "buy_vehicle":
            price = prices.get(counting.whole((entry.get("params") or {}).get("engine_id")))
        if price is None:
            unpriced.append(name)
            continue
        known += price
    return known, unpriced


async def _prices(gateway: NttdGateway) -> dict[int | None, int]:
    """Engine id to price, across every vehicle type this engine sells.

    An empty map when the query fails, which makes every purchase unpriced and is reported as
    such. A failed price lookup must not stop a commit: pricing is a courtesy that saves a game
    day, not a gate on playing.
    """
    prices: dict[int | None, int] = {}
    for vehicle_type in VEHICLE_TYPES:
        try:
            engines = await gateway.query("get_engines", {"vehicle_type": vehicle_type})
        except httpx.HTTPError:
            continue
        for engine in engines or []:
            identifier = counting.whole((engine or {}).get("id"))
            if identifier is not None:
                prices[identifier] = int((engine or {}).get("price") or 0)
    return prices


def _loan_first(batch: list[dict[str, Any]], current_loan: int) -> list[dict[str, Any]]:
    """The batch with a borrowing set_loan moved to the front, everything else in order.

    Only a set_loan that RAISES the loan is moved. A repayment staged after a sale is money the
    batch does not have until the sale has run, so hoisting that one would break a batch that
    works exactly as it was staged.
    """
    borrowings = [
        entry for entry in batch
        if entry.get("action") == "set_loan"
        and (counting.whole((entry.get("params") or {}).get("amount")) or 0) > current_loan
    ]
    if not borrowings:
        return list(batch)
    rest = [entry for entry in batch if not any(entry is moved for moved in borrowings)]
    return borrowings + rest


def _borrowing(batch: list[dict[str, Any]], current_loan: int) -> int:
    """How much a set_loan staged in this batch puts in the bank before the builds run.

    Counted only for a set_loan that PRECEDES the first action that spends, which is what
    _loan_first has already arranged. Both halves are needed: crediting a set_loan wherever it
    sits would credit money the step has not borrowed yet at the moment the build executes, and
    without the reordering a late set_loan would lose its credit and the batch would be refused as
    unaffordable at the balance it had before borrowing.
    """
    wanted: int | None = None
    for entry in batch:
        if _spends(str(entry.get("action") or "")):
            break
        if entry.get("action") == "set_loan":
            wanted = counting.whole((entry.get("params") or {}).get("amount"))
    if wanted is None:
        return 0
    return max(0, wanted - current_loan)


def _verdicts(group: list[dict[str, Any]], results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """What the game made of each action, paired back up with what was asked.

    The error is passed through word for word. nttd's refusals carry the coordinate that fixes
    the bug, "1 of 71 have no through connection, first at (93,185)", and a paraphrase loses the
    only part worth having. `result` carries the new station and vehicle ids, which the next
    tool needs and which exist nowhere else.
    """
    paired: list[dict[str, Any]] = []
    for index, entry in enumerate(group):
        outcome = (results[index] if index < len(results) else {}) or {}
        succeeded = outcome.get("status") == "success"
        paired.append({
            "action": entry.get("action"),
            "params": {
                name: value for name, value in (entry.get("params") or {}).items()
                if name != "company_id"
            },
            "ok": succeeded,
            "error": "" if succeeded else (outcome.get("error") or "no verdict came back"),
            "error_name": "" if succeeded else (outcome.get("error_name") or ""),
            "result": outcome.get("changed_entities") or {},
        })
    return paired
