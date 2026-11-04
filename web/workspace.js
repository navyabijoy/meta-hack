// Gecko workspace: the customer's view of their bugs, repos and impact.
const $ = (s, el = document) => el.querySelector(s)
const $$ = (s, el = document) => [...el.querySelectorAll(s)]
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c])

const STATUS = {
  queued: ['Queued', 'wait'], running: ['In progress', 'live'], fixed: ['Fixed', 'ok'],
  reproduced: ['Reproduced', 'warn'], 'not-reproduced': ["Couldn't reproduce", 'quiet'], error: ['Error', 'bad'],
}
const VIA = { 'report-link': 'Report link', 'github-issue': 'GitHub issue', upload: 'Upload' }
const STAGES = ['ingest', 'understand', 'ground', 'reproduce', 'fix']
const STAGE_TEXT = { ingest: 'Watching the recording', understand: 'Reading the bug', ground: 'Finding what was clicked', reproduce: 'Reproducing it', fix: 'Writing the fix' }
const FILTERS = [['all', 'All'], ['active', 'In progress'], ['fixed', 'Fixed'], ['declined', "Couldn't reproduce"], ['error', 'Errors']]
const inFilter = (b, f) => f === 'all' || (f === 'active' && ['queued', 'running'].includes(b.status)) ||
  (f === 'fixed' && b.status === 'fixed') || (f === 'declined' && b.status === 'not-reproduced') || (f === 'error' && b.status === 'error')

const state = { bugs: [], repos: [], impact: null, me: {}, filter: 'all', repo: '', q: '', view: 'overview' }
try { state.repo = localStorage.getItem('gecko.repo') || '' } catch {}

const ago = (t) => {
  const m = Math.round((Date.now() / 1000 - t) / 60)
  if (m < 1) return 'just now'
  if (m < 60) return m + ' min ago'
  const h = Math.round(m / 60)
  return h < 24 ? h + ' h ago' : Math.round(h / 24) + ' d ago'
}
const mins = (s) => (s ? (s / 60).toFixed(1) : null)
const repoName = (r) => (r || '').split('/')[1] || r
const icon = (id) => `<svg class="i"><use href="#i-${id}"/></svg>`

async function api(path, opts) {
  const r = await fetch(path, opts)
  const j = await r.json().catch(() => ({}))
  if (!r.ok) throw new Error(j.detail || r.statusText)
  return j
}

async function load() {
  const q = state.repo ? '?repo=' + encodeURIComponent(state.repo) : ''
  const [bugs, repos, impact, me] = await Promise.all([
    api('/api/workspace/bugs' + q), api('/api/workspace/repos'), api('/api/workspace/impact' + q), api('/api/workspace/me'),
  ])
  Object.assign(state, { bugs, repos, impact, me })
  if (state.repo && !repos.some((r) => r.repo === state.repo)) state.repo = ''
  render()
}

const visibleBugs = () => {
  const q = state.q.trim().toLowerCase()
  return state.bugs.filter((b) => !q || [b.title, b.reporter, b.repo, b.id, VIA[b.origin]].some((x) => (x || '').toLowerCase().includes(q)))
}

function render() {
  renderChrome()
  if (state.view === 'overview') { renderKpis(); renderTtf(); renderGauge(); renderSources(); renderLatest(); renderTable($('#recent'), visibleBugs().slice(0, 6), true) }
  if (state.view === 'bugs') { renderChips(); renderTable($('#allBugs'), visibleBugs().filter((b) => inFilter(b, state.filter)), false) }
  if (state.view === 'repos') renderRepoCards()
  if (state.view === 'try') renderTry()
}

