/**
 * The cheapest proof that this is the image under test and not something else
 * answering on the port: it serves the app, and reports the commit it was
 * built from. CI builds the image with `GIT_SHA=${{ github.sha }}` and passes
 * the same value as `E2E_GIT_SHA`. Run locally without it, this only checks the
 * image was stamped at all -- an unstamped build says "unknown".
 */
import { expect, test } from '@playwright/test'

test('the built image serves the app and reports the commit under test', async ({
  page,
  request,
}) => {
  const response = await request.get('/api/version')
  expect(response.ok()).toBe(true)
  const { git_sha } = (await response.json()) as { git_sha: string }
  const expected = process.env.E2E_GIT_SHA
  if (expected) expect(git_sha).toBe(expected)
  else expect(git_sha).not.toBe('unknown')

  await page.goto('/')
  // The page title is the app's; a dev server would serve the same HTML, which
  // is why the version check above comes first.
  await expect(page).toHaveTitle(/darts/i)
})
