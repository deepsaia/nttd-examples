"""Whether the corridor that was committed is actually a route, and what is missing if not.

**A build returning success is not a working route**, and on rail that gap is wider than
anywhere else. `connect_rail` keeps whatever segments it managed and reports the rest, so a
line with one gap in the middle reports as built, draws on the map, and carries nothing. Every
downstream report looks healthy: the stations exist, the track exists, the train exists, and
the cargo sits at the platform.

So this asks the game what is there, compares it against what plan_build_corridor wrote down,
and says which of the four parts is missing. It is the tool that decides whether a corridor is
ready for a train, and buying one before it says so is how a run spends its money on a
locomotive that never moves.

Free: it costs no game day. There is no reason not to call it after every corridor commit.
"""

from __future__ import annotations

from typing import Any

from neuro_san.interfaces.coded_tool import CodedTool

try:
    from agents.neuro_san.coded_tools.ns import constants as key
    from agents.neuro_san.coded_tools.ns import session
    from agents.neuro_san.coded_tools.ns.gateway import NttdGateway, QueryRefused
    from agents.neuro_san.coded_tools.ns_rail_agent import rail_keys as rail
except ImportError:
    from ns import constants as key
    from ns import session
    from ns.gateway import NttdGateway, QueryRefused

    from ns_rail_agent import rail_keys as rail

# How close a station has to be to where it was asked for to count as the one that was meant.
# A station built a tile or two off is still the station; one built ten tiles away is serving
# something else, which is the air network's catchment failure in rail form.
NEAR_ENOUGH = 4


class ConfirmRailRoute(CodedTool):
    """What of the intended corridor actually exists, and what is missing. Free."""

    async def async_invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> Any:
        return await session.guarded(self._confirm, args, sly_data)

    async def _confirm(
        self, gateway: NttdGateway, args: dict[str, Any], sly_data: dict[str, Any]
    ) -> Any:
        intents = sly_data.get(rail.INTENT) or {}
        if not intents:
            return (
                "Error: no corridor has been staged, so there is nothing to confirm. "
                "plan_build_corridor writes down what it meant to build; this reads it back."
            )

        pair_id = str(args.get("pair_id") or "").strip()
        if pair_id:
            wanted = {k: v for k, v in intents.items() if _same(k, pair_id)}
            if not wanted:
                return f"Error: {pair_id} was never staged. Staged: {', '.join(intents)}."
        else:
            wanted = intents

        try:
            stations: list[dict[str, Any]] = await gateway.query("get_stations") or []
        except QueryRefused as refused:
            return f"Error: the station list was refused ({refused}). Nothing was confirmed."

        reports = []
        for key_id, intent in wanted.items():
            report = await self._one(gateway, intent, stations, sly_data)
            intent["stage"] = report["stage"]
            intents[key_id] = intent
            reports.append(report)

        ready = [r for r in reports if r["stage"] == "ready_for_a_train"]
        return {
            "corridors": reports,
            "ready_for_a_train": [r["pair_id"] for r in ready],
            "next": (
                "A corridor is ready only when both stations exist, the line joins them and a "
                "depot is connected to it. Anything short of that is not a route, and a train "
                "bought for it will sit. plan_add_depot finishes one that has its line."
                if not ready else
                "choose_train, then plan_buy_train for a corridor listed in "
                "ready_for_a_train. Remember that a locomotive alone carries nothing."
            ),
        }

    async def _one(
        self, gateway: NttdGateway, intent: dict[str, Any],
        stations: list[dict[str, Any]], sly_data: dict[str, Any],
    ) -> dict[str, Any]:
        """One corridor, part by part, in the order each part depends on the last."""
        missing: list[str] = []

        ends = {}
        for role in ("producer", "consumer"):
            wanted = intent[role]
            found = _station_near(stations, int(wanted["x"]), int(wanted["y"]))
            if found is None:
                missing.append(
                    f"no station within {NEAR_ENOUGH} tiles of the {role} end at "
                    f"({wanted['x']},{wanted['y']})"
                )
            ends[role] = found

        if missing:
            return _report(intent, "stations_missing", missing, ends)

        joined, why = await self._line_joins(gateway, ends)
        if not joined:
            return _report(intent, "line_not_joined", [why], ends)

        depot = intent.get("depot")
        if not depot:
            return _report(
                intent, "no_depot",
                ["the line is laid but has no depot, and a train can only be built in one"],
                ends,
            )

        # Asked of the game, every time, and never taken from the record. plan_add_depot writes
        # `connected: False` when it stages the pair, and nothing else can honestly flip it: a
        # staged connect_depot may have been refused, and the commit reply for it is not in
        # hand here. Trusting the flag meant it stayed False forever and no corridor ever
        # reached ready_for_a_train, which is a build loop that can never finish.
        joined, why = await self._depot_joins(gateway, depot, ends["producer"])
        depot["connected"] = joined
        if not joined:
            return _report(intent, "depot_not_connected", [why], ends)

        # Recorded as a route only once every part is there, because route_report and the
        # ranker both read this list and a half-built corridor in it reads as served.
        _remember_route(sly_data, intent, ends)
        return _report(intent, "ready_for_a_train", [], ends)

    async def _line_joins(
        self, gateway: NttdGateway, ends: dict[str, Any]
    ) -> tuple[bool, str]:
        """Whether the track between the two platforms actually joins up.

        `trace_route` walks the pieces and models the geometry: a curve that does not meet, or
        track joining side-on, is rejected rather than counted. Asked of the game rather than
        inferred from the build reply, because the build reply is the thing that lies. A
        partial connect_rail keeps what it laid and reports the rest, so a line with a gap in
        the middle still answers with a list of segments it built.

        **A yes here is necessary, not sufficient**, and the engine's own docs say so: it does
        not model whether a platform can be entered on the approach axis, whether a train
        would have to reverse in a dead end, train length against platform length, or signals.
        The exact answer only exists after dispatch, as `lost` on get_vehicle_info, which is
        what rail_health_check reads. So this gate stops a train being bought for a line with
        a hole in it; it does not promise the train will run.
        """
        producer, consumer = ends["producer"], ends["consumer"]
        try:
            answer = await gateway.query("trace_route", {
                "from_x": int(producer["x"]), "from_y": int(producer["y"]),
                "to_x": int(consumer["x"]), "to_y": int(consumer["y"]),
                "transport_type": "rail",
            })
        except QueryRefused as refused:
            # Not assumed joined. A corridor wrongly called ready buys a locomotive that sits
            # for the rest of the run; one wrongly called broken costs another confirm call.
            return False, (
                f"the track could not be traced ({refused}), so the line is not assumed to "
                "join. Inspect the two station tiles and the track between them before buying"
            )

        if not isinstance(answer, dict):
            return False, "the trace returned nothing readable, so the line is not assumed to join"
        if answer.get("exhausted"):
            return False, (
                "the trace gave up before reaching the far end, which means either a very long "
                "line or a gap; raise max_iterations or inspect the middle"
            )
        if not answer.get("line_exists"):
            reached = answer.get("tiles_reachable")
            return False, (
                "the track does not join the two platforms: a gap was left"
                + (f", with {reached} tiles reachable from the producer end" if reached else "")
            )
        return True, ""


    async def _depot_joins(
        self, gateway: NttdGateway, depot: dict[str, Any], platform: dict[str, Any],
    ) -> tuple[bool, str]:
        """Whether track actually runs from the depot to the line. Rule 5.

        Building a depot does not join it: the neighbouring track needs a curve piece facing
        the entrance, which is what connect_depot supplies. A depot without one reads as a
        depot in every report, and a train built inside it cannot leave.

        Traced FROM the depot tile, which the walk supports: it treats a rail depot tile as
        track, and its first hop tests adjacency rather than a from-through-to triple, because
        a depot has track on only one side. That is a fix the engine carries for exactly this
        case, and it was found by a depot the game called connected whose train then ran the
        route at 107 km/h while the trace said the line did not exist.
        """
        try:
            answer = await gateway.query("trace_route", {
                "from_x": int(depot["x"]), "from_y": int(depot["y"]),
                "to_x": int(platform["x"]), "to_y": int(platform["y"]),
                "transport_type": "rail",
            })
        except QueryRefused as refused:
            return False, (
                f"the depot could not be traced to the line ({refused}), so it is not assumed "
                "joined. A train built in an unjoined depot cannot leave it"
            )

        if not isinstance(answer, dict):
            return False, "the depot trace returned nothing readable"
        if answer.get("exhausted"):
            return False, (
                "the depot trace gave up before reaching the platform, which on a distance "
                "this short means a gap rather than a long walk"
            )
        if not answer.get("line_exists"):
            return False, (
                "the depot exists but no track runs from it to the line. Building one does "
                "not connect it; connect_depot lays the curve piece that does"
            )
        return True, ""


