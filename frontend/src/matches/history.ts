/**
 * Everything the history list and the match detail screen show, in one place.
 *
 * The same split as `setup/config.ts` and `play/x01.ts`: the decisions are pure
 * functions here and the components only render them, which is what lets the
 * tests enumerate the awkward cases -- a busted visit, an abandoned match, the
 * last page of an odd total -- against fixtures rather than driving them
 * through the UI.
 *
 * `describeMatch` and `opponents` are also what the home screen's resume card
 * uses. They were private to `Home.tsx` until #26 needed a match named the same
 * way in a list; a second copy would have been two places for "what do we call
 * a 501 best-of-5" to drift apart, so they moved here and `Home.tsx` imports
 * them. Nothing else about that screen changed.
 *
 * Nothing here recomputes a dart. `is_bust`, `counted` and `caused_bust` are
 * the server's verdicts and arrive already decided; the one piece of arithmetic
 * is `score_before - score_after`, which is what a visit scored and is zero for
 * a bust because the server reverted it. That zero is the whole point of
 * criterion 3 and is why it is read rather than special-cased.
 */
import type { LegHistory, MatchPage, MatchStatus } from '../api/history'
import type { Match } from '../api/matches'
import type { Visit } from '../api/play'
import { PAGE_SIZE } from '../api/history'

function capitalise(word: string): string {
  return word.charAt(0).toUpperCase() + word.slice(1)
}

/**
 * "501 · Best of 5" or "Cricket · Cutthroat", the way #4's mockups title a game.
 *
 * `variant` and `start_score` are each nullable because the other game type has
 * no use for them, and a match that arrives without the one it needs is
 * described by what it does have. Nothing here supplies a default 501: the
 * server is authoritative about what was configured, and a plausible-looking
 * guess is worse than a shorter title.
 */
export function describeMatch({ config }: Match): string {
  // Both are optional *and* nullable in the schema -- FastAPI writes a field
  // with a default that way -- so absence is flattened once, here.
  const variant = config.variant ?? null
  const startScore = config.start_score ?? null
  if (config.game_type === 'cricket') {
    return variant === null ? 'Cricket' : `Cricket · ${capitalise(variant)}`
  }
  const bestOf = `Best of ${String(config.best_of)}`
  return startScore === null ? bestOf : `${String(startScore)} · ${bestOf}`
}

/** "Jack v Dad", naming teams by their members when they have no name of their own. */
export function opponents({ teams }: Match): string {
  return teams
    .map((team) => team.name ?? team.members.map((member) => member.display_name).join(' & '))
    .join(' v ')
}

/**
 * What to call each status, which is criterion 6.
 *
 * An abandoned match has to be visibly distinguishable from a completed one, and
 * the two are otherwise identical in a list: both are over, neither is
 * resumable. So the word is different, the styling is different -- see
 * `History.css` -- and the accessible label says it too, because a colour is not
 * a distinction to somebody using a screen reader.
 */
const STATUS_TEXT: Record<MatchStatus, string> = {
  in_progress: 'In progress',
  complete: 'Complete',
  abandoned: 'Abandoned',
}

export function statusLabel(status: MatchStatus): string {
  return STATUS_TEXT[status]
}

/**
 * When a match happened, in the shortest form that is still unambiguous.
 *
 * `toLocaleDateString` with an explicit option set rather than a hand-rolled
 * format: the Pi is a single-locale device on a LAN, and the browser already
 * knows how this household writes a date.
 */
export function matchDate(iso: string): string {
  const when = new Date(iso)
  if (Number.isNaN(when.getTime())) return ''
  return when.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' })
}

export interface HistoryRow {
  matchId: number
  title: string
  players: string
  date: string
  status: MatchStatus
  statusText: string
  /** How a whole row reads aloud, spelled out. */
  label: string
}

/**
 * One row per match in the page.
 *
 * The accessible label is built here rather than left to the markup because a
 * row stacks a date, a title, a scoreline and a status badge as adjacent
 * elements, and a screen reader runs those together -- "3 Mar 2026501 · Best of
 * 5Jack v DadComplete". `x01.ts:cardLabel` is the precedent and the reason this
 * is a function with a test rather than a hopeful `aria-label` template.
 */
export function historyRows(page: MatchPage | undefined): HistoryRow[] {
  if (page === undefined) return []
  return page.items.map((match) => {
    const title = describeMatch(match)
    const players = opponents(match)
    const date = matchDate(match.created_at)
    const statusText = statusLabel(match.status)
    return {
      matchId: match.id,
      title,
      players,
      date,
      status: match.status,
      statusText,
      label: [players, title, statusText, date].filter((part) => part !== '').join(', '),
    }
  })
}

export interface PageInfo {
  /** 1-based, for a human. */
  page: number
  pages: number
  hasPrev: boolean
  hasNext: boolean
  /** "1–20 of 57", or "None yet" for an empty history. */
  summary: string
  prevOffset: number
  nextOffset: number
}

/**
 * Where in the history the current page sits.
 *
 * Driven by the server's `total`, which is why criterion 4 needs no accumulated
 * client state: the page knows how many there are without having fetched them.
 * `limit` and `offset` are echoed back on the response, so this reads them from
 * there rather than from the arguments that produced them -- if the server
 * clamped a limit, the footer says what actually happened.
 */
