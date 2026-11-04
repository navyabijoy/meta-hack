// Hand-written reference tests for the seeded bugs. Each one FAILS on the buggy app.
// Kept outside demo-app so Repro must generate its own.
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

test('counter updates when a task is completed', async ({ page }) => {
  await page.getByLabel('New task').fill('Walk dog')
  await page.getByRole('button', { name: 'Save' }).click()
  await expect(page.getByText('1 task left')).toBeVisible()
  await page.getByText('Walk dog', { exact: true }).click()
  await expect(page.getByText('0 tasks left')).toBeVisible()
})
