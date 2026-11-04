// Gecko workspace: the customer's view of their bugs, repos and impact.
const $ = (s, el = document) => el.querySelector(s)
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c])

const STATUS = {
  queued: ['Queued', 'wait'], running: ['In progress', 'live'], fixed: ['Fixed', 'ok'],
  reproduced: ['Reproduced, no verified fix', 'warn'], 'not-reproduced': ["Couldn't reproduce", 'quiet'], error: ['Error', 'bad'],
}
const VIA = { 'report-link': 'Report link', 'github-issue': 'GitHub issue', upload: 'Upload' }
const STAGES = ['ingest', 'understand', 'ground', 'reproduce', 'fix']
const STAGE_TEXT = {
  ingest: 'Watching the recording', understand: 'Reading the bug', ground: 'Finding what was clicked',
  reproduce: 'Reproducing it in a real browser', fix: 'Writing and checking the fix',
}
const FILTERS = [['all', 'All'], ['active', 'In progress'], ['fixed', 'Fixed'], ['declined', "Couldn't reproduce"]]
const inFilter = (b, f) => f === 'all' || (f === 'active' && ['queued', 'running'].includes(b.status)) ||
  (f === 'fixed' && b.status === 'fixed') || (f === 'declined' && b.status === 'not-reproduced')

const state = { bugs: [], repos: [], impact: null, me: {}, filter: 'all', repo: '', loaded: false }
try { state.repo = localStorage.getItem('gecko.repo') || '' } catch {}

function ago(t) {
  const m = Math.round((Date.now() / 1000 - t) / 60)
  if (m < 1) return 'just now'
  if (m < 60) return m + ' min ago'
  const h = Math.round(m / 60)
  return h < 24 ? h + ' h ago' : Math.round(h / 24) + ' d ago'
}
const dur = (s) => (s ? (s >= 90 ? (s / 60).toFixed(1) + ' min' : Math.round(s) + ' s') : '')
const repoName = (r) => r.split('/')[1] || r

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
  Object.assign(state, { bugs, repos, impact, me, loaded: true })
  if (state.repo && !repos.some((r) => r.repo === state.repo)) state.repo = ''
  render()
}

function render() {
  renderHead()
  renderImpact()
  renderChips()
  renderList()
  renderLatest()
  renderRepos()
}

function renderHead() {
  const sel = $('#repoSel')
  const opts = ['<option value="">All repositories</option>'].concat(state.repos.map((r) => `<option value="${esc(r.repo)}">${esc(r.repo)}</option>`)).join('')
  if (sel.dataset.sig !== opts) { sel.innerHTML = opts; sel.dataset.sig = opts }
  sel.value = state.repo
  sel.hidden = state.repos.length < 2
  $('#title').textContent = state.repo ? repoName(state.repo) : 'Bugs Gecko is on'
  if (state.me.install_url) $('#addRepo').href = state.me.install_url
  if (state.me.login) { $('#who').style.display = 'block'; $('#who').textContent = '@' + state.me.login }
}

function renderImpact() {
  const i = state.impact
  const none = !state.repos.length
  $('#impactWrap').hidden = none
  if (none || !i) return
  const stat = (l, v, s, unit = '') => `<div class="stat"><span class="eyebrow">${l}</span><div class="v serif">${v}${unit ? `<small>${unit}</small>` : ''}</div><div class="s">${s}</div></div>`
  $('#impact').innerHTML =
    stat('Reports', i.reports, `sent in the last ${i.days} days`) +
    stat('Verified fixes', i.fixed, `${i.merged} merged · ${i.declined} correctly declined`) +
    stat('To a fix', i.median_minutes_to_fix ?? '—', 'median, recording to pull request', i.median_minutes_to_fix != null ? 'min' : '') +
    stat('Hours saved', i.hours_saved, 'estimated for your team')
  $('#impactFoot').textContent = 'Hours saved assumes ' + i.hours_saved_assumption.replace(/ \(.*\)$/, '') + ' (reproducing and fixing by hand).'
}

