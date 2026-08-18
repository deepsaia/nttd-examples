"""Repair what is already owned, before spending on anything new.

Most of a well-played run is repair, not construction. Every failure worth catching in eight
hand-played runs was a SINGLE vehicle failing quietly while the fleet count looked healthy: a
train wandering the far corner of the map for 130 days, four aircraft parked in a hangar for
sixty, ships circling the pool their depot sat in.

A vehicle that is not earning is money already spent. Fixing it is always cheaper than buying
its replacement, and until it is fixed a network cannot tell whether its last decision worked.

What this does NOT do is guess. Each repair is the one the game's own report calls for:

- **no orders**: give it the route's two stations. This is the cheap one and it is common,
  because a vehicle bought without orders sits in its depot looking bought.
- **stopped**: start it. Harmless if it was already running, since the action is idempotent.
- **lost**: re-issue its orders, which is what clears a vehicle that has forgotten its way.
- **still stuck after that**: send it to a depot and sell it. Recovering the capital beats
  paying running costs on something that has never moved; measured, selling two stranded
  trains returned enough to fund another route.
"""

from __future__ import annotations

from typing import Any

from neuro_san.interfaces.coded_tool import CodedTool

try:
    from agents.neuro_san.coded_tools.nttd_gateway import NttdGateway
except ImportError:
    from nttd_gateway import NttdGateway


class FixProblems(CodedTool):
    """Repair every vehicle that is not earning, in as few steps as possible."""

    async def async_invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> Any:
        gateway = NttdGateway(sly_data)
        world = await gateway.observe()
        vehicles = world.get("vehicles") or []
        routes = sly_data.get("routes") or []
        if not vehicles:
            return {"fixed": 0, "why": "nothing owned yet"}

        stations = _stations_for(routes)
        batch: list[dict[str, Any]] = []
        acted: list[str] = []
        give_up: list[int] = []

        for vehicle in vehicles:
            problem = _problem(vehicle)
            if not problem:
                continue
            vid = vehicle["id"]
            tried = sly_data.setdefault("repairs", {})
            attempts = int(tried.get(str(vid), 0))

            if attempts >= 2:
                # Tried twice and still not moving. Stop paying to keep it.
                give_up.append(vid)
                acted.append(f"{vehicle.get('type')} {vid}: giving up, selling")
                continue

            tried[str(vid)] = attempts + 1
            if problem in ("no orders", "lost") and stations:
                for station in stations[:2]:
                    batch.append(gateway.envelope(
                        "add_order", vehicle_id=vid, station_id=station, order_flags=0,
                    ))
            batch.append(gateway.envelope("start_vehicle", vehicle_id=vid))
            acted.append(f"{vehicle.get('type')} {vid}: {problem}, re-dispatched")

        # Selling needs the vehicle in a depot first, so it is asked for now and the sale
        # happens on a later pass once it has arrived.
        for vid in give_up:
            batch.append(gateway.envelope("send_to_depot", vehicle_id=vid))
            batch.append(gateway.envelope("sell_vehicle", vehicle_id=vid))

        if not batch:
            return {"fixed": 0, "note": "every vehicle is moving; nothing to repair"}

        # One step for the whole repair, not one per vehicle.
        await gateway.act(batch)
        return {
            "fixed": len(acted),
            "did": acted,
            "next": "let a little time pass, then read the position again: a repair that did "
                    "not take shows up as the same vehicle still not moving",
        }


def _stations_for(routes: list[dict[str, Any]]) -> list[int]:
    """The stations a repaired vehicle can be sent between."""
    for route in reversed(routes):
        if route.get("stations"):
            return list(route["stations"])
    return []


def _problem(vehicle: dict[str, Any]) -> str:
    """What is wrong with this vehicle, in the game's own words where it has them."""
    if vehicle.get("lost"):
        return "lost"
    if vehicle.get("idle_reason"):
        return str(vehicle["idle_reason"])
    if not (vehicle.get("orders") or vehicle.get("order_count")):
        return "no orders"
    if not vehicle.get("current_speed") and not vehicle.get("in_depot"):
        return "not moving"
    return ""
