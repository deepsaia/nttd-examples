"""Get the money back out of an aircraft that will never earn it. Stages only; commits nothing.

**Selling is two stage, across turns, and that is the whole design.** `sell_vehicle` requires
the vehicle to have ARRIVED and stopped in a depot, and `send_to_depot` only asks it to go: it
finishes the current leg first. The two in the SAME step therefore always fails, because when
the sale executes the aircraft is still in the air. Measured: one run issued `sell_vehicle` on
a flying aircraft three times, ERR_VEHICLE_NOT_IN_DEPOT each time, and the sale finally
completed 32 game days after the first attempt. The repair tool that did it batched the pair
into one step and its retry counter was already spent, so it resubmitted the same refused pair
forever.

So: this call stages `send_to_depot` and writes down that the aircraft is awaiting sale. A
LATER call sees it has arrived, reads that from the game rather than assuming, and stages
`sell_vehicle`. Nothing else gets a vehicle from the sky into a hangar.

**Recovering capital beats paying running costs on something that never moved.** Selling two
stranded trains once funded another route. But an aircraft is only hopeless after repointing
has been tried and given time to show, and nothing is hopeless before day 75, because cargo
delivered was exactly 0 until day 73 of the run that scored best. A tool that sells early sells
working aircraft.

**The proceeds are not money yet.** The round trip is 20 to 35 game days on a long leg. A run
that spent the expected proceeds while the aircraft was still flying bottomed out at 7,707.
"""

from __future__ import annotations

from typing import Any

from neuro_san.interfaces.coded_tool import CodedTool

try:
    # Loaded as part of this repository, which is how the tests import it.
    from agents.neuro_san.coded_tools.ns.envelope import action, check
    from agents.neuro_san.coded_tools.ns.gateway import NttdGateway
    from agents.neuro_san.coded_tools.ns.plan import Plan
    from agents.neuro_san.coded_tools.ns_air import air_keys as air
    from agents.neuro_san.coded_tools.ns_air.air_health_check import RAMP_DAYS
    from agents.neuro_san.coded_tools.ns_air.choose_aircraft import AIRCRAFT
    from agents.neuro_san.coded_tools.ns_air.plan_repoint import REPOINT_GRACE_DAYS
except ImportError:
    # Loaded by neuro-san from AGENT_TOOL_PATH, where ns and ns_air are siblings and the
    # package above them is not on the path. Both spellings are needed because
    # AGENT_TOOL_PATH_ONLY=true deliberately stops a class reference resolving from anywhere
    # on PYTHONPATH.
    from ns.envelope import action, check
    from ns.gateway import NttdGateway
    from ns.plan import Plan

    from ns_air import air_keys as air
    from ns_air.air_health_check import RAMP_DAYS
    from ns_air.choose_aircraft import AIRCRAFT
    from ns_air.plan_repoint import REPOINT_GRACE_DAYS

# How many times a sale may be asked for. A sale is only ever staged once the game says the
# aircraft is stopped in its hangar, so a refusal after that is something else, and asking a
# fourth time is the loop that filled one run's action log with the same refused pair.
SELL_ATTEMPTS = 3


class PlanRetire(CodedTool):
    """Send a hopeless aircraft to a hangar, and sell it on a later call once it is there."""

    async def async_invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> Any:
        gateway = NttdGateway(sly_data)
        record = sly_data.get(air.HEALTH) or {}
        seen = record.get("vehicles") or {}
        retiring = sly_data.setdefault(air.RETIRING, {})
        # A disposal already under way is finished even with no health record, because the
        # second half of a sale is a fact about where the aircraft is and not a judgement.
        # Starting one needs the record: what is hopeless is decided on elapsed time.
        if not seen and not retiring:
            return (
                "Error: no aircraft have been looked at yet. Run air_health_check first. It is "
                "free, it decides what is hopeless on elapsed time rather than on a single "
                "reading, and it supplies the vehicle ids this tool takes."
            )

        day = int(record.get("day") or 0)
        fleet: list[dict[str, Any]] = await gateway.query(
            "get_vehicles", {"vehicle_type": AIRCRAFT}
        ) or []
        owned = {str(vehicle.get("id")): vehicle for vehicle in fleet}

        # The sweep runs first, over the aircraft already in the pipeline, and only then are new
        # ones sent. That ordering is what guarantees this tool can never stage send_to_depot
        # and sell_vehicle for the same aircraft in one step.
        batch, selling, waiting, done = _sweep(retiring, owned, day)

        targets, refusal = _targets(args, seen, owned, retiring, day)
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
                "note": refusal or "nothing to retire and nothing has arrived in a hangar yet",
            }

        problems = check(batch)
        if problems:
            return f"Error: this disposal would be refused: {problems}. Nothing was staged."

        plan = Plan(sly_data)
        plan.add(*batch)
        return {
            "staged": plan.describe()[-len(batch):],
            "sent_to_hangar": sent,
            "being_sold": selling,
            "awaiting_sale": waiting,
            "sold": done,
            "note": refusal,
            "already_refused": plan.already_refused(),
            "warning": (
                "the proceeds are not money yet. Getting an aircraft home and sold takes 20 to "
                "35 game days on a long leg, and a run that spent the expected proceeds while "
                "it was still flying bottomed out at 7,707."
            ),
            "next": (
                "commit_plan submits this. Then let days pass and call this tool again: an "
                "aircraft can only be sold once the game reports it stopped in its hangar, "
                "which is why the sale is a separate call and not a second action in this step."
            ),
        }


