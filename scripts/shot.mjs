// Screenshot a page: node scripts/shot.mjs <url> <out.png> [width] [height] [fullPage]
import { chromium } from '../backend/browser/node_modules/@playwright/test/index.mjs'
const [url, out, w = '1440', h = '1000', full = '1'] = process.argv.slice(2)
const b = await chromium.launch({ channel: 'chrome' })
const p = await b.newPage({ viewport: { width: +w, height: +h }, colorScheme: process.env.DARK ? 'dark' : 'light' })
await p.goto(url); await p.waitForTimeout(2500)
await p.screenshot({ path: out, fullPage: full === '1' })
await b.close()