// ---------------------------------------------------------------- chrome
function renderChrome() {
  const sel = $('#repoSel')
  const opts = ['<option value="">All repositories</option>'].concat(state.repos.map((r) => `<option value="${esc(r.repo)}">${esc(r.repo)}</option>`)).join('')
  if (sel.dataset.sig !== opts) { sel.innerHTML = opts; sel.dataset.sig = opts }
  sel.value = state.repo
  sel.hidden = state.repos.length < 2
  const active = state.bugs.filter((b) => ['queued', 'running'].includes(b.status)).length
  $('#navCount').textContent = active ? String(active) : ''
  const install = state.me.install_url || '/github/setup'
  $('#addRepo').href = install; $('#addRepo2').href = install
  const link = reportUrl()
  $('#reportBtn').href = link || '#try'
  $('#reportBtn').target = link ? '_blank' : ''
  const pc = $('#promoCopy')
  pc.disabled = !link
  pc.onclick = () => copy(link, pc, 'Copy report link')
}

function reportUrl() {
  const r = state.repos.find((x) => (!state.repo || x.repo === state.repo) && x.report_link)
  return r ? location.origin + r.report_link : ''
}

async function copy(text, btn, label) {
  try { await navigator.clipboard.writeText(text) } catch {
    const t = document.createElement('textarea'); t.value = text; document.body.appendChild(t); t.select(); document.execCommand('copy'); t.remove()
  }
  btn.textContent = 'Copied'
  setTimeout(() => { btn.textContent = label }, 1400)
}

// ---------------------------------------------------------------- overview
function delta(cur, prev, lowerIsBetter = false) {
  if (cur == null) return ''
  if (!prev) return `<span class="delta new" title="No data for the previous period">New</span>`
  const pct = Math.round(((cur - prev) / prev) * 100)
  const good = lowerIsBetter ? pct <= 0 : pct >= 0
  return `<span class="delta ${good ? 'up' : 'down'}">${pct >= 0 ? '▲' : '▼'} ${Math.abs(pct)}%</span>`
}

function renderKpis() {
  const i = state.impact, p = i.previous || {}
  const card = (title, ic, v, unit, d, vs) => `<div class="card kpi"><div class="ch"><h3>${title}</h3><div class="sp"></div>${icon(ic)}</div>
    <div class="v"><b>${v}${unit ? `<small>${unit}</small>` : ''}</b>${d}</div><div class="vs">${vs}</div></div>`
  const vsTxt = (x, u = '') => (x ? `vs. ${x}${u} last period` : 'first period on Gecko')
  $('#kpis').innerHTML =
    card('Reports', 'inbox', i.reports, '', delta(i.reports, p.reports), vsTxt(p.reports)) +
    card('Verified fixes', 'check', i.fixed, '', delta(i.fixed, p.fixed), `${i.merged} merged · ${i.declined} correctly declined`) +
    card('Time to fix', 'clock', i.median_minutes_to_fix ?? '—', i.median_minutes_to_fix != null ? 'min' : '', delta(i.median_minutes_to_fix, p.median_minutes_to_fix, true), vsTxt(p.median_minutes_to_fix, ' min')) +
    card('Hours saved', 'hour', i.hours_saved, 'h', delta(i.hours_saved, p.hours_saved), `at ${i.hours_saved_assumption.split(' ')[0]} h per fix, an estimate`)
  $('#rangeTxt').textContent = `Last ${i.days} days`
}

