/**
 * #32 spec 1: create two players, start a 501 double-out best-of-3, play it
 * with a bust, an undo and a checkout, and verify the numbers on the stats
 * screens.
 *
 * The match is scripted dart for dart so that every figure asserted at the end
 * was worked out by hand first. It is played to the end, 2-0, rather than for
 * one leg: "matches won" only means something for a finished match, and the
 * leaderboard hides anyone under 50 x01 darts (`DEFAULT_MIN_DARTS`), so one leg
 * of about 27 darts each would leave it empty.
 *
 * Ava throws 20-20-20 and Ben 15-15-15, so that nobody ever reaches a finish by
 * accident. Leg 1, Ava first:
 *
 *   Ava  7 x (20 20 20) = 420, then 20 20 1 = 41      -> 40 left
 *   Ava  20 20                                        -> bust (0 on a single)
 *        busting 20 undone and thrown again            -> the same bust
 *   Ava  19, undone; D20                              -> checkout
 *   Ben  9 x (15 15 15)                               -> 96 left
 *
 * Leg 2, Ben first: the same without the bust and the undo.
 *
 *   Ava  52 darts (27 + 25), 1002 points, 19 visits
 *        3-dart average   3 x 1002 / 52   = 57.81
 *        average visit    1002 / 19       = 52.74
 *        first 9 average  6 x 60 / 18 x 3 = 60.00
 *        60+ visits       7 + 7           = 14
 *        checkout         4 attempts (40 and 20 in the bust, 40 twice
 *                         before D20), 2 hit = 50.0%, best 40
 *   Ben  54 darts, 18 visits of 45        -> 45.00 everywhere
 *
 * The average is what proves two of the three moments reached the database:
 * if the bust's two darts were not counted it would be 60.12, and if the
 * undone 19 were it would be 56.72. The busting 20 that is undone and thrown
 * again (#69's undo-after-the-turn check) leaves the same darts in the
 * database, so it moves none of these figures.
 */
import { expect, type Page, test } from '@playwright/test'
import { addPlayer, lanLatency, startMatch, undo, visit } from './helpers.ts'

const AVA = 'Ava'
const BEN = 'Ben'

/** The scoreboard tile for a player, found by the start of its label. */
function tile(page: Page, name: string) {
  return page.getByRole('group', { name: new RegExp(`^${name}, `) })
}

/** Seven 60s for Ava and seven 45s for Ben, in throwing order. */
async function sevenRounds(page: Page, first: 'ava' | 'ben'): Promise<void> {
  for (let round = 0; round < 7; round++) {
    if (first === 'ava') {
      await visit(page, '20', '20', '20')
      await visit(page, '15', '15', '15')
    } else {
      await visit(page, '15', '15', '15')
      await visit(page, '20', '20', '20')
    }
  }
}

/** The "All time" cell of one row of a stat card table. */
function allTime(page: Page, table: string, metric: string) {
  return page
    .getByRole('table', { name: table, exact: true })
    .getByRole('row', { name: new RegExp(`^${metric.replace(/[+%]/g, '\\$&')}: `) })
    .getByRole('cell')
    .nth(1)
}

async function expectCard(page: Page, expected: Record<string, Record<string, string>>) {
  for (const [table, metrics] of Object.entries(expected)) {
    for (const [metric, value] of Object.entries(metrics)) {
      await expect(allTime(page, table, metric), `${table} / ${metric}`).toHaveText(value)
    }
  }
}

