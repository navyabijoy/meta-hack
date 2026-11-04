// Hidden reference test: written by hand, never shown to Gecko. Used by the eval to check fixes.
import { test, expect } from '@playwright/test'

test.beforeEach(async ({ page }) => {
  await page.goto('/')
  await page.evaluate(() => localStorage.clear())
  await page.reload()
})

test('delete under Active filter removes the clicked task', async ({ page }) => {
  for (const t of ['A', 'B', 'C']) {
    await page.getByLabel('New task').fill(t)
    await page.getByRole('button', { name: 'Save' }).click()
    await expect(page.getByText(t, { exact: true })).toBeVisible()
  }
  await page.getByText('A', { exact: true }).click()
  await page.getByRole('button', { name: 'Active' }).click()
  await page.getByRole('button', { name: 'Delete C' }).click()
  await page.getByRole('button', { name: 'All' }).click()
  await expect(page.getByText('C', { exact: true })).toHaveCount(0)
  await expect(page.getByText('B', { exact: true })).toHaveCount(1)
})
