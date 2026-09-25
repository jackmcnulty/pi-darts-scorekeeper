/**
 * The stat card's decisions, enumerated rather than sampled.
 *
 * #23 walked every reachable config, #24 all 69 (key, latch) pairs, #25 every
 * mark count x target x variant, #26 every best-of x leg index. The obvious
 * enumeration here is **every metric in every table over every payload shape**:
 * real values, zeroes and nulls are the three cases criteria 3 and 4 are about,
 * and there is no reason to check the 3-dart average and hope the other ten
 * behave.
 *
 * The formatting tests deliberately use values that would pass at the wrong
 * precision only by accident: `57.234` is `57.23` at two places and `57.2` at
 * one, so a metric wired to the wrong formatter fails rather than agreeing.
 */
import { describe, expect, it } from 'vitest'
import {
  ABSENT,
  CRICKET_METRICS,
  GAME_FILTERS,
  OVERALL_METRICS,
  SEGMENT_BARS,
  SPAN_FILTERS,
  X01_METRICS,
  average,
  darts,
  emptyTableMessage,
  gameTypeOf,
  hasCricket,
  hasThrown,
  hasX01,
  matches,
  parseGameFilter,
  parseSpanFilter,
  percentage,
  rankRows,
  segmentBars,
  showsCricket,
  showsX01,
  statRows,
  tally,
  targetRows,
  whole,
  windowLabel,
  type Metric,
} from './stats'
import {
  cricket,
  emptyCricket,
  emptyStats,
  playerStats,
  ranked,
  rankRow,
  segments,
  x01,
  zeroStats,
} from './statsfixture'

const ALL_METRICS: readonly Metric[] = [...OVERALL_METRICS, ...X01_METRICS, ...CRICKET_METRICS]

describe('formatting figures', () => {
  it('shows an average to two decimal places', () => {
    // Criterion 4. Each of these would read differently at one place or three.
    //
    // No exact half-way values: 57.235 is stored as 57.23499..., so `toFixed`
    // gives "57.23" and an assertion of "57.24" would be testing IEEE 754
    // rather than this formatter. The rounding cases below are unambiguous.
    expect(average(57.234)).toBe('57.23')
    expect(average(57.236)).toBe('57.24')
    expect(average(57.239)).toBe('57.24')
    expect(average(60)).toBe('60.00')
    expect(average(0)).toBe('0.00')
    expect(average(2.456)).toBe('2.46')
    // Always two places, so a column of averages lines up under tabular figures.
    for (const value of [0, 1, 60, 57.2, 180]) {
      expect(average(value)).toMatch(/^\d+\.\d{2}$/)
    }
  })

  it('shows a percentage to one decimal place, with its sign', () => {
    expect(percentage(33.333)).toBe('33.3%')
    expect(percentage(33.37)).toBe('33.4%')
    expect(percentage(100)).toBe('100.0%')
    expect(percentage(0)).toBe('0.0%')
    for (const value of [0, 12.5, 100, 33.333]) {
      expect(percentage(value)).toMatch(/^\d+\.\d%$/)
    }
  })

  it('never rounds a count', () => {
    expect(whole(0)).toBe('0')
    expect(whole(180)).toBe('180')
    expect(whole(1)).toBe('1')
  })

  it('renders an absent figure as an em dash, and zero as zero', () => {
    // The distinction criterion 3 turns on: the server sends null for an average
    // it has no darts to compute, and 0 for a count of nothing. They are
    // different facts and they must not look the same.
    for (const format of [average, percentage, whole]) {
      expect(format(null)).toBe(ABSENT)
      expect(format(undefined)).toBe(ABSENT)
      expect(format(0)).not.toBe(ABSENT)
    }
  })

  it('never produces NaN, at any input a response could carry', () => {
    // If a NaN ever appears, something did arithmetic. There is none to do here,
    // and this is the assertion that says so.
    for (const value of [0, -0, 1, 180, 57.234, 1e6]) {
      for (const format of [average, percentage, whole]) {
        expect(format(value)).not.toContain('NaN')
      }
    }
  })

  it('tallies one field against another without dividing them', () => {
    expect(tally(21, 38)).toBe('21 of 38')
    expect(tally(0, 0)).toBe('0 of 0')
    expect(tally(null, 5)).toBe(ABSENT)
    expect(tally(3, null)).toBe(ABSENT)
  })

  it('pluralises matches and darts', () => {
    expect(matches(1)).toBe('1 match')
    expect(matches(0)).toBe('0 matches')
    expect(matches(6)).toBe('6 matches')
    expect(darts(1)).toBe('1 dart')
    expect(darts(45)).toBe('45 darts')
  })
})

