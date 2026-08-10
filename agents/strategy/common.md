---
name: playing-nttd
description: How to run a transport company in nttd, whatever moves the cargo. Read this before any mode-specific strategy.
---

# Playing well, in any mode

This is strategy, not reference. What each action is called and what it takes is
generated from nttd's manifest by `agents/action_brief.py`, and must never be repeated
here: a hand-written parameter list is the thing that goes stale.

## The one rule that matters most

**Complete ONE working route before building anything else.**

A working route is two stations in different places, a connection between them, a depot,
a vehicle, and orders. Anything less earns nothing at all. Half-built infrastructure is
not partial progress; it is cost with no revenue, and it is the commonest way a run ends
with a large map of track and no money.

If you have stations without vehicles, buy vehicles. Do not build more stations.

## Revenue

Payment is for cargo **delivered**, and it falls the longer cargo sits in transit. So:

- Two stations in the **same town earn almost nothing**. Distance is what pays.
- A short busy route beats a long idle one.
- Cargo piling up at a station means too few vehicles. Vehicles arriving empty mean too
  many, or the wrong destination.

## Catchment, or zero revenue

A station only serves what is inside its catchment. A station placed near an industry but
not covering it looks identical to a working one and earns nothing.

**Always ask a finder where something fits.** `find_bus_stop_spots`, `find_station_spot`,
`find_dock_spots`, `find_airport_spots` and the rest run a real dry run inside the game,
under the same company as you, with the parameters you gave. A tile they return is one
the game has already agreed to. Guessing a tile is the single commonest wasted step.

When a finder returns an **empty list**, that is the failure to handle: try another town
or a larger radius. An error is rarer than no result.

## Money

Borrow to build something that will earn, not to hold cash. Interest runs whether or not
the money is working, and company value is not a score.

## Reading a build result

`connect_road`, `connect_rail` and `build_path` can partly succeed. They report a
`status` of `complete` or `partial` and a list of gaps, and a partial route carries
nothing: a gap means no route at all.

**Never test them as a boolean.** Check that the status is complete and no gaps remain.

## Acting on a refusal

Each step returns what every action in your batch did, including why a refusal happened.
Read it. Repeating a refused action unchanged wastes the run, and the world will look
exactly the same either way, because a refused action usually changes nothing.

## Patience

A new route takes time to earn. Leave running vehicles alone; do not judge a route on the
step after you built it, and never sell your whole fleet.
