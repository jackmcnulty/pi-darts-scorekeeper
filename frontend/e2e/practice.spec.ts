/**
 * #68: a practice match -- one player, nobody to beat -- played to the end, and
 * the stats it leaves behind.
 *
 * Pip plays 501, straight out (#67's default), first to two legs. Each leg is
 * the same nine darts:
 *
 *   Pip  T20 T20 T20 | T20 T20 T20 | T20 T20 T7    -> 501 in 9 darts
 *
 * Every visit is Pip's, because there is nobody else to throw. Two legs, 18
 * darts, 1002 points: a 3-dart average of 3 x 1002 / 18 = 167.00.
 *
 * What #68 decided, and what this asserts against the real image:
 *   - the board, the leg sheet and the match sheet say "finished", never "won";
 *   - the darts count: the card shows all 18 and the 167.00 average;
 *   - the match does not: "Legs won 0 of 0" and "Matches won 0 of 0".
 *
 * 18 darts is under the leaderboard's 50 (`DEFAULT_MIN_DARTS`), so Pip is not
 * on it, and x01.spec's exact Ava-then-Ben leaderboard still holds when this
 * file runs first. The card is reached by id, read from the match with a GET:
 * a read rather than seeding, since the leaderboard is the only link to a card
 * and Pip is rightly not on it.
 */
import { expect, type Page, test } from '@playwright/test'
import { addPlayer, lanLatency, startMatch, visit } from './helpers.ts'

const PIP = 'Pip'

/** The score tile, found by the start of its label. */
function tile(page: Page) {
  return page.getByRole('group', { name: new RegExp(`^${PIP}, `) })
}

/** The "All time" cell of one row of a stat card table. */
function allTime(page: Page, table: string, metric: string) {
  return page
    .getByRole('table', { name: table, exact: true })
    .getByRole('row', { name: new RegExp(`^${metric}: `) })
    .getByRole('cell')
    .nth(1)
}

async function nineDarter(page: Page): Promise<void> {
  await visit(page, 'T20', 'T20', 'T20')
  await visit(page, 'T20', 'T20', 'T20')
  await visit(page, 'T20', 'T20', 'T7')
}

test('practice 501, first to two: finished not won, darts counted, match not', async ({ page }) => {
  await lanLatency(page, 150)
  await addPlayer(page, PIP)
  const matchId = await startMatch(page, { game: '501', players: [PIP], legsToWin: 2 })
  await expect(page.getByText('501 · Leg 1 · Best of 3')).toBeVisible()
  // One tile, and nobody else on the board.
  await expect(page.getByRole('group', { name: /remaining/ })).toHaveCount(1)
  await expect(tile(page)).toHaveAccessibleName('Pip, 501 remaining, 0 legs finished, throwing now')

  await test.step('leg 1: finished, and Pip throws first again', async () => {
    await nineDarter(page)
    const sheet = page.getByRole('dialog', { name: 'Leg 1 complete' })
    await expect(sheet.getByText('Pip finished the leg')).toBeVisible()
    await expect(sheet.getByText('Pip throws first in leg 2')).toBeVisible()
    await sheet.getByRole('button', { name: 'Continue' }).click()
  })

  await test.step('leg 2 finishes the match', async () => {
    await expect(page.getByText('501 · Leg 2 · Best of 3')).toBeVisible()
    await expect(tile(page)).toHaveAccessibleName(/^Pip, 501 remaining, 1 leg finished/)
    await nineDarter(page)
    const sheet = page.getByRole('dialog', { name: 'Match complete' })
    await expect(sheet.getByText('Pip finished the match')).toBeVisible()
    // The tally names nobody as the winner.
    await expect(sheet.getByRole('listitem', { name: 'Pip, 2 legs' })).toBeVisible()
    await expect(page.getByText(/won the/)).toHaveCount(0)
  })

  await test.step("the card counts Pip's darts but not the match", async () => {
    const match = (await (await page.request.get(`/api/matches/${String(matchId)}`)).json()) as {
      teams: { members: { player_id: number }[] }[]
    }
    expect(match.teams).toHaveLength(1)
    const pipId = match.teams[0]?.members[0]?.player_id
    await page.goto(`/stats/${String(pipId)}`)
    await expect(page.getByRole('heading', { level: 1, name: PIP })).toBeVisible()
    const expected: Record<string, Record<string, string>> = {
      Overall: { 'Darts thrown': '18', 'Legs won': '0 of 0', 'Matches won': '0 of 0' },
      x01: { '3-dart average': '167.00', 'x01 darts': '18' },
    }
    for (const [table, metrics] of Object.entries(expected)) {
      for (const [metric, value] of Object.entries(metrics)) {
        await expect(allTime(page, table, metric), `${table} / ${metric}`).toHaveText(value)
      }
    }
  })
})
