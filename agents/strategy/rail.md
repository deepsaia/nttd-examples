---
name: rail-transport
description: Running trains in nttd. The hardest mode. Read playing-nttd first.
---

# Rail

Rail pays best over distance and bulk cargo, and has the most ways to fail silently.

## Order of work, and it is not the obvious one

1. Pick a producer and a consumer, and use the **industry id, not the town id**, for a
   cargo route. A station sited at a town near an industry does not serve the industry.
2. `find_station_spot` for each end. It returns candidate spots, each with the platform
   **orientations that would actually work**.
3. Build both stations, passing the `direction` the finder reported. Leaving it to
   default is the classic silent failure: the station builds on the wrong axis and the
   pathfinder cannot join it.
4. **Lay the track between the built stations.** Use `connect_rail`, and give it the
   station platform tiles as the hint parameters at each end. That is what makes the
   route join up to your platforms rather than merely reach them.
5. **Then** the depot. `find_rail_depot_spot` looks for a tile adjacent to existing rail,
   so before track exists it correctly returns nothing. Asking earlier is not an error to
   work around; it is the wrong order.
6. Buy the train and give it orders.

## The mistake that earns nothing

**A locomotive on its own carries no cargo.** Buying a vehicle gives you an engine. To
haul anything you need wagons coupled to it, which is what `build_train` is for. A rail
route built end to end with a bare locomotive looks complete, runs, and earns zero.

## Laying track yourself

`build_path` takes the tiles you chose and works out how each piece must sit, including
the three-tile context rail needs, so nothing has to reason about track orientation. Use
it when you want a route of your own design; use `connect_rail` when you would rather the
pathfinder chose.

`build_rail_track` lays a single piece in a chosen orientation. It is the only way to
express a siding, a junction stub or a passing loop, because a path implies its
orientations from the tiles either side and a stub has no path to imply anything.

## Signals

Signals are what let more than one train share a line. A single train on a simple
out-and-back route does not need them; add them when a second train joins.

## Patience

Rail is slow to pay back. Give a new route several hundred game-days before judging it.
