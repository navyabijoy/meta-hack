// Stage 5, Replay: drive the live app through the grounded steps in a real browser.
//
// Input (JSON on stdin): { url, outDir, steps: [{ id, action, text, role, name, which,
//   ordinal, count, variant }] }
// Output (JSON on stdout): per step, the element chosen, why, the Playwright locator
// that selects it uniquely, and a screenshot path. The locators become the test.
import { chromium } from '@playwright/test'
import { mkdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'

// Icon glyphs users and models write differently: "x", "×", "✕" all mean the same button.
const GLYPHS = { '×': 'x', '✕': 'x', '✖': 'x', '⨯': 'x', '╳': 'x', '✗': 'x', '🗑': 'trash', '＋': '+', '⋯': '...', '…': '...' }
const ICON_WORDS = { x: ['close', 'delete', 'remove', 'dismiss', 'clear'], trash: ['delete', 'remove'], '+': ['add', 'new', 'create'] }


const input = JSON.parse(readFileSync(0, 'utf8'))
mkdirSync(input.outDir, { recursive: true })

const browser = await chromium.launch({ channel: 'chrome' })
const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 } })
const page = await ctx.newPage()
const log = []
let ok = true

try {
  await page.goto(input.url)
  const fresh = input.freshState ?? ['localStorage']
  if (fresh.length) {
    await page.evaluate((kinds) => { for (const k of kinds) if (k in window) window[k].clear() }, fresh)
    await page.reload()
  }
  await page.screenshot({ path: join(input.outDir, 'step00.png') })

  for (const step of input.steps) {
    const entry = { id: step.id, action: step.action }
    try {
      if (step.action === 'type' || step.action === 'press') {
        const focused = await describeFocused(page)
        entry.locator = focused?.locator ?? null
        if (step.action === 'type') {
          // Type into the named field when the focus is not already on a text box.
          let target = focused?.role === 'textbox' ? null : step.name ? await chooseElement(page, { ...step, role: 'textbox' }) : null
          if (target?.handle && target.meta.element?.role === 'textbox') {
            Object.assign(entry, target.meta)
            await target.handle.click()
          }
          await page.keyboard.type(step.text ?? '', { delay: 10 })
          entry.code = `await ${entry.locator ?? 'page.locator(":focus")'}.fill(${JSON.stringify(step.text ?? '')})`
        } else {
          await page.keyboard.press(step.text || 'Enter')
          entry.code = `await page.keyboard.press(${JSON.stringify(step.text || 'Enter')})`
        }
        entry.reason = 'acts on the focused element'
      } else {
        const pick = await chooseElement(page, step)
        Object.assign(entry, pick.meta)
        if (!pick.handle) throw new Error(pick.meta.reason)
        const loc = entry.locator
        const variant = step.variant || step.action
        if (variant === 'dblclick') {
          await pick.handle.dblclick()
          entry.code = `await ${loc}.dblclick()`
        } else if (variant === 'rapid-double-submit') {
          await Promise.all([pick.handle.click(), pick.handle.click({ delay: 30 })])
          entry.code = `await Promise.all([${loc}.click(), ${loc}.click({ delay: 30 })])`
        } else {
          await pick.handle.click()
          entry.code = `await ${loc}.click()`
        }
      }
      await page.waitForTimeout(step.waitAfter ?? 700)
      entry.screenshot = join(input.outDir, `step${String(step.id).padStart(2, '0')}.png`)
      await page.screenshot({ path: entry.screenshot })
      entry.ok = true
    } catch (e) {
      entry.ok = false
      entry.error = String(e.message || e).split('\n')[0]
      ok = false
      log.push(entry)
      break
    }
    log.push(entry)
  }
  const final = join(input.outDir, 'final.png')
  await page.screenshot({ path: final })
  const text = await page.evaluate(() => document.body.innerText)
  process.stdout.write(JSON.stringify({ ok, steps: log, final, text }))
} finally {
  await browser.close()
}

// ---------------------------------------------------------------------------

