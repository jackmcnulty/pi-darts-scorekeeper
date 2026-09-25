/**
 * Everything the cricket board shows, derived from one payload in one place.
 *
 * The same split `x01.ts` uses and for the same reason: the decisions are pure
 * functions in a `.ts` that `cricket.test.ts` can enumerate -- every mark count
 * against every cell state, every variant -- and `CricketBoard.tsx` only draws
 * what they return.
 *
 * Nothing here plays cricket. The marks and the points arrive on
 * `MatchStateResponse` already counted by `engine/cricket.py`, and this file
 * never adds a mark, never awards a point and never decides a win. The one
 * thing it *does* derive is whether a target is dead, which is presentation:
 * see `rowIsDead` for why that is not the same question the engine asks.
 *
 * Zipped once
 * -----------
 * `noUncheckedIndexedAccess` is set and this screen is almost all positional
 * data -- `legs_won` to `teams`, `marks` to targets, targets to columns. So the
 * payload is zipped into a view model exactly once, in `boardView`, and the
 * component indexes nothing. #24 learned this the hard way on a single array.
 */
import type { LegState, MatchState, TeamLeg } from '../api/play'
import type { components } from '../api/schema'

/** Which cricket is being played. The only setting a cricket leg has. */
export type Variant = components['schemas']['Variant']

/**
 * The variant of a match, defaulting to standard.
 *
 * `GameConfig` is one flat model rather than a discriminated union, so
 * `game_type === 'cricket'` does not narrow `variant` away from `null` and the
 * fallback has to exist. The server sets it for every cricket match -- #23
 * cannot create one without -- so this only ever fires for an x01 config, which
 * this board is not asked to draw. Standard is the right default anyway: it is
 * `CricketConfig`'s own, and it shows the points column rather than hiding
 * something a player was expecting.
 */
export function variantOf(config: MatchState['config']): Variant {
  return config.variant ?? 'standard'
}

/**
 * The seven targets, in scoreboard order -- the order `engine/cricket.py`
 * declares them in and the order #4's mockup drew them.
 */
export const CRICKET_TARGETS: readonly number[] = [20, 19, 18, 17, 16, 15, 25]

/** Marks that close a target. Marks beyond this are surplus, which is the engine's problem. */
export const MARKS_TO_CLOSE = 3

/**
 * The board numbers the keypad dims: everything that is not a cricket target.
 *
 * Dim, not disabled. #25's scope line is explicit that a dart at 12 is still a
 * dart and must be recorded, so these keys stay live and post exactly what they
 * post in x01; `Keypad.css` styles them recessed rather than faded for the same
 * reason. 25 is a target and is not in here, and the miss is segment 0, which
 * is a legal entry in cricket as much as anywhere else.
 */
export const NON_TARGETS: ReadonlySet<number> = new Set(
  Array.from({ length: 20 }, (_, i) => i + 1).filter((n) => !CRICKET_TARGETS.includes(n)),
)

/**
 * Cricket notation. One mark is a slash, two a cross, three closes the number.
 *
 * Taken from #4's mockup, including its reasoning: a plain "X" for closed reads
 * as the same glyph as the two-mark "X" at arm's length, and telling those two
 * apart at a glance is the entire job of the board. Hence the ringed cross.
 */
export const MARK_GLYPHS: readonly string[] = ['·', '/', 'X', '⊗']

/**
 * What one cell is.
 *
 * `dead` implies `closed` -- a target is only dead once every team has closed
 * it, so a dead cell necessarily holds three marks. The distinction is what the
 * player needs: a number you have closed and can still score on looks different
 * from one that is finished for everybody.
 */
export type CellState = 'open' | 'closed' | 'dead'

export interface Cell {
  teamId: number
  marks: number
  state: CellState
  glyph: string
  /**
   * How the cell reads aloud. A grid of slashes and crosses is meaningless to a
   * screen reader without one, and adjacent spans concatenate with no
   * separator, which is why every component #24 touched spells its label out.
   */
  label: string
}

export interface BoardRow {
  target: number
  /** "20", or "Bull" for 25 -- what the spine down the middle prints. */
  label: string
  /** Closed by every team. Rendered dead in every column. */
  dead: boolean
  cells: Cell[]
}

