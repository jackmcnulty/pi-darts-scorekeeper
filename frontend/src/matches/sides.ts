/**
 * A single-sided match (#68): one team, practice, with nobody to beat.
 *
 * Jack decided on #68 that such a match is never a win. The server enforces
 * that in its stats; this is the same rule for the words on screen. A finished
 * practice leg is "finished", never "won", on the board, the completion sheet
 * and the match detail, so no screen calls something a win that the stats will
 * not count as one.
 *
 * Read off the team count, which is what the server derives it from too --
 * there is no flag on the wire, so there is nothing for the two to disagree
 * about. Not to be confused with `TeamResponse.is_solo`, which is older and
 * means a team with one *member*: every 1v1 has two `is_solo` teams and is not
 * single-sided, and a pair practising together is single-sided with no
 * `is_solo` team at all.
 */

/** Anything with the match's teams on it: a `Match` or a `MatchState`. */
export interface HasTeams {
  teams: readonly unknown[]
}

export function isSingleSided(match: HasTeams): boolean {
  return match.teams.length === 1
}

/**
 * The verb for the team that took the last leg or the match.
 *
 * Takes the answer to `isSingleSided` rather than the match, because the view
 * models that call it -- a score card, a board column -- hold one team, not
 * the match it is in.
 */
export function outcomeVerb(singleSided: boolean): 'won' | 'finished' {
  return singleSided ? 'finished' : 'won'
}

/** "3 legs won", or "3 legs finished" when there was nobody to win them from. */
export function legsText(legs: number, singleSided: boolean): string {
  return `${String(legs)} ${legs === 1 ? 'leg' : 'legs'} ${outcomeVerb(singleSided)}`
}
