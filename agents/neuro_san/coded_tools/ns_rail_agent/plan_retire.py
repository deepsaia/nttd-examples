"""Get the money back out of a train that will never earn it. Stages only; commits nothing.

**Selling is two stage, across turns.** `sell_vehicle` requires the train to have ARRIVED and
stopped in a depot, and `send_to_depot` only asks it to go: it finishes the current leg first.
Both in the SAME step therefore always fails, because when the sale executes the train is still
on the line. So this call stages `send_to_depot` and writes down that the train is awaiting
sale; a LATER call sees it has arrived, reads that from the game rather than assuming, and
stages `sell_vehicle`.

**A train can only reach a depot joined to its own line, and rail is where that bites.** An
aircraft always has a hangar, because the hangar is part of the airport. A rail corridor has a
depot only if plan_add_depot built one and `connect_depot` joined it. Sent to a depot it cannot
reach, a train keeps its old orders and never arrives, the sale never becomes possible, and the
disposal sits in this record forever looking like it is in progress. So the route's depot is
checked BEFORE anything is staged, and a route without one is told to build the depot rather
than being sent nowhere.

**A locomotive with no wagons is hopeless straight away.** Everywhere else this tool insists a
repoint was tried first, because fixing orders is free and selling is not. That train's orders
are fine; it runs its route perfectly and carries nothing, and there is no order that couples a
wagon. Selling it and rebuilding with plan_buy_train is the only repair, so it skips the
repoint precondition. It does not skip the depot one: it still has to get there.

**The proceeds are not money yet.** A train crossing a long corridor takes tens of game days to
reach a depot. Spending expected proceeds while it is still moving is how a run runs out of
cash holding an asset it has already counted.

**A staged sale is not an attempt.** commit_plan is what submits, so the allowance of three
sale attempts is spent only once a commit has carried one and the train is still in the fleet.
Counted at staging time, three plans nobody committed exhausted it and the tool reported
refusals the game had never been asked for.
"""

from __future__ import annotations

from typing import Any

from neuro_san.interfaces.coded_tool import CodedTool

try:
    from agents.neuro_san.coded_tools.ns import constants as key
    from agents.neuro_san.coded_tools.ns import session
    from agents.neuro_san.coded_tools.ns.envelope import action, check
    from agents.neuro_san.coded_tools.ns.gateway import NttdGateway
    from agents.neuro_san.coded_tools.ns.plan import Plan
    from agents.neuro_san.coded_tools.ns_rail_agent import rail_keys as rail
    from agents.neuro_san.coded_tools.ns_rail_agent.plan_repoint import REPOINT_GRACE_DAYS
    from agents.neuro_san.coded_tools.ns_rail_agent.rail_health_check import TRAIN
except ImportError:
    from ns import constants as key
    from ns import session
    from ns.envelope import action, check
    from ns.gateway import NttdGateway
    from ns.plan import Plan

    from ns_rail_agent import rail_keys as rail
    from ns_rail_agent.plan_repoint import REPOINT_GRACE_DAYS
    from ns_rail_agent.rail_health_check import TRAIN

# How many times a sale may be asked for. A sale is only ever staged once the game says the
# train is stopped in a depot, so a refusal after that is something else, and asking a fourth
# time is the loop that fills an action log with the same refused pair.
SELL_ATTEMPTS = 3

# Verdicts a train has to hold before selling it is the right move. Anything milder means the
# free repair has not been tried.
WORTH_SELLING = ("stuck", "lost", "carries_nothing")

# The one verdict no repoint can fix, so it needs no repoint before a sale.
BEYOND_REPOINT = "carries_nothing"


