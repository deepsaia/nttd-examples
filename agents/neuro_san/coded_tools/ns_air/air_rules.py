"""What an airport type decides, and nothing else.

Two things follow from the type alone, and each one was learned by losing a run.

**Coverage decides whether the town is seen at all.** A commuter field reaches 4 tiles. One
sited 16 to 28 tiles from its town collected a single passenger from a town of 4,379 people,
and a quarter's income went from 25 to 131,740 once it was moved inside the catchment.

**The type decides what may land.** A big plane at a commuter field crashes. There is no
warning and no refusal: one run lost three of them, about 150,000, to bare `vehicle_crashed`
events, because every field it had built was commuter.

The table lives here because several tools have to agree on it. `get_airport_types` also
serves `coverage` live and lists only the types the current year offers, so a caller holding
that reply should prefer its numbers; these are the fallback and the record of what was
measured in play.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Final

LARGE: Final = 1
HELIPORT: Final = 2
METROPOLITAN: Final = 3
INTERNATIONAL: Final = 4
COMMUTER: Final = 5
HELIDEPOT: Final = 6
INTERCONTINENTAL: Final = 7
HELISTATION: Final = 8

NAMES: Final = {
    LARGE: "large",
    HELIPORT: "heliport",
    METROPOLITAN: "metropolitan",
    INTERNATIONAL: "international",
    COMMUTER: "commuter",
    HELIDEPOT: "helidepot",
    INTERCONTINENTAL: "intercontinental",
    HELISTATION: "helistation",
}

# Fields an aeroplane can use at all. 2, 6 and 8 are helicopter-only, so a fleet ordered into
# one of them never flies. Type 0, small, is not offered in the era these runs are played in.
AEROPLANE_TYPES: Final = (COMMUTER, LARGE, METROPOLITAN, INTERNATIONAL, INTERCONTINENTAL)

# Fields a big plane survives. COMMUTER is deliberately absent: it is a SMALL field and the
# crash it causes is silent. The best run flew big planes between type 4 and type 1 and scored
# 173; the run that used mostly commuter fields was capped at small aircraft and scored 118.
BIG_PLANE_TYPES: Final = (LARGE, METROPOLITAN, INTERNATIONAL, INTERCONTINENTAL)

# Tiles the catchment reaches, as Manhattan distance from the airport. Measured in play, and
# confirmed by the engine, which computes `within_coverage` as abs(dx) + abs(dy) <= coverage.
COVERAGE: Final = {
    COMMUTER: 4,
    LARGE: 5,
    METROPOLITAN: 6,
    INTERNATIONAL: 8,
    INTERCONTINENTAL: 10,
}


def takes_aeroplanes(airport_type: int) -> bool:
    """Whether an aeroplane can use this field at all.

    A type that is not in the table is answered no. An unknown field has to be treated as
    unusable rather than assumed usable, because the assumption is what parks or crashes a
    fleet that has already been paid for.
    """
    return airport_type in AEROPLANE_TYPES


def takes_big_planes(airport_type: int) -> bool:
    """Whether a big plane survives here."""
    return airport_type in BIG_PLANE_TYPES


def both_take_big_planes(first: int, second: int) -> bool:
    """Whether a big plane may fly this leg.

    The SMALLER field decides. An aircraft that may take off from an international airport
    still crashes at the commuter field at the other end.
    """
    return takes_big_planes(first) and takes_big_planes(second)


def coverage(airport_type: int) -> int:
    """Tiles the catchment reaches, Manhattan, from the measured table.

    Zero for a type not in the table, which makes a caller search nothing rather than search a
    radius it cannot justify.
    """
    return COVERAGE.get(airport_type, 0)


def name(airport_type: int) -> str:
    """The type as a person would say it, so a report does not read as bare integers."""
    return NAMES.get(airport_type, f"type {airport_type}")


def largest_first(airport_types: Iterable[int]) -> list[int]:
    """The aeroplane types among these, biggest catchment first.

    Biggest first, not smallest that fits. Smallest-that-covers always lands on commuter
    fields, which caps every leg at a small plane: measured, small planes on 35 tile legs
    earned about 13,000 each while one big plane on a 205 tile leg earned 74,986. Ties break
    on the id so two calls give the same order.
    """
    usable = {airport_type for airport_type in airport_types if takes_aeroplanes(airport_type)}
    return sorted(usable, key=lambda airport_type: (-coverage(airport_type), airport_type))