function renderTtf() {
  const fixed = state.bugs.filter((b) => b.status === 'fixed' && b.seconds).slice(0, 12).reverse()
  const el = $('#ttfChart')
  if (!fixed.length) { $('#ttfBig').innerHTML = ''; el.innerHTML = '<div class="empty" style="padding:28px">No fixes yet.</div>'; return }
  const vals = fixed.map((b) => b.seconds / 60)
  const sorted = [...vals].sort((a, b) => a - b)
  const med = sorted.length % 2 ? sorted[(sorted.length - 1) / 2] : (sorted[sorted.length / 2 - 1] + sorted[sorted.length / 2]) / 2
  $('#ttfBig').innerHTML = `<b>${med.toFixed(1)} min</b><span>median across ${fixed.length} fixes</span>`
  const W = Math.max(el.clientWidth || 600, 280), H = 200, L = 34, B = 26, T = 8
  const top = Math.max(5, Math.ceil(Math.max(...vals) + 0.5))
  const step = top > 10 ? 5 : top > 5 ? 2 : 1
  const yMax = Math.ceil(top / step) * step
  const Y = (v) => T + (H - T - B) * (1 - v / yMax)
  const slot = (W - L) / fixed.length, bw = Math.min(28, slot * 0.55)
  let svg = `<svg viewBox="0 0 ${W} ${H}" height="${H}" role="img" aria-label="Minutes from report to fix for the last ${fixed.length} fixed bugs; median ${med.toFixed(1)} minutes">`
  for (let v = 0; v <= yMax; v += step) svg += `<line class="grid" x1="${L}" x2="${W}" y1="${Y(v)}" y2="${Y(v)}"/><text class="ax" x="${L - 8}" y="${Y(v) + 4}" text-anchor="end">${v}</text>`
  fixed.forEach((b, i) => {
    const v = vals[i], x = L + slot * i + (slot - bw) / 2, y = Y(v), h = Y(0) - y
    // Square at the baseline, 4px rounded at the data end.
    svg += `<path class="bar" data-i="${i}" d="M${x} ${Y(0)} V${y + 4} q0 -4 4 -4 h${bw - 8} q4 0 4 4 V${Y(0)} Z"/>`
    svg += `<rect x="${L + slot * i}" y="${T}" width="${slot}" height="${H - T - B}" fill="transparent" class="hit" data-i="${i}"/>`
    if (fixed.length <= 12) svg += `<text class="ax" x="${x + bw / 2}" y="${H - 8}" text-anchor="middle">${new Date(b.created * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</text>`
  })
  svg += `<line class="med" x1="${L}" x2="${W}" y1="${Y(med)}" y2="${Y(med)}"/><text class="medl" x="${W}" y="${Y(med) - 6}" text-anchor="end">median ${med.toFixed(1)}</text></svg>`
  el.innerHTML = svg
  const tip = $('#tip')
  $$('.hit', el).forEach((h) => {
    h.addEventListener('mousemove', (e) => {
      const i = +h.dataset.i, b = fixed[i]
      $$('.bar', el).forEach((bar) => bar.classList.toggle('dim', +bar.dataset.i !== i))
      tip.innerHTML = `<b>${esc(b.title)}</b>${vals[i].toFixed(1)} min to a verified fix`
      tip.hidden = false; tip.style.left = Math.min(e.clientX + 14, innerWidth - 300) + 'px'; tip.style.top = e.clientY + 14 + 'px'
    })
    h.addEventListener('mouseleave', () => { tip.hidden = true; $$('.bar', el).forEach((bar) => bar.classList.remove('dim')) })
    h.addEventListener('click', () => { location.href = '/runs/' + fixed[+h.dataset.i].id })
    h.style.cursor = 'pointer'
  })
}

function renderGauge() {
  const done = state.bugs.filter((b) => !['queued', 'running'].includes(b.status))
  const fixed = done.filter((b) => b.status === 'fixed').length
  const rate = done.length ? Math.round((100 * fixed) / done.length) : 0
  const n = 48, R = 100, cx = 120, cy = 116
  let ticks = ''
  for (let k = 0; k < n; k++) {
    const a = Math.PI * (1 - k / (n - 1))
    const x1 = cx + Math.cos(a) * (R - 16), y1 = cy - Math.sin(a) * (R - 16), x2 = cx + Math.cos(a) * R, y2 = cy - Math.sin(a) * R
    ticks += `<line class="tick ${k / (n - 1) <= rate / 100 && done.length ? 'on' : ''}" x1="${x1.toFixed(1)}" y1="${y1.toFixed(1)}" x2="${x2.toFixed(1)}" y2="${y2.toFixed(1)}"/>`
  }
  $('#gauge').innerHTML = `<svg viewBox="0 0 240 128" role="img" aria-label="Verified fix rate ${rate} percent">${ticks}</svg>
    <div class="gv"><b>${done.length ? rate + '%' : '—'}</b><span>${fixed} of ${done.length} finished reports</span></div>`
}

