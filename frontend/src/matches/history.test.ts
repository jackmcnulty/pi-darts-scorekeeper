/**
 * The history list's rows and pager, and the dart-by-dart view's visit lines.
 *
 * The pagination cases are enumerated at the boundaries rather than sampled in
 * the middle: an exact multiple of the page size, one over, one under, a single
 * page, an empty history and a page whose `offset` puts it last. Those are where
 * an off-by-one in "1–20 of 57" lives, and the criterion is about the pager
 * being right rather than about it existing.
 */
import { describe, expect, it } from 'vitest'
import type { MatchStatus } from '../api/history'
import {
  describeMatch,
  historyRows,
  legDartsThrown,
  matchDate,
  opponents,
  pageInfo,
  statusLabel,
  visitLabel,
  visitLines,
  visitScored,
} from './history'
import { bustedVisit, DAD, JACK, leg, match, matchPage, visit } from './historyfixture'

describe('describeMatch', () => {
  it('titles an x01 match with its start score and length', () => {
    expect(describeMatch(match({ startScore: 501, bestOf: 5 }))).toBe('501 · Best of 5')
  })

  it('titles a cricket match with its variant, capitalised', () => {
    expect(describeMatch(match({ gameType: 'cricket', variant: 'cutthroat' }))).toBe(
      'Cricket · Cutthroat',
    )
  })

  it('describes a match by what it has rather than defaulting to 501', () => {
    const noScore = match()
    // A payload without the field the game type needs: the title is shorter
    // rather than a plausible-looking guess.
    const stripped = { ...noScore, config: { ...noScore.config, start_score: null } }
    expect(describeMatch(stripped)).toBe('Best of 3')
  })

  it('falls back to plain Cricket when the variant is absent', () => {
    const cricket = match({ gameType: 'cricket' })
    const stripped = { ...cricket, config: { ...cricket.config, variant: null } }
    expect(describeMatch(stripped)).toBe('Cricket')
  })
})

describe('opponents', () => {
  it('names solo teams after their members', () => {
    expect(opponents(match())).toBe('Jack v Dad')
  })

  it('prefers a team name when it has one', () => {
    const named = match()
    expect(
      opponents({
        ...named,
        teams: named.teams.map((team, index) => ({
          ...team,
          name: index === 0 ? 'Reds' : 'Blues',
        })),
      }),
    ).toBe('Reds v Blues')
  })
})

describe('statusLabel', () => {
  it('gives each status its own word, which is criterion 6', () => {
    expect(statusLabel('complete')).toBe('Complete')
    expect(statusLabel('abandoned')).toBe('Abandoned')
    expect(statusLabel('in_progress')).toBe('In progress')
    // The three must be distinct, or the badge distinguishes nothing.
    const every: MatchStatus[] = ['complete', 'abandoned', 'in_progress']
    expect(new Set(every.map(statusLabel)).size).toBe(3)
  })
})

describe('matchDate', () => {
  it('formats a real timestamp', () => {
    expect(matchDate('2026-03-03T19:30:00Z')).not.toBe('')
  })

  it('is empty rather than "Invalid Date" for something unparseable', () => {
    expect(matchDate('not a date')).toBe('')
  })
})

describe('historyRows', () => {
  it('spells out a whole row, because adjacent elements concatenate', () => {
    const rows = historyRows(matchPage([match({ id: 42, bestOf: 5 })], 1))
    expect(rows).toHaveLength(1)
    const row = rows[0]!
    expect(row.matchId).toBe(42)
    expect(row.label).toContain('Jack v Dad')
    expect(row.label).toContain('501 · Best of 5')
    // The status is in the label, not only in the styling.
    expect(row.label).toContain('Complete')
  })

  it('says abandoned in the label of an abandoned match', () => {
    const rows = historyRows(matchPage([match({ status: 'abandoned' })], 1))
    expect(rows[0]!.label).toContain('Abandoned')
    expect(rows[0]!.status).toBe('abandoned')
  })

  it('is empty while the request is in flight', () => {
    expect(historyRows(undefined)).toEqual([])
  })
})

