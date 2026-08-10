---
name: air-transport
description: Running aircraft in nttd. Read playing-nttd first.
---

# Air

The mode where the usual "closest pair first" advice is wrong.

## Go long

Aircraft are fast and expensive, and they ignore terrain entirely. They earn on
**long-haul** routes where every other mode is slow. Pick the longest pair you can
reach, not the nearest, and prefer the two largest towns: demand between towns scales
roughly with the product of their populations over the distance between them.

Two airports in the **same town** earn nothing. Different towns, always.

## Order of work

1. Pick two large, distant towns.
2. `find_airport_spots` in each.
3. Build both airports, one **before** buying anything.
4. Ask `get_hangars` for the hangar tile. The build tells you the station it created but
   not where the hangar is, and the hangar is the depot an aircraft is bought into.
5. Buy the aircraft, give it orders between the two airports.

The split between building and buying is real here, not superstition: the airport must
exist before its hangar can be found.

## Airport size

Start with the smallest airport type. A smaller footprint fits where nothing else does,
and on a crowded or hilly map the difference between a route and no route is usually
whether the airport fitted.

## The refusal to expect

A town will refuse a further station once it already has several, reporting too many
stations in that town. The fix is **another town, not another tile**: retrying nearby in
the same town will keep failing.

## Patience

Give an air route around a hundred game-days before judging it.
