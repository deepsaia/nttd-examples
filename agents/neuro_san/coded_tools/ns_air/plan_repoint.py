"""Put a stuck aircraft back on the route it belongs to. Stages only; commits nothing.

This owns the worst bug the previous repair tool had. That tool called `add_order`, which
APPENDS, without removing what was there, and it read the route to use from the tail of a list
rather than from the vehicle. So a lost aircraft came out of a repair with four orders
zig-zagging between two unrelated town pairs, an order count that looked busy, and a route
nobody planned. A vehicle re-ordered onto the wrong route is worse than one left alone: the
first still flies somewhere, the second flies somewhere useless and costs the same.

So the shape is fixed and it is not negotiable:

1. read `get_orders`, which is free,
2. `remove_order` for every existing index in DESCENDING order, because removing index 0 first
   shifts every later index down by one and the second removal then takes the wrong order,
3. the two `add_order` calls for THIS vehicle's route, matched to the route whose stations its
   current orders already name,
4. `start_vehicle`, since a repointed aircraft that is stopped stays stopped.

All of it in one Plan, so the whole repair costs one game day rather than five.

The route is resolved from the game, never from the model. `vehicle_id` is the one argument
that may be supplied and it comes back out of air_health_check, which read it from the game:
35 refused purchases in one measured run were made with invented ids.

Nothing here calls step. Planning is free and only commit_plan spends the day, which is also why
what this tool writes into the health record is that a repoint was STAGED. air_health_check turns
that into a repoint that happened, once it sees the aircraft move.
"""

from __future__ import annotations

from typing import Any

from neuro_san.interfaces.coded_tool import CodedTool

try:
    # Loaded as part of this repository, which is how the tests import it.
    from agents.neuro_san.coded_tools.ns import constants as key
    from agents.neuro_san.coded_tools.ns import session
    from agents.neuro_san.coded_tools.ns.envelope import action, check
    from agents.neuro_san.coded_tools.ns.gateway import NttdGateway
    from agents.neuro_san.coded_tools.ns.plan import Plan
    from agents.neuro_san.coded_tools.ns_air import air_keys as air
    from agents.neuro_san.coded_tools.ns_air.choose_aircraft import (
        known_routes,
        name_of,
        route_for,
    )
    from agents.neuro_san.coded_tools.ns_air.plan_dispatch import (
        STOPS_PER_ROUTE,
        TAKE_WHAT_IS_THERE,
    )
except ImportError:
    # Loaded by neuro-san from AGENT_TOOL_PATH, where ns and ns_air are siblings and the
    # package above them is not on the path. Both spellings are needed because
    # AGENT_TOOL_PATH_ONLY=true deliberately stops a class reference resolving from anywhere
    # on PYTHONPATH.
    from ns import constants as key
    from ns import session
    from ns.envelope import action, check
    from ns.gateway import NttdGateway
    from ns.plan import Plan

    from ns_air import air_keys as air
    from ns_air.choose_aircraft import known_routes, name_of, route_for
    from ns_air.plan_dispatch import STOPS_PER_ROUTE, TAKE_WHAT_IS_THERE

# How long a repoint is given before the same aircraft may be repointed again. A repair is not
# visible the next day: the far end of a 289 tile trunk did not see its first aircraft until
# day 43 of the run. Without this window the vehicle is still sitting where it was when the
# next health check runs, is called stuck again, and is repointed forever, which is exactly the
# loop the previous repair tool ran.
REPOINT_GRACE_DAYS = 20