describe('pageInfo at the boundaries', () => {
  it('is a single page when the total fits in one', () => {
    const info = pageInfo(matchPage([], 12, 20, 0))
    expect(info).toMatchObject({ page: 1, pages: 1, hasPrev: false, hasNext: false })
    expect(info.summary).toBe('1–12 of 12')
  })

  it('has no next page on an exact multiple', () => {
    // 40 of 40: the trap is an off-by-one that offers a 41st that is not there.
    const info = pageInfo(matchPage([], 40, 20, 20))
    expect(info).toMatchObject({ page: 2, pages: 2, hasPrev: true, hasNext: false })
    expect(info.summary).toBe('21–40 of 40')
  })

  it('has a next page one over a multiple', () => {
    const info = pageInfo(matchPage([], 41, 20, 20))
    expect(info).toMatchObject({ page: 2, pages: 3, hasPrev: true, hasNext: true })
    expect(info.summary).toBe('21–40 of 41')
  })

  it('clamps the last page summary to the total', () => {
    const info = pageInfo(matchPage([], 41, 20, 40))
    expect(info).toMatchObject({ page: 3, pages: 3, hasPrev: true, hasNext: false })
    expect(info.summary).toBe('41–41 of 41')
  })

  it('offers no paging and no summary for an empty history', () => {
    // The screen has its own empty state; a pager reading "0 of 0" beside it
    // would be a control over nothing.
    const info = pageInfo(matchPage([], 0, 20, 0))
    expect(info).toMatchObject({ hasPrev: false, hasNext: false, summary: '' })
  })

  it('says nothing at all while the request is in flight', () => {
    expect(pageInfo(undefined).summary).toBe('')
  })

  it('steps by the limit the server echoed, not the one that was asked for', () => {
    // If the server clamped the limit, the pager has to step by what it did.
    const info = pageInfo(matchPage([], 100, 50, 50))
    expect(info.prevOffset).toBe(0)
    expect(info.nextOffset).toBe(100)
    expect(info.page).toBe(2)
  })

  it('never offers a negative offset', () => {
    expect(pageInfo(matchPage([], 100, 20, 10)).prevOffset).toBe(0)
  })

  it('falls back to the page size if the server echoed a nonsense limit', () => {
    // Defensive: a zero limit would otherwise divide by zero and give Infinity.
    const info = pageInfo(matchPage([], 10, 0, 0))
    expect(Number.isFinite(info.pages)).toBe(true)
  })
})

describe('visitScored', () => {
  it('is the difference the server recorded', () => {
    expect(visitScored(visit({ scoreBefore: 501, scoreAfter: 441, labels: ['T20'] }))).toBe(60)
  })

  it('is zero for a bust, because the server reverted the score', () => {
    expect(visitScored(bustedVisit({ scoreBefore: 141, labels: ['T20', 'T20', 'T20'] }))).toBe(0)
  })
})