export function pageInfo(page: MatchPage | undefined): PageInfo {
  // No summary for either "not yet known" or "there are none": the screen has
  // its own empty state, and a pager reading "0 of 0" beside it would be a
  // control over nothing. The caller shows the pager only when there are rows.
  if (page === undefined || page.total === 0) {
    return {
      page: 1,
      pages: 1,
      hasPrev: false,
      hasNext: false,
      summary: '',
      prevOffset: 0,
      nextOffset: 0,
    }
  }
  const limit = page.limit > 0 ? page.limit : PAGE_SIZE
  const pages = Math.ceil(page.total / limit)
  const current = Math.floor(page.offset / limit) + 1
  const from = page.offset + 1
  const to = Math.min(page.offset + limit, page.total)
  return {
    page: current,
    pages,
    hasPrev: page.offset > 0,
    hasNext: to < page.total,
    summary: `${String(from)}–${String(to)} of ${String(page.total)}`,
    prevOffset: Math.max(0, page.offset - limit),
    nextOffset: page.offset + limit,
  }
}

export interface VisitLine {
  visitId: number
  visitIndex: number
  playerId: number
  playerName: string
  /** The dart labels, as the server printed them: "T20", "BULL", "MISS". */
  darts: string[]
  /** What the visit scored. Zero for a bust, which is the point of a bust. */
  scored: number
  isBust: boolean
  /** The dart that busted it, or null. */
  bustDart: string | null
  scoreBefore: number
  scoreAfter: number
  /** How many darts were thrown, which a bust does not reduce. */
  dartsThrown: number
  /**
   * What `score_after` means in words: "21 left" in x01, "12 pts" in cricket.
   *
   * The column cannot say which -- `score_before`/`score_after` are x01
   * remaining in an x01 leg and the throwing team's points in a cricket one,
   * exactly as `VisitResponse` documents -- so the game type has to be supplied
   * and the wording chosen once, here.
   */
  scoreText: string
  label: string
}

/**
 * What a visit scored: the difference the server recorded.
 *
 * Zero for a bust, because `score_after` was reverted to `score_before` when it
 * busted. Read rather than special-cased -- a client that returned 0 for
 * `is_bust` and the difference otherwise would be reimplementing the revert.
 */
export function visitScored(visit: Visit): number {
  return visit.score_before - visit.score_after
}

/**
 * How a visit reads aloud, and criterion 3's claim in words.
 *
 * A struck-through row and a "BUST" chip are both visual, so neither reaches
 * somebody using a screen reader. The label therefore says the whole thing
 * outright: which darts, that it busted, that it scored nothing, and how many
 * darts it still cost. That last clause is the part of the criterion a strike
 * through a row does not by itself communicate.
 */
export function visitLabel(line: Omit<VisitLine, 'label'>): string {
  const darts = line.darts.length > 0 ? line.darts.join(', ') : 'no darts'
  const parts = [`${line.playerName}: ${darts}`]
  if (line.isBust) {
    parts.push('bust')
    if (line.bustDart !== null) parts.push(`on ${line.bustDart}`)
    parts.push(
      `scored nothing, but ${String(line.dartsThrown)} ${line.dartsThrown === 1 ? 'dart' : 'darts'} thrown`,
    )
    parts.push(`still on ${String(line.scoreAfter)}`)
  } else {
    parts.push(`scored ${String(line.scored)}`)
    parts.push(line.scoreText)
  }
  return parts.join(', ')
}

/** Which game a leg is, which decides what `score_after` is called. */
export type LegGame = 'x01' | 'cricket'

/**
 * Every visit of a leg as a renderable line, oldest first.
 *
 * `names` resolves a `player_id`; a visit carries the id and not the name,
 * because the payload names players once on the match rather than on every one
 * of a hundred visits.
 *
 * `game` decides the wording of `scoreText` only. Cricket cannot bust -- there
 * is no bust rule in cricket -- so the bust branch is unreachable for a cricket
 * leg rather than suppressed for one.
 */
export function visitLines(
  leg: LegHistory,
  names: Map<number, string>,
  game: LegGame = 'x01',
): VisitLine[] {
  return leg.visits.map((visit) => {
    const bust = visit.darts.find((dart) => dart.caused_bust)
    const line = {
      visitId: visit.visit_id,
      visitIndex: visit.visit_index,
      playerId: visit.player_id,
      playerName: names.get(visit.player_id) ?? `Player ${String(visit.player_id)}`,
      darts: visit.darts.map((dart) => dart.label),
      scored: visitScored(visit),
      isBust: visit.is_bust,
      bustDart: bust?.label ?? null,
      scoreBefore: visit.score_before,
      scoreAfter: visit.score_after,
      dartsThrown: visit.darts.length,
      scoreText:
        game === 'cricket'
          ? `${String(visit.score_after)} pts`
          : `${String(visit.score_after)} left`,
    }
    return { ...line, label: visitLabel(line) }
  })
}

/** Darts thrown in a leg, busted visits included -- which is the claim. */
export function legDartsThrown(leg: LegHistory): number {
  return leg.visits.reduce((total, visit) => total + visit.darts.length, 0)
}
