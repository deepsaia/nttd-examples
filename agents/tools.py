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
        """Buyable engines of one type. What is buyable is gated by the game year."""
        return self.client.gs_query(
            "get_engines", {"vehicle_type": vehicle_type},
        ).get("result", [])

    def find_bus_stop_spots(self, town_id: int, max_results: int = 5) -> list[dict[str, Any]]:
        """Road tiles near a town where a stop will fit. ``[{tile, x, y}]``"""
        return self.client.gs_query(
            "find_bus_stop_spots", {"town_id": town_id, "max_results": max_results},
        ).get("result", [])

    def find_depot_spots(self, town_id: int, max_results: int = 5) -> list[dict[str, Any]]:
        """Road tiles near a town where a depot will fit. ``[{tile, x, y}]``"""
        return self.client.gs_query(
            "find_depot_spots", {"town_id": town_id, "max_results": max_results},
        ).get("result", [])

    def get_tile_info(self, tile: int) -> dict[str, Any]:
        """Terrain and infrastructure on one tile."""
        return self.client.gs_query("get_tile_info", {"tile": tile}).get("result", {})

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
            "get_finance", "get_engines", "find_bus_stop_spots",
            "find_depot_spots", "get_tile_info",
        )
        return [
            StructuredTool.from_function(
                func=getattr(self, name),
                name=name,
                description=(getattr(self, name).__doc__ or "").strip(),
            )
            for name in readable
        ]
