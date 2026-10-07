// Temporary: fails on purpose to prove CI uploads the trace. Removed in the next commit.
import { expect, test } from '@playwright/test'

test('deliberately failing, to prove trace upload', async ({ page }) => {
  await page.goto('/players')
  await expect(page.getByRole('heading', { name: 'This heading does not exist' })).toBeVisible({
    timeout: 2_000,
  })
})
