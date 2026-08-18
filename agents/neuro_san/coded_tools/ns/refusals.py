"""What the game has already refused, said once each.

The measured failure this exists for: one run submitted `buy_vehicle` 35 times with engine
ids 30, 40, 21, 60 and 90, every one invented, every one refused ERR_PRECONDITION_FAILED,
because nothing carried the refusal from one turn to the next and nothing showed the network
its own history.

Deduplicated, with a count, because the fix is the opposite failure. Thirty-five copies of
one error say the same thing once, and printing all of them buries the single new refusal
that actually needs acting on under thirty-four it has already ignored.

Newest first, because the refusal from this turn is the one being reasoned about. The
gateway keeps a bounded ledger, so a refusal that has fallen off the end is genuinely old.

The parameters of the most recent attempt are shown alongside, since the error names what
was wrong with them and the next attempt has to differ in exactly that.
"""

from __future__ import annotations

from typing import Any

from neuro_san.interfaces.coded_tool import CodedTool

try:
    from agents.neuro_san.coded_tools.ns import constants as key
except ImportError:
    from ns import constants as key


class Refusals(CodedTool):
    """The refusal ledger, one line per distinct error rather than one per attempt."""

    async def async_invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> Any:
        ledger = sly_data.get(key.REFUSALS) or []
        if not ledger:
            return (
                "Nothing has been refused. Either nothing has been submitted yet, or "
                "everything submitted worked."
            )

        # Grouped on the action and the error text. The parameters deliberately do not join
        # the key: five purchases refused for five different invented engine ids are one
        # mistake made five times, and keying on params would report them as five.
        grouped: dict[tuple[str, str], dict[str, Any]] = {}
        for position, entry in enumerate(ledger):
            identity = (str(entry.get("action") or "?"), str(entry.get("error") or "refused"))
            group = grouped.setdefault(
                identity, {"count": 0, "newest": -1, "params": {}, "error_name": ""}
            )
            group["count"] += 1
            group["newest"] = position
            group["params"] = entry.get("params") or {}
            group["error_name"] = str(entry.get("error_name") or "")

        ordered = sorted(grouped.items(), key=lambda item: item[1]["newest"], reverse=True)
        lines = [f"{len(ledger)} refusals held, {len(ordered)} distinct, newest first:"]
        for (action, error), group in ordered:
            lines.extend(_entry_lines(action, error, group))
        lines.append(
            "Do not submit any of these again unchanged. The error names what was wrong; "
            "change that and nothing else, or do something different instead."
        )
        return "\n".join(lines)


def _entry_lines(action: str, error: str, group: dict[str, Any]) -> list[str]:
    """One distinct refusal: how often, what it was, and what it was tried with."""
    times = f"x{group['count']}" if group["count"] > 1 else "  "
    named = f" [{group['error_name']}]" if group["error_name"] else ""
    return [
        f"  {times:<4} {action}{named}",
        f"       {error}",
        f"       last tried with: {_as_submitted(group['params'])}",
    ]


def _as_submitted(params: dict[str, Any]) -> str:
    """The parameters as they went to the game, minus the one nobody chose.

    company_id is applied from the token by the server, so showing it invites a model to
    think it is a knob and to try changing it when a refusal has nothing to do with it.
    """
    shown = sorted(
        (name, value) for name, value in (params or {}).items() if name != "company_id"
    )
    return ", ".join(f"{name}={value}" for name, value in shown) or "no parameters"
