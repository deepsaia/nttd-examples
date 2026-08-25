"""A locomotive and the wagons that make it carry something.

**The vehicle type is the literal "train".** get_engines takes train, road, ship or aircraft,
and anything else answers with trains regardless, so a typo returns a plausible list of the
wrong thing.

**Engines and wagons come back in the same list**, and telling them apart is the whole job.
The list publishes `is_wagon`, so it is read rather than inferred: a run that bought the top of
the list by capacity bought wagons and no engine, and a run that bought by power bought an
engine and hauled nothing, which is rule 6 and is how a rail route earns zero while every
report about it looks healthy.

**The identifier is `id`.** The engine list calls it that. `engine_id` is what a VEHICLE
carries, and reading that name here returns None from every entry, which becomes engine 0 and
a purchase the game refuses for a reason that has nothing to do with the real mistake.

**Both must match the line's rail type**, and this is not a formality. Measured on a 2020 map:
of 40 train engines only 12 were rail type 0, the type `connect_rail` builds by default, and
the rest were monorail and maglev. Choosing the fastest engine there buys a maglev that cannot
enter the track just laid.

**The cargo is handled by refitting rather than by hunting.** A wagon's `cargo_type` is what it
carries out of the box, not the only thing it can carry, so `build_train` takes a `cargo_id`
and refits what it couples. Insisting on a wagon whose default already matches rejects most of
the list and, on maps where nothing matches, refuses to build a train at all for a corridor
that would have worked.
"""

from __future__ import annotations

from typing import Any

from neuro_san.interfaces.coded_tool import CodedTool

try:
    from agents.neuro_san.coded_tools.ns import session
    from agents.neuro_san.coded_tools.ns.gateway import NttdGateway, QueryRefused
    from agents.neuro_san.coded_tools.ns_rail_agent import rail_keys as rail
except ImportError:
    from ns import session
    from ns.gateway import NttdGateway, QueryRefused

    from ns_rail_agent import rail_keys as rail

# The one value get_engines accepts for trains.
TRAIN = "train"


class ChooseTrain(CodedTool):
    """The engine and wagon to build for a corridor, from the game's own list. Free."""

    async def async_invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> Any:
        return await session.guarded(self._choose, args, sly_data)

    async def _choose(
        self, gateway: NttdGateway, args: dict[str, Any], sly_data: dict[str, Any]
    ) -> Any:
        pair_id = str(args.get("pair_id") or "").strip()
        intents = sly_data.get(rail.INTENT) or {}
        intent = next(
            (value for key_id, value in intents.items() if _same(key_id, pair_id)), None,
        )
        if intent is None:
            return (
                f"Error: {pair_id or '(nothing)'} is not a staged corridor, and engine choice "
                "depends on the line's rail type and the cargo it carries. Use a pair_id from "
                "confirm_rail_route."
            )

        rail_type = int(intent.get("rail_type") or sly_data.get(rail.RAIL_TYPE) or 0)
        cargo_id = intent.get("cargo_id")

        try:
            engines: list[dict[str, Any]] = await gateway.query(
                "get_engines", {"vehicle_type": TRAIN},
            ) or []
        except QueryRefused as refused:
            return f"Error: the engine list was refused ({refused}). Nothing was chosen."

        usable = [e for e in engines if _runs_on(e, rail_type)]
        if not usable:
            return (
                f"Error: the game offers no train engine for rail type {rail_type}. Either the "
                "year has none yet, or the line was built in a type nothing runs on."
            )

        locomotives = [e for e in usable if not _is_wagon(e)]
        wagons = [e for e in usable if _is_wagon(e) and _capacity(e) > 0]

        if not locomotives:
            return (
                f"Error: every engine offered for rail type {rail_type} is a wagon, and a "
                "wagon cannot pull itself. Of 40 engines on one measured map only 12 ran on "
                "the default type, so this is usually the line being built in a type the year "
                "has no locomotive for rather than a temporary shortage."
            )
        if not wagons:
            return (
                f"Error: rail type {rail_type} has no wagon with any capacity, so there is "
                "nothing for a locomotive to pull."
            )

        # Most power among locomotives, most capacity among wagons. A first corridor is short
        # of pulling something at all rather than of speed.
        locomotive = max(locomotives, key=lambda e: int(e.get("power") or 0))
        # A wagon that already carries this cargo is preferred over a bigger one that has to be
        # refitted, because a refit that the game declines leaves wagons carrying the wrong
        # thing. Both are offered, and build_train is told the cargo either way.
        wagon = max(wagons, key=lambda e: (_already_carries(e, cargo_id), _capacity(e)))

        return {
            "pair_id": intent["pair_id"],
            "cargo": intent.get("cargo"),
            "cargo_id": cargo_id,
            "engine_id": int(locomotive.get("id") or 0),
            "engine_name": locomotive.get("name"),
            "engine_price": locomotive.get("price"),
            "engine_power": locomotive.get("power"),
            "wagon_id": int(wagon.get("id") or 0),
            "wagon_name": wagon.get("name"),
            "wagon_price": wagon.get("price"),
            "wagon_capacity": _capacity(wagon),
            "wagon_needs_refit": not _already_carries(wagon, cargo_id),
            "rail_type": rail_type,
            "next": (
                "plan_buy_train takes these two ids and builds them as ONE train, coupling "
                "wagons rather than buying a bare locomotive, because a locomotive on its own "
                "runs the route and carries nothing. It passes the cargo too, so the wagons "
                "are refitted on the way out, and the reply says how many actually attached."
            ),
        }


def _runs_on(engine: dict[str, Any], rail_type: int) -> bool:
    """Whether this engine can use the line that was built.

    A locomotive bought for a different rail type cannot enter its own route, and the game
    sells it anyway.
    """
    declared = engine.get("rail_type")
    return declared is None or int(declared) == rail_type


def _is_wagon(engine: dict[str, Any]) -> bool:
    """Whether this entry is a wagon rather than a locomotive.

    `is_wagon` is published by the engine list, so it is read rather than derived. Power is
    the fallback for an entry that omits the flag, on the reasoning that a thing with no power
    cannot pull: a usable second opinion, but not the first one when a fact is available.
    """
    flag = engine.get("is_wagon")
    if isinstance(flag, bool):
        return flag
    return int(engine.get("power") or 0) <= 0


def _capacity(engine: dict[str, Any]) -> int:
    """How much this vehicle holds. The engine list publishes one number, not a table."""
    return int(engine.get("capacity") or 0)


def _already_carries(engine: dict[str, Any], cargo_id: Any) -> bool:
    """Whether this wagon carries the corridor's cargo without being refitted.

    A tie-break rather than a filter. A wagon that has to be refitted is still usable, since
    build_train takes the cargo and refits what it couples; one that does not need refitting is
    simply one fewer thing that can decline.
    """
    if cargo_id is None:
        return False
    declared = engine.get("cargo_type")
    return declared is not None and int(declared) == int(cargo_id)


def _same(one: str, other: str) -> bool:
    return "".join(one.split()).lower() == "".join(other.split()).lower()