export interface BoardColumn {
  teamId: number
  /** Whoever is at the oche for this team, or its first member. */
  name: string
  /** The rest of the team, for a 2v2. Undefined for a solo team. */
  teammates?: string
  /** This team's running total. Meaningless in `quick`, which hides it. */
  points: number
  legsWon: number
  active: boolean
  accent: string
}

export interface BoardView {
  columns: BoardColumn[]
  rows: BoardRow[]
  /** Whether the points totals are worth showing at all. See `showsPoints`. */
  showsPoints: boolean
}

/**
 * Whether this variant has points worth showing.
 *
 * `quick` wastes every surplus mark, so every team's total is zero for the
 * whole leg. #25 asks for the column to be hidden entirely rather than greyed,
 * and a column of zeroes is exactly the wrong thing to draw: it invites the
 * player to wonder what they have to do to move it. This mirrors #23 making
 * cricket's in/out rule controls absent rather than disabled.
 */
export function showsPoints(variant: Variant): boolean {
  return variant !== 'quick'
}

/** How a variant reads in the context line. "Cut-throat", as the house spells it. */
const VARIANT_LABELS: Record<Variant, string> = {
  standard: 'Standard',
  cutthroat: 'Cut-throat',
  quick: 'Quick',
}

/** The line along the top: which game, which variant, which leg, how long. */
export function contextLine(state: MatchState, leg: LegState): string {
  const variant = VARIANT_LABELS[variantOf(state.config)]
  return `Cricket · ${variant} · Leg ${String(leg.leg_index + 1)} · Best of ${String(
    state.config.best_of,
  )}`
}

/**
 * Marks held on one target, from the payload's `marks` map.
 *
 * `marks` is `dict[int, int]` in Python and therefore `{ [key: string]: number }`
 * on the wire -- JSON object keys are strings, so the served payload really is
 * `{"marks":{"20":3,"19":1}}`. Indexing it with a number works at runtime, but
 * `noUncheckedIndexedAccess` types the result `number | undefined` either way, so
 * the fallback is needed rather than merely tidy: a target nobody has hit yet is
 * absent from the map, not zero in it.
 *
 * Null `marks` means an x01 leg, which this board is never asked to draw.
 */
function marksOn(team: TeamLeg, target: number): number {
  return team.marks?.[String(target)] ?? 0
}

/**
 * Whether a target is dead: closed by every team.
 *
 * This is #25's wording and #25's criterion, and it is deliberately NOT how
 * `engine/cricket.py` decides whether a dart scores. The engine asks whether
 * every *opponent* has closed the target, because surplus only pays out while
 * somebody is still open to score against; for two teams the two questions
 * coincide, and for three they do not. Nothing here feeds a rule -- the engine
 * has already decided what every dart was worth -- so this is the presentation
 * question only: is this number finished for everybody at the board.
 */
function rowIsDead(teams: readonly TeamLeg[], target: number): boolean {
  return teams.length > 0 && teams.every((team) => marksOn(team, target) >= MARKS_TO_CLOSE)
}

function cellState(marks: number, dead: boolean): CellState {
  if (marks < MARKS_TO_CLOSE) return 'open'
  return dead ? 'dead' : 'closed'
}

const STATE_WORDS: Record<CellState, string> = {
  open: 'open',
  closed: 'closed',
  dead: 'dead',
}

/** "Bull" for 25, the number itself otherwise. */
export function targetLabel(target: number): string {
  return target === 25 ? 'Bull' : String(target)
}

/**
 * The whole board, zipped once.
 *
 * Columns come from `state.teams` so the order is the match's team order, and
 * `legs_won` is positional to it. Rows come from `CRICKET_TARGETS` so the board
 * reads top to bottom the way a scoreboard does, whatever order the payload
 * happens to list the leg's teams in -- which is why the leg teams are indexed
 * by id rather than by position.
 */
