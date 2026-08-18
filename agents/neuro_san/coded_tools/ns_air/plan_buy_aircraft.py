"""Stage the purchase of aircraft for a corridor that exists. Costs nothing until committed.

**Nothing here is named by the model.** It asks for a number of aircraft and, at most, a town.
The engine is resolved by choose_aircraft and the hangar is read back from the game. That is
the whole point of the tool: an earlier surface took engine_id and depot coordinates as
arguments, and a run submitted buy_vehicle 35 times with invented engine ids at a guessed
hangar, every one refused ERR_PRECONDITION_FAILED. A model asked for an identifier it has no
way to obtain will invent one, so it is never asked.

**The purchases go into the plan together.** A step is a game day, so buying four aircraft one
step at a time spends four days of a 366 day run on paperwork. commit_plan submits them as one
batch and one day covers all of them.

**Buying late is buying nothing.** An aircraft needs roughly 120 days to return its price. One
bought with 60 days left is cash converted into a depreciating asset, and the loan it was
borrowed against still scores against the rating.

This tool does not step. Nothing that plans does: the clock belongs to commit_plan, advance_days
and set_loan_to.
"""

from __future__ import annotations

from typing import Any

from neuro_san.interfaces.coded_tool import CodedTool

try:
    # Loaded as part of this repository, which is how the tests import it.
    from agents.neuro_san.coded_tools.ns import counting, session
    from agents.neuro_san.coded_tools.ns.envelope import action, check
    from agents.neuro_san.coded_tools.ns.gateway import NttdGateway
    from agents.neuro_san.coded_tools.ns.plan import Plan
    from agents.neuro_san.coded_tools.ns_air.choose_aircraft import (
        airports_of,
        known_routes,
        name_of,
        rank_aircraft,
        route_for,
    )
except ImportError:
    # Loaded by neuro-san from AGENT_TOOL_PATH, where ns and ns_air are siblings and the
    # package above them is not on the path. Both spellings are needed because
    # AGENT_TOOL_PATH_ONLY=true deliberately stops a class reference resolving from anywhere
    # on PYTHONPATH.
    from ns import counting, session
    from ns.envelope import action, check
    from ns.gateway import NttdGateway
    from ns.plan import Plan

    from ns_air.choose_aircraft import (
        airports_of,
        known_routes,
        name_of,
        rank_aircraft,
        route_for,
    )

# Roughly what an aircraft needs to return its price at the rates measured in play.
DAYS_TO_PAY_BACK = 120

# More than this in one batch is a fleet bought before a single one has been seen to fly.
MOST_AT_ONCE = 4


class PlanBuyAircraft(CodedTool):
    """Aircraft for a corridor, staged into the plan and bought in one game day."""

    async def async_invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> Any:
        return await session.guarded(self._stage_purchase, args, sly_data)

    async def _stage_purchase(
        self, gateway: NttdGateway, args: dict[str, Any], sly_data: dict[str, Any]
    ) -> Any:
        route = route_for(sly_data, town=args.get("town"))
        if route is None:
            return (
                "Error: no corridor to buy for. Corridors on record: "
                f"{known_routes(sly_data) or 'none yet'}. Build one first: an aircraft with "
                "nowhere to land earns nothing and still costs running money."
            )

        late = refuse_if_late(await gateway.observe())
        if late:
            return late

        airports = await airports_of(gateway, route)
        ranked = await rank_aircraft(gateway, route, airports)
        if not ranked:
            return (
                "Error: no aeroplane on sale can land at both ends of "
                f"{name_of(route)}. Call choose_aircraft to see what the airports allow."
            )
        engine = ranked[0]

        hangar = await hangar_for(gateway, route, airports)
        if hangar is None:
            return (
                f"Error: no hangar is recorded for {name_of(route)} and the game reports none "
                "for its stations. Run confirm_airports on the corridor first: an aircraft is "
                "bought at a hangar tile, and that tile cannot be worked out from the airport."
            )

        wanted, note_on_count = counting.counted(args.get("count"), 1, most=MOST_AT_ONCE)
        purchases = [
            action("buy_vehicle", engine_id=int(engine["id"]), **hangar) for _ in range(wanted)
        ]
        problems = check(purchases)
        if problems:
            return f"Error: the purchase is malformed and was not staged. {'; '.join(problems)}"

        plan = Plan(sly_data)
        plan.add(*purchases)

        money = (await gateway.situation()).get("money") or {}
        price = int(engine.get("price") or 0)
        return {
            "staged": wanted,
            "corridor": name_of(route),
            "engine": engine.get("name"),
            "price_each": price,
            "cost": price * wanted,
            "balance": money.get("balance"),
            # What is left if this is committed as it stands. Negative means borrow first with
            # set_loan_to: one run bottomed out at 7,707 by committing purchases it could not
            # cover and spent the next thirty days unable to do anything at all.
            "cash_after": int(money.get("balance") or 0) - price * wanted,
            "hangar": hangar,
            "unconfirmed": unconfirmed(route),
            # What the game has already refused, so the same purchase is not staged twice. The
            # ledger this reads is the one whose absence let 35 identical calls be submitted.
            "already_refused": plan.already_refused(),
            "plan": plan.describe(),
            "next": (
                f"commit_plan spends ONE game day on all {wanted}, then plan_dispatch gives "
                "them their orders. A vehicle id does not exist until the purchase is "
                "committed, which is why the orders cannot go in this batch."
            ),
        } | counting.said(note_on_count)


