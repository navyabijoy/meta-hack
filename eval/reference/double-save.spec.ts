// Hidden reference test: written by hand, never shown to Gecko. Used by the eval to check fixes.
import { test, expect } from '@playwright/test'

test.beforeEach(async ({ page }) => {
  await page.goto('/')
  await page.evaluate(() => localStorage.clear())
  await page.reload()
})

test('double Save adds the task once', async ({ page }) => {
  await page.getByLabel('New task').fill('Buy milk')
  await page.getByRole('button', { name: 'Save' }).dblclick()
  await page.waitForTimeout(800)
  await expect(page.getByText('Buy milk', { exact: true })).toHaveCount(1)
})