def _sweep(
    retiring: dict[str, Any],
    owned: dict[str, dict[str, Any]],
    day: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """Move every aircraft already in the pipeline as far as the game allows today."""
    batch: list[dict[str, Any]] = []
    selling: list[dict[str, Any]] = []
    waiting: list[dict[str, Any]] = []
    done: list[str] = []

    for vid, entry in list(retiring.items()):
        name = entry.get("name", vid)
        vehicle = owned.get(vid)
        if vehicle is None:
            # Gone from the fleet. Either the sale went through or the aircraft was lost, and
            # either way there is nothing left to act on.
            done.append(f"{name} is no longer in the fleet, so its disposal is finished")
            del retiring[vid]
            continue

        if not vehicle.get("in_depot"):
            waiting.append({
                "vehicle_id": int(vid),
                "name": name,
                "days_since_sent": max(0, day - int(entry.get("sent_day", day))),
                "state": "still on its way to a hangar",
            })
            continue

        attempts = int(entry.get("sell_attempts", 0))
        if attempts >= SELL_ATTEMPTS:
            waiting.append({
                "vehicle_id": int(vid),
                "name": name,
                "state": (
                    f"stopped in its hangar but {attempts} sale attempts were refused. Something "
                    "other than its position is wrong; read the refusals before asking again"
                ),
            })
            continue

        batch.append(action("sell_vehicle", vehicle_id=int(vid)))
        entry["stage"] = "selling"
        entry["sell_attempts"] = attempts + 1
        entry["sell_day"] = day
        selling.append({"vehicle_id": int(vid), "name": name, "why": entry.get("why")})

    return batch, selling, waiting, done


def _targets(
    args: dict[str, Any],
    seen: dict[str, Any],
    owned: dict[str, dict[str, Any]],
    retiring: dict[str, Any],
    day: int,
) -> tuple[list[str], str]:
    """Which aircraft to send to a hangar, and why not, when the answer is none."""
    given = args.get("vehicle_id")
    if given is not None:
        vid = str(given)
        if vid not in owned:
            return [], (
                f"Error: {vid} is not an aircraft this company owns. air_health_check lists the "
                f"ids it read from the game; use one of those."
            )
        if vid in retiring:
            return [], (
                f"Error: {seen.get(vid, {}).get('name', vid)} is already being retired, at stage "
                f"{retiring[vid].get('stage')}. Let days pass and call this tool again rather "
                "than sending it to a hangar twice."
            )
        if day < RAMP_DAYS:
            return [], (
                f"Error: it is day {day} and nothing is sold before day {RAMP_DAYS}. Cargo "
                "delivered was exactly 0 until day 73 of the best measured run, so an aircraft "
                "that looks idle now is a fleet still ramping, and selling it sells a working "
                "aircraft."
            )
        return [vid], ""

    hopeless = [vid for vid, entry in seen.items() if _is_hopeless(vid, entry, owned, retiring, day)]
    if not hopeless:
        return [], (
            "no aircraft is hopeless. One qualifies only after the health check has called it "
            f"stuck, plan_repoint has been tried on it, and {REPOINT_GRACE_DAYS} days have "
            "passed since without it moving. Repointing is cheaper than replacing, so it goes "
            "first."
        )
    return hopeless, ""


def _is_hopeless(
    vid: str,
    entry: dict[str, Any],
    owned: dict[str, dict[str, Any]],
    retiring: dict[str, Any],
    day: int,
) -> bool:
    """Stuck, already repointed, and given time to show that the repoint did not take.

    Every clause is load-bearing. Without the repoint clause this sells an aircraft that a free
    order fix would have saved; without the elapsed-days clause it sells one that was on its way
    back when it was looked at.
    """
    if vid not in owned or vid in retiring:
        return False
    if entry.get("verdict") != "stuck":
        return False
    repointed = entry.get("repointed_day")
    if repointed is None:
        return False
    return day - int(repointed) >= REPOINT_GRACE_DAYS