function renderSources() {
  const since = Date.now() / 1000 - 30 * 86400
  const items = state.bugs.filter((b) => b.created >= since)
  const kinds = [['report-link', 'Report link', 'var(--s1)'], ['github-issue', 'GitHub issue', 'var(--s2)'], ['upload', 'Upload', 'var(--s3)']]
  const total = items.length || 1
  $('#srcs').innerHTML = kinds.map(([k, l, c]) => {
    const n = items.filter((b) => b.origin === k).length
    return `<div class="src"><div class="n"><i style="background:${c}"></i>${n}</div><div class="l">${l}</div>
      <div class="bar" style="background:linear-gradient(90deg, ${c} ${Math.round((100 * n) / total)}%, var(--line-2) 0)"></div></div>`
  }).join('')
}

function renderLatest() {
  const b = state.bugs.find((x) => x.status === 'fixed')
  $('#latest').innerHTML = !b ? '' : `<div class="card dark"><div class="who"><i></i>gecko · ${ago(b.created)}</div>
    <p><b>Caught it.</b> ${esc(b.title)}</p>
    <div class="res"><span>${mins(b.seconds)} min to a fix</span>${b.pr_url ? `<a href="${esc(b.pr_url)}" target="_blank" rel="noopener">View PR${b.pr_state ? ' · ' + esc(b.pr_state) : ''}</a>` : `<a href="/runs/${esc(b.id)}">See the proof</a>`}</div></div>`
}

// ---------------------------------------------------------------- tables
function statusCell(b) {
  const [label, cls] = STATUS[b.status] || [b.status, 'quiet']
  if (b.status !== 'running') return `<span class="st ${cls}">${label}</span>`
  const at = Math.max(STAGES.indexOf(b.stage), 0)
  return `<span class="st live">${STAGE_TEXT[b.stage] || 'Starting'}</span><div class="prog" aria-hidden="true">${STAGES.map((_, i) => `<span class="${i < at ? 'on' : i === at ? 'now' : ''}"></span>`).join('')}</div>`
}

function renderTable(el, rows, compact) {
  if (!state.repos.length) {
    el.innerHTML = `<div class="empty"><h3>Connect your first repo</h3><p>Install Gecko on a repository and it starts catching bugs. Setup takes about a minute.</p>
      <ol class="steps"><li>Install the Gecko GitHub App on a repo</li><li>Add a small <code>.gecko.yml</code> so Gecko can start your app</li><li>Share your report link with support, QA or customers</li></ol>
      <a class="btn primary" href="${esc(state.me.install_url || '/github/setup')}">${icon('gh')}Install on GitHub</a></div>`
    return
  }
  if (!rows.length) {
    el.innerHTML = state.q ? `<div class="empty"><h3>No matches</h3><p>Nothing matches “${esc(state.q)}”.</p></div>`
      : state.bugs.length ? `<div class="empty"><h3>Nothing here</h3><p>No bugs match this filter.</p></div>`
      : `<div class="empty"><h3>No bugs yet</h3><p>Share your report link, or try Gecko with a recording.</p><a class="btn primary" href="#try">Try it</a></div>`
    return
  }
  el.innerHTML = `<table class="tbl"><thead><tr><th>Bug</th><th class="hide-s">Reported by</th><th class="hide-s">Via</th><th>Status</th><th class="hide-s">Time</th><th>Links</th></tr></thead><tbody>
    ${rows.map((b) => `<tr data-id="${esc(b.id)}">
      <td><span class="t">${esc(b.title)}</span>${b.why && !compact ? `<span class="why">${esc(b.why)}</span>` : `<span class="id">${[b.repo && esc(repoName(b.repo)), ago(b.created)].filter(Boolean).join(' · ')}</span>`}</td>
      <td class="hide-s mut">${esc(b.reporter || '—')}</td>
      <td class="hide-s mut">${esc(VIA[b.origin] || b.origin)}</td>
      <td>${statusCell(b)}</td>
      <td class="hide-s num">${b.seconds ? mins(b.seconds) + ' min' : '—'}</td>
      <td>${b.issue_url ? `<a class="lnk" href="${esc(b.issue_url)}" target="_blank" rel="noopener">Issue</a>` : ''}${b.pr_url ? `<a class="lnk" href="${esc(b.pr_url)}" target="_blank" rel="noopener">PR${b.pr_state ? ' · ' + esc(b.pr_state) : ''}</a>` : b.status === 'fixed' && b.origin === 'upload' ? `<button class="btn small openpr" data-id="${esc(b.id)}" style="margin-left:10px">Open PR</button>` : ''}</td>
    </tr>`).join('')}</tbody></table>`
  $$('tbody tr', el).forEach((tr) => tr.onclick = (e) => { if (!e.target.closest('a,button')) location.href = '/runs/' + tr.dataset.id })
  $$('.openpr', el).forEach((b) => b.onclick = async () => {
    b.disabled = true; b.textContent = 'Opening…'
    try { await api(`/api/runs/${b.dataset.id}/pr`, { method: 'POST' }); await load() } catch (e) { b.textContent = 'Failed'; b.title = e.message }
  })
}

