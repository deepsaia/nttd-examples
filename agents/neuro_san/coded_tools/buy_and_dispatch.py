"""Buy vehicles for a route that exists, and leave them running.

**Nothing here is named by the model.** It asks for a number of vehicles on a route; the
engine, the depot and the orders are all resolved from the game. That is not tidiness, it is
the whole point: an earlier version took `engine_id` and `depot_x/y` as arguments and a run
submitted buy_vehicle THIRTY-FIVE times with invented ids, 30, 40, 21, 60, 90, at a hangar
coordinate that was also a guess. Every one was refused with ERR_PRECONDITION_FAILED. A model
asked for an identifier it has no way to obtain will invent one, so it is never asked.

Five failures live in this one recipe, which is why it is one tool and not four actions:

**The engine has to be buyable and it has to fit.** A large aircraft crashes at a small
airport. A maglev cannot run on the plain rail `connect_rail` lays by default, and in this
era the engine list is dominated by maglev and monorail, so picking the fastest gets a train
that cannot move on the track just built.

**Start it once.** `start_vehicle` used to toggle, so a dispatch followed by an explicit
start left the vehicle parked while reporting success both times.

**Full load parks a vehicle on a slow source**, so orders take what is there.

**Buying near the end is buying nothing.** A vehicle needs roughly 120 days to return its
price; bought later it is cash turned into a depreciating asset.

**Buying before there is anywhere to go** is the failure this tool was added to prevent.
"""

from __future__ import annotations

from typing import Any

from neuro_san.interfaces.coded_tool import CodedTool

try:
    from agents.neuro_san.coded_tools.nttd_gateway import NttdGateway
except ImportError:
    from nttd_gateway import NttdGateway

# Roughly what a vehicle needs to return its price at the rates measured in play.
DAYS_TO_PAY_BACK = 120

# Aircraft small enough not to crash at a commuter field.
SMALL_PLANE = 1

_VEHICLE_TYPE = {"air": "aircraft", "road": "road", "rail": "rail", "water": "ship"}


class BuyAndDispatch(CodedTool):
    """Vehicles for the route just built, ordered between its two ends and running."""

    async def async_invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> Any:
        gateway = NttdGateway(sly_data)
        # Which route to buy for. Growth is adding vehicles to a route that is already
        # carrying, so an existing one can be named by its town; otherwise the newest.
        routes = sly_data.get("routes") or []
        route = sly_data.get("route") or {}
        wanted_town = args.get("town")
        if wanted_town:
            for candidate in routes:
                if wanted_town in (candidate.get("towns") or []):
                    route = candidate
                    break
        if not route.get("stations"):
            return {
                "bought": 0,
                "why": "no route yet. Call build_route first: a vehicle with nowhere to go "
                       "earns nothing and still costs running money.",
            }

        position = sly_data.get("position") or {}
        days_left = position.get("days_left")
        if days_left is not None and days_left < DAYS_TO_PAY_BACK:
            return {
                "bought": 0,
                "why": f"{days_left} days left and a vehicle needs about {DAYS_TO_PAY_BACK} "
                       "to return its price. Holding cash scores more.",
            }

        mode = str(route.get("mode") or "air")
        wanted = max(1, min(int(args.get("count") or 1), 4))
        engine = await _engine_for(gateway, mode, route)
        if mode == "rail" and not route.get("wagon_id"):
            wagon = await _wagon_for(gateway, route)
            if wagon is None:
                return {"bought": 0, "why": "no wagon that carries this cargo"}
            route["wagon_id"] = wagon["id"]
        if engine is None:
            return {"bought": 0, "why": f"no buyable {_VEHICLE_TYPE.get(mode, mode)} engine"}

        depot = route.get("depot")
        if not depot:
            return {"bought": 0, "why": "the route has no depot or hangar to buy into"}

        # ONE step for every purchase rather than one step each. A step is a game day, so
        # buying four vehicles one at a time spent four days of a 366 day run on paperwork.
        before = {v["id"] for v in (await gateway.query("get_vehicles") or [])}
        bought = await gateway.act([
            _purchase(gateway, mode, engine, depot, route)
            for _ in range(wanted)
        ])
        refused = [r.get("error") for r in bought if r.get("status") != "success"]
        after = [v for v in (await gateway.query("get_vehicles") or []) if v["id"] not in before]
        if not after:
            return {"bought": 0, "why": refused[0] if refused else "nothing was bought",
                    "engine_tried": engine["name"]}

        # Orders and starts for every new vehicle, also in one step.
        stations = route["stations"][:2]
        batch: list[dict[str, Any]] = []
        for vehicle in after:
            for station in stations:
                batch.append(gateway.envelope(
                    "add_order", vehicle_id=vehicle["id"], station_id=station, order_flags=0,
                ))
            batch.append(gateway.envelope("start_vehicle", vehicle_id=vehicle["id"]))
        await gateway.act(batch)

        return {
            "bought": len(after),
            "engine": engine["name"],
            "vehicles": [v["id"] for v in after],
            "refused": refused,
            "next": "let time pass, then read the position: a vehicle that is not moving is "
                    "the thing to fix before buying more",
        }


