---
name: road-transport
description: Running buses and trucks in nttd. Read playing-nttd first.
---

# Road

The easiest mode to get a first route earning, and the right one to start with.

## Order of work

1. Pick the **closest** pair of different towns. Short routes succeed; long ones time out
   in the pathfinder.
2. Ask `find_bus_stop_spots` for a stop site in each town.
3. Build both stops.
4. **Connect them.** Towns do not have roads between them by default. Without a
   connecting road your buses never arrive and the route earns nothing, however good the
   stops are.
5. Build a depot **on the network you just built**, not on an isolated tile.
6. Buy a vehicle, give it orders, and let it run.

## Where the steps go wrong

**Abort after a failed connection.** If the road does not join up, do not buy vehicles
into it. A depot and a bus serving a broken route is money spent on nothing, and it is
the most expensive mistake in this mode.

**One stop per town** until the route is working. A second stop in the same town splits
catchment and adds no distance, so it adds cost and no revenue.

**Match the vehicle to the stop.** A bus stop takes passengers, a truck stop takes goods,
and the stop must be built as the right kind: `is_truck_stop` is what chooses. Check the
generated reference for the exact spelling rather than guessing a shorter one; a stop
built as the wrong kind still builds, and the route then earns nothing.

## Growing

When a route is saturated, meaning cargo waits at the stop faster than vehicles clear it,
add a vehicle. When it is not, do not: an extra bus on an idle route just costs running
expenses.

Prefer **a new town pair** over piling more vehicles onto a working route. New distance
earns more than more capacity on distance you already serve.