async function candidates(page) {
  return page.evaluate(() => {
    const visible = (el) => {
      const r = el.getBoundingClientRect()
      const s = getComputedStyle(el)
      return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'
    }
    const roleOf = (el) => {
      const r = el.getAttribute('role')
      if (r) return r
      const t = el.tagName.toLowerCase()
      if (t === 'button') return 'button'
      if (t === 'a' && el.href) return 'link'
      if (t === 'select') return 'combobox'
      if (t === 'textarea') return 'textbox'
      if (t === 'input') {
        const ty = (el.type || 'text').toLowerCase()
        if (ty === 'checkbox') return 'checkbox'
        if (ty === 'radio') return 'radio'
        if (['button', 'submit', 'reset'].includes(ty)) return 'button'
        return 'textbox'
      }
      return 'text'
    }
    const labelText = (el) => {
      if (el.id) {
        const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`)
        if (l) return l.innerText.trim()
      }
      const wrap = el.closest('label')
      return wrap ? wrap.innerText.trim() : ''
    }
    const rowText = (el) => {
      const row = el.closest('li, tr, [role=row], [role=listitem]')
      return row ? row.innerText.replace(/\s+/g, ' ').trim() : ''
    }
    const out = []
    const seen = new Set()
    const push = (el, role) => {
      if (seen.has(el) || !visible(el)) return
      seen.add(el)
      const r = el.getBoundingClientRect()
      out.push({
        idx: out.length,
        role,
        tag: el.tagName.toLowerCase(),
        aria: el.getAttribute('aria-label') || '',
        label: labelText(el),
        text: (el.innerText || el.value || '').replace(/\s+/g, ' ').trim().slice(0, 80),
        placeholder: el.getAttribute('placeholder') || '',
        row: rowText(el),
        box: [r.x, r.y, r.width, r.height],
      })
      el.setAttribute('data-gecko-idx', String(out.length - 1))
    }
    document.querySelectorAll('[data-gecko-idx]').forEach((e) => e.removeAttribute('data-gecko-idx'))
    for (const el of document.querySelectorAll('button, a, input, textarea, select, [role], label')) push(el, roleOf(el))
    // Leaf text (spans, list items) for "clicked on the words" steps.
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_ELEMENT)
    while (walker.nextNode()) {
      const el = walker.currentNode
      if (el.children.length === 0 && el.innerText && el.innerText.trim()) push(el, 'text')
    }
    return out
  })
}

function norm(s) {
  return (s || '').toLowerCase().replace(/[×✕✖⨯╳✗🗑＋⋯…]/gu, (c) => ` ${GLYPHS[c]} `)
    .replace(/[^\p{L}\p{N}+.]+/gu, ' ').replace(/\s+/g, ' ').trim()
}

function nameScore(c, name) {
  const n = norm(name)
  if (!n) return 0
  const fields = [c.aria, c.label, c.text, c.placeholder].map(norm)
  if (fields.some((f) => f === n)) return 3
  // Short names ("x", "+") only match whole words, never substrings of other words.
  if (n.length <= 2) {
    const icon = ICON_WORDS[n] || []
    if (fields.some((f) => f.split(' ').includes(n))) return 2.5
    if (icon.length && fields.some((f) => icon.some((w) => f.split(' ').includes(w)))) return 2
    return 0
  }
  if (fields.some((f) => f && (f.includes(n) || n.includes(f)))) return 2
  const words = n.split(' ')
  if (fields.some((f) => words.some((w) => w.length > 2 && f.includes(w)))) return 1
  return 0
}

function quotedNames(which) {
  return [...(which || '').matchAll(/['"‘’“”]([^'"‘’“”]+)['"‘’“”]/g)].map((m) => norm(m[1]))
}

function roleScore(c, role) {
  if (!role) return 0
  if (c.role === role) return 1
  // Planner says "tab"/"text" loosely; buttons and labels are close cousins.
  const close = { tab: ['button'], text: ['label', 'button', 'checkbox'], checkbox: ['label', 'text'] }
  return (close[role] || []).includes(c.role) ? 0.5 : 0
}

async function chooseElement(page, step) {
  const all = await candidates(page)
  const scored = all
    .map((c) => ({ c, s: nameScore(c, step.name) * 2 + roleScore(c, step.role) }))
    .filter((x) => x.s >= 2)
    .sort((a, b) => b.s - a.s)
  const meta = { candidates: scored.length }
  if (!scored.length) {
    // Fallback: the planner's description of the row ("the x on the 'Book flights' row"),
    // narrowed by role, with the SAM ordinal to break ties.
    const quoted = quotedNames(step.which)
    const clickable = ['button', 'link', 'checkbox', 'tab', 'radio']
    let rows = quoted.length ? all.filter((c) => clickable.includes(c.role) && quoted.some((q) => norm(c.row).includes(q))) : []
    if (rows.length > 1 && step.role) {
      const sameRole = rows.filter((c) => c.role === step.role)
      if (sameRole.length) rows = sameRole
    }
    // In a row, the element that is not the row's own text is the control (e.g. the x, not the label).
    if (rows.length > 1) {
      const notText = rows.filter((c) => !quoted.some((q) => norm(c.text) === q || norm(c.label) === q))
      if (notText.length) rows = notText
    }
    if (rows.length === 1) {
      const chosen = rows[0]
      return {
        handle: await page.$(`[data-gecko-idx="${chosen.idx}"]`),
        meta: { ...meta, confidence: 'medium', reason: `no element named "${step.name}"; matched by row description`,
          element: { role: chosen.role, aria: chosen.aria, text: chosen.text, row: chosen.row },
          locator: await stableLocator(page, chosen) },
      }
    }
    return { handle: null, meta: { ...meta, reason: `no element named "${step.name}"` } }
  }

  const top = scored.filter((x) => x.s === scored[0].s).map((x) => x.c)
  top.sort((a, b) => a.box[1] - b.box[1] || a.box[0] - b.box[0])
  let chosen = top[0]
  const signals = []

  if (top.length > 1) {
    // Signal 1: SAM ordinal, valid only when SAM saw the same number of look-alikes.
    let bySam = null
    if (step.ordinal != null && step.count === top.length) bySam = top[step.ordinal]
    // Signal 2: the planner's "which" words (quoted names) found in the element's row.
    let byWhich = null
    const quoted = quotedNames(step.which)
    if (quoted.length) byWhich = top.find((c) => quoted.some((q) => norm(c.row).includes(q))) || null
    if (bySam && byWhich) {
      chosen = bySam
      signals.push(bySam === byWhich ? 'SAM ordinal and description agree' : 'SAM ordinal and description DISAGREE; trusting SAM')
      meta.confidence = bySam === byWhich ? 'high' : 'low'
    } else if (bySam) {
      chosen = bySam
      signals.push(`SAM ordinal #${step.ordinal + 1} of ${step.count}`)
      meta.confidence = 'medium'
    } else if (byWhich) {
      chosen = byWhich
      signals.push('matched the description of the row')
      meta.confidence = 'medium'
    } else {
      signals.push(`${top.length} look-alikes; took the first`)
      meta.confidence = 'low'
    }
  } else {
    signals.push('unique name match')
    meta.confidence = 'high'
  }

  meta.element = { role: chosen.role, tag: chosen.tag, aria: chosen.aria, label: chosen.label, text: chosen.text, row: chosen.row }
  meta.reason = signals.join('; ')
  const handle = await page.$(`[data-gecko-idx="${chosen.idx}"]`)
  meta.locator = await stableLocator(page, chosen)
  return { handle, meta }
}

// The shortest Playwright locator that selects exactly this element.
async function stableLocator(page, c) {
  const options = []
  const accName = c.aria || c.label || c.text
  if (['button', 'checkbox', 'link', 'textbox', 'radio', 'combobox', 'tab'].includes(c.role) && accName) {
    options.push(`page.getByRole(${JSON.stringify(c.role)}, { name: ${JSON.stringify(accName)}, exact: true })`)
  }
  if (c.role === 'textbox' && c.label) options.push(`page.getByLabel(${JSON.stringify(c.label)}, { exact: true })`)
  if (c.aria && c.role === 'textbox') options.push(`page.getByLabel(${JSON.stringify(c.aria)}, { exact: true })`)
  if (c.placeholder) options.push(`page.getByPlaceholder(${JSON.stringify(c.placeholder)}, { exact: true })`)
  if (c.text) options.push(`page.getByText(${JSON.stringify(c.text)}, { exact: true })`)
  for (const expr of options) {
    try {
      // eslint-disable-next-line no-new-func
      const loc = new Function('page', `return ${expr}`)(page)
      if ((await loc.count()) !== 1) continue
      const same = await loc.evaluate((el, idx) => el.getAttribute('data-gecko-idx') === String(idx), c.idx)
      if (same) return expr
    } catch {}
  }
  return `page.locator('[data-gecko-idx="${c.idx}"]')`
}

async function describeFocused(page) {
  const all = await candidates(page)
  const idx = await page.evaluate(() => document.activeElement?.getAttribute('data-gecko-idx'))
  if (idx == null) return null
  const c = all[Number(idx)]
  return c ? { role: c.role, locator: await stableLocator(page, c) } : null
}