def unconfirmed(route: dict[str, Any]) -> str | None:
    """A warning when confirm_airports did not pass this corridor, else None.

    Reported rather than refused. The airports are built and paid for either way, so the choice
    is between flying them and abandoning them, and a fleet is still worth more than nothing.
    What it is worth saying is which corridor it is: one run built a metropolitan airport 29
    tiles from the town it was meant for, the game attached it to a 348 person village and
    named the station after it, and the run scored 118 against a baseline of 173.
    """
    if route.get("ready") is False:
        return (
            "confirm_airports has not passed this corridor. Read its warnings before spending "
            "more here: an airport that landed in the wrong catchment carries almost nobody."
        )
    return None


def days_left(world: dict[str, Any]) -> int | None:
    """How much of the run remains, or None when the run is not bounded by days.

    Read from the observation's game block. The situation report is the right source for money,
    fleet and problems and it carries no horizon at all; nttd publishes the count onto
    world.game precisely so an agent deciding whether a vehicle has time to pay for itself can
    read it. A total of zero means unbounded, which is not the same as none left: testing the
    remaining count for truthiness conflates them and stands this guard down on the last day,
    the one moment it exists for.
    """
    game = world.get("game") or {}
    if not int(game.get("game_days_total") or 0):
        return None
    return int(game.get("game_days_remaining") or 0)


def refuse_if_late(world: dict[str, Any]) -> str | None:
    """The refusal to hand back when there is no time left to earn a price back, else None."""
    remaining = days_left(world)
    if remaining is None or remaining >= DAYS_TO_PAY_BACK:
        return None
    return (
        f"Error: {remaining} game days remain and an aircraft needs about {DAYS_TO_PAY_BACK} to "
        "return its price, so this one would be cash turned into a depreciating asset. Buy "
        "nothing. Keep what is flying in the air and pay the loan down instead."
    )


async def hangar_for(
    gateway: NttdGateway,
    route: dict[str, Any],
    airports: list[dict[str, Any]] | None = None,
) -> dict[str, int] | None:
    """Where this route's aircraft are built, as parameters an action takes.

    Either {"depot_tile": n} or {"depot_x": x, "depot_y": y}: buy_vehicle and clone_vehicle both
    accept either, and which one comes back depends on what was recorded.

    The route record written by confirm_airports is preferred because it costs no query, and
    the game is asked when it holds nothing usable. Neither path computes anything: the offset
    from an airport tile to its hangar is not derivable, measured as +5 in x for metropolitan
    and large, +4 in x for commuter and +3 in y for international, and four buy_vehicle calls at
    an airport's own coordinates failed ERR_UNKNOWN with no diagnostic at all.
    """
    for recorded in (route.get("hangar"), route.get("depot"), *recorded_hangars(route)):
        params = _depot_params(recorded)
        if params:
            return params

    if airports is None:
        airports = await airports_of(gateway, route)
    for entry in airports:
        params = _depot_params(entry)
        if params:
            return params
    return None


def recorded_hangars(route: dict[str, Any]) -> list[Any]:
    """Whatever confirm_airports cached under `hangars`, as a flat list of candidates.

    Public because plan_dispatch reads the same records from the other direction, asking which
    route a hangar belongs to rather than where to buy, and one reader of this shape is enough.

    A mapping of station to hangar and a list of hangar records are both handled, and the keys
    are not read at all. sly_data crosses the turn boundary as JSON and JSON has no integer
    keys, so a station id written as an int this turn is a string next turn and a lookup by id
    misses. Any hangar on this route will do: both ends have one, and the orders decide where
    the aircraft goes afterwards.
    """
    recorded = route.get("hangars")
    if isinstance(recorded, dict):
        return list(recorded.values())
    if isinstance(recorded, list):
        return list(recorded)
    return []


def _depot_params(recorded: Any) -> dict[str, int] | None:
    """One recorded hangar as depot parameters, or None when it is not one."""
    if isinstance(recorded, int):
        return {"depot_tile": recorded}
    if not isinstance(recorded, dict):
        return None
    for x_field, y_field in (("hangar_x", "hangar_y"), ("x", "y")):
        if recorded.get(x_field) is not None and recorded.get(y_field) is not None:
            return {"depot_x": int(recorded[x_field]), "depot_y": int(recorded[y_field])}
    for tile_field in ("hangar_tile", "tile"):
        if isinstance(recorded.get(tile_field), int):
            return {"depot_tile": int(recorded[tile_field])}
    return None
