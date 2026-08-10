"""Typed reads over one nttd session, for a runner or an LLM's tool list.

Everything here goes through ``NttdClient``, so the participant token travels with every
call and there is no ``company_id`` argument anywhere: the token says which company you
are. The previous version built its own URLs from ``client.base_url`` and took a company
id, which is how it ended up calling routes that had moved.

Two kinds of read, and the difference is worth knowing:

**From nttd's cached world state.** Cheap, and already consistent with the observation
your last step returned.

**From the GameScript**, for questions only the running game can answer, such as where a
depot will actually fit. A round trip into the game, so it costs real time; in stepped
mode the world is paused while it happens, so it costs no game-days.
"""

from __future__ import annotations

from typing import Any

from agents.nttd_client import NttdClient


class NttdTools:
    """Reads scoped to the company your token owns."""

    def __init__(self, client: NttdClient) -> None:
        self.client = client

    # ------------------------------------------------------------------
    # From the cached world state
    # ------------------------------------------------------------------

    def get_towns(self) -> list[dict[str, Any]]:
        """Every town. ``[{id, name, population, x, y, is_city}]``"""
        return self.client.get_state("towns")

    def get_industries(self) -> list[dict[str, Any]]:
        """Every industry. ``[{id, name, type_name, x, y, is_raw, production}]``"""
        return self.client.get_state("industries")

    def get_vehicles(self) -> list[dict[str, Any]]:
        """Your vehicles. ``[{id, type, name, profit_this_year, in_depot, orders}]``"""
        return self.client.get_state("vehicles")

    def get_stations(self) -> list[dict[str, Any]]:
        """Your stations. ``[{id, name, x, y, cargo_waiting, cargo_rating}]``"""
        return self.client.get_state("stations")

    def get_compact(self) -> dict[str, Any]:
        """A small view of the whole world, for a loop that polls often."""
        return self.client.get_compact_snapshot()

    def get_finance(self) -> dict[str, Any]:
        """Your balance, loan, income and value."""
        companies = self.client.get_full_state().get("companies") or []
        return companies[0] if companies else {}

    # ------------------------------------------------------------------
    # From the GameScript
    # ------------------------------------------------------------------

    def get_engines(self, vehicle_type: int = 1) -> list[dict[str, Any]]:
        """Buyable engines of one type. What is buyable is gated by the game year.

        vehicle_type: 0 rail, 1 road, 2 water, 3 air. Entries carry is_wagon and
        rail_type, so check both: a wagon bought as a locomotive hauls nothing, and an
        engine of the wrong rail_type cannot run on the track you laid.
        """
        return self._gs("get_engines", {"vehicle_type": vehicle_type})

    def scan_town_area(self, town_id: int, radius: int = 15) -> dict[str, Any]:
        """What is buildable around a town, what is built, and where the roads run."""
        return self._gs("scan_town_area", {"town_id": town_id, "radius": radius})

    def get_tile_info(self, tile: int) -> dict[str, Any]:
        """Terrain and infrastructure on one tile."""
        return self._gs("get_tile_info", {"tile": tile})

    # ------------------------------------------------------------------
    # Finders: where something will actually fit
    # ------------------------------------------------------------------
    #
    # These matter more than any other tool here. Each runs a real dry run inside the
    # game, under your company, with the parameters you gave, so a tile one returns is a
    # tile the game has already agreed to. Guessing a tile instead is the commonest
    # wasted step there is.
    #
    # Every mode needs its own. A first shakedown run had only the two road finders
    # exposed, and the rail agent said so in its own reasoning before guessing a tile and
    # being refused: "the action list I actually have does NOT include find_station_spot".

    def find_bus_stop_spots(self, town_id: int, max_results: int = 5) -> list[dict[str, Any]]:
        """Road tiles near a town where a bus or truck stop could go."""
        return self._gs("find_bus_stop_spots", {"town_id": town_id, "max_results": max_results})

    def find_depot_spots(self, town_id: int, max_results: int = 5) -> list[dict[str, Any]]:
        """Road tiles near a town where a road depot would fit."""
        return self._gs("find_depot_spots", {"town_id": town_id, "max_results": max_results})

    def find_station_spot(
        self, town_id: int | None = None, industry_id: int | None = None,
        platform_length: int = 3, max_results: int = 5,
    ) -> dict[str, Any]:
        """Where a rail station serving a town or an industry could go.

        Give industry_id for a cargo route and town_id for passengers. Each spot carries
        valid_directions: pass one of those as the station's direction, or it builds on
        an axis the pathfinder cannot join.
        """
        params: dict[str, Any] = {
            "platform_length": platform_length, "max_results": max_results,
        }
        if town_id is not None:
            params["town_id"] = town_id
        if industry_id is not None:
            params["industry_id"] = industry_id
        return self._gs("find_station_spot", params)

    def find_rail_depot_spot(self, tile: int, max_results: int = 3) -> list[dict[str, Any]]:
        """Where a rail depot would fit near a tile.

        It looks for ground adjacent to existing rail, so it correctly returns nothing
        before track exists. Ask it after laying track, not before.
        """
        return self._gs("find_rail_depot_spot", {"tile": tile, "max_results": max_results})

    def find_dock_spots(self, town_id: int, max_results: int = 5) -> list[dict[str, Any]]:
        """Coastal tiles near a town where a dock could go, best first."""
        return self._gs("find_dock_spots", {"town_id": town_id, "max_results": max_results})

    def find_water_depot_spots(self, town_id: int, max_results: int = 5) -> list[dict[str, Any]]:
        """Water near a town where a ship depot could go."""
        return self._gs("find_water_depot_spots", {"town_id": town_id, "max_results": max_results})

    def find_airport_spots(
        self, town_id: int, airport_type: int = 0, max_results: int = 5,
    ) -> list[dict[str, Any]]:
        """Where an airport of the given type would fit near a town."""
        return self._gs(
            "find_airport_spots",
            {"town_id": town_id, "airport_type": airport_type, "max_results": max_results},
        )

    def _gs(self, action: str, params: dict[str, Any]) -> Any:
        """One GameScript query, unwrapped."""
        return self.client.gs_query(action, params).get("result", [])

    # ------------------------------------------------------------------
    # For an LLM's tool list
    # ------------------------------------------------------------------

    def as_langchain_tools(self) -> list[Any]:
        """These reads, wrapped as LangChain tools.

        **Reads only, deliberately.** Acting goes through the step call, so a model
        proposes actions as data and the runner submits them in one batch. A model that
        could act through a tool would act between steps, and a step would then mean a
        different amount of world depending on how many tools it happened to call.
        """
        from langchain_core.tools import StructuredTool

        readable = (
            "get_towns", "get_industries", "get_vehicles", "get_stations",
            "get_finance", "get_engines", "get_tile_info", "scan_town_area",
            "find_bus_stop_spots", "find_depot_spots", "find_station_spot",
            "find_rail_depot_spot", "find_dock_spots", "find_water_depot_spots",
            "find_airport_spots",
        )
        return [
            StructuredTool.from_function(
                func=getattr(self, name),
                name=name,
                description=(getattr(self, name).__doc__ or "").strip(),
            )
            for name in readable
        ]