class PlanRetire(CodedTool):
    """Send a hopeless train to a depot, and sell it on a later call once it is there."""

    async def async_invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> Any:
        return await session.guarded(self._stage_disposal, args, sly_data)

    async def _stage_disposal(
        self, gateway: NttdGateway, args: dict[str, Any], sly_data: dict[str, Any]
    ) -> Any:
        record = sly_data.get(rail.HEALTH) or {}
        seen = record.get("vehicles") or {}
        retiring = sly_data.setdefault(rail.RETIRING, {})
        # A disposal already under way is finished even with no health record, because the
        # second half of a sale is a fact about where the train is and not a judgement.
        # Starting one needs the record: what is hopeless is decided on elapsed time.
        if not seen and not retiring:
            return (
                "Error: no train has been looked at yet. Run rail_health_check first. It is "
                "free, it decides what is hopeless on elapsed time rather than on a single "
                "reading, and it supplies the vehicle ids this tool takes."
            )

        day = int(record.get("day") or 0)
        fleet: list[dict[str, Any]] = await gateway.query(
            "get_vehicles", {"vehicle_type": TRAIN}
        ) or []
        owned = {str(vehicle.get("id")): vehicle for vehicle in fleet}

        # The sweep runs first, over the trains already in the pipeline, and only then are new
        # ones sent. That ordering is what guarantees this tool can never stage send_to_depot
        # and sell_vehicle for the same train in one step.
        batch, selling, waiting, done = _sweep(
            retiring, owned, day, _sales_still_staged(Plan(sly_data))
        )

        targets, refusal = _targets(args, seen, owned, retiring, day)
        if targets and not _has_depot(sly_data):
            # Rail's own failure. Sending a train to a depot that does not exist on its network
            # leaves it running its old orders while this record says it is being retired.
            return (
                "Error: no route this company owns has a depot joined to its line, so a train "
                "sent to one would never arrive and the sale could never be made. Build one "
                "with plan_add_depot, confirm_rail_route that it joined, and come back. "
                f"Meanwhile {len(waiting)} train(s) are already in the pipeline."
            )

        sent: list[dict[str, Any]] = []
        for vid in targets:
            batch.append(action("send_to_depot", vehicle_id=int(vid)))
            retiring[vid] = {
                "stage": "sent",
                "sent_day": day,
                "name": seen.get(vid, {}).get("name", vid),
                "why": seen.get(vid, {}).get("why", "asked for by name"),
            }
            sent.append({"vehicle_id": int(vid), "name": retiring[vid]["name"]})

        if not batch:
            # A refusal about the argument is returned as the Error string a retry prompt needs.
            # "nothing is hopeless" is not a failure, it is the answer, so it comes back as a
            # report with whatever is still in the pipeline beside it.
            if refusal.startswith("Error:"):
                return refusal
            return {
                "staged": [],
                "awaiting_sale": waiting,
                "sold": done,
                "note": refusal or "nothing to retire and nothing has arrived in a depot yet",
            }

        problems = check(batch)
        if problems:
            return f"Error: this disposal would be refused: {problems}. Nothing was staged."

        plan = Plan(sly_data)
        plan.add(*batch)
        return {
            "staged": plan.describe()[-len(batch):],
            "sent_to_depot": sent,
            "sales_staged": selling,
            "awaiting_sale": waiting,
            "sold": done,
            "note": refusal,
            "already_refused": plan.already_refused(),
            "warning": (
                "the proceeds are not money yet. A train has to finish its current leg and "
                "then cross the line to a depot, which is tens of game days on a long "
                "corridor. Do not spend what this is expected to raise until it has raised it."
            ),
            "next": (
                "commit_plan submits this. Then let days pass and call this tool again: a "
                "train can only be sold once the game reports it stopped in a depot, which is "
                "why the sale is a separate call and not a second action in this step."
            ),
        }


def _has_depot(sly_data: dict[str, Any]) -> bool:
    """Whether any confirmed route has a depot the game has been seen to join.

    `connected` is checked rather than the depot's mere presence, because building a depot and
    joining it are two actions and a depot that built but never got its curve piece reads as a
    depot in every report while no train can enter or leave it.
    """
    for route in sly_data.get(key.ROUTES) or []:
        if (route.get("depot") or {}).get("connected"):
            return True
    return False


def _sales_still_staged(plan: Plan) -> set[str]:
    """The vehicle ids whose sale is sitting in the plan, unsubmitted.

    commit_plan clears the plan when it submits, so an action no longer in it is one a commit
    carried. That is the only evidence available here that a staged sale was really asked of the
    game, and it is what separates a refusal from a batch nobody committed.
    """
    return {
        str((entry.get("params") or {}).get("vehicle_id"))
        for entry in plan.actions if entry.get("action") == "sell_vehicle"
    }


