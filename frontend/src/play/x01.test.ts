/**
 * What the x01 screen decides, against payloads it can be handed.
 *
 * These are the states that are awkward to reach by tapping and easy to get
 * wrong by reading: the one response that carries two legs, the third of the
 * time when `current_visit` is null, a bust, a won match that can still be
 * undone, and a score with no finish. Each is stated as a payload and checked
 * as a rendering decision -- which is the whole claim of this ticket, since the
 * arithmetic all happened on the server.
 */
import { describe, expect, it } from 'vitest'
import { leg, matchState, pairTeams, visit } from './statefixture'
import { bustOf, cardLabel, checkoutText, contextLine, teamCards, visitTotal } from './x01'

describe('the bust banner', () => {
  it('names the dart and the score the visit went back to', () => {
    // `score_after == score_before` on a bust is the revert, already applied by
    // the server. Nothing here recomputes it.
    const busted = visit({
      scoreBefore: 121,
      scoreAfter: 121,
      labels: ['T20', 'T20'],
      isBust: true,
      bustAt: 1,
    })

    expect(visitTotal(busted)).toBe(0)
    expect(bustOf(busted)).toEqual({ dart: 'T20', revertedTo: 121 })
  })

  it('is absent for a visit that did not bust, and for no visit at all', () => {
    const fine = visit({ scoreBefore: 501, scoreAfter: 441, labels: ['T20'], isComplete: false })
    expect(bustOf(fine)).toBeNull()
    expect(bustOf(null)).toBeNull()
  })
})

describe('the checkout strip', () => {
  it('shows the best path, as the server ordered them', () => {
    const checkout = leg({
      checkoutPaths: [
        ['T18', 'D20'],
        ['T20', 'D10'],
      ],
    }).checkout
    expect(checkoutText(checkout)).toBe('T18 D20')
  })

  it('shows a one-dart finish as one throw', () => {
    expect(checkoutText(leg({ checkoutPaths: [['D20']] }).checkout)).toBe('D20')
  })

  it('says there is no finish for a score that cannot be checked out', () => {
    // 185 with three darts, or 3 with one. The criterion #24 asks for by name.
    expect(checkoutText(leg({ checkoutReason: 'not_checkable' }).checkout)).toBe('No finish')
  })

  it('explains every other reason the server can give', () => {
    // The enum is closed, so the mapping is exhaustive and a new reason added
    // server-side would fail to typecheck here rather than render undefined.
    expect(checkoutText(leg({ checkoutReason: 'not_open' }).checkout)).toBe('Not open yet')
    expect(checkoutText(leg({ checkoutReason: 'leg_complete' }).checkout)).toBe('Leg won')
    expect(checkoutText(leg({ checkoutReason: 'match_abandoned' }).checkout)).toBe('Abandoned')
    expect(checkoutText(leg({ checkoutReason: 'no_thrower' }).checkout)).toBe('—')
    expect(checkoutText(leg({ checkoutReason: 'not_x01' }).checkout)).toBe('—')
  })
})

describe('the score cards', () => {
  it('zips the leg tally on to the teams, positionally', () => {
    const state = matchState({ legsWon: [2, 1], remaining: [134, 301] })
    const cards = teamCards(state, state.current_leg)

    expect(cards.map((card) => [card.name, card.score, card.legsWon])).toEqual([
      ['Jack', 134, 2],
      ['Dad', 301, 1],
    ])
  })

  it('marks exactly the team at the oche as active', () => {
    const state = matchState({ thrower: 1 })
    expect(teamCards(state, state.current_leg).map((card) => card.active)).toEqual([false, true])
  })

  it('carries the average through as null until a team has thrown', () => {
    // Null, not 0: the server sends null for a team with no darts, and 0.0 would
    // read as a terrible average rather than an absent one.
    const state = matchState({ averages: [58.4, null] })
    expect(teamCards(state, state.current_leg).map((card) => card.average)).toEqual([58.4, null])
  })

  it('leads a 2v2 card with whoever is throwing, and names the partner below', () => {
    // `ScoreCard` has one name slot and `MemberResponse` has no `short_name`, so
    // the card leads with the thrower -- the thing the screen exists to say --
    // and the rest of the team goes on the second line.
    const state = matchState({ teams: pairTeams(), thrower: 0, throwerMember: 1 })
    const [first, second] = teamCards(state, state.current_leg)

    expect(first?.name).toBe('Ellie')
    expect(first?.teammates).toBe('Jack')
    // Not this team's turn, so it leads with its first member instead.
    expect(second?.name).toBe('Dad')
    expect(second?.teammates).toBe('Sam')
  })

  it('gives a solo team no second line at all', () => {
    const state = matchState()
    expect(teamCards(state, state.current_leg)[0]?.teammates).toBeUndefined()
  })

  it('reads aloud as a sentence rather than a run-on number', () => {
    const state = matchState({ teams: pairTeams(), legsWon: [1, 0], remaining: [134, 301] })
    const [first, second] = teamCards(state, state.current_leg)

    expect(cardLabel(first!)).toBe('Jack, with Ellie, 134 remaining, 1 leg won, throwing now')
    expect(cardLabel(second!)).toBe('Dad, with Sam, 301 remaining, 0 legs won')
  })
})

describe('the context line', () => {
  it('names the game, the leg as a human counts it, and the match length', () => {
    const state = matchState({ startScore: 501, legIndex: 1, bestOf: 5 })
    expect(contextLine(state, state.current_leg)).toBe('501 · Leg 2 · Best of 5')
  })
})

describe('a payload that does not line up', () => {
  it('degrades instead of crashing, rather than trusting positional alignment', () => {
    // None of this is a response the server sends: `legs_won` is built from
    // `teams`, every team has a member, and the leg carries an entry per team.
    // But all three are positional or looked-up, and the failure mode of getting
    // one wrong is a white screen at a board -- so the fallbacks are real code
    // and this is what exercises them.
    const state = matchState({ legsWon: [1, 0] })
    const broken: typeof state = {
      ...state,
      // The second team, which is not the one at the oche -- an active team is
      // named from `next_thrower` and so survives having no members at all.
      teams: [state.teams[0]!, { ...state.teams[1]!, members: [] }],
      legs_won: [],
      current_leg: { ...state.current_leg, teams: [] },
    }

    const cards = teamCards(broken, broken.current_leg)

    expect(cards[0]).toMatchObject({ name: 'Jack', score: null, average: null, legsWon: 0 })
    expect(cards[1]).toMatchObject({ name: 'Team 2', score: null, average: null, legsWon: 0 })
    // A card with no score says so by omission rather than by claiming 0 left.
    expect(cardLabel(cards[1]!)).toBe('Team 2, 0 legs won')
  })

  it('names the game type when there is no start score, as in cricket', () => {
    // `start_score` is absent on a cricket config -- `GameConfig` refuses it --
    // so the context line says "cricket" rather than "undefined".
    const state = matchState({ gameType: 'cricket', legIndex: 0, bestOf: 1 })
    expect(contextLine(state, state.current_leg)).toBe('cricket · Leg 1 · Best of 1')
  })
})
