// Hidden reference test: written by hand, never shown to Gecko. Used by the eval to check fixes.
import { test, expect } from '@playwright/test'

test.beforeEach(async ({ page }) => {
  await page.goto('/')
  await page.evaluate(() => localStorage.clear())
  await page.reload()
})

test('counter updates when a task is completed', async ({ page }) => {
  await page.getByLabel('New task').fill('Walk dog')
  await page.getByRole('button', { name: 'Save' }).click()
  await expect(page.getByText('1 task left')).toBeVisible()
  await page.getByText('Walk dog', { exact: true }).click()
  await expect(page.getByText('0 tasks left')).toBeVisible()
})