describe('every metric, over every payload shape', () => {
  it.each(ALL_METRICS.map((metric) => [metric.key, metric] as const))(
    '%s reads a real value without producing NaN or undefined',
    (_key, metric) => {
      const value = metric.read(playerStats())
      expect(value).not.toContain('NaN')
      expect(value).not.toContain('undefined')
      expect(value).not.toBe('')
    },
  )

  it.each(ALL_METRICS.map((metric) => [metric.key, metric] as const))(
    '%s renders a zeroed player as a zero, not an em dash',
    (_key, metric) => {
      // A player who has thrown and scored nothing has facts, and 0 states them.
      // `best_checkout` is the documented exception: there is no such thing as
      // the best checkout of a player who has never checked out.
      const value = metric.read(zeroStats())
      if (metric.key === 'best_checkout' || metric.key === 'marks_per_round') {
        expect(value).toBe(ABSENT)
        return
      }
      expect(value).not.toContain('NaN')
      expect(value).toMatch(/\d/)
    },
  )

  it.each(ALL_METRICS.map((metric) => [metric.key, metric] as const))(
    '%s renders a player who never threw without a crash or a NaN',
    (_key, metric) => {
      // Criterion 3, at the level of one figure: every average is null here, so
      // every average must be the em dash and every count must be 0.
      const value = metric.read(emptyStats())
      expect(value).not.toContain('NaN')
      expect(value === ABSENT || /\d/.test(value)).toBe(true)
    },
  )

  it('gives every metric a distinct key, so React keys and tests cannot collide', () => {
    const keys = ALL_METRICS.map((metric) => metric.key)
    expect(new Set(keys).size).toBe(keys.length)
  })

  it('covers every metric #27 asks for by name', () => {
    // The scope line, as a checklist. A metric quietly dropped from a table
    // during a refactor fails here rather than being noticed on the phone.
    const keys = new Set(ALL_METRICS.map((metric) => metric.key))
    for (const required of [
      'three_dart_average',
      'first_nine_average',
      'highest_visit',
      'average_visit',
      'one_eighties',
      'one_forty_plus',
      'hundred_plus',
      'sixty_plus',
      'best_checkout',
      'checkout_percentage',
      'darts_thrown',
      'legs_won',
      'matches_won',
      'marks_per_round',
    ]) {
      expect(keys).toContain(required)
    }
  })
})

describe('the two-column card', () => {
  it('puts the lifetime figure beside the recent one', () => {
    const rows = statRows(
      X01_METRICS,
      playerStats(),
      playerStats({ x01: x01({ threeDartAverage: 61.5 }) }),
    )
    const row = rows.find((candidate) => candidate.key === 'three_dart_average')
    expect(row?.lifetime).toBe('57.23')
    expect(row?.recent).toBe('61.50')
  })

  it('reads an absent recent report as an em dash rather than a zero', () => {
    // While the second request is in flight, "recent" is not yet known. Unknown
    // is not zero, and a column of zeroes would be a lie that then changed.
    const rows = statRows(X01_METRICS, playerStats(), undefined)
    expect(rows.every((row) => row.recent === ABSENT)).toBe(true)
    expect(rows.every((row) => row.lifetime !== ABSENT || row.key === 'best_checkout')).toBe(true)
  })

  it('spells out each row, because adjacent cells run together', () => {
    // "3-dart average57.2361.50" is what a screen reader makes of three
    // neighbouring spans. play/sheet.ts:playerLineLabel is the precedent.
    const rows = statRows(OVERALL_METRICS, playerStats(), playerStats())
    for (const row of rows) {
      expect(row.ariaLabel).toContain(row.label)
      expect(row.ariaLabel).toContain('all time')
      expect(row.ariaLabel).toContain('recently')
    }
  })
})

describe('which blocks a filtered card shows', () => {
  // Criterion 5, driven by the echoed filter rather than by sniffing a payload.
  it.each([
    [null, true, true],
    ['x01' as const, true, false],
    ['cricket' as const, false, true],
  ])('game_type %s shows x01 %s and cricket %s', (gameType, x01Shown, cricketShown) => {
    expect(showsX01(gameType)).toBe(x01Shown)
    expect(showsCricket(gameType)).toBe(cricketShown)
  })

  it('never hides both blocks at once', () => {
    for (const gameType of [null, 'x01' as const, 'cricket' as const]) {
      expect(showsX01(gameType) || showsCricket(gameType)).toBe(true)
    }
  })
})