def _sweep(
    retiring: dict[str, Any],
    owned: dict[str, dict[str, Any]],
    day: int,
    still_staged: set[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """Move every train already in the pipeline as far as the game allows today."""
    batch: list[dict[str, Any]] = []
    selling: list[dict[str, Any]] = []
    waiting: list[dict[str, Any]] = []
    done: list[str] = []

    for vid, entry in list(retiring.items()):
        name = entry.get("name", vid)
        vehicle = owned.get(vid)
        if vehicle is None:
            # Gone from the fleet. Either the sale went through or the train was lost, and
            # either way there is nothing left to act on.
            done.append(f"{name} is no longer in the fleet, so its disposal is finished")
            del retiring[vid]
            continue

        if not vehicle.get("in_depot"):
            waiting.append({
                "vehicle_id": int(vid),
                "name": name,
                "days_since_sent": max(0, day - int(entry.get("sent_day", day))),
                "state": "still on its way to a depot",
            })
            continue

        if vid in still_staged:
            # The sale this tool staged on an earlier call is in the plan and has not been sent,
            # so staging a second one would submit two sales for one train.
            waiting.append({
                "vehicle_id": int(vid),
                "name": name,
                "state": (
                    "stopped in a depot with its sale already staged and not committed. "
                    "commit_plan submits it; nothing more is needed here"
                ),
            })
            continue

        attempts = _sales_the_game_refused(entry)
        if attempts >= SELL_ATTEMPTS:
            waiting.append({
                "vehicle_id": int(vid),
                "name": name,
                "state": (
                    f"stopped in a depot but {attempts} sale attempts were refused. Something "
                    "other than its position is wrong; read the refusals before asking again"
                ),
            })
            continue

        batch.append(action("sell_vehicle", vehicle_id=int(vid)))
        # The intent, in the same shape plan_repoint uses. An attempt is only counted once a
        # commit has carried this action and the train is still here, because a batch never
        # committed is not a refusal.
        entry["stage"] = "sell_staged"
        entry[rail.SELL_STAGED_DAY] = day
        selling.append({"vehicle_id": int(vid), "name": name, "why": entry.get("why")})

    return batch, selling, waiting, done


def _sales_the_game_refused(entry: dict[str, Any]) -> int:
    """How many sales the game has actually refused for this train, promoting any staged one.

    A staged sale that has left the plan was carried by a commit, and this train is still in the
    fleet and still stopped in a depot, so the game refused it. That is the moment it becomes an
    attempt. Counted at staging time instead, a plan nobody committed spends the whole allowance
    and the tool reports refusals about a sale the game was never asked for.
    """
    staged = entry.get(rail.SELL_STAGED_DAY)
    attempts = int(entry.get(rail.SELL_ATTEMPTS, 0))
    if staged is None:
        return attempts
    del entry[rail.SELL_STAGED_DAY]
    attempts += 1
    entry[rail.SELL_ATTEMPTS] = attempts
    return attempts


def _targets(
    args: dict[str, Any],
    seen: dict[str, Any],
    owned: dict[str, dict[str, Any]],
    retiring: dict[str, Any],
    day: int,
) -> tuple[list[str], str]:
    """Which trains to send to a depot, and why not, when the answer is none."""
    given = args.get("vehicle_id")
    if given is not None:
        vid = str(given)
        if vid not in owned:
            return [], (
                f"Error: {vid} is not a train this company owns. rail_health_check lists the "
                "ids it read from the game; use one of those."
            )
        if vid in retiring:
            return [], (
                f"Error: {seen.get(vid, {}).get('name', vid)} is already being retired, at "
                f"stage {retiring[vid].get('stage')}. Let days pass and call this tool again "
                "rather than sending it to a depot twice."
            )
        entry = seen.get(vid, {})
        verdict = entry.get("verdict")
        if verdict not in WORTH_SELLING:
            return [], (
                f"Error: the health check calls {entry.get('name', vid)} "
                f"'{verdict or 'unseen'}', and only a train it calls "
                f"{', '.join(WORTH_SELLING)} is worth selling. Run rail_health_check, and if "
                "it is lost or stuck try plan_repoint first: fixing orders is free and "
                "selling is not."
            )
        return [vid], ""

    hopeless = [
        vid for vid, entry in seen.items() if _is_hopeless(vid, entry, owned, retiring, day)
    ]
    if not hopeless:
        return [], (
            "no train is hopeless. A lost or stuck one qualifies only after a repoint has "
            f"been committed, seen to take effect, and {REPOINT_GRACE_DAYS} days have passed "
            "since without it moving, because repointing is cheaper than replacing and a "
            "repoint staged but never committed does not count as having been tried. A "
            "locomotive that carries nothing qualifies at once, since no order couples a wagon."
        )
    return hopeless, ""


def _is_hopeless(
    vid: str,
    entry: dict[str, Any],
    owned: dict[str, dict[str, Any]],
    retiring: dict[str, Any],
    day: int,
) -> bool:
    """Beyond an order fix, or repointed and given time to show the repoint did not take.

    The completed repoint marker is read and the staged one deliberately is not. A repoint that
    was staged and never committed repaired nothing, and treating it as tried is how a train
    that had never actually been repointed became hopeless and was sold.
    """
    if vid not in owned or vid in retiring:
        return False
    verdict = entry.get("verdict")
    if verdict == BEYOND_REPOINT:
        return True
    if verdict not in WORTH_SELLING:
        return False
    repointed = entry.get(rail.REPOINTED_DAY)
    if repointed is None:
        return False
    return day - int(repointed) >= REPOINT_GRACE_DAYS