test('501 double-out best-of-3: bust, undo, checkout, and the stats that follow', async ({
  page,
}) => {
  await lanLatency(page, 150)
  await addPlayer(page, AVA)
  await addPlayer(page, BEN)
  // Double out by name, not by default: the leg-1 bust below only exists under it.
  await startMatch(page, {
    game: '501',
    players: [AVA, BEN],
    legsToWin: 2,
    outRule: 'Double out',
  })
  await expect(page.getByText('501 · Leg 1 · Best of 3')).toBeVisible()

  // ---- Leg 1 ----
  await sevenRounds(page, 'ava')
  await visit(page, '20', '20', '1')
  await visit(page, '15', '15', '15')
  await expect(tile(page, AVA)).toHaveAccessibleName(/^Ava, 40 remaining, 0 legs won, throwing now/)

  await test.step('bust: 20 then 20 reaches zero on a single', async () => {
    await visit(page, '20', '20')
    await expect(page.getByRole('status').filter({ hasText: 'Bust' })).toHaveText(
      'Bust on 20 — back to 40',
    )
    // #69: the turn has passed to Ben after two darts, so the strip is his and
    // empty. The visit's result stays until his first dart: it scored 0.
    await expect(tile(page, AVA)).toHaveAccessibleName('Ava, 40 remaining, 0 legs won')
    await expect(tile(page, BEN)).toHaveAccessibleName(/throwing now$/)
    await expect(page.getByLabel(/^Dart \d, not thrown$/)).toHaveCount(3)
    await expect(page.getByLabel('Visit scored 0')).toBeVisible()
    // The live average already includes the two busted darts: 3 x 461 / 26.
    await expect(tile(page, AVA)).toContainText('Avg 53.2')

    // Undo after the turn has passed: Ava's visit comes back with its first 20,
    // the bust is gone, and she is throwing at 20 again.
    await undo(page)
    await expect(tile(page, AVA)).toHaveAccessibleName(
      /^Ava, 20 remaining, 0 legs won, throwing now/,
    )
    await expect(page.getByLabel('Dart 1, 20')).toBeVisible()
    await expect(page.getByLabel('Dart 2, not thrown')).toBeVisible()
    await expect(page.getByRole('status').filter({ hasText: 'Bust' })).toHaveCount(0)

    // The same 20 again, the same bust, and the strip clears for Ben again.
    await visit(page, '20')
    await expect(page.getByRole('status').filter({ hasText: 'Bust' })).toHaveText(
      'Bust on 20 — back to 40',
    )
    await expect(tile(page, AVA)).toHaveAccessibleName('Ava, 40 remaining, 0 legs won')
    await expect(page.getByLabel(/^Dart \d, not thrown$/)).toHaveCount(3)
    await expect(tile(page, AVA)).toContainText('Avg 53.2')
  })

  await visit(page, '15', '15', '15')

  await test.step('undo: a wrong 19 is taken back', async () => {
    await visit(page, '19')
    await expect(tile(page, AVA)).toHaveAccessibleName(/^Ava, 21 remaining/)
    await expect(page.getByLabel('Dart 1, 19')).toBeVisible()
    await undo(page)
    await expect(tile(page, AVA)).toHaveAccessibleName(/^Ava, 40 remaining/)
    // The 19 was the only dart of Ava's visit, so undoing it leaves no visit in
    // progress and the strip is empty for her (#69).
    await expect(page.getByLabel('Dart 1, 19')).toHaveCount(0)
    await expect(page.getByLabel(/^Dart \d, not thrown$/)).toHaveCount(3)
  })

  await test.step('checkout: D20 wins leg 1', async () => {
    await visit(page, 'D20')
    const sheet = page.getByRole('dialog', { name: 'Leg 1 complete' })
    await expect(sheet).toBeVisible()
    await expect(sheet.getByText('Ava won the leg')).toBeVisible()
    await expect(sheet.getByLabel('Checkout, D20')).toBeVisible()
    // 27 darts, not 28: the undone 19 is not one of them. 3 x 501 / 27 = 55.7.
    await expect(
      sheet.getByRole('listitem', { name: 'Ava, 55.7 three-dart average, 27 darts thrown' }),
    ).toBeVisible()
    await expect(
      sheet.getByRole('listitem', { name: 'Ben, 45.0 three-dart average, 27 darts thrown' }),
    ).toBeVisible()
    await expect(sheet.getByText('Ben throws first in leg 2')).toBeVisible()
    await sheet.getByRole('button', { name: 'Continue' }).click()
  })

  // ---- Leg 2 ----
  await expect(page.getByText('501 · Leg 2 · Best of 3')).toBeVisible()
  await expect(tile(page, BEN)).toHaveAccessibleName(/throwing now$/)
  await sevenRounds(page, 'ben')
  await visit(page, '15', '15', '15')
  await visit(page, '20', '20', '1')
  await visit(page, '15', '15', '15')
  await visit(page, 'D20')

  const matchSheet = page.getByRole('dialog', { name: 'Match complete' })
  await expect(matchSheet.getByText('Ava won the match')).toBeVisible()
  await expect(matchSheet.getByRole('listitem', { name: 'Ava, 2 legs, winner' })).toBeVisible()
  await expect(matchSheet.getByRole('listitem', { name: 'Ben, 0 legs' })).toBeVisible()
  // The sheet's "Match average" lines are deliberately not asserted. They come
  // from the match-stats query the leg 1 sheet already fetched, which stays
  // fresh for STALE_TIME_MS (5 s) and is not invalidated by a dart -- so a
  // deciding leg thrown in under five seconds, as this one is, shows leg 1's
  // figures. No person throws a leg that fast. Filed as #72, whose fix should
  // add the assertions: Ava 57.8 over 52 darts, Ben 45.0 over 54.

  // ---- The stats screens ----
  await page.goto('/stats')
  const ranks = page.getByRole('list').getByRole('link')
  await expect(ranks).toHaveText([/^Ava/, /^Ben/])
  await expect(ranks.nth(0)).toHaveAccessibleName(
    'Ava: 57.81 three-dart average, 52 darts thrown, 0 maximums',
  )
  await expect(ranks.nth(1)).toHaveAccessibleName(
    'Ben: 45.00 three-dart average, 54 darts thrown, 0 maximums',
  )

  await ranks.nth(0).click()
  await expect(page.getByRole('heading', { level: 1, name: AVA })).toBeVisible()
  await expectCard(page, {
    Overall: { 'Darts thrown': '52', 'Legs won': '2 of 2', 'Matches won': '1 of 1' },
    x01: {
      '3-dart average': '57.81',
      'First 9 average': '60.00',
      'Average visit': '52.74',
      'Highest visit': '60',
      '180s': '0',
      '100+': '0',
      '60+': '14',
      'Checkout %': '50.0%',
      'Best checkout': '40',
      'x01 darts': '52',
    },
  })

  await page.getByRole('link', { name: '← Leaderboard' }).click()
  await page.getByRole('list').getByRole('link').nth(1).click()
  await expect(page.getByRole('heading', { level: 1, name: BEN })).toBeVisible()
  await expectCard(page, {
    Overall: { 'Darts thrown': '54', 'Legs won': '0 of 2', 'Matches won': '0 of 1' },
    x01: {
      '3-dart average': '45.00',
      'First 9 average': '45.00',
      'Average visit': '45.00',
      'Highest visit': '45',
      '60+': '0',
      'Checkout %': '—',
      'Best checkout': '—',
      'x01 darts': '54',
    },
  })
})
