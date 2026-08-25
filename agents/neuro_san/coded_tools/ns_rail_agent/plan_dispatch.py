"""Give a built train its two orders and start it, once it can actually carry something.

Three rules meet here.

**Check the wagons first.** `build_train` sells back any wagon it could not attach and reports
how many it managed. A locomotive whose wagons all failed is a train in every report the game
offers: it has a name, a position, a profit line and a route. The only thing that says it is
broken is a capacity of nothing, and by the time cargo-delivered shows zero the run has spent
a hundred days on it. So dispatch reads the build reply rather than trusting that a vehicle id
came back.

**Leave the order flags off.** A station only starts producing once a vehicle has visited it,
so a train told to wait for a full load sits in an empty station forever and the route never
starts. This is the rail version of the trap, and it is worse than the air one because the
train looks busy: it is at a platform, loading.

**start_vehicle is a TOGGLE.** Called on a train already running it STOPS it. So it is staged
only for a train that is stopped in its depot, which is where a freshly built one is.
"""

from __future__ import annotations

from typing import Any

from neuro_san.interfaces.coded_tool import CodedTool

try:
    from agents.neuro_san.coded_tools.ns import envelope, session
    from agents.neuro_san.coded_tools.ns.gateway import NttdGateway, QueryRefused
    from agents.neuro_san.coded_tools.ns.plan import Plan
    from agents.neuro_san.coded_tools.ns_rail_agent import rail_keys as rail
    from agents.neuro_san.coded_tools.ns_rail_agent import rail_rules as rules
except ImportError:
    from ns import envelope, session
    from ns.gateway import NttdGateway, QueryRefused
    from ns.plan import Plan

    from ns_rail_agent import rail_keys as rail
    from ns_rail_agent import rail_rules as rules

# No flags at all. Written as a named constant so nobody adds a full-load flag "just for the
# loaded direction": the station has produced nothing yet, so there is nothing to fill up on.
NO_FLAGS = 0


class PlanDispatch(CodedTool):
    """Stages two station orders and a start for a train that can carry cargo."""

    async def async_invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> Any:
        return await session.guarded(self._stage, args, sly_data)

    async def _stage(
        self, gateway: NttdGateway, args: dict[str, Any], sly_data: dict[str, Any]
    ) -> Any:
        vehicle_id = args.get("vehicle_id")
        pair_id = str(args.get("pair_id") or "").strip()
        if vehicle_id is None:
            return (
                "Error: no vehicle_id was given. It comes back from the build_train that "
                "commit_plan submitted; do not invent one."
            )

        intents = sly_data.get(rail.INTENT) or {}
        intent = next(
            (value for key_id, value in intents.items() if _same(key_id, pair_id)), None,
        )
        if intent is None:
            return (
                f"Error: {pair_id or '(nothing)'} is not a staged corridor, and a train needs "
                "the two stations of one to be ordered between."
            )

        try:
            built = await gateway.query("get_vehicle_info", {"vehicle_id": int(vehicle_id)})
        except QueryRefused as refused:
            return f"Error: train {vehicle_id} could not be read ({refused}). Nothing staged."

        if not isinstance(built, dict) or not built:
            return f"Error: the game does not know a vehicle {vehicle_id}. Nothing staged."

        # Rule 6, checked at the last moment it can still be cheap to fix. The VEHICLE form:
        # `built` here is a get_vehicle_info reply, and the build-reply form reads fields no
        # vehicle carries, so it would have refused to dispatch every train ever built.
        if not rules.vehicle_carries_cargo(built):
            return (
                f"Error: train {vehicle_id} has no wagons carrying anything, so dispatching it "
                "would run an empty locomotive up and down the line for the rest of the run. "
                "Sell it and build another with plan_buy_train, which couples wagons in the "
                "same call and reports how many attached."
            )

        stations = [
            intent["producer"].get("station_id") if isinstance(intent.get("producer"), dict) else None,
            intent["consumer"].get("station_id") if isinstance(intent.get("consumer"), dict) else None,
        ]
        stations = [s for s in stations if s is not None]
        if len(stations) != 2:
            route = _route_for(sly_data, intent["pair_id"])
            stations = list((route or {}).get("stations") or [])
        if len(stations) != 2:
            return (
                f"Error: {intent['pair_id']} does not have two known station ids yet. Run "
                "confirm_rail_route first; it reads them from the game and records them."
            )

        actions = [
            envelope.action(
                "add_order", vehicle_id=int(vehicle_id),
                station_id=int(stations[0]), order_flags=NO_FLAGS,
            ),
            envelope.action(
                "add_order", vehicle_id=int(vehicle_id),
                station_id=int(stations[1]), order_flags=NO_FLAGS,
            ),
            envelope.action("start_vehicle", vehicle_id=int(vehicle_id)),
        ]
        problems = envelope.check(actions)
        if problems:
            return f"Error: the dispatch cannot be staged as written: {'; '.join(problems)}"

        plan = Plan(sly_data)
        waiting = plan.add(*actions)
        return {
            "staged": len(actions),
            "waiting_in_plan": waiting,
            "train": {
                "vehicle_id": int(vehicle_id), "name": built.get("name"),
                "carries": built.get("capacity_by_cargo"),
                "between": stations,
            },
            "plan": plan.describe(),
            "next": (
                "commit_plan submits all three in one day. The orders carry no flags on "
                "purpose: neither station has produced anything yet, so a train told to wait "
                "for a full load waits forever while looking busy at a platform. Give the "
                "route a few hundred days before judging it; rail pays back slowly."
            ),
        }


def _route_for(sly_data: dict[str, Any], pair_id: str) -> dict[str, Any] | None:
    try:
        from agents.neuro_san.coded_tools.ns import constants as key  # noqa: PLC0415
    except ImportError:
        from ns import constants as key  # noqa: PLC0415
    for route in sly_data.get(key.ROUTES) or []:
        if _same(str(route.get("pair_id") or ""), pair_id):
            return route
    return None


def _same(one: str, other: str) -> bool:
    return "".join(one.split()).lower() == "".join(other.split()).lower()
