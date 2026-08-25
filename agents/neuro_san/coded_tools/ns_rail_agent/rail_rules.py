"""What rail costs a run to learn, as constants and checks rather than as prose.

Every rule here was paid for. Two hand-played rail runs scored 17 and 1 against 173 for air,
and none of the difference was strategy: it was six ways for a rail route to look finished and
carry nothing. A prompt asking a model to remember them varies run to run and nothing catches
it when it forgets, so they live here, where they are enforced.

**Rail is not air with track.** An air corridor is two airports and nothing between them, so
it is one batch and it either built or it did not. A rail corridor is two stations, a line
between them, and a depot joined to that line, in that order, each step depending on the last
having actually happened. Success on any one of them is not a route.

The six, in the order they bite:

1. **A station has an axis, and the axis comes from the APPROACH.** This is the one that cost
   three separate rail sessions. Taking the nearest spot and accepting whatever orientation it
   offered built a station whose platform met the arriving track side-on: a train cannot enter
   a platform sideways, and `trace_route` from it reached exactly 3 tiles, the platform itself,
   isolated. The axis is therefore derived from the line's own heading FIRST, and then a spot
   is asked for that takes it. Measured: deriving the axis that way produced a line that
   verified first try, where siting by distance had failed three different ways, and of 14
   spots offered at each end 8 and 7 faced the needed axis. The constraint is cheap once asked.

1b. **`valid_directions` is not `reachable_directions`.** The finder reports both and they are
   different questions: the first says the platform FITS, the second says a train could reach
   it. The engine's own note: a spot offered with BOTH orientations valid had neither entry
   usable along x, a town building and a road in the way, and one usable along y. Building on
   the first valid one made a station no train could ever enter and the only repair was to
   demolish it. So an axis must be in both lists, and an empty `reachable_directions` means
   the footprint fits and the station would be useless.

2. **The finder dry-runs ONE platform of THREE tiles.** That is the footprint the game agreed
   to. `build_rail_station` defaults to two platforms of five, which needs ground the finder
   never checked, and the refusal reads "ground occupied" at a tile the finder just called
   clear. A first live run failed exactly there: good tiles, then refused on both.

3. **Track comes after both stations, and needs hints.** `connect_rail` takes the platform
   tiles as `from_hint`/`to_hint` so the line joins the platforms rather than merely reaching
   them. It reports partial builds, and one gap means no route, so the reply is read rather
   than assumed.

4. **The depot comes after the track.** `find_rail_depot_spot` looks for a tile adjacent to
   existing rail, so asked before the line exists it correctly returns nothing. That is not an
   error to work around; it is the wrong order.

5. **Building a depot does not connect it.** The neighbouring track needs a curve facing the
   entrance, which is what `connect_depot` supplies and what `connect_rail` cannot, because it
   lays rail on both endpoints and so fails against the depot itself.

6. **A locomotive on its own carries nothing.** This is the one that earns zero while looking
   complete. `buy_vehicle` gives an engine; hauling cargo needs wagons coupled to it, which is
   `build_train`, and its reply says how many actually attached.

One rule that is not about building: **leave the order flags off**. A station only starts
producing once a vehicle has visited it, so a train told to wait for a full load sits in an
empty station forever and the route never starts.
"""

from __future__ import annotations

from typing import Any, Final

# What the finder dry-runs, and therefore the only footprint the game has agreed to. Both are
# passed explicitly on every build for rule 2.
PLATFORMS: Final = 1
PLATFORM_LENGTH: Final = 3

# The platform axes build_rail_station understands. The GameScript's own arithmetic defines
# them: for direction 0 it steps the footprint by (1, 0) and for direction 1 by (0, 1), so 0
# runs the platforms along x and 1 along y. That is the fact the approach heading is matched
# against, and it is read from the engine rather than from the compass words in the manifest.
AXIS_ALONG_X: Final = 0
AXIS_ALONG_Y: Final = 1
AXES: Final = (AXIS_ALONG_X, AXIS_ALONG_Y)

# The finder's two orientation lists, which answer different questions. Named because reading
# the wrong one is rule 1b and it is invisible: both are lists of the same two integers.
FITS_HERE: Final = "valid_directions"
TRAIN_CAN_ENTER: Final = "reachable_directions"

# Rail pays over distance, and a corridor shorter than this is a road route wearing rails: the
# haul is too short to cover a locomotive against what a lorry would have cost. Measured only
# loosely, so it is a floor to rank by rather than a refusal.
SHORTEST_WORTH_RAILS: Final = 20

# Beyond this a single train spends its life in transit and the line earns once a lap. A cap on
# what to RANK, not on what is possible.
LONGEST_WORTH_ONE_TRAIN: Final = 120

# How many wagons to couple by default. Three fills a three-tile platform, which is rule 2's
# footprint: a train longer than its platform does not load fully, so the two numbers are the
# same number and are written as such.
WAGONS: Final = PLATFORM_LENGTH