function renderChips() {
  const rows = visibleBugs()
  $('#chips').innerHTML = FILTERS.map(([k, l]) => `<button class="chip" data-f="${k}" aria-pressed="${state.filter === k}">${l}<b>${rows.filter((b) => inFilter(b, k)).length}</b></button>`).join('')
  $$('.chip').forEach((c) => c.onclick = () => { state.filter = c.dataset.f; render() })
}

// ---------------------------------------------------------------- repos
function renderRepoCards() {
  const el = $('#repoCards')
  if (!state.repos.length) { el.innerHTML = ''; renderTable(el, [], false); return }
  el.innerHTML = state.repos.map((r) => {
    const url = r.report_link ? location.origin + r.report_link : ''
    const pct = Math.min(100, Math.round((100 * r.fixes_this_month) / Math.max(r.monthly_fix_limit, 1)))
    return `<div class="card"><div class="ch"><h3 class="mono">${esc(r.repo)}</h3><div class="sp"></div>
        <span class="st ${r.configured ? 'ok' : 'warn'}">${r.configured ? 'Ready' : 'Needs setup'}</span></div>
      <p class="cs">${r.configured ? 'Gecko can start this app and run its tests.' : 'Add a <code>.gecko.yml</code> so Gecko can start your app. <a class="lnk" style="margin:0" href="/welcome#setup">How</a>'}</p>
      <div class="rowline" style="display:block"><div class="k">Report link</div>
        ${url ? `<div class="linkbox"><input readonly value="${esc(url)}" aria-label="Report link for ${esc(r.repo)}"><button class="btn small copy" data-url="${esc(url)}">Copy</button></div>` : '<div class="cs" style="margin:6px 0 0">Turned off. Reports only come in through GitHub issues.</div>'}</div>
      <div class="rowline"><span class="k">Accepting reports through the link</span><button class="switch" role="switch" aria-checked="${r.report_link ? 'true' : 'false'}" aria-label="Accepting reports for ${esc(r.repo)}" data-repo="${esc(r.repo)}"></button></div>
      <div class="rowline"><span class="k">Monthly fix limit</span><span><input class="numin" type="number" min="0" step="10" value="${r.monthly_fix_limit}" data-repo="${esc(r.repo)}" aria-label="Monthly fix limit for ${esc(r.repo)}"></span></div>
      <div class="rowline" style="display:block"><div style="display:flex;justify-content:space-between"><span class="k">Fixes this month</span><span class="mono">${r.fixes_this_month} / ${r.monthly_fix_limit}</span></div><div class="meter"><span style="width:${pct}%"></span></div></div>
    </div>`
  }).join('')
  $$('.copy', el).forEach((b) => b.onclick = () => copy(b.dataset.url, b, 'Copy'))
  $$('.switch', el).forEach((s) => s.onclick = () => saveSetting(s.dataset.repo, { report_link: s.getAttribute('aria-checked') !== 'true' }))
  $$('.numin', el).forEach((n) => n.onchange = () => saveSetting(n.dataset.repo, { monthly_fix_limit: Number(n.value) }))
}

