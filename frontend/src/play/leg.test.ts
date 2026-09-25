/**
 * What a leg decides, whatever game is being played on it.
 *
 * The states that are awkward to reach by tapping and easy to get wrong by
 * reading: the one response that carries two legs, the third of the time when
 * `current_visit` is null, a won match that can still be undone. #24 wrote
 * these against `x01.ts` because x01 was the only board there was; they moved
 * here with the functions, unchanged, because #25's board asks the same
 * questions of the same payload.
 */
import { describe, expect, it } from 'vitest'
import { ApiError, OfflineError } from '../api/client'
import { leg, matchState, visit } from './statefixture'
import {
  canUndo,
  isPlayable,
  legInPlay,
  legToUndo,
  mintDartId,
  refusalText,
  shownVisit,
} from './leg'

describe('which leg is in play', () => {
  it('is the current leg for all but one response in a match', () => {
    const state = matchState({ legId: 7 })
    expect(legInPlay(state).leg_id).toBe(7)
  })

  it('is the active leg on the one response that reports a leg won', () => {
    // `active_leg` is non-null only here, and `current_leg` is the leg that was
    // just won. Rolling straight on to the new leg is #24's behaviour; #26 owns
    // the interstitial that stops to say who won it.
    const next = leg({ legId: 8, legIndex: 1 })
    const state = matchState({ legId: 7, winnerTeamId: 1, activeLeg: next })

    expect(state.current_leg.leg_id).toBe(7)
    expect(legInPlay(state).leg_id).toBe(8)
  })
})

describe('whether a dart can be thrown', () => {
  it('yes, for a match in progress with somebody at the oche', () => {
    expect(isPlayable(matchState())).toBe(true)
  })

  it('no, for a won match', () => {
    expect(isPlayable(matchState({ status: 'complete', winner: 1 }))).toBe(false)
  })

  it('no, for an abandoned match', () => {
    // #18 withholds `active_leg_id` and the thrower for an abandoned match, so
    // it reads but offers nothing to do.
    expect(isPlayable(matchState({ status: 'abandoned', thrower: null }))).toBe(false)
  })

  it('is decided the same way for a cricket match', () => {
    // The leg-level questions are game-agnostic, which is the whole reason this
    // module exists. A cricket match in progress accepts a dart on the same
    // three conditions an x01 one does.
    expect(isPlayable(matchState({ gameType: 'cricket' }))).toBe(true)
    expect(isPlayable(matchState({ gameType: 'cricket', status: 'complete', winner: 1 }))).toBe(
      false,
    )
  })
})

describe('which leg an undo addresses', () => {
  it('the leg in play, normally', () => {
    const state = matchState({ legId: 7, dartsThrown: 4 })
    expect(legToUndo(state).leg_id).toBe(7)
    expect(canUndo(state)).toBe(true)
  })

  it('the leg that was won, when the new one has no darts yet', () => {
    // The dart somebody wants back at a leg boundary is the one that won the
    // leg, not a dart in the empty leg opened behind it. `play.undo` reopens the
    // won leg for exactly this and refuses only if a *later* leg was thrown into.
    const next = leg({ legId: 8, legIndex: 1, dartsThrown: 0 })
    const state = matchState({ legId: 7, dartsThrown: 9, winnerTeamId: 1, activeLeg: next })

    expect(legInPlay(state).leg_id).toBe(8)
    expect(legToUndo(state).leg_id).toBe(7)
  })

  it('is offered on a won match, which is the only way to fix the winning dart', () => {
    const state = matchState({ status: 'complete', winner: 1, dartsThrown: 9 })
    expect(isPlayable(state)).toBe(false)
    expect(canUndo(state)).toBe(true)
  })

  it('is refused on an abandoned match, and with nothing thrown', () => {
    expect(canUndo(matchState({ status: 'abandoned', dartsThrown: 9 }))).toBe(false)
    expect(canUndo(matchState({ dartsThrown: 0 }))).toBe(false)
  })
})

describe('the visit on screen', () => {
  it('is the one being thrown while it is being thrown', () => {
    const current = visit({ scoreBefore: 501, scoreAfter: 441, labels: ['T20'], isComplete: false })
    expect(shownVisit(leg({ currentVisit: current }))?.visit_id).toBe(current.visit_id)
  })

  it('falls back to the visit that just finished, so the third dart is visible', () => {
    // `current_visit` goes null the moment a visit ends. Showing only that would
    // make every third dart vanish as it was entered.
    const previous = visit({ scoreBefore: 501, scoreAfter: 321, labels: ['T20', 'T20', 'T20'] })
    expect(shownVisit(leg({ currentVisit: null, previousVisit: previous }))?.visit_id).toBe(
      previous.visit_id,
    )
  })

  it('is nothing at all before the first dart of a leg', () => {
    expect(shownVisit(leg())).toBeNull()
  })
})

describe('a refusal', () => {
  function conflict(reason: string) {
    return new ApiError(409, {
      error: { code: 'conflict', message: 'raw server message', detail: { reason } },
    })
  }

  it('is said in words, keyed on the discriminator and never the message', () => {
    expect(refusalText(conflict('leg_complete'), 'leg_complete')).toBe(
      'That leg has been won already.',
    )
    expect(refusalText(conflict('nothing_to_undo'), 'nothing_to_undo')).toBe(
      'There is nothing left to undo.',
    )
  })

  it('falls back to the server message when there is no reason to key on', () => {
    expect(refusalText(new OfflineError(new Error('down')), null)).toBe(
      'Cannot reach the scoreboard',
    )
    expect(refusalText(conflict('something_new'), 'something_new')).toBe('raw server message')
  })
})

describe('the dart id', () => {
  it('is fresh every time, because two taps are two darts', () => {
    const ids = new Set(Array.from({ length: 200 }, () => mintDartId()))
    expect(ids.size).toBe(200)
  })

  it('is non-blank and short enough for the column', () => {
    const id = mintDartId()
    expect(id.trim()).not.toBe('')
    expect(id.length).toBeLessThanOrEqual(128)
  })
})

describe('the dart id without a secure context', () => {
  it('falls back when randomUUID is missing, because the Pi serves plain HTTP', () => {
    // `crypto.randomUUID` is absent outside a secure context, and the Pi is
    // http:// on the LAN -- so this branch is the one that runs in the place
    // this app actually lives, not an exotic fallback.
    const real = globalThis.crypto
    Object.defineProperty(globalThis, 'crypto', { value: {}, configurable: true })
    try {
      const ids = new Set(Array.from({ length: 50 }, () => mintDartId()))
      expect(ids.size).toBe(50)
      for (const id of ids) {
        expect(id).toMatch(/^dart-\d+-[a-z0-9]+$/)
        expect(id.length).toBeLessThanOrEqual(128)
      }
    } finally {
      Object.defineProperty(globalThis, 'crypto', { value: real, configurable: true })
    }
  })
})
