---
name: water-transport
description: Running ships in nttd. Read playing-nttd first.
---

# Water

Cheap to build, slow to run, and the mode with the most ways to build something that
cannot work.

## Keep it short

Ships are very slow. Route length is the single most important choice here, far more so
than in any other mode: a long route is hundreds of game-days at zero revenue before the
first delivery. Prefer pairs well under sixty tiles apart.

Two docks in the **same town** earn nothing.

## Order of work

1. Pick two close coastal towns.
2. `find_dock_spots` in each. It already sorts by cargo acceptance and then distance, so
   the first result is usually the right one. Prefer a spot whose acceptance list holds
   cargo you can actually carry.
3. Build both docks.
4. Build a ship depot **on water connected to both docks**. Nothing validates this, and
   a depot on a separate lake or on the far side of a headland produces a ship that can
   never reach its route.
5. Buy the ship and give it orders.

## Connectivity is yours to check

No nttd action answers whether two dock sites share a navigable body of water.
`find_dock_spots` answers only "could a dock be built here". Work it out yourself from
the terrain before committing, or accept the risk of a stranded ship.

## Laying water routes

There is no path-laying action for water. `build_path` with a water transport type does
nothing at all and reports success, which is worse than an error: it will look like the
route was laid. Canals are laid tile by tile, and slopes need locks.

Most useful water routes need no canal: open water between two coastal towns is the case
to look for first.

## Buoys

A buoy is a waypoint, not a station. An order routed through one must name its
destination tile. Passing a buoy where a station is expected is accepted and then
misread, so the ship silently goes somewhere else.

## Patience

Three to five hundred game-days before judging a ship route. They really are that slow.