def station_footprint() -> dict[str, int]:
    """The station arguments every build passes, so no caller can default them away."""
    return {"num_platforms": PLATFORMS, "platform_length": PLATFORM_LENGTH}


def axis_for_approach(one_x: int, one_y: int, other_x: int, other_y: int) -> int:
    """The platform axis a line between these two points will arrive on. Rule 1.

    The dominant delta decides it: a corridor that is mostly east-west arrives along x, so the
    platform has to run along x for the track to meet its END rather than its side. Ties go to
    x, which is arbitrary and has to be, since a perfectly diagonal corridor arrives at 45
    degrees and neither axis is the answer; the trace after the build is what settles those.

    This is deliberately computed from the corridor and not read off a spot. Reading it off a
    spot is siting by distance and then accepting whatever orientation came with it, which is
    what built a platform no train could enter.
    """
    return AXIS_ALONG_X if abs(one_x - other_x) >= abs(one_y - other_y) else AXIS_ALONG_Y


def spot_takes_axis(spot: dict[str, Any], axis: int) -> tuple[bool, str]:
    """Whether this spot can be built on the axis the corridor needs. Rule 1b.

    BOTH lists have to contain it. `valid_directions` alone means the footprint fits somewhere
    a train may not be able to enter; `reachable_directions` is the one to build on, and an
    empty list means this spot is unusable however good it looks.

    The reason is returned rather than just a no, because "fits but nothing can enter" and
    "does not fit at all" call for different next moves: the first wants a different spot near
    the same industry, the second may want a different industry.
    """
    fits = _axes_in(spot, FITS_HERE)
    enterable = _axes_in(spot, TRAIN_CAN_ENTER)

    if not enterable:
        return False, (
            "the finder reports no orientation a train could enter here, so the footprint "
            "fits and the station would be unusable"
        )
    if axis not in fits:
        return False, f"the platform does not fit along {_named(axis)} here"
    if axis not in enterable:
        return False, (
            f"the platform fits along {_named(axis)} but no train could enter it on that "
            "axis; something is blocking both ends"
        )
    return True, ""


def _axes_in(spot: dict[str, Any], field: str) -> list[int]:
    """The axis numbers in one of the finder's orientation lists, ignoring anything else."""
    offered = spot.get(field)
    if not isinstance(offered, list):
        return []
    return [value for value in offered if isinstance(value, int) and value in AXES]


def _named(axis: int) -> str:
    return "x" if axis == AXIS_ALONG_X else "y"


def build_carries_cargo(built: dict[str, Any]) -> bool:
    """Whether what build_train just built can actually haul something. Rule 6.

    Reads a BUILD REPLY, and only a build reply. `wagons_attached` and `capacity_by_cargo` are
    fields of `build_train`'s result; no vehicle carries either of them. Handed a vehicle
    instead, both read as absent and every train in the fleet answers no, which turns a healthy
    fleet into a list of hopeless ones and then sells it. Hence two functions with two names.
    """
    if int(built.get("wagons_attached") or 0) <= 0:
        return False
    capacity = built.get("capacity_by_cargo") or {}
    if isinstance(capacity, dict):
        return any(int(amount or 0) > 0 for amount in capacity.values())
    return True


def vehicle_carries_cargo(info: dict[str, Any]) -> bool:
    """Whether a train already in the fleet has room for anything. Rule 6, live.

    Reads `cargo` from get_vehicle_info, which the GameScript builds by walking every cargo
    type and appending an entry only where capacity is above zero. So an EMPTY list is the
    fact itself: this consist can hold nothing, which is a locomotive whose wagons never
    attached. It runs its route, reports a position and a profit line, and earns zero.

    Absent rather than empty means the reply did not carry the field, which is a different
    thing and must not read as a fault: a query that failed would otherwise condemn the fleet.
    """
    loads = info.get("cargo")
    if not isinstance(loads, list):
        return True
    return any(int(entry.get("capacity") or 0) > 0 for entry in loads)


def line_is_whole(reply: dict[str, Any]) -> tuple[bool, str]:
    """Whether connect_rail actually laid a continuous line. Rule 3.

    Three published fields, and `gaps` is the one that matters most, because it is the one a
    reply can report while `failed` is empty. The engine says it in those words: "every segment
    built, but the line is not continuous: 1 of 5 have no through connection, first at
    (175,231)". Every segment built. A tool reading only `failed` calls that a finished line,
    and one gap means no route at all.
    """
    failed = reply.get("failed") or []
    if failed:
        return False, f"{len(failed)} segment(s) of the line were not laid"

    gaps = reply.get("gaps") or []
    if gaps:
        return False, (
            f"every segment built but the line is not continuous: {len(gaps)} have no through "
            f"connection, first at {gaps[0]}"
        )

    status = str(reply.get("status") or "").lower()
    if status and status not in ("ok", "success", "built", "complete"):
        return False, f"connect_rail reported status '{status}' rather than a finished line"
    return True, ""
