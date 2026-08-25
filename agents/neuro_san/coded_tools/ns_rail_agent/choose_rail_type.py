"""Which rail technology this company builds with, decided once and then used by everything.

A station, every track segment, the depot and the locomotive all carry a rail type, and a line
built in one with an engine bought in another is a train that cannot enter its own route. The
game numbers them itself and gates them by year, so the number is asked for rather than
assumed, and then cached: it does not change within a year and four tools need it.

Cheapest available that something can actually run on. Cheapest alone is not enough, and this
is the trap the mode is known for: rolling stock is gated by rail type, and on a measured 2020
map only 12 of 40 train engines ran on type 0, the one `connect_rail` builds by default. The
rest were monorail and maglev. A type with no locomotive is a corridor that builds, joins,
confirms, and can never have a train put on it, and nothing about the track says so.

So the engine list is read HERE, before anything is built, and a type is only offered if the
game will sell both a locomotive and a wagon for it. Otherwise the expensive types are faster
and a first corridor is not short of speed, it is short of existing at all; upgrading later is
one `convert_rail` call over a line that is already earning.
"""

from __future__ import annotations

from typing import Any

from neuro_san.interfaces.coded_tool import CodedTool

try:
    from agents.neuro_san.coded_tools.ns import session
    from agents.neuro_san.coded_tools.ns.gateway import NttdGateway, QueryRefused
    from agents.neuro_san.coded_tools.ns_rail_agent import rail_keys as rail
    from agents.neuro_san.coded_tools.ns_rail_agent.choose_train import TRAIN
except ImportError:
    from ns import session
    from ns.gateway import NttdGateway, QueryRefused

    from ns_rail_agent import rail_keys as rail
    from ns_rail_agent.choose_train import TRAIN


class ChooseRailType(CodedTool):
    """The rail type to build with, from what the game says is available. Costs no game day."""

    async def async_invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> Any:
        return await session.guarded(self._choose, args, sly_data)

    async def _choose(
        self, gateway: NttdGateway, args: dict[str, Any], sly_data: dict[str, Any]
    ) -> Any:
        types: list[dict[str, Any]] = await gateway.query("get_rail_types") or []
        available = [entry for entry in types if entry.get("available")]
        if not available:
            return (
                "Error: the game reports no available rail type, so nothing can be built yet. "
                "This is a property of the year the scenario starts in, not something to retry."
            )

        try:
            engines: list[dict[str, Any]] = await gateway.query(
                "get_engines", {"vehicle_type": TRAIN},
            ) or []
        except QueryRefused as refused:
            return (
                f"Error: the engine list was refused ({refused}), and a rail type is not "
                "chosen without knowing what runs on it. Nothing was decided."
            )

        runnable = [entry for entry in available if _has_a_train(engines, entry)]
        if not runnable:
            return (
                "Error: the game sells no locomotive and wagon pair for any available rail "
                f"type. {len(available)} type(s) can be built and none can be run on, which is "
                "a property of the year rather than something to retry."
            )

        # Cheapest per tile among those. A corridor is mostly track, so this is most of the
        # bill, but only among types a train exists for.
        runnable.sort(key=lambda entry: int(entry.get("build_cost_per_tile") or 0))
        chosen = runnable[0]
        sly_data[rail.RAIL_TYPE] = int(chosen.get("id") or 0)

        unusable = [
            entry.get("name") for entry in available if entry not in runnable
        ]
        return {
            "rail_type": int(chosen.get("id") or 0),
            "name": chosen.get("name"),
            "build_cost_per_tile": chosen.get("build_cost_per_tile"),
            "engines_available": _count_for(engines, chosen),
            "also_available": [
                {"rail_type": int(other.get("id") or 0), "name": other.get("name"),
                 "build_cost_per_tile": other.get("build_cost_per_tile"),
                 "engines_available": _count_for(engines, other)}
                for other in runnable[1:]
            ],
            "skipped_no_rolling_stock": unusable,
            "next": (
                "Every station, track segment, depot and engine from here on uses this number, "
                "and the tools take it from the same store, so it does not need passing "
                "around. A faster type is a convert_rail away once a line is earning."
            ),
        }


def _for_type(engines: list[dict[str, Any]], entry: dict[str, Any]) -> list[dict[str, Any]]:
    """The engines that run on this rail type.

    An engine that declares no rail type at all is counted, because the absence is a gap in
    the reply rather than a statement that it runs on nothing, and excluding those would
    report every type as unusable on any build that stopped publishing the field.
    """
    wanted = int(entry.get("id") or 0)
    return [
        engine for engine in engines
        if engine.get("rail_type") is None or int(engine["rail_type"]) == wanted
    ]


def _has_a_train(engines: list[dict[str, Any]], entry: dict[str, Any]) -> bool:
    """Whether this type has both a locomotive and a wagon. One without the other hauls nothing."""
    counts = _count_for(engines, entry)
    return counts["locomotives"] > 0 and counts["wagons"] > 0


def _count_for(engines: list[dict[str, Any]], entry: dict[str, Any]) -> dict[str, int]:
    """How many locomotives and wagons the game will sell for this type, right now."""
    usable = _for_type(engines, entry)
    wagons = sum(1 for engine in usable if engine.get("is_wagon"))
    return {"locomotives": len(usable) - wagons, "wagons": wagons}
