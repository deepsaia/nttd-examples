"""Where the company stands, computed rather than described.

A model asked to read numbers and summarise them is the worst use of one here: it costs a
call per step, varies run to run, and can get arithmetic wrong in ways nothing catches.
Everything on this report is computed. The model's job starts after it.

The fields exist because each one answers a question that went wrong in play:

- `days_left` because a vehicle bought too late cannot pay for itself. Aircraft take roughly
  190 days to return their price, so buying one with 60 days to go converts cash into a
  depreciating asset and nothing else.
- `loan_costs_score` because the rating's loan component is `250,000 - current_loan`. A run
  borrowed to the 300,000 ceiling forfeits all of it, and no amount of good play recovers it.
- `problems` because every failure worth catching was a SINGLE vehicle failing quietly while
  the fleet count looked healthy.
"""

from __future__ import annotations

from typing import Any

from neuro_san.interfaces.coded_tool import CodedTool

try:
    # Loaded as part of this repository, which is how the tests import it.
    from agents.neuro_san.coded_tools.nttd_gateway import NttdGateway
except ImportError:
    # Loaded by neuro-san from AGENT_TOOL_PATH, where these modules are siblings and the
    # package above them is not on the path. Both spellings are needed because
    # AGENT_TOOL_PATH_ONLY=true deliberately stops a tool resolving from anywhere on
    # PYTHONPATH, which is what keeps a `class` reference in a HOCON from reaching
    # arbitrary code.
    from nttd_gateway import NttdGateway

# The rating's loan component is 250,000 minus the loan, clamped at zero.
LOAN_SCORES_NOTHING_ABOVE = 250_000


class ReadPosition(CodedTool):
    """Money, fleet, stations and what is going wrong, as computed facts."""

    async def async_invoke(self, args: dict[str, Any], sly_data: dict[str, Any]) -> Any:
        gateway = NttdGateway(sly_data)
        world = await gateway.observe()

        company = (world.get("companies") or [{}])[0]
        vehicles = world.get("vehicles") or []
        stations = world.get("stations") or []
        game = world.get("game") or {}

        # From the game, which is the only thing that knows. This used to read a budget out
        # of sly_data that nothing ever put there, so days_left was always None and the
        # payback guard it exists for could never fire.
        # Zero total means the run is not bounded by days at all, which is a different
        # thing from having none left. Testing the remaining count for truthiness conflated
        # them, so the last day of a run reported "no horizon" and the payback guard that
        # exists for exactly that moment stood down.
        days_left = (
            int(game.get("game_days_remaining") or 0)
            if int(game.get("game_days_total") or 0)
            else None
        )

        loan = int(company.get("loan") or 0)
        report = {
            "money": company.get("money"),
            "loan": loan,
            "loan_costs_score": loan > LOAN_SCORES_NOTHING_ABOVE,
            "rating": company.get("performance_rating"),
            "cargo_delivered": company.get("cargo_delivered_total"),
            "days_left": days_left,
            "stations": len(stations),
            "vehicles": len(vehicles),
            "problems": _problems(vehicles, stations),
        }
        sly_data["position"] = report
        return report


def _problems(vehicles: list[dict], stations: list[dict]) -> list[str]:
    """Everything wrong that a count would hide, in plain words.

    A fleet of nine and a flat profit line is what a lost train, a plane parked in its
    hangar and a ship circling its own pool all look like from outside.
    """
    found: list[str] = []
    for vehicle in vehicles:
        name = f"{vehicle.get('type', 'vehicle')} {vehicle.get('id')}"
        if vehicle.get("lost"):
            found.append(f"{name} is lost")
        elif vehicle.get("idle_reason"):
            found.append(f"{name}: {vehicle['idle_reason']}")
        elif not (vehicle.get("orders") or vehicle.get("order_count")):
            found.append(f"{name} has no orders")
        elif not vehicle.get("current_speed") and not vehicle.get("in_depot"):
            found.append(f"{name} is not moving")

    if stations and not vehicles:
        found.append("stations built but nothing bought to serve them")
    for station in stations:
        waiting = sum(int(c.get("waiting") or 0) for c in (station.get("cargo_waiting") or []))
        if waiting > 200:
            found.append(f"{station.get('name')} has {waiting} waiting: under-served")
    return found