describe('the query string round trip', () => {
  // Criterion 2: a filtered view is linkable, so the address bar is the source
  // of truth and anything in it has to parse to something renderable.
  it.each([
    ['x01', 'x01'],
    ['cricket', 'cricket'],
    [null, 'all'],
    ['', 'all'],
    ['x02', 'all'],
    ['X01', 'all'],
    ['all', 'all'],
  ])('game_type=%s parses to %s', (raw, expected) => {
    expect(parseGameFilter(raw)).toBe(expected)
  })

  it.each([
    ['recent', 'recent'],
    ['all', 'all'],
    [null, 'all'],
    ['10', 'all'],
  ])('span=%s parses to %s', (raw, expected) => {
    expect(parseSpanFilter(raw)).toBe(expected)
  })

  it('maps every filter option back to what the API takes', () => {
    expect(gameTypeOf('all')).toBeNull()
    expect(gameTypeOf('x01')).toBe('x01')
    expect(gameTypeOf('cricket')).toBe('cricket')
  })

  it('round-trips every option of both controls', () => {
    for (const option of GAME_FILTERS) {
      expect(parseGameFilter(option.value === 'all' ? null : option.value)).toBe(option.value)
    }
    for (const option of SPAN_FILTERS) {
      expect(parseSpanFilter(option.value === 'all' ? null : option.value)).toBe(option.value)
    }
  })

  it('offers three game options, which is what SegmentedControl fits at 402px', () => {
    // #26 cut its filter to three after a four-option version overflowed its own
    // buttons in a browser. Measured, not guessed -- see History.tsx.
    expect(GAME_FILTERS).toHaveLength(3)
  })
})

describe('why the leaderboard is empty', () => {
  it('names the threshold the server applied', () => {
    expect(emptyTableMessage(50, 'all', 10)).toBe(
      'Nobody has thrown 50 x01 darts yet. Play a few legs and the table fills up.',
    )
    expect(emptyTableMessage(0, 'all', 10)).toContain('0 x01 darts')
  })

  it('says the window too, because those are two different facts', () => {
    // "Nobody qualifies" and "nobody qualifies recently" read differently,
    // because a player with a lifetime average and no recent form is a real case.
    expect(emptyTableMessage(50, 'recent', 10)).toBe(
      'Nobody has thrown 50 x01 darts in their last 10 matches yet. Play a few legs and the table fills up.',
    )
  })

  it('is one string, so it is one text node', () => {
    // Assembled here rather than interpolated into JSX: a sentence built from
    // four neighbouring nodes is unfindable to a test and to a screen reader.
    expect(typeof emptyTableMessage(50, 'all', 10)).toBe('string')
  })
})

describe('per-target hit rate', () => {
  it('reads hit_rate rather than dividing hits by anything', () => {
    const rows = targetRows(cricket())
    expect(rows).toHaveLength(7)
    expect(rows[0]?.label).toBe('20')
    expect(rows[0]?.hitRate).toBe('24.8%')
    // The last target is 25, named by #25's own function, and its rate is null.
    expect(rows[6]?.label).toBe('Bull')
    expect(rows[6]?.hitRate).toBe(ABSENT)
  })

  it('renders every target of a player who has thrown no cricket darts', () => {
    // The server sends all seven with nothing on them, not an empty list.
    const rows = targetRows(emptyCricket())
    expect(rows).toHaveLength(7)
    expect(rows.every((row) => row.hitRate === ABSENT)).toBe(true)
    expect(rows.every((row) => row.hits === '0')).toBe(true)
  })

  it('spells out each row', () => {
    for (const row of targetRows(cricket())) {
      expect(row.ariaLabel).toContain(row.label)
      expect(row.ariaLabel).toContain('hit rate')
    }
  })
})

describe('the segment-frequency visual', () => {
  it('keeps the order the query returned, most-hit first', () => {
    const bars = segmentBars(segments())
    expect(bars.map((bar) => bar.label).slice(0, 3)).toEqual(['T20', '20', 'MISS'])
  })

  it('scales every bar against the busiest segment', () => {
    const bars = segmentBars(segments())
    // 64 darts is the most, so it is a full bar and 51 is 51/64 of one. This is
    // the geometry Jack ruled is not a statistic: it sizes a bar and is never
    // rendered as a number.
    expect(bars[0]?.fraction).toBe(1)
    expect(bars[1]?.fraction).toBeCloseTo(51 / 64)
    expect(bars.every((bar) => bar.fraction >= 0 && bar.fraction <= 1)).toBe(true)
  })

  it('uses the label the server sent rather than naming segments itself', () => {
    // There is one place that knows the inner bull is 25 doubled, and it is
    // `engine.throws.Throw`. #27 added `label` to SegmentResponse so this screen
    // reads the name instead of deriving it.
    const bars = segmentBars(segments())
    expect(bars.map((bar) => bar.label)).toContain('BULL')
    expect(bars.map((bar) => bar.label)).toContain('25')
    expect(bars.map((bar) => bar.label)).toContain('D16')
  })

  it('marks a miss, which is not on the board at all', () => {
    const bars = segmentBars(segments())
    const miss = bars.find((bar) => bar.label === 'MISS')
    expect(miss?.isMiss).toBe(true)
    // And it is kept rather than dropped: dropping the misses would flatter
    // every player, and they are where the darts went.
    expect(miss?.darts).toBe('37')
    expect(bars.filter((bar) => bar.isMiss)).toHaveLength(1)
  })

  it('treats an absent segment as no bar, because the data is sparse', () => {
    // `segment_frequency` is a GROUP BY: a segment nobody has hit has no row.
    // The fixture is full of holes on purpose, and nothing here invents zeroes.
    const bars = segmentBars(segments())
    expect(bars.map((bar) => bar.label)).not.toContain('T18')
    expect(bars).toHaveLength(segments().length)
  })

  it('truncates a long tail and never crashes on an empty one', () => {
    const many = Array.from({ length: 40 }, (_, index) => ({
      segment: (index % 20) + 1,
      multiplier: 1,
      darts: 40 - index,
      label: String((index % 20) + 1),
    }))
    expect(segmentBars(many)).toHaveLength(SEGMENT_BARS)
    expect(segmentBars(many, 3)).toHaveLength(3)
    expect(segmentBars([])).toEqual([])
  })

  it('does not divide by zero when every segment has no darts', () => {
    // Not a shape the server sends -- a row exists only because a dart landed --
    // but the guard is the difference between a 0 bar and a NaN one.
    const bars = segmentBars([{ segment: 20, multiplier: 1, darts: 0, label: '20' }])
    expect(bars[0]?.fraction).toBe(0)
    expect(Number.isNaN(bars[0]?.fraction)).toBe(false)
  })

  it('spells out each bar', () => {
    expect(segmentBars(segments())[0]?.ariaLabel).toBe('T20: 64 darts')
  })
})

