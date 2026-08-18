"""One thing, in full, without pulling the whole world into the chat.

The escape hatch behind the compact reports. read_situation is about 25 lines on purpose, so
when a decision turns on one station's cargo acceptance or one vehicle's exact orders, the
alternative used to be dumping /state/full and spending the context on four hundred lines
describing everything else.

It resolves by NAME as well as by id, which is the part that matters. A model can read
"Hondinghall Airport" off a report and ask about it; it cannot know that Hondinghall is
station 0, and a model asked for an id invents one. Measured on the same failure that runs
this whole rule: 35 purchases refused for engine ids nothing had ever returned.

A vehicle's orders are resolved to station names here rather than left as the tile numbers
the game reports, because "goes to 47659 then 14281" is not an answer to "where does this
aircraft fly".

Named inspect_entity rather than inspect because neuro-san puts AGENT_TOOL_PATH on
sys.path, and a module called inspect there SHADOWS the standard library's inspect for the
whole process. Measured: importing the package that way broke leaf_common with a circular
import of logging, which reads as a neuro-san fault and is not one. The agent-facing tool
is still named inspect in the registry; only the file name matters.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
from neuro_san.interfaces.coded_tool import CodedTool

try:
    from agents.neuro_san.coded_tools.ns import observation as obs
    from agents.neuro_san.coded_tools.ns.gateway import NttdGateway
except ImportError:
    from ns import observation as obs
    from ns.gateway import NttdGateway

KINDS = ("station", "vehicle", "town")

# How many names to offer back when nothing matched. The list is the retry prompt: a refusal
# that does not say what WOULD have worked gets the same wrong guess again.
NAMES_OFFERED = 12


class Inspect(CodedTool):
    """The full record for one station, vehicle or town, found by name or by id."""

    async def async_invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> Any:
        kind = str(args.get("what") or "").strip().lower().rstrip("s")
        which = str(args.get("which") or "").strip()
        if kind not in KINDS:
            return f"Error: what must be one of {list(KINDS)}, not {args.get('what')!r}."
        if not which:
            return "Error: which is empty. Give a name as it appears on a report, or an id."

        try:
            gateway = NttdGateway(sly_data)
            world = await obs.world(gateway, sly_data)
        except ValueError as missing:
            return f"Error: {missing}. The runner supplies both; do not invent them."
        except httpx.HTTPError as unreachable:
            return f"Error: nttd did not answer: {unreachable}. Do not retry more than once."

        records = _population(kind, world)
        found = _find(records, which)
        if found is None:
            return _nothing_matched(kind, which, records)

        lines = [f"{kind} {found.get('name') or found.get('id')}:", json.dumps(found, indent=2)]
        lines.extend(_extra(kind, found, world))
        return "\n".join(lines)


def _population(kind: str, world: dict[str, Any]) -> list[dict[str, Any]]:
    """The records of one kind. Stations and vehicles are ours; towns belong to nobody."""
    if kind == "station":
        return obs.our_stations(world)
    if kind == "vehicle":
        return obs.our_vehicles(world)
    return world.get("towns") or []


def _find(records: list[dict[str, Any]], which: str) -> dict[str, Any] | None:
    """By id if it reads as one, else by name: exact first, then a unique partial.

    A partial match is accepted only when it is unique. "Hond" naming two stations is not an
    answer, and picking the first would report on the wrong one silently.
    """
    if which.lstrip("-").isdigit():
        wanted = int(which)
        return next((r for r in records if int(r.get("id", -1)) == wanted), None)

    lowered = which.lower()
    exact = [r for r in records if str(r.get("name") or "").lower() == lowered]
    if exact:
        return exact[0]
    partial = [r for r in records if lowered in str(r.get("name") or "").lower()]
    return partial[0] if len(partial) == 1 else None


def _nothing_matched(kind: str, which: str, records: list[dict[str, Any]]) -> str:
    """A refusal that carries the answer, so the next call is not the same guess."""
    names = sorted(str(r.get("name") or r.get("id")) for r in records)
    if not names:
        return f"Error: there are no {kind}s at all. Nothing has been built or surveyed yet."
    shown = ", ".join(names[:NAMES_OFFERED])
    more = f" and {len(names) - NAMES_OFFERED} more" if len(names) > NAMES_OFFERED else ""
    return (
        f"Error: no {kind} called {which!r}, or the name matched more than one. "
        f"The {kind}s are: {shown}{more}. Use one of those exactly, or its id."
    )


def _extra(kind: str, found: dict[str, Any], world: dict[str, Any]) -> list[str]:
    """The part of the record that is unreadable as the game stores it."""
    if kind == "vehicle":
        by_tile = obs.station_by_tile(world)
        stops = [
            str(by_tile[int(order.get("destination") or 0)].get("name"))
            for order in (found.get("orders") or [])
            if int(order.get("destination") or 0) in by_tile
        ]
        return [f"its orders send it to: {' then '.join(stops)}" if stops else
                "its orders send it to no station of ours"]
    if kind == "station":
        width = int((world.get("game") or {}).get("map_width") or 0)
        if not width:
            return []
        tile = int(found.get("y", 0)) * width + int(found.get("x", 0))
        return [f"its tile is {tile}, which is what an action taking a tile wants"]
    return []