class PlanRepoint(CodedTool):
    """Clear an aircraft's orders and give it its own route's two stations, then start it."""

    async def async_invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> Any:
        return await session.guarded(self._stage_repoint, args, sly_data)

    async def _stage_repoint(
        self, gateway: NttdGateway, args: dict[str, Any], sly_data: dict[str, Any]
    ) -> Any:
        record = sly_data.get(air.HEALTH) or {}
        seen = record.get("vehicles") or {}
        if not seen:
            return (
                "Error: no aircraft have been looked at yet. Run air_health_check first. It is "
                "free, it says which aircraft are stuck and why, and it supplies the vehicle "
                "ids this tool takes."
            )

        routes = _air_routes(sly_data.get(key.ROUTES) or [])
        if not routes:
            return (
                "Error: no air route is recorded, so there is nowhere to point an aircraft. "
                "Build a route first. An aircraft sent to stations that are not its own is "
                "worse off than one left alone."
            )

        # A named town is resolved by the package's own route lookup, so a corridor is named the
        # same way here as it is when one is bought for. It is never called without a name:
        # asked bare it answers with the NEWEST route, and re-ordering an aircraft onto the
        # newest route regardless of which one it flies is the bug this tool exists to undo.
        town = str(args.get("town") or "").strip()
        named = route_for(sly_data, town=town) if town else None
        if town and named is None:
            return (
                f"Error: no recorded corridor serves {town}. On record: "
                f"{known_routes(sly_data) or 'none yet'}."
            )

        day = int(record.get("day") or 0)
        # An aircraft on its way to a hangar to be sold must not be given fresh orders: the new
        # orders would take it back out of the hangar and the sale would never become possible.
        retiring = sly_data.get(air.RETIRING) or {}
        targets, refusal = _targets(args, seen, retiring, day)
        if refusal:
            return refusal

        world = await gateway.observe()
        width = int((world.get("game") or {}).get("map_width") or 0)
        tiles = _station_tiles(world.get("stations") or [], width)

        batch: list[dict[str, Any]] = []
        repointed: list[dict[str, Any]] = []
        skipped: list[str] = []

        for vid in targets:
            entry = seen[vid]
            existing = await gateway.query("get_orders", {"vehicle_id": int(vid)}) or {}
            orders = existing.get("orders") or []
            route = named or _route_flown(orders, routes, tiles)
            if route is None:
                skipped.append(
                    f"{entry.get('name', vid)}: its orders name no recorded corridor and more "
                    f"than one is on record, so which it flies cannot be told from the game. "
                    f"Call again naming the town it serves. On record: {known_routes(sly_data)}."
                )
                continue
            if len(route.get("stations") or []) < STOPS_PER_ROUTE:
                skipped.append(
                    f"{entry.get('name', vid)}: {name_of(route)} records "
                    f"{len(route.get('stations') or [])} station and an aircraft needs two to "
                    "fly between. Confirm the second airport first."
                )
                continue
            batch.extend(_repair(int(vid), orders, route))
            repointed.append({
                "vehicle_id": int(vid),
                "name": entry.get("name"),
                "orders_removed": len(orders),
                "route": name_of(route),
                "was": entry.get("why"),
            })

        if not batch:
            return {
                "staged": [],
                "skipped": skipped,
                "note": "nothing was repointed; every candidate is listed above with its reason",
            }

        problems = check(batch)
        if problems:
            # Reported before anything is staged, because a batch that cannot be accepted is a
            # game day spent finding that out.
            return f"Error: this repair would be refused: {problems}. Nothing was staged."

        plan = Plan(sly_data)
        plan.add(*batch)
        for done in repointed:
            entry = seen[str(done["vehicle_id"])]
            # The INTENT, not the accomplishment. Nothing has been submitted yet: commit_plan
            # spends the day, and it may be refused or never called. An earlier version wrote
            # repointed_day here, and a repair that never happened then read as done, so the
            # health check stopped flagging a vehicle that was still stuck and plan_retire sold
            # it as beyond repair. air_health_check promotes this to a completed repoint when it
            # sees the aircraft move.
            entry[air.REPOINT_STAGED_DAY] = day
            entry["intended_route"] = done["route"]

        return {
            "staged": plan.describe()[-len(batch):],
            "will_repoint_when_committed": repointed,
            "skipped": skipped,
            "already_refused": plan.already_refused(),
            "next": (
                "commit_plan submits this; it costs one game day for the whole repair. Then "
                f"leave it alone: an aircraft is not back on its route the next morning, so "
                f"this tool will not repoint the same one again for {REPOINT_GRACE_DAYS} days."
            ),
        }


