# Phase 1 evaluation

Phase 1 delivers a pure Python engine. Evaluate scoring and progression through
the tests and replay API; the phone UI and persistence arrive in later phases.
No Pi or running service is needed for these checks.

## Run the gate

From the repository root, with the project's uv environment installed:

```sh
bash scripts/validate-phase1.sh
```

This runs lint, formatting, strict backend typing, checkout-table regeneration
verification, and the complete test suite with the existing 85% coverage gate.
It stops on the first failure. The HTML report is `htmlcov/phase1/index.html`
(ignored by git). Open it in a browser to inspect uncovered lines. Use
`bash scripts/validate-phase1.sh -v` for each test's name and result.

Passing means all ticket scenarios below pass and the generated lookup matches
its source. Line coverage is a useful gap detector, not proof of rule correctness:
the expected scores, event recipients, rotation tables and boundary assertions
are the feature-level evidence. Existing FastAPI/Starlette deprecation warnings
do not indicate engine test failures.

## Ticket coverage map

All paths in this table are under `tests/engine/`. Test files retain the original
ticket's unit-level acceptance cases; `test_replay.py` exercises their composition.

| Ticket | Features and acceptance evidence |
| --- | --- |
| #5 | `test_throws.py`: all 63 throws, every illegal construction, strict parse/label round trips, bull/miss scores. `test_purity.py`: every engine module plus a synthetic banned-import failure, including relative imports. |
| #6 | `test_x01.py`: all nine in/out combinations, all bust reasons, whole-visit score and opening rollback, bull finish distinction, stop-on-bust/checkout, 301/501/701, complete legs and random legality properties. |
| #7 | `test_checkout.py`: 170/169 and impossible set, exact 170/40/32 suggestions, every returned path verified through scoring, per-rule ceilings, nonpositive scores, bounded result count, exhaustive checkability cross-check, ranking, attempt detection and generation under five seconds. Generator check verifies the committed table. |
| #8 | `test_cricket.py`: 0/1/2 prior marks × multiplier table, T20 closing and 40 surplus, bull accounting and 25 surplus, dead/wasted targets, closed-but-trailing, legal non-target/miss darts, capped marks, and complete two-player leg. |
| #9 | `test_cricket_variants.py`: full awards to each open opponent, exclusion of closed opponents, reversed cut-throat win comparison, quick's zero points/events, shared mark accounting, exact three-team event ledger, and win-condition tables. |
| #10: rotation | `test_replay.py::test_rotation_tables`: 12 full visits for 1v1, 2v1, 2v2, 2v3 and three-way; every partial-visit prefix also verifies the member and darts left. |
| #10: starters | `test_match_start_rules`, `test_three_way_alternate_and_loser_starts`, `test_three_way_match_alternates_each_leg_and_resets_members`, `test_start_rule_uses_winner_even_when_not_previous_starter`: all four rules and repeated new legs. |
| #10: match | `test_best_of_boundaries`: best-of-1/3/5 for either winning side, no early completion, losing side below threshold, rejection of later legs, and reopening by undoing the winning dart. |
| #10: replay/undo | `test_random_leg_undo_at_every_position`: Hypothesis generates rules and legal dart sequences, testing every prefix until a win. Dedicated bust/opening rollback and full cut-throat ledger tests assert concrete before/after values, beyond replay self-consistency. |
| #10: opening | `test_empty_leg_and_empty_undo`: correct starting member, 3 darts left and unchanged empty undo for x01 and all cricket variants. |

For a quick integrated acceptance run:

```sh
uv run pytest tests/engine/test_replay.py -v
```

Concrete results to look for in those tests:

- Double-in 61: `D20` leaves 21 and opens; `T20` busts back to 61 and closed.
  Undo restores 21/open, the same member, and two darts left. The next recorded
  dart after a bust belongs to the opposing team without padding the visit.
- Checkout hint `T20 T20 BULL` finishes 170. No next thrower or darts remain;
  undo restores the position before the bull.
- Three-team cut-throat finishes with points `(57, 120, 117)`, team 0 winning.
  Events are `(thrower, recipient, points)`:
  `(0,1,60), (0,2,60), (1,0,57), (1,2,57), (2,1,60)`.
  Undoing the first scoring dart removes both recipients' awards.
- Best-of-5 ends at three wins, including a 3–2 finish. Dropping the winning
  dart reopens that leg and reduces the match tally.

## Try the API yourself

Start `uv run python` from the repository root, then paste:

```python
from darts.engine.replay import LegConfig, replay, undo
from darts.engine.types import Team
from darts.engine.throws import Throw
from darts.engine.x01 import Rule, X01Config

teams = (Team(("Jack", "Partner")), Team(("Opponent",)))
cfg = LegConfig(X01Config(61, Rule.DOUBLE, Rule.DOUBLE))
recorded = tuple(map(Throw.parse, ("D20", "T20")))
busted = replay(cfg, teams, recorded)
print(busted.teams[0])  # remaining=61, is_open=False, darts=2
print(busted.next_thrower)  # team_index=1, member='Opponent'
restored = undo(cfg, teams, busted)
print(restored.teams[0])  # remaining=21, is_open=True, darts=1
print(restored.darts_left)  # 2
```

## API conventions settled in #10

- `LegConfig` holds scoring rules and the starting team. `replay` consumes a
  tuple of actual `Throw` values. It returns immutable team states, visits with
  original per-dart outcomes, winner, next member, and darts left.
- Team order is tuple order. Each team's member counter advances once per
  complete visit, including an early bust, and resets each leg. Member strings
  are opaque identifiers owned by the caller.
- `MatchConfig` selects a positive odd best-of count and start rule.
  `replay_match` derives tallies only from replayed leg winners; it has no
  separate update path. Its `legs` contains supplied replayed histories and
  `current_leg` is the unfinished last leg or a fresh opening leg, or `None`
  after completion. Only the last supplied history may be unfinished.
- Alternate uses `leg_index % n_teams`; fixed uses `fixed_team` (default 0).
  Winner/loser rules use `fixed_team` for the first leg. As agreed with Jack,
  multiple losers resolve to the next non-winning team in cyclic order after
  the previous starter. Solo loser-starts falls back to the only team.
- Every team in a multi-team match needs `ceil(best_of / 2)` wins; the total
  legs can exceed best-of when more than two teams divide the wins.
- Undo drops the final actual dart and replays. At match level, shorten the
  last history and call `replay_match` again. Darts after a leg win and histories
  after a match win are rejected rather than silently discarded.
- Cricket event recipient indices remain relative to the throwing team's
  opponents, in original team order with that team omitted. The visit's thrower
  and per-dart outcomes provide attribution without a persistence dependency.

## Limits of this sign-off

The unchanged CI pipeline also runs all these tests on Linux. This validates
the Phase 1 engine, not SQLite durability, phone interactions, or Pi deployment.
Checkout ranking retuning (#40) remains separate; tests validate the currently
specified ranking. The previously deferred Pi `uv sync --frozen` check from #1
is still a hardware validation item. No microsecond latency claim is made by
these correctness checks.
