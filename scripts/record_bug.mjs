// Records a "user" reproducing a seeded bug in the demo app, as a plain video with a
// visible cursor. Used to create test inputs; Repro never sees how it was made.
// Usage: node scripts/record_bug.mjs <bug> <out-dir>   (bug: double-save | wrong-delete | counter)
import { chromium } from '../demo-app/node_modules/@playwright/test/index.mjs'
import { mkdirSync, renameSync, readdirSync } from 'node:fs'
import { join } from 'node:path'

const [bug, outDir] = process.argv.slice(2)
const URL = process.env.APP_URL ?? 'http://localhost:5173'
mkdirSync(outDir, { recursive: true })

const CURSOR = `
  const c = document.createElement('div');
  c.id = '__cursor';
  c.style.cssText = 'position:fixed;left:0;top:0;width:22px;height:22px;z-index:2147483647;pointer-events:none;transition:transform 0.18s ease-out;';
  c.innerHTML = '<svg width="22" height="22" viewBox="0 0 22 22"><path d="M2 2 L2 18 L7 13 L10 20 L13 19 L10 12 L17 12 Z" fill="black" stroke="white" stroke-width="1.5"/></svg>';
  const add = () => document.body && !document.getElementById('__cursor') && document.body.appendChild(c);
  document.addEventListener('DOMContentLoaded', add); add();
  document.addEventListener('mousemove', e => { c.style.transform = 'translate(' + e.clientX + 'px,' + e.clientY + 'px)'; }, true);
  document.addEventListener('mousedown', () => { c.style.scale = '0.8'; }, true);
  document.addEventListener('mouseup', () => { c.style.scale = '1'; }, true);
`

const browser = await chromium.launch({ channel: 'chrome' })
const ctx = await browser.newContext({
  viewport: { width: 1280, height: 800 },
  recordVideo: { dir: outDir, size: { width: 1280, height: 800 } },
})
await ctx.addInitScript(CURSOR)
const page = await ctx.newPage()
await page.goto(URL)
await page.evaluate(() => localStorage.clear())
await page.reload()
await page.mouse.move(640, 600)
await page.waitForTimeout(800)

async function moveTo(locator) {
  const b = await locator.boundingBox()
  await page.mouse.move(b.x + b.width / 2, b.y + b.height / 2, { steps: 20 })
  await page.waitForTimeout(250)
}
async function click(locator, opts = {}) {
  await moveTo(locator)
  await locator.click(opts)
  await page.waitForTimeout(500)
}
async function type(text) {
  const input = page.getByLabel('New task')
  await click(input)
  await page.keyboard.type(text, { delay: 70 })
  await page.waitForTimeout(300)
}
async function add(text) {
  await type(text)
  await click(page.getByRole('button', { name: 'Save' }))
  await page.waitForTimeout(500)
}

if (bug === 'double-save') {
  await add('Walk dog')
  await type('Buy milk')
  await moveTo(page.getByRole('button', { name: 'Save' }))
  await page.getByRole('button', { name: 'Save' }).dblclick()
  await page.waitForTimeout(1500)
} else if (bug === 'wrong-delete') {
  for (const t of ['Call mom', 'Pay rent', 'Book flights']) await add(t)
  await click(page.getByText('Call mom', { exact: true }))
  await click(page.getByRole('button', { name: 'Active' }))
  await click(page.getByRole('button', { name: 'Delete Book flights' }))
  await click(page.getByRole('button', { name: 'All' }))
  await page.waitForTimeout(1500)
} else if (bug === 'counter') {
  await add('Water plants')
  await add('Reply to Sam')
  await click(page.getByText('Water plants', { exact: true }))
  await page.waitForTimeout(1500)
} else if (bug === 'not-a-bug-reload') {
  // The user claims a task vanishes on reload. It does not.
  await add('Renew passport')
  await page.waitForTimeout(600)
  await page.reload()
  await page.mouse.move(640, 500, { steps: 10 })
  await page.waitForTimeout(1800)
} else {
  throw new Error('unknown bug ' + bug)
}

await page.mouse.move(640, 700, { steps: 10 })
await page.waitForTimeout(700)
const video = page.video()
await ctx.close()
await browser.close()
const src = await video.path()
renameSync(src, join(outDir, 'raw.webm'))
console.log(join(outDir, 'raw.webm'))