async def _wagon_for(gateway: NttdGateway, route: dict) -> dict | None:
    """A wagon for what this route carries, biggest first.

    Chosen rather than guessed for the same reason as the engine: an id the model invents is
    an id the game refuses.
    """
    engines = await gateway.query("get_engines", {"vehicle_type": "rail"}) or []
    wanted = int(route.get("cargo_id") or 0)
    wagons = [
        e for e in engines
        if e.get("is_wagon") and (e.get("cargo_type") in (wanted, None) or wanted == 0)
    ]
    if not wagons:
        return None
    return max(wagons, key=lambda e: e.get("capacity") or 0)


def _purchase(
    gateway: NttdGateway, mode: str, engine: dict, depot: dict, route: dict
) -> dict[str, Any]:
    """One purchase, in the form the mode requires.

    Rail is not bought, it is ASSEMBLED. buy_vehicle gives a locomotive on its own, and
    buying wagons separately half worked: the loco and one wagon appeared, three more failed
    and nothing was attached. build_train takes the engine, the wagon, how many and the cargo
    and produces a whole train.
    """
    if mode != "rail":
        return gateway.envelope(
            "buy_vehicle", engine_id=engine["id"], depot_x=depot["x"], depot_y=depot["y"],
        )
    return gateway.envelope(
        "build_train",
        engine_id=engine["id"],
        wagon_id=int(route.get("wagon_id") or 0),
        num_wagons=4,
        depot_x=depot["x"], depot_y=depot["y"],
        cargo_id=int(route.get("cargo_id") or 0),
    )


async def _engine_for(gateway: NttdGateway, mode: str, route: dict) -> dict | None:
    """A engine the game will actually sell, that fits what was built.

    get_engines only returns what is buildable now, so the id comes from there and never
    from a guess. The filtering after that is what stops a vehicle being bought that cannot
    use the thing it was bought for.
    """
    engines = await gateway.query(
        "get_engines", {"vehicle_type": _VEHICLE_TYPE.get(mode, "aircraft")},
    ) or []
    usable = [e for e in engines if not e.get("is_wagon")]

    if mode == "air" and not route.get("takes_big_planes", True):
        # A large aircraft at a commuter field crashes, with no warning and no refusal.
        usable = [e for e in usable if e.get("plane_type") == SMALL_PLANE]
    if mode == "rail":
        # Only what can run on the track that was actually laid.
        laid = int(route.get("rail_type") or 0)
        usable = [e for e in usable if e.get("rail_type") == laid]

    if not usable:
        return None
    # Capacity per unit of running cost: what it carries against what it costs to keep.
    return max(usable, key=lambda e: (e.get("capacity") or 0) / max(e.get("running_cost") or 1, 1))