function renderChips() {
  $('#chips').innerHTML = FILTERS.map(([k, l]) => {
    const n = state.bugs.filter((b) => inFilter(b, k)).length
    return `<button class="chip" data-f="${k}" aria-pressed="${state.filter === k}">${l}<b>${n}</b></button>`
  }).join('')
  document.querySelectorAll('.chip').forEach((c) => c.onclick = () => { state.filter = c.dataset.f; renderChips(); renderList() })
}

function row(b) {
  const [label, cls] = STATUS[b.status] || [b.status, 'quiet']
  const meta = [b.reporter && esc(b.reporter), esc(VIA[b.origin] || b.origin), !state.repo && b.repo && `<span class="mono">${esc(repoName(b.repo))}</span>`, ago(b.created)]
    .filter(Boolean).join(' <i></i> ')
  let body = ''
  if (b.status === 'running') {
    const at = Math.max(STAGES.indexOf(b.stage), 0)
    body = `<div class="stagetxt">${STAGE_TEXT[b.stage] || 'Starting'}…</div><div class="prog" aria-hidden="true">${STAGES.map((_, i) => `<span class="${i < at ? 'on' : i === at ? 'now' : ''}"></span>`).join('')}</div>`
  } else if (b.status === 'not-reproduced' && b.why) {
    body = `<div class="why">${esc(b.why)}</div>`
  } else if (b.status === 'error' && b.why) {
    body = `<div class="why">${esc(b.why)}</div>`
  }
  const pr = b.pr_url ? `<a class="link" href="${esc(b.pr_url)}" target="_blank" rel="noopener">PR${b.pr_state ? ' · ' + esc(b.pr_state) : ''}</a>`
    : b.status === 'fixed' && b.origin === 'upload' ? `<button class="pill small ghost openpr" data-id="${esc(b.id)}">Open PR</button>` : ''
  const issue = b.issue_url ? `<a class="link" href="${esc(b.issue_url)}" target="_blank" rel="noopener">Issue</a>` : ''
  return `<div class="row">
    <div><a class="t" href="/runs/${esc(b.id)}">${esc(b.title)}</a><div class="meta">${meta}</div>${body}</div>
    <div class="right"><span class="st ${cls}">${label}</span><span class="time">${dur(b.seconds)}</span><div class="acts">${issue}${pr}</div></div></div>`
}

function renderList() {
  const list = $('#list')
  const shown = state.bugs.filter((b) => inFilter(b, state.filter))
  if (!state.repos.length) {
    list.innerHTML = `<div class="empty"><h3 class="serif">Connect your first repo</h3>
      <p>Install Gecko on a repository and it starts watching for bug reports. Setup takes about a minute.</p>
      <ol class="steps"><li>Install the Gecko GitHub App on a repo</li><li>Add a small <code>.gecko.yml</code> so Gecko can start your app</li><li>Share your report link with support, QA or customers</li></ol>
      <a class="pill" href="${esc(state.me.install_url || '/github/setup')}">Install on GitHub</a></div>`
    return
  }
  if (!shown.length) {
    list.innerHTML = state.bugs.length
      ? `<div class="empty"><h3 class="serif">Nothing here</h3><p>No bugs match this filter.</p></div>`
      : `<div class="empty"><h3 class="serif">No bugs yet</h3><p>Share your report link, or drop a recording on the right to see Gecko work.</p></div>`
    return
  }
  list.innerHTML = shown.slice(0, 40).map(row).join('')
  list.querySelectorAll('.openpr').forEach((b) => b.onclick = async () => {
    b.disabled = true; b.textContent = 'Opening…'
    try { await api(`/api/runs/${b.dataset.id}/pr`, { method: 'POST' }); await load() } catch (e) { b.textContent = 'Failed'; b.title = e.message }
  })
}

function renderLatest() {
  const b = state.bugs.find((x) => x.status === 'fixed')
  $('#latest').innerHTML = !b ? '' : `<div class="card dark"><div class="who2"><i></i>gecko · ${ago(b.created)}</div>
    <p><b>Caught it.</b> ${esc(b.title)}</p>
    <div class="res"><span>${dur(b.seconds)} to a fix</span>${b.pr_url ? `<a class="l" href="${esc(b.pr_url)}" target="_blank" rel="noopener">View PR${b.pr_state ? ' (' + esc(b.pr_state) + ')' : ''}</a>` : `<a class="l" href="/runs/${esc(b.id)}">See the proof</a>`}</div></div>`
}

