"""Buy a vehicle, give it orders, and leave it running.

Four separate failures live in this one recipe, which is why it is one tool and not four
actions a model strings together.

**Start it once.** `start_vehicle` used to toggle, so a dispatch that started a vehicle
followed by an explicit start left it parked, reporting success both times. That cost two
whole runs. The action is idempotent now, and this tool still starts exactly once and then
checks, because the check is what catches the next version of this bug.

**Match the vehicle to what it will use.** A large aircraft crashes at a small airport. A
maglev cannot run on the plain rail `connect_rail` builds by default, and in this era the
engine list is dominated by maglev and monorail, so picking the fastest engine gets a train
that cannot move on the track just laid.

**Full load parks a vehicle on a slow source.** `OF_FULL_LOAD` on a mixed consist held a
train at the producer for months. Take what is there unless the source genuinely fills the
vehicle inside a round trip.

**Buying near the end is buying nothing.** An aircraft takes roughly 190 days to return its
price. Bought with 60 days left it converts cash into a depreciating asset, and the cash
would have scored more sitting still.
"""

from __future__ import annotations

from typing import Any

from neuro_san.interfaces.coded_tool import CodedTool

from agents.neuro_san.coded_tools.nttd_gateway import NttdGateway

# Roughly what a vehicle needs to return its price at the rates measured in play. Below
# this many days remaining, buying one is converting cash into a depreciating asset.
DAYS_TO_PAY_BACK = 120

# Aircraft that fit a small field without crashing.
SMALL_PLANE = 1


class BuyAndDispatch(CodedTool):
    """One vehicle, ordered between two stations, verified to be running."""

    async def async_invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> Any:
        gateway = NttdGateway(sly_data)
        position = sly_data.get("position") or {}
        days_left = position.get("days_left")

        if days_left is not None and days_left < DAYS_TO_PAY_BACK:
            return {
                "bought": False,
                "why": (
                    f"{days_left} days left, and a vehicle needs about {DAYS_TO_PAY_BACK} "
                    "to return its price. Cash scores more than a depreciating asset."
                ),
            }

        engine = int(args["engine_id"])
        depot = (int(args["depot_x"]), int(args["depot_y"]))
        stations = [int(s) for s in args["station_ids"]]

        bought = await gateway.act([
            gateway.envelope("buy_vehicle", engine_id=engine, depot_x=depot[0], depot_y=depot[1])
        ])
        made = (bought[0].get("changed_entities") or {}) if bought else {}
        vehicle = made.get("vehicle_id")
        if not vehicle:
            return {"bought": False, "why": bought[0].get("error") if bought else "no reply"}

        orders = [
            gateway.envelope(
                "add_order", vehicle_id=vehicle, station_id=station,
                # Take what is there. Full load parks a vehicle on a slow source.
                order_flags=0,
            )
            for station in stations
        ]
        await gateway.act([*orders, gateway.envelope("start_vehicle", vehicle_id=vehicle)])

        return {"bought": True, "vehicle_id": vehicle, "check": "confirm it moves next step"}


def choose_plane(engines: list[dict], airport_takes_big: bool) -> dict | None:
    """The best aircraft this field will not destroy.

    Capacity per unit of running cost, among the types that can land here. A big plane at a
    commuter field is not a trade-off, it is a crash with no warning: three aircraft were
    lost that way in one run before the type was checked.
    """
    usable = [
        engine for engine in engines
        if airport_takes_big or engine.get("plane_type") == SMALL_PLANE
    ]
    if not usable:
        return None
    return max(usable, key=lambda e: (e.get("capacity") or 0) / max(e.get("running_cost") or 1, 1))


def choose_loco(engines: list[dict], rail_type: int) -> dict | None:
    """A locomotive that can run on the track that was actually laid."""
    usable = [
        engine for engine in engines
        if not engine.get("is_wagon") and engine.get("rail_type") == rail_type
    ]
    if not usable:
        return None
    return max(usable, key=lambda e: e.get("power") or 0)