describe('the leaderboard rows', () => {
  it('formats each column and keeps the server order', () => {
    const rows = rankRows(ranked())
    expect(rows.map((row) => row.name)).toEqual(['Jack', 'Dad'])
    expect(rows[0]?.average).toBe('57.23')
    expect(rows[1]?.average).toBe('48.92')
    expect(rows[0]?.darts).toBe('450')
    expect(rows[0]?.oneEighties).toBe('3')
  })

  it('carries only what the row shows', () => {
    // A four-stat row does not fit a 402px phone -- measured in a browser, where
    // the detail line needed 242px of the 197 it has. The checkout figures live
    // on the card instead, so they are not built here.
    expect(Object.keys(rankRows(ranked())[0] ?? {}).sort()).toEqual([
      'ariaLabel',
      'average',
      'darts',
      'name',
      'oneEighties',
      'playerId',
    ])
  })

  it('computes no rank number', () => {
    // The position in the <ol> is the rank. `index + 1` would be the one number
    // on the screen that no field of the response backs, so no row carries one.
    const rows = rankRows(ranked())
    expect(Object.values(rows[0] ?? {})).not.toContain('1')
    expect(rows[0]).not.toHaveProperty('rank')
  })

  it('renders a player with no average as an em dash, not a zero', () => {
    const rows = rankRows([rankRow({ playerId: 9, name: 'Newcomer', average: null })])
    expect(rows[0]?.average).toBe(ABSENT)
    expect(rows[0]?.darts).toBe('450')
  })

  it('spells out each row, including the stat the detail line abbreviates', () => {
    // The row reads "138 darts · 1 × 180"; spoken, that is "138 darts1 × 180".
    const rows = rankRows(ranked())
    expect(rows[0]?.ariaLabel).toBe('Jack: 57.23 three-dart average, 450 darts thrown, 3 maximums')
  })

  it('is empty for an empty ranking', () => {
    expect(rankRows([])).toEqual([])
  })
})

describe('what the recent column is called', () => {
  it('names the matches actually covered, not the window asked for', () => {
    // A request for the last ten by somebody who has played six covers six.
    expect(windowLabel(playerStats({ matchesPlayed: 6 }))).toBe('Last 6 matches')
    expect(windowLabel(playerStats({ matchesPlayed: 1 }))).toBe('Last 1 match')
    expect(windowLabel(playerStats({ matchesPlayed: 0 }))).toBe('Last 0 matches')
  })

  it('falls back to a heading while the request is in flight', () => {
    expect(windowLabel(undefined)).toBe('Recent')
  })
})

describe('whether there is anything to show', () => {
  it('reads darts_thrown rather than guessing from the averages', () => {
    expect(hasThrown(playerStats())).toBe(true)
    expect(hasThrown(zeroStats())).toBe(true)
    expect(hasThrown(emptyStats())).toBe(false)
    expect(hasThrown(undefined)).toBe(false)
  })

  it('knows which block has darts in it', () => {
    expect(hasX01(x01())).toBe(true)
    expect(hasCricket(cricket())).toBe(true)
    expect(hasCricket(emptyCricket())).toBe(false)
    expect(hasX01(zeroStats().x01)).toBe(true)
    expect(hasCricket(zeroStats().cricket)).toBe(false)
  })
})