export function boardView(state: MatchState, leg: LegState): BoardView {
  const thrower = leg.next_thrower
  const byId = new Map(leg.teams.map((team) => [team.team_id, team]))
  // Positional to `state.teams`, so a leg that lists its teams in another order
  // still lines each column up with the right marks.
  const legTeams = state.teams.map((team) => byId.get(team.id))

  const columns: BoardColumn[] = state.teams.map((team, index) => {
    const active = thrower?.team_id === team.id
    const names = team.members.map((member) => member.display_name)
    const lead = active ? (thrower?.display_name ?? names[0]) : names[0]
    const rest = names.filter((name) => name !== lead)

    return {
      teamId: team.id,
      name: lead ?? `Team ${String(index + 1)}`,
      teammates: rest.length > 0 ? rest.join(', ') : undefined,
      points: byId.get(team.id)?.points ?? 0,
      // `?? 0` is unreachable -- the server builds `legs_won` from `teams` --
      // but the index signature is optional and a team has won no legs until it
      // has won one.
      legsWon: state.legs_won[index] ?? 0,
      active,
      accent: `var(--accent-${String((index % 8) + 1)})`,
    }
  })

  const present = legTeams.filter((team): team is TeamLeg => team !== undefined)

  const rows: BoardRow[] = CRICKET_TARGETS.map((target) => {
    const dead = rowIsDead(present, target)
    const name = targetLabel(target)

    return {
      target,
      label: name,
      dead,
      cells: columns.map((column, index) => {
        const legTeam = legTeams[index]
        const marks = legTeam === undefined ? 0 : marksOn(legTeam, target)
        const state = cellState(marks, dead)
        return {
          teamId: column.teamId,
          marks,
          state,
          glyph: MARK_GLYPHS[Math.min(marks, MARKS_TO_CLOSE)] ?? MARK_GLYPHS[0] ?? '·',
          label: `${column.name}, ${name}, ${String(marks)} ${
            marks === 1 ? 'mark' : 'marks'
          }, ${STATE_WORDS[state]}`,
        }
      }),
    }
  })

  return {
    columns,
    rows,
    showsPoints: showsPoints(variantOf(state.config)),
  }
}

/** How a column's heading reads aloud, name and points run together otherwise. */
export function columnLabel(column: BoardColumn, withPoints: boolean): string {
  const parts = [column.name]
  if (column.teammates !== undefined) parts.push(`with ${column.teammates}`)
  if (withPoints) parts.push(`${String(column.points)} points`)
  parts.push(`${String(column.legsWon)} ${column.legsWon === 1 ? 'leg' : 'legs'} won`)
  if (column.active) parts.push('throwing now')
  return parts.join(', ')
}

/**
 * What changed between two boards, for the state-change signals.
 *
 * #25 asks that closing a number clearly signal the change, and #25's
 * cut-throat criterion asks that points be seen accruing to opponents rather
 * than to the thrower. Both are the same question -- what moved since the last
 * payload -- so both are answered here, as a pure diff of two view models with
 * no React in it.
 *
 * This derives nothing about the rules. A cell closed because the server says
 * it now holds three marks and said it held fewer a moment ago; a team gained
 * points because the server's total for it went up. The board never works out
 * who *should* have been paid, which under cut-throat is exactly the rule that
 * would be easiest to get subtly wrong.
 */
export interface BoardChanges {
  /** `teamId:target` for each cell that has just closed. */
  closed: ReadonlySet<string>
  /** Team ids whose points total has just risen. */
  gained: ReadonlySet<number>
}

export const NO_CHANGES: BoardChanges = { closed: new Set(), gained: new Set() }

/** The key a cell is tracked by. Exported so the component cannot invent a second spelling. */
export function cellKey(teamId: number, target: number): string {
  return `${String(teamId)}:${String(target)}`
}

export function changesBetween(previous: BoardView | null, next: BoardView): BoardChanges {
  // Nothing has "just" happened on the first payload: a board loaded mid-match
  // would otherwise announce every number already closed as closing now.
  if (previous === null) return NO_CHANGES

  const wasClosed = new Set<string>()
  for (const row of previous.rows) {
    for (const cell of row.cells) {
      if (cell.marks >= MARKS_TO_CLOSE) wasClosed.add(cellKey(cell.teamId, row.target))
    }
  }

  const closed = new Set<string>()
  for (const row of next.rows) {
    for (const cell of row.cells) {
      const key = cellKey(cell.teamId, row.target)
      if (cell.marks >= MARKS_TO_CLOSE && !wasClosed.has(key)) closed.add(key)
    }
  }

  const before = new Map(previous.columns.map((column) => [column.teamId, column.points]))
  const gained = new Set<number>()
  for (const column of next.columns) {
    const had = before.get(column.teamId)
    if (had !== undefined && column.points > had) gained.add(column.teamId)
  }

  return { closed, gained }
}