async function saveSetting(repo, body) {
  await api(`/api/workspace/repos/${repo}/settings`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
  load()
}

// ---------------------------------------------------------------- try it
let tryFile = null
function renderTry() {
  const source = state.repo || state.repos[0]?.repo
  $('#tryRepo').textContent = source ? 'against ' + source : 'connect a repository first'
}
const pick = (f) => { if (!f) return; tryFile = f; $('#dropText').textContent = f.name + ' · ' + (f.size / 1e6).toFixed(1) + ' MB'; $('#drop').classList.add('has'); $('#go').disabled = false }
$('#file').onchange = () => pick($('#file').files[0])
$('#drop').ondragover = (e) => e.preventDefault()
$('#drop').ondrop = (e) => { e.preventDefault(); pick(e.dataTransfer.files[0]) }
$('#go').onclick = async () => {
  const source = state.repo || state.repos[0]?.repo
  const note = $('#tryNote')
  if (!source) { note.className = 'note bad'; note.textContent = 'Connect a repository first.'; return }
  $('#go').disabled = true; note.className = 'note'; note.textContent = 'Uploading…'
  const fd = new FormData(); fd.append('video', tryFile); fd.append('source', source)
  try {
    const r = await api('/api/runs', { method: 'POST', body: fd })
    note.innerHTML = `Started. <a class="lnk" style="margin:0" href="/runs/${esc(r.id)}">Watch it live</a>`
    $('#dropText').textContent = 'Choose a video, or drop it here'; $('#drop').classList.remove('has'); tryFile = null
    load()
  } catch (e) { note.className = 'note bad'; note.textContent = e.message; $('#go').disabled = false }
}

// ---------------------------------------------------------------- routing, search, theme
function route() {
  const v = (location.hash.slice(1) || 'overview')
  state.view = ['overview', 'bugs', 'repos', 'try'].includes(v) ? v : 'overview'
  $$('.view').forEach((s) => { s.hidden = s.id !== 'v-' + state.view })
  $$('.navl a[data-v]').forEach((a) => {
    if (a.dataset.v === state.view) a.setAttribute('aria-current', 'page')
    else a.removeAttribute('aria-current')
  })
  $('#sb').classList.remove('open')
  render()
  scrollTo(0, 0)
}
addEventListener('hashchange', route)

$('#q').oninput = (e) => {
  state.q = e.target.value
  if (state.q && state.view !== 'bugs' && state.view !== 'overview') location.hash = 'bugs'
  else render()
}
addEventListener('keydown', (e) => { if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); $('#q').focus() } })
$('#repoSel').onchange = (e) => { state.repo = e.target.value; try { localStorage.setItem('gecko.repo', state.repo) } catch {} ; load() }
$('#menuBtn').onclick = () => $('#sb').classList.toggle('open')
$('#themeBtn').onclick = () => {
  const dark = document.documentElement.dataset.theme ? document.documentElement.dataset.theme === 'dark' : matchMedia('(prefers-color-scheme: dark)').matches
  document.documentElement.dataset.theme = dark ? 'light' : 'dark'
  try { localStorage.setItem('gecko.theme', document.documentElement.dataset.theme) } catch {}
}
addEventListener('resize', () => { if (state.view === 'overview') renderTtf() })

async function tick() {
  if (!document.hidden) { try { await load() } catch {} }
  setTimeout(tick, state.bugs.some((b) => ['queued', 'running'].includes(b.status)) ? 4000 : 20000)
}
state.view = (location.hash.slice(1) || 'overview')
load().then(route).catch((e) => { $('#recent').innerHTML = `<div class="empty"><h3>Couldn't load your workspace</h3><p>${esc(e.message)}</p></div>` })
setTimeout(tick, 4000)
