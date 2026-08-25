"""Replies shaped like the ones nttd really sends, for the rail tool tests.

The point of this module is that the fixtures CANNOT agree with a bug. A fake reply written
freehand is written by the same hand that wrote the tool, so it carries the same wrong idea of
what a field is called and the test passes while the tool is broken. That is not hypothetical:
it is how the spend-reporting tests in this repository passed against code that read the wrong
level of a nested payload.

So every reply here is built through `shaped`, which refuses a field name that is not in FIELDS.
FIELDS is in turn checked against the running game by test_rail_action_surface.py. The loop that
makes closes both ways:

  * a tool that reads a field FIELDS does not list gets None from every fixture, and the
    behaviour test that depends on the value fails
  * a field in FIELDS the engine does not publish fails the surface test

Neither half is much use alone. Together they are the difference between testing the tools and
testing my memory of the game.
"""

from __future__ import annotations

from typing import Any

# Every reply field the rail tools read, per action. Checked against the live manifest by
# test_rail_action_surface.py, and used here as the vocabulary a fixture may use.
FIELDS: dict[str, set[str]] = {
    "get_rail_types": {"available", "build_cost_per_tile", "id", "name"},
    "get_engines": {"id", "name", "price", "power", "capacity", "cargo_type", "is_wagon",
                    "rail_type"},
    "get_industries": {"id", "name", "x", "y", "produces_cargo", "accepts_cargo"},
    "get_stations": {"id", "name", "x", "y", "has_rail"},
    "get_vehicles": {"id", "name", "x", "y", "in_depot", "age", "order_count",
                     "profit_this_year"},
    "get_vehicle_info": {"id", "lost", "cargo", "orders", "in_depot", "name", "x", "y"},
    "get_orders": {"orders"},
    "find_station_spot": {"spots", "x", "y", "distance", "cargo_acceptance",
                          "valid_directions", "reachable_directions"},
    "find_rail_depot_spot": {"x", "y", "depot_direction", "adjacent_track_x",
                             "adjacent_track_y"},
    "trace_route": {"exhausted", "line_exists", "tiles_reachable"},
    "connect_rail": {"failed", "gaps", "status"},
    "build_train": {"wagons_attached", "capacity_by_cargo"},
}


def shaped(action: str, **fields: Any) -> dict[str, Any]:
    """One reply object for `action`, refusing any field name it does not publish."""
    known = FIELDS[action]
    unknown = sorted(set(fields) - known)
    if unknown:
        raise AssertionError(
            f"{action} does not publish {unknown}. It publishes {sorted(known)}. If the game "
            "really does return it, add it to FIELDS and the surface test will confirm."
        )
    return dict(fields)


def spot(x: int, y: int, *, fits: list[int], enterable: list[int], distance: int = 1,
         acceptance: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """One entry of find_station_spot's `spots` list.

    The two orientation lists are separate arguments with no default between them, so a test
    cannot accidentally write a spot where they agree. They disagree in the real game often
    enough that a station was built which had to be demolished.
    """
    return shaped(
        "find_station_spot", x=x, y=y, distance=distance,
        valid_directions=fits, reachable_directions=enterable,
        cargo_acceptance=acceptance or [],
    )


def train(vid: int, *, x: int = 10, y: int = 10, in_depot: bool = False, age: int = 400,
          orders: int = 2, profit: int = 500) -> dict[str, Any]:
    """One entry of the get_vehicles list. Carries no `lost` and no `cargo`: they are not on it."""
    return shaped(
        "get_vehicles", id=vid, name=f"train {vid}", x=x, y=y, in_depot=in_depot,
        age=age, order_count=orders, profit_this_year=profit,
    )


def train_detail(vid: int, *, x: int = 10, y: int = 10, lost: bool = False,
                 capacity: int = 40, in_depot: bool = False,
                 destinations: tuple[int, int] | None = (1, 2)) -> dict[str, Any]:
    """One get_vehicle_info reply, which is the only place `lost` and `cargo` live.

    `capacity` of zero produces an EMPTY cargo list rather than an entry of zero, because that
    is what the GameScript does: it appends an entry only where capacity is above zero, so an
    empty list is how a locomotive with no wagons announces itself.
    """
    orders = [
        {"index": index, "destination": where, "is_goto_station": True}
        for index, where in enumerate(destinations or ())
    ]
    return shaped(
        "get_vehicle_info", id=vid, name=f"train {vid}", x=x, y=y, lost=lost,
        in_depot=in_depot, orders=orders,
        cargo=[{"cargo_id": 0, "capacity": capacity, "loaded": 0}] if capacity else [],
    )
