/**
 * #68's single-sided match, as the screens name it. `isSingleSided` is the team
 * count and nothing else -- in particular it is not `is_solo`, which a 1v1 has
 * on both teams.
 */
import { describe, expect, it } from 'vitest'
import { pairTeams, practiceTeams, soloTeams } from '../play/statefixture'
import { isSingleSided, legsText, outcomeVerb } from './sides'

describe('isSingleSided', () => {
  it('is one team, and nothing to do with is_solo', () => {
    expect(isSingleSided({ teams: practiceTeams() })).toBe(true)
    // Two is_solo teams: a 1v1, not practice.
    expect(soloTeams().every((team) => team.is_solo)).toBe(true)
    expect(isSingleSided({ teams: soloTeams() })).toBe(false)
    expect(isSingleSided({ teams: pairTeams() })).toBe(false)
  })
})

describe('the words for a finish', () => {
  it('says won against somebody and finished alone', () => {
    expect(outcomeVerb(false)).toBe('won')
    expect(outcomeVerb(true)).toBe('finished')
  })

  it('counts legs in the same words', () => {
    expect(legsText(1, false)).toBe('1 leg won')
    expect(legsText(2, true)).toBe('2 legs finished')
    expect(legsText(0, true)).toBe('0 legs finished')
  })
})