def _air_routes(routes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The recorded routes an aircraft can fly: two stations, and not somebody else's mode."""
    return [
        route for route in routes
        if len(route.get("stations") or []) >= 2 and (route.get("mode") or "air") == "air"
    ]


def _station_tiles(stations: list[dict[str, Any]], width: int) -> dict[int, int]:
    """Station id to tile index.

    An order's destination is a TILE, and a route records STATION IDS, so one of them has to be
    converted before they can be compared. The map width comes off the snapshot rather than
    being assumed: a tile index computed against the wrong width points at somewhere real and
    somewhere else, which is worse than having no answer.
    """
    if not width:
        return {}
    return {int(s["id"]): int(s["y"]) * width + int(s["x"]) for s in stations if "id" in s}


def _route_flown(
    orders: list[dict[str, Any]],
    routes: list[dict[str, Any]],
    tiles: dict[int, int],
) -> dict[str, Any] | None:
    """The corridor THIS aircraft flies, decided from the game and not from a guess.

    Its own orders first, because they are the only evidence about this aircraft rather than
    about the company. A single recorded corridor second, since with one there is nothing to get
    wrong. Otherwise nothing, and the caller is asked to name the town: an aircraft on the wrong
    corridor is worse than one left alone, and the newest corridor is not an answer to the
    question "which one does this aircraft fly".
    """
    destinations = {
        int(order.get("destination")) for order in orders
        if order.get("is_goto_station") and order.get("destination") is not None
    }
    if destinations:
        scored = [
            (len(destinations & {tiles[sid] for sid in route["stations"] if sid in tiles}), route)
            for route in routes
        ]
        scored.sort(key=lambda pair: pair[0], reverse=True)
        best = scored[0]
        # A tie between two routes is not a match. Two routes that both explain the orders
        # equally well mean the evidence does not name one.
        tied = sum(1 for score, _ in scored if score == best[0])
        if best[0] and tied == 1:
            return best[1]

    if len(routes) == 1:
        return routes[0]
    return None


def _repair(vid: int, orders: list[dict[str, Any]], route: dict[str, Any]) -> list[dict[str, Any]]:
    """Clear, re-order, start. In that order, and the clearing runs backwards.

    What follows the removals is exactly the triplet plan_dispatch stages for a new aircraft,
    taken from it rather than written again: a repoint is a clear plus a dispatch, and if the
    dispatch shape ever changes, a repair that kept its own copy would quietly stop matching it.
    """
    indices = sorted(
        (int(order.get("index", position)) for position, order in enumerate(orders)),
        reverse=True,
    )
    batch = [action("remove_order", vehicle_id=vid, order_index=index) for index in indices]
    batch.extend(
        action(
            "add_order",
            vehicle_id=vid,
            station_id=int(station),
            order_flags=TAKE_WHAT_IS_THERE,
        )
        for station in route["stations"][:STOPS_PER_ROUTE]
    )
    batch.append(action("start_vehicle", vehicle_id=vid))
    return batch


def _targets(
    args: dict[str, Any],
    seen: dict[str, Any],
    retiring: dict[str, Any],
    day: int,
) -> tuple[list[str], str]:
    """Which aircraft to repoint, and why not, when the answer is none.

    With no vehicle_id this is every aircraft the last health check marked stuck or found with
    the wrong orders. Those two are what repointing fixes; anything else it would only disturb.
    """
    given = args.get("vehicle_id")
    if given is not None:
        vid = str(given)
        if vid not in seen:
            known = sorted(int(k) for k in seen)
            return [], (
                f"Error: {vid} is not an aircraft this company owns. air_health_check last saw "
                f"{known}. Use an id from it rather than one worked out from a name or a count."
            )
        if vid in retiring:
            return [], (
                f"Error: {seen[vid].get('name', vid)} is on its way to a hangar to be sold. New "
                "orders would fly it back out and the sale would never become possible. Either "
                "let plan_retire finish, or decide to keep it before repointing it."
            )
        waited = _days_since_repoint(seen[vid], day)
        if waited is not None and waited < REPOINT_GRACE_DAYS:
            return [], (
                f"Error: {seen[vid].get('name', vid)} had a repoint staged {waited} days ago and a "
                f"repoint is not visible the next day. Leave it until "
                f"{REPOINT_GRACE_DAYS - waited} more days have passed; repointing it again now "
                f"is the loop that resubmitted the same repair forever. If nothing was committed, "
                f"that is commit_plan's business rather than a second repair."
            )
        return [vid], ""

    ready = [
        vid for vid, entry in seen.items()
        if entry.get("needs_repoint") and vid not in retiring and _out_of_grace(entry, day)
    ]
    if not ready:
        return [], (
            "Error: no aircraft is waiting to be repointed. Either none is stuck, or the ones "
            f"that were have been repointed inside the last {REPOINT_GRACE_DAYS} days and have "
            "not been given time to show it. Let days pass and check again."
        )
    return ready, ""


def _days_since_repoint(entry: dict[str, Any], day: int) -> int | None:
    """How long ago this aircraft was last repointed OR had one staged, whichever is later.

    The staged day counts, and it has to: the loop this window exists to stop is re-staging the
    same repair every turn, and a batch that is waiting to be committed is exactly the case where
    nothing has moved yet. Both days are read because a promotion writes the completed one and
    removes the staged one.
    """
    days = [
        int(entry[field]) for field in (air.REPOINT_STAGED_DAY, air.REPOINTED_DAY)
        if entry.get(field) is not None
    ]
    if not days:
        return None
    return max(0, day - max(days))


def _out_of_grace(entry: dict[str, Any], day: int) -> bool:
    """Whether this aircraft may be repointed again.

    Spelled out rather than folded into the comprehension, because "repointed today" is 0 days
    ago and a falsy 0 read as "never repointed" would repoint the same aircraft every turn,
    which is the loop this window exists to stop.
    """
    waited = _days_since_repoint(entry, day)
    return waited is None or waited >= REPOINT_GRACE_DAYS