function renderRepos() {
  const el = $('#repos')
  const repos = state.repos.filter((r) => !state.repo || r.repo === state.repo)
  if (!repos.length) {
    el.innerHTML = `<h3 class="serif">Repositories</h3><p class="sub">No repository connected yet.</p><a class="pill small" href="${esc(state.me.install_url || '/github/setup')}">Install on GitHub</a>`
    return
  }
  el.innerHTML = `<h3 class="serif">Report link</h3><p class="sub">Send this to anyone who sees bugs. They never need GitHub.</p>` + repos.map((r) => {
    const url = r.report_link ? location.origin + r.report_link : ''
    const pct = Math.min(100, Math.round((100 * r.fixes_this_month) / Math.max(r.monthly_fix_limit, 1)))
    return `<div class="repo"><div class="n">${esc(r.repo)}</div>
      <div class="setup"><span class="dot ${r.configured ? '' : 'off'}"></span>${r.configured ? 'Ready to catch bugs' : 'Needs a <code>.gecko.yml</code> · <a class="link" href="/welcome#setup">how</a>'}</div>
      ${r.report_link ? `<div class="linkbox"><input readonly value="${esc(url)}" aria-label="Report link for ${esc(r.repo)}"><button class="pill small ghost copy" data-url="${esc(url)}">Copy</button></div>` : '<div class="why" style="color:var(--muted);font-size:13px">The report link is off.</div>'}
      <div class="sw"><span>Accepting reports</span><button class="switch" role="switch" aria-checked="${r.report_link ? 'true' : 'false'}" aria-label="Accepting reports for ${esc(r.repo)}" data-repo="${esc(r.repo)}"></button></div>
      <div class="use"><span>Fixes this month</span><span class="mono">${r.fixes_this_month} / ${r.monthly_fix_limit}</span></div><div class="meter"><span style="width:${pct}%"></span></div></div>`
  }).join('')
  el.querySelectorAll('.copy').forEach((b) => b.onclick = async () => {
    try { await navigator.clipboard.writeText(b.dataset.url) } catch { b.previousElementSibling.select(); document.execCommand('copy') }
    b.textContent = 'Copied'; setTimeout(() => { b.textContent = 'Copy' }, 1500)
  })
  el.querySelectorAll('.switch').forEach((s) => s.onclick = async () => {
    const on = s.getAttribute('aria-checked') !== 'true'
    s.setAttribute('aria-checked', String(on))
    await api(`/api/workspace/repos/${s.dataset.repo}/settings`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ report_link: on }) })
    load()
  })
}

// ---- try it
let tryFile = null
const pick = (f) => { if (!f) return; tryFile = f; $('#dropText').textContent = f.name; $('#drop').classList.add('has'); $('#go').disabled = false }
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
    await api('/api/runs', { method: 'POST', body: fd })
    note.textContent = 'Started. It will appear in your bugs in a moment.'
    $('#dropText').textContent = 'Choose a video, or drop it here'; $('#drop').classList.remove('has'); tryFile = null
    await load()
  } catch (e) { note.className = 'note bad'; note.textContent = e.message; $('#go').disabled = false }
}

$('#repoSel').onchange = (e) => {
  state.repo = e.target.value
  try { localStorage.setItem('gecko.repo', state.repo) } catch {}
  load()
}

// ---- live refresh: fast while something is running
async function tick() {
  if (!document.hidden) { try { await load() } catch {} }
  const active = state.bugs.some((b) => ['queued', 'running'].includes(b.status))
  setTimeout(tick, active ? 4000 : 20000)
}
load().catch((e) => { $('#list').innerHTML = `<div class="empty"><h3 class="serif">Couldn't load your workspace</h3><p>${esc(e.message)}</p></div>` })
setTimeout(tick, 4000)
