"""Game writes. The only layer allowed to change what has been thrown.

`darts.repo` knows how to write a row; it does not know whether the row is
legal. This layer does. `play.throw` loads a leg's darts, replays them through
`darts.engine`, decides what the incoming dart means, and writes every row that
follows from it inside one transaction.

Where `darts.repo` leaves the transaction boundary to its caller, this layer
*is* that caller. `throw`, `undo` and `rebuild_caches` each open exactly one
transaction and compose repository functions inside it, so a dart is recorded
completely or not at all.

Nothing here returns rows. The public type is `state.GameState`, a tree of
frozen dataclasses; #18 maps it to whatever the wire wants.
"""
