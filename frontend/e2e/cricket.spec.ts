/**
 * #32 spec 2: a full cut-throat cricket leg, asserting that points go to the
 * opponents of the thrower.
 *
 * In cut-throat, a mark past three on a number you have closed scores for you
 * against every opponent who has not closed it: the points land in *their*
 * column, and the fewest points wins. The script makes that visible in both
 * directions before closing out:
 *
 *   Cal  T20 20 20    closes 20, then two 20s onto Dee      Cal 0,  Dee 40
 *   Dee  T19 19 MISS  closes 19, then one 19 onto Cal       Cal 19, Dee 40
 *   Cal  T19 T18 T17  closes 19 (now dead), 18, 17
 *   Dee  MISS x3
 *   Cal  T16 T15 BULL closes 16, 15, two marks on the bull
 *   Dee  MISS x3
 *   Cal  25           closes the bull: all closed, 19 < 40 -> Cal wins
 *
 * It is one leg, one leg to win, so the leg is the match and ends on the
 * match sheet. tests/api/playfixtures.py's CRICKET_CLOSE_OUT was no use here:
 * it scores nought by design, which is exactly what this spec must not do.
 */
import { expect, type Page, test } from '@playwright/test'
import { addPlayer, lanLatency, startMatch, visit } from './helpers.ts'

/** A team's header on the board, whose label leads with its points. */
function points(page: Page, name: string) {
  return page.getByLabel(new RegExp(`^${name}, \\d+ points`))
}

test('cut-throat cricket: points accrue to the opponent, fewest points wins', async ({ page }) => {
  await lanLatency(page, 150)
  await addPlayer(page, 'Cal')
  await addPlayer(page, 'Dee')
  await startMatch(page, { game: 'Cut-throat cricket', players: ['Cal', 'Dee'], legsToWin: 1 })
  await expect(page.getByText('Cricket · Cut-throat · Leg 1 · Best of 1')).toBeVisible()
  await expect(points(page, 'Cal')).toHaveAccessibleName(/^Cal, 0 points/)
  await expect(points(page, 'Dee')).toHaveAccessibleName(/^Dee, 0 points/)

  await test.step("Cal's surplus 20s score for Dee, not Cal", async () => {
    await visit(page, 'T20')
    await expect(page.getByRole('cell', { name: 'Cal, 20, 3 marks, closed' })).toBeVisible()
    await visit(page, '20')
    await expect(points(page, 'Dee')).toHaveAccessibleName(/^Dee, 20 points/)
    await visit(page, '20')
    await expect(points(page, 'Dee')).toHaveAccessibleName(/^Dee, 40 points/)
    await expect(points(page, 'Cal')).toHaveAccessibleName(/^Cal, 0 points/)
  })

  await test.step("Dee's surplus 19 scores for Cal, not Dee", async () => {
    await visit(page, 'T19', '19')
    await expect(points(page, 'Cal')).toHaveAccessibleName(/^Cal, 19 points/)
    await expect(points(page, 'Dee')).toHaveAccessibleName(/^Dee, 40 points/)
    await visit(page, 'MISS')
  })

  await test.step('Cal closes everything and wins on fewer points', async () => {
    await visit(page, 'T19', 'T18', 'T17')
    await expect(page.getByRole('cell', { name: 'Cal, 19, 3 marks, dead' })).toBeVisible()
    await visit(page, 'MISS', 'MISS', 'MISS')
    await visit(page, 'T16', 'T15', 'BULL')
    await visit(page, 'MISS', 'MISS', 'MISS')
    await visit(page, '25')

    // Nothing Cal threw on the way out added to anybody: 19 and 40 at the end.
    await expect(points(page, 'Cal')).toHaveAccessibleName(/^Cal, 19 points/)
    await expect(points(page, 'Dee')).toHaveAccessibleName(/^Dee, 40 points/)

    const sheet = page.getByRole('dialog', { name: 'Match complete' })
    await expect(sheet.getByText('Cal won the match')).toBeVisible()
    await expect(sheet.getByRole('listitem', { name: 'Cal, 1 leg, winner' })).toBeVisible()
    // 23 marks in 10 darts, 4 marks in 9. This sheet's query is fetched for
    // the first time here, so -- unlike spec 1's second sheet -- it is fresh.
    await expect(
      sheet.getByRole('listitem', { name: 'Cal, 6.90 marks per round, 10 darts thrown' }),
    ).toBeVisible()
    await expect(
      sheet.getByRole('listitem', { name: 'Dee, 1.33 marks per round, 9 darts thrown' }),
    ).toBeVisible()
  })
})