describe('visitLines and the bust', () => {
  const names = new Map([
    [JACK, 'Jack'],
    [DAD, 'Dad'],
  ])

  it('reports a busted visit as bust, scoring nothing, with its darts still counted', () => {
    const lines = visitLines(
      leg({ visits: [bustedVisit({ scoreBefore: 141, labels: ['T20', 'T20', 'T20'] })] }),
      names,
    )
    const line = lines[0]!
    expect(line.isBust).toBe(true)
    expect(line.scored).toBe(0)
    // The claim criterion 3 makes: three darts thrown, nothing scored.
    expect(line.dartsThrown).toBe(3)
    expect(line.darts).toEqual(['T20', 'T20', 'T20'])
    expect(line.bustDart).toBe('T20')
    expect(line.scoreAfter).toBe(141)
  })

  it('names the dart that caused the bust, not merely the last one', () => {
    const lines = visitLines(
      leg({
        visits: [bustedVisit({ scoreBefore: 20, labels: ['T20', 'D5', 'MISS'], bustAt: 0 })],
      }),
      names,
    )
    expect(lines[0]!.bustDart).toBe('T20')
  })

  it('says the whole thing in the label, since a strike-through is only visual', () => {
    const lines = visitLines(
      leg({ visits: [bustedVisit({ scoreBefore: 141, labels: ['T20', 'T20', 'T20'] })] }),
      names,
    )
    const label = lines[0]!.label
    expect(label).toContain('bust')
    expect(label).toContain('scored nothing')
    // The part a strike-through would otherwise contradict.
    expect(label).toContain('3 darts thrown')
    expect(label).toContain('still on 141')
  })

  it('reports an ordinary visit with what it scored and what is left', () => {
    const lines = visitLines(
      leg({
        visits: [visit({ scoreBefore: 501, scoreAfter: 441, labels: ['T20', 'MISS', 'MISS'] })],
      }),
      names,
    )
    const line = lines[0]!
    expect(line.isBust).toBe(false)
    expect(line.scored).toBe(60)
    expect(line.scoreText).toBe('441 left')
    expect(line.label).toContain('scored 60')
  })

  it('calls the score points in a cricket leg, where it is not a remainder', () => {
    const lines = visitLines(
      leg({ visits: [visit({ scoreBefore: 0, scoreAfter: 12, labels: ['T20'] })] }),
      names,
      'cricket',
    )
    expect(lines[0]!.scoreText).toBe('12 pts')
    expect(lines[0]!.label).toContain('12 pts')
  })

  it('names a player the match has no name for rather than showing a blank', () => {
    const lines = visitLines(
      leg({
        visits: [visit({ playerId: 99, scoreBefore: 501, scoreAfter: 501, labels: ['MISS'] })],
      }),
      names,
    )
    expect(lines[0]!.playerName).toBe('Player 99')
  })

  it('keeps the visits in the order they were thrown', () => {
    const lines = visitLines(
      leg({
        visits: [
          visit({ visitIndex: 0, scoreBefore: 501, scoreAfter: 441, labels: ['T20'] }),
          visit({
            visitIndex: 1,
            playerId: DAD,
            scoreBefore: 501,
            scoreAfter: 501,
            labels: ['MISS'],
          }),
          visit({ visitIndex: 2, scoreBefore: 441, scoreAfter: 381, labels: ['T20'] }),
        ],
      }),
      names,
    )
    expect(lines.map((line) => line.visitIndex)).toEqual([0, 1, 2])
  })

  it('handles a visit of one dart without saying "1 darts"', () => {
    const lines = visitLines(
      leg({ visits: [bustedVisit({ scoreBefore: 20, labels: ['T20'] })] }),
      names,
    )
    expect(lines[0]!.label).toContain('1 dart thrown')
    expect(lines[0]!.label).not.toContain('1 darts')
  })
})

describe('visitLabel on an empty visit', () => {
  it('says there were no darts rather than rendering an empty list', () => {
    expect(
      visitLabel({
        visitId: 1,
        visitIndex: 0,
        playerId: JACK,
        playerName: 'Jack',
        darts: [],
        scored: 0,
        isBust: false,
        bustDart: null,
        scoreBefore: 501,
        scoreAfter: 501,
        dartsThrown: 0,
        scoreText: '501 left',
      }),
    ).toContain('no darts')
  })

  it('omits the causing dart when a bust somehow has none named', () => {
    expect(
      visitLabel({
        visitId: 1,
        visitIndex: 0,
        playerId: JACK,
        playerName: 'Jack',
        darts: ['T20'],
        scored: 0,
        isBust: true,
        bustDart: null,
        scoreBefore: 141,
        scoreAfter: 141,
        dartsThrown: 1,
        scoreText: '141 left',
      }),
    ).toBe('Jack: T20, bust, scored nothing, but 1 dart thrown, still on 141')
  })
})

describe('legDartsThrown', () => {
  it('counts busted darts, which is the whole point', () => {
    const counted = leg({
      visits: [
        visit({
          visitIndex: 0,
          scoreBefore: 501,
          scoreAfter: 441,
          labels: ['T20', 'MISS', 'MISS'],
        }),
        bustedVisit({ visitIndex: 1, scoreBefore: 141, labels: ['T20', 'T20', 'T20'] }),
      ],
    })
    // Six darts thrown, three of which scored nothing.
    expect(legDartsThrown(counted)).toBe(6)
  })

  it('is zero for a leg nobody has thrown in', () => {
    expect(legDartsThrown(leg({ visits: [] }))).toBe(0)
  })
})
