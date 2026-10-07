/**
 * The few things both specs do: add a player, start a match, throw a dart.
 *
 * Everything goes through the UI a person would touch, found by role and
 * accessible name. Nothing here calls the API to set up state: #32 is a test of
 * the whole thing working together, and seeding through the API would skip
 * exactly the screens it exists to cover.
 */
import { expect, type Page } from '@playwright/test'

/** Add a player on /players and wait until the list shows them. */
export async function addPlayer(page: Page, name: string): Promise<void> {
  await page.goto('/players')
  await page.getByRole('button', { name: 'Add', exact: true }).click()
  // `exact`, because Playwright matches accessible names by substring and
  // "Name" is also the end of "Short name".
  await page.getByRole('textbox', { name: 'Name', exact: true }).fill(name)
  await page.getByRole('button', { name: 'Add player' }).click()
  await expect(page.getByRole('button', { name, exact: true })).toBeVisible()
}

/**
 * Start a one-against-one match on /setup and return its id.
 *
 * `game` is the name of the game button ("501", "Cut-throat cricket").
 * Setup opens at three legs to win, so `legsToWin` is reached by tapping
 * Decrease, and asserted rather than assumed.
 */
export async function startMatch(
  page: Page,
  options: { game: string; players: [string, string]; legsToWin: number },
): Promise<number> {
  await page.goto('/setup')
  await page.getByRole('button', { name: options.game, exact: true }).click()
  for (const name of options.players) {
    await page.getByRole('button', { name: new RegExp(`^${name},`) }).click()
  }
  const legs = page.getByRole('region', { name: 'Rules' }).getByRole('status')
  for (let shown = Number(await legs.textContent()); shown > options.legsToWin; shown--) {
    await page.getByRole('button', { name: 'Decrease Legs to win' }).click()
  }
  await expect(legs).toHaveText(String(options.legsToWin))
  await page.getByRole('button', { name: 'Start match' }).click()
  await page.waitForURL(/\/play\/\d+$/)
  return Number(new URL(page.url()).pathname.split('/').pop())
}

/**
 * Throw one dart on the keypad, written the way a scorer says it: "20", "D20",
 * "T19", "25" (outer bull), "BULL", "MISS".
 *
 * The multiplier is a radio above the numbers and drops back to Single after
 * every dart, so a double or triple is two taps, exactly as on the phone.
 */
export async function dart(page: Page, code: string): Promise<void> {
  if (code === 'MISS') {
    await page.getByRole('button', { name: 'Miss', exact: true }).click()
    return
  }
  if (code === 'BULL') {
    await page.getByRole('button', { name: 'Bull, 50' }).click()
    return
  }
  if (code === '25') {
    await page.getByRole('button', { name: 'Outer bull, 25' }).click()
    return
  }
  const match = /^([DT]?)(\d{1,2})$/.exec(code)
  if (!match) throw new Error(`not a dart: ${code}`)
  const [, multiplier, number] = match
  if (multiplier === 'D') await page.getByRole('radio', { name: 'Double' }).click()
  if (multiplier === 'T') await page.getByRole('radio', { name: 'Triple' }).click()
  await page.getByRole('button', { name: new RegExp(`^${String(number)}, `) }).click()
}

/** Throw every dart of a visit, in order. */
export async function visit(page: Page, ...codes: string[]): Promise<void> {
  for (const code of codes) await dart(page, code)
}
