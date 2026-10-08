/**
 * The few things every spec does: add a player, start a match, throw a dart.
 *
 * Everything goes through the UI a person would touch, found by role and
 * accessible name. Nothing here calls the API to set up state: #32 is a test of
 * the whole thing working together, and seeding through the API would skip
 * exactly the screens it exists to cover.
 */
import { expect, type Locator, type Page } from '@playwright/test'

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
 * Start a match on /setup and return its id.
 *
 * `players` are tapped in order, so two make one against one and one is #68's
 * practice match.
 * `game` is the name of the game button ("501", "Cut-throat cricket").
 * `outRule` is the Out rule radio's name ("Double out") and is x01 only; left
 * out, the match plays whatever /setup opens on.
 *
 * The leg count is stepped from wherever the screen opens, up or down, and
 * asserted rather than assumed. The first version only counted down from
 * three and never set the out rule, so x01.spec quietly depended on the old
 * double-out, first-to-three defaults. #67 changed them to straight out and one
 * leg, and the spec's scripted bust only exists under double out. A spec
 * whose figures depend on a rule has to say so here.
 */
export async function startMatch(
  page: Page,
  options: {
    game: string
    players: [string, ...string[]]
    legsToWin: number
    outRule?: string
  },
): Promise<number> {
  await page.goto('/setup')
  await page.getByRole('button', { name: options.game, exact: true }).click()
  if (options.outRule !== undefined) {
    const outRule = page.getByRole('radiogroup', { name: 'Out rule' })
    await outRule.getByRole('radio', { name: options.outRule, exact: true }).click()
    await expect(outRule.getByRole('radio', { name: options.outRule, exact: true })).toBeChecked()
  }
  for (const name of options.players) {
    await page.getByRole('button', { name: new RegExp(`^${name},`) }).click()
  }
  const legs = page.getByRole('region', { name: 'Rules' }).getByRole('status')
  const shown = Number(await legs.textContent())
  const direction = shown < options.legsToWin ? 'Increase' : 'Decrease'
  for (let step = 0; step < Math.abs(options.legsToWin - shown); step++) {
    await page.getByRole('button', { name: `${direction} Legs to win` }).click()
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
  if (code === 'MISS')
    return landed(page, 'darts', page.getByRole('button', { name: 'Miss', exact: true }))
  if (code === 'BULL') return landed(page, 'darts', page.getByRole('button', { name: 'Bull, 50' }))
  if (code === '25')
    return landed(page, 'darts', page.getByRole('button', { name: 'Outer bull, 25' }))
  const match = /^([DT]?)(\d{1,2})$/.exec(code)
  if (!match) throw new Error(`not a dart: ${code}`)
  const [, multiplier, number] = match
  if (multiplier === 'D') await page.getByRole('radio', { name: 'Double' }).click()
  if (multiplier === 'T') await page.getByRole('radio', { name: 'Triple' }).click()
  await landed(
    page,
    'darts',
    page.getByRole('button', { name: new RegExp(`^${String(number)}, `) }),
  )
}

/** Tap UNDO, and wait until the server has taken the dart back. */
export async function undo(page: Page): Promise<void> {
  await landed(page, 'undo', page.getByRole('button', { name: 'UNDO' }))
}

/**
 * Tap a key and wait until its request has been answered and the screen has
 * caught up, as a person waits to see a dart appear before throwing the next.
 *
 * The keypad deliberately ignores a tap while a dart is in flight (#24), and
 * Playwright's click still "succeeds" -- so a script that taps without waiting
 * loses darts silently, the more so on a slow runner. That is how #32's first
 * CI runs on the PR failed. Waiting for the response is not enough on its own:
 * the keypad unlocks on the re-render that follows, which TanStack Query
 * schedules on a later task, hence the two timer hops and a frame.
 */
async function landed(page: Page, kind: 'darts' | 'undo', key: Locator): Promise<void> {
  const answered = page.waitForResponse(
    (response) =>
      response.request().method() === 'POST' &&
      new RegExp(`/api/legs/\\d+/${kind}$`).test(new URL(response.url()).pathname),
  )
  await key.click()
  const response = await answered
  if (!response.ok()) throw new Error(`${kind} was refused: ${String(response.status())}`)
  await page.evaluate(
    () =>
      new Promise<void>((resolve) => {
        setTimeout(() => setTimeout(() => requestAnimationFrame(() => resolve()), 0), 0)
      }),
  )
}

/** Throw every dart of a visit, in order. */
export async function visit(page: Page, ...codes: string[]): Promise<void> {
  for (const code of codes) await dart(page, code)
}

/**
 * Hold every dart and undo request for `ms` before it reaches the server.
 *
 * The keypad ignores taps while a dart is in flight (#24: the keys are not
 * greyed, the tap is simply dropped). On a fast machine a scripted tap almost
 * never lands in that window; on a slow CI runner some did, and darts went
 * missing. Making the window wide on every run turns that from a race that
 * depends on the runner into something every run exercises.
 */
export async function lanLatency(page: Page, ms: number): Promise<void> {
  await page.route(/\/api\/legs\/\d+\/(darts|undo)$/, async (route) => {
    await new Promise((resolve) => setTimeout(resolve, ms))
    await route.continue()
  })
}