def _station_near(
    stations: list[dict[str, Any]], x: int, y: int
) -> dict[str, Any] | None:
    """The company's RAIL station nearest this point, within NEAR_ENOUGH tiles.

    `has_rail` is required. The station list holds every station the company owns, of every
    kind, and a bus stop or a dock a few tiles from where a platform was meant to go matches on
    distance alone. Accepting one records a route whose ends are not the ones that were built,
    and every check after this reads that record rather than the map.
    """
    for station in stations:
        if not station.get("has_rail"):
            continue
        if abs(int(station.get("x") or 0) - x) + abs(int(station.get("y") or 0) - y) <= NEAR_ENOUGH:
            return {"station_id": station.get("id"), "name": station.get("name"),
                    "x": station.get("x"), "y": station.get("y")}
    return None


def _report(
    intent: dict[str, Any], stage: str, missing: list[str], ends: dict[str, Any]
) -> dict[str, Any]:
    return {
        "pair_id": intent["pair_id"],
        "cargo": intent.get("cargo"),
        "stage": stage,
        "missing": missing,
        "producer_station": ends.get("producer"),
        "consumer_station": ends.get("consumer"),
    }


def _remember_route(
    sly_data: dict[str, Any], intent: dict[str, Any], ends: dict[str, Any]
) -> None:
    routes = sly_data.setdefault(key.ROUTES, [])
    if any(_same(str(route.get("pair_id") or ""), intent["pair_id"]) for route in routes):
        return
    routes.append({
        "pair_id": intent["pair_id"],
        "cargo": intent.get("cargo"),
        "cargo_id": intent.get("cargo_id"),
        "rail_type": intent.get("rail_type"),
        "stations": [ends["producer"]["station_id"], ends["consumer"]["station_id"]],
        "depot": intent.get("depot"),
    })


def _same(one: str, other: str) -> bool:
    return "".join(one.split()).lower() == "".join(other.split()).lower()
