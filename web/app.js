// Repro run page: builds the view from the run's event stream.
const $ = (s, el = document) => el.querySelector(s)
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c])
const fileUrl = (p) => {
  if (!p) return ''
  const i = p.indexOf('/runs/')
  if (i >= 0) return '/files' + p.slice(i)
  const j = p.indexOf('/samples/')
  return j >= 0 ? '/files' + p.slice(j) : '/files/' + p
}
const pad5 = (n) => String(n).padStart(5, '0')
const money = (n) => '$' + (n ?? 0).toFixed(n < 0.1 ? 3 : 2)

async function budget() {
  try {
    const b = await (await fetch('/api/budget')).json()
    $('#budget').textContent = `Meta API spend ${money(b.spent)} of $${b.cap}`
  } catch {}
}
budget()
setInterval(budget, 10000)

const m = location.pathname.match(/^\/runs\/([\w-]+)/)
if (m) runPage(m[1])
else homePage()

// ---------------------------------------------------------------- home
async function homePage() {
  const runs = await (await fetch('/api/runs')).json()
  $('#app').innerHTML = `
  <div class="home">
    <section class="card">
      <h2>Report a bug</h2>
      <p class="sub">Drop a screen recording. Narration helps but is optional.</p>
      <label class="drop" id="drop"><input type="file" id="file" accept="video/*" hidden>
        <div id="dropText">Choose or drop a video (MP4 or MOV)</div></label>
      <textarea id="report" rows="2" placeholder="Optional: what went wrong, in a sentence"></textarea>
      <button class="primary" id="go" disabled>Reproduce it</button>
    </section>
    <section class="card">
      <h2>Runs</h2>
      <p class="sub">Every run, with what Repro proved.</p>
      <ul class="runs">${runs.map((r) => `
        <li><span class="pill ${esc(r.status)}">${esc(label(r.status))}</span>
        <a class="t" href="/runs/${esc(r.id)}">${esc(r.title || r.id)}</a>
        <span class="meta">${r.seconds ? Math.round(r.seconds) + 's · ' : ''}${r.usd != null ? money(r.usd) : ''}</span></li>`).join('') || '<li class="meta">No runs yet.</li>'}</ul>
    </section>
  </div>`
  const file = $('#file'), drop = $('#drop')
  const pick = (f) => { if (!f) return; file.files = toList(f); $('#dropText').textContent = f.name; drop.classList.add('has'); $('#go').disabled = false }
  file.onchange = () => pick(file.files[0])
  drop.ondragover = (e) => { e.preventDefault() }
  drop.ondrop = (e) => { e.preventDefault(); pick(e.dataTransfer.files[0]) }
  $('#go').onclick = async () => {
    $('#go').disabled = true; $('#go').textContent = 'Uploading…'
    const fd = new FormData()
    fd.append('video', file.files[0]); fd.append('report', $('#report').value)
    const r = await (await fetch('/api/runs', { method: 'POST', body: fd })).json()
    location.href = '/runs/' + r.id
  }
}
function toList(f) { const dt = new DataTransfer(); dt.items.add(f); return dt.files }
function label(s) { return { running: 'Running', fixed: 'Fixed', reproduced: 'Reproduced', 'not-reproduced': 'Not reproduced', error: 'Error' }[s] || s }

// ---------------------------------------------------------------- run page
async function runPage(id) {
  const st = { id, events: [], summary: null, selected: null }
  const data = await (await fetch('/api/runs/' + id)).json()
  st.summary = data.summary
  $('#app').innerHTML = `<div class="runhead" id="head"></div>
    <div class="grid"><div class="left" id="left"></div><div id="stages"></div></div>`
  for (const e of data.events) st.events.push(e)
  render(st, true)
  if (!st.summary) {
    const es = new EventSource(`/api/runs/${id}/stream`)
    let skip = data.events.length
    es.onmessage = async (msg) => {
      if (skip > 0) { skip--; return }
      const e = JSON.parse(msg.data)
      st.events.push(e)
      if (e.kind === 'done') {
        es.close()
        st.summary = (await (await fetch('/api/runs/' + id)).json()).summary
      }
      render(st, false)
    }
  }
}

function derive(st) {
  const d = { stages: {}, attempts: {}, fixRounds: [], hypotheses: null, t: 0, usd: 0 }
  const usd0 = st.events[0]?.usd ?? 0
  for (const e of st.events) {
    d.t = e.t
    d.usd = (e.usd ?? usd0) - usd0
    if (e.kind === 'stage') d.stages[e.stage] = { ...(d.stages[e.stage] || {}), ...e, [e.status + 'At']: e.t }
    if (e.kind === 'replay') d.attempts[e.attempt] = { ...(d.attempts[e.attempt] || {}), replay: e }
    if (e.kind === 'judge') d.attempts[e.attempt] = { ...(d.attempts[e.attempt] || {}), judge: e }
    if (e.kind === 'test') d.attempts[e.attempt] = { ...(d.attempts[e.attempt] || {}), test: e }
    if (e.kind === 'hypotheses') d.hypotheses = e.hypotheses
    if (e.kind === 'fix_start') d.fixRounds.push({ n: e.round, running: true })
    if (e.kind === 'fix_round') d.fixRounds[d.fixRounds.length - 1] = { ...e, running: false }
    if (e.kind === 'error') d.error = e.error
    if (e.kind === 'done') d.done = e
  }
  if (st.summary) d.usd = st.summary.usd
  return d
}

function render(st, first) {
  const d = derive(st)
  const s = st.summary
  const plan = d.stages.understand?.plan || s?.plan
  const status = s?.status || (d.error ? 'error' : 'running')

  $('#head').innerHTML = `
    <div><h1>${esc(plan?.title || 'Reading the recording…')}</h1>
      <div class="sub"><span class="pill ${status}">${label(status)}</span>
      ${s?.branch ? ` &nbsp;branch <code>${esc(s.branch)}</code>` : ''}</div></div>
    <div class="stats"><div><b>${Math.round(s?.seconds ?? d.t)}s</b>elapsed</div><div><b>${money(d.usd)}</b>Meta API</div></div>`

  if (first || !$('#left video')) renderLeft(st, d)
  renderEvidence(st, d)
  $('#stages').innerHTML = [
    stageUnderstand(st, d, plan),
    stageGround(st, d, plan),
    stageReproduce(st, d, plan),
    stageFix(st, d),
    stageDeliver(st, d),
  ].join('')
  const prBtn = $('#prBtn')
  if (prBtn) prBtn.onclick = async () => {
    prBtn.disabled = true; prBtn.textContent = 'Opening…'
    const r = await fetch(`/api/runs/${st.id}/pr`, { method: 'POST' })
    const j = await r.json()
    if (r.ok) { st.summary.pr_url = j.pr_url; render(st, false) } else { prBtn.textContent = 'Failed: ' + (j.detail || r.status) }
  }
}

function renderLeft(st, d) {
  const v = d.stages.ingest?.video || st.summary?.video
  if (!v) return
  $('#left').innerHTML = `
    <div><div class="media"><video id="vid" src="${fileUrl(v)}" controls muted playsinline></video></div>
    <div class="caption"><span>The bug report: a plain screen recording</span><span id="dur"></span></div></div>
    <div><div class="media"><img id="evImg" alt="SAM 3.1 grounding"></div>
    <div class="caption"><span id="evCap">SAM 3.1 grounding appears here</span></div></div>
    <div class="evidence" id="evStrip"></div>`
}

function renderEvidence(st, d) {
  const g = d.stages.ground?.grounding
  const strip = $('#evStrip')
  if (!g || !strip) return
  const shots = g.filter((x) => x.overlay)
  if (strip.childElementCount !== shots.length) {
    strip.innerHTML = shots.map((x, i) => `<button data-i="${i}"><img src="${fileUrl(x.overlay)}"><span>step ${x.step_id}</span></button>`).join('')
    strip.querySelectorAll('button').forEach((b) => b.onclick = () => select(Number(b.dataset.i)))
    if (st.selected == null) {
      // Show the most interesting step: the one with most look-alikes.
      const best = shots.reduce((bi, x, i) => (x.count > (shots[bi]?.count ?? 0) ? i : bi), 0)
      select(best)
    }
  }
  function select(i) {
    st.selected = i
    const x = shots[i]
    $('#evImg').src = fileUrl(x.overlay)
    $('#evCap').innerHTML = `Step ${x.step_id}: <b>${esc(x.prompt || '—')}</b>, ${esc(x.reason)} <span class="chip ${x.confidence}">${x.confidence}</span>`
    strip.querySelectorAll('button').forEach((b, j) => b.classList.toggle('on', j === i))
  }
}

function stageBox(n, name, model, stage, body, opts = {}) {
  const cls = opts.fail ? 'fail' : stage?.doneAt != null ? 'done' : stage?.startAt != null ? 'active' : 'pending'
  const time = stage?.doneAt != null ? `${(stage.doneAt - stage.startAt).toFixed(0)}s` : stage?.startAt != null ? 'working…' : ''
  return `<section class="stage ${cls}"><div class="hd"><span class="num">${opts.fail ? '✕' : cls === 'done' ? '✓' : n}</span>
    <span class="name">${name}</span>${model.map((m) => `<span class="model">${m}</span>`).join('')}
    <span class="time">${time}</span></div><div class="bd">${body || ''}</div></section>`
}

function stageUnderstand(st, d, plan) {
  const s = d.stages.understand
  let body = ''
  if (plan) {
    const heard = plan.transcript?.transcript
    body = `${heard ? `<blockquote>“${esc(heard)}”</blockquote>` : ''}
      <dl class="kv"><dt>Expected</dt><dd>${esc(plan.expected)}</dd><dt>Actual</dt><dd>${esc(plan.actual)}</dd></dl>
      <ol class="steps">${plan.steps.map((x) => `<li>${esc(x.action)} <b>${esc(x.target?.name)}</b>${x.text ? ` “${esc(x.text)}”` : ''} <span class="why">frame ${x.frame}</span></li>`).join('')}</ol>`
  } else if (d.stages.ingest?.doneAt != null) {
    body = `<div class="why">${d.stages.ingest.frames} frames kept from ${d.stages.ingest.duration}s of video. Reading them…</div>`
  }
  return stageBox(1, 'Understand the recording', ['Muse Voice Transcribe', 'Muse Spark'], s ? { ...s, startAt: d.stages.ingest?.startAt } : d.stages.ingest, body)
}

function stageGround(st, d) {
  const s = d.stages.ground
  const g = s?.grounding
  const body = g ? `<ol class="steps">${g.map((x) => `<li>step ${x.step_id}<span class="chip ${x.confidence}">${x.confidence}</span> <span class="why">${esc(x.reason)}</span></li>`).join('')}</ol>` : ''
  return stageBox(2, 'Find what was clicked', ['SAM 3.1'], s, body)
}

function stageReproduce(st, d, plan) {
  const s = d.stages.reproduce
  const atts = Object.entries(d.attempts).sort((a, b) => a[0] - b[0])
  const bugFrame = plan ? `/files/runs/${st.id}/frames/f${pad5(plan.bug_frame)}.jpg` : ''
  let body = ''
  for (const [n, a] of atts) {
    const variants = Object.entries(a.replay?.variants || {}).map(([k, v]) => `step ${k} as <b>${esc(v)}</b>`).join(', ')
    let verdict = '<span class="verdict info">running</span>'
    if (a.test) {
      const rep = !a.test.passed && a.test.failure === 'assertion' && a.judge?.same_bug
      verdict = rep ? '<span class="verdict bad">Bug reproduced: test fails</span>' : a.test.passed ? '<span class="verdict info">No bug in replay: test passes</span>' : '<span class="verdict info">Inconclusive</span>'
    }
    if (n > 1 && Number(n) === 2 && d.hypotheses) {
      body += `<div class="hyp"><b>The straight replay did not show the bug.</b> Hypotheses from Muse Spark:<ul>${d.hypotheses.map((h) => `<li>step ${h.step} as <b>${esc(h.variant)}</b>: ${esc(h.why)}</li>`).join('')}</ul></div>`
    }
    body += `<div class="attempt"><div class="ah">Attempt ${n}${variants ? ` <span class="why">(${variants})</span>` : ''}<span style="margin-left:auto">${verdict}</span></div>
      ${a.replay?.final ? `<div class="compare"><figure><img src="${bugFrame}"><figcaption>User's video, bug frame</figcaption></figure>
      <figure><img src="${fileUrl(a.replay.final)}"><figcaption>Repro's replay in Chrome</figcaption></figure></div>` : ''}
      ${a.judge ? `<div class="why">Muse Spark: ${esc(a.judge.why)}</div>` : ''}
      ${a.test && !a.test.passed ? `<pre class="err">${esc(firstLines(a.test.error, 4))}</pre>` : ''}</div>`
  }
  const code = st.summary?.test_code || [...atts].reverse().find(([, a]) => a.test)?.[1].test.code
  if (code && s?.doneAt != null && s.reproduced) body += `<details open><summary>Generated test</summary><pre>${esc(code)}</pre></details>`
  if (s?.doneAt != null && !s.reproduced) body += `<div class="hyp"><b>Could not reproduce.</b> ${esc(s.why)}. Repro reports this instead of guessing.</div>`
  return stageBox(3, 'Reproduce in a real browser', ['Playwright', 'Muse Spark judge'], s, body, { fail: s?.doneAt != null && !s.reproduced })
}

function stageFix(st, d) {
  const s = d.stages.fix
  let body = ''
  for (const r of d.fixRounds) {
    if (r.running) { body += `<div class="why">Round ${r.n}: Muse Code is working on the fix…</div>`; continue }
    body += `<div class="attempt"><div class="ah">Round ${r.round}<span style="margin-left:auto">${r.accepted ? '<span class="verdict ok">Test passes · suite green</span>' : '<span class="verdict bad">Rejected</span>'}</span></div>
      <p>${esc(r.summary)}</p>${r.problem ? `<pre class="err">${esc(firstLines(r.problem, 4))}</pre>` : ''}
      ${r.diff ? `<pre class="diff">${colorDiff(r.diff)}</pre>` : ''}</div>`
  }
  return stageBox(4, 'Fix, with the test as the spec', ['Muse Code'], s, body, { fail: s?.doneAt != null && !s.fixed })
}

function stageDeliver(st, d) {
  const s = st.summary
  let body = ''
  if (s?.status === 'fixed') {
    body = s.pr_url ? `<div class="prbox"><span class="verdict ok">Pull request opened</span><a href="${esc(s.pr_url)}" target="_blank">${esc(s.pr_url)}</a></div>`
      : `<div class="prbox"><button id="prBtn">Open pull request</button><span class="why">Two commits on <code>${esc(s.branch)}</code>: the failing test, then the fix.</span></div>`
  }
  const stage = s?.pr_url ? { startAt: 0, doneAt: 0 } : null
  return stageBox(5, 'Deliver', ['GitHub'], stage, body)
}

function firstLines(s, n) { return String(s || '').split('\n').filter((l) => l.trim()).slice(0, n).join('\n') }
function colorDiff(diff) {
  return diff.split('\n').filter((l) => !l.startsWith('diff --git') && !l.startsWith('index ')).map((l) => {
    const e = esc(l)
    if (l.startsWith('+++') || l.startsWith('---')) return `<span class="why">${e}</span>`
    if (l.startsWith('+')) return `<span class="add">${e}</span>`
    if (l.startsWith('-')) return `<span class="del">${e}</span>`
    if (l.startsWith('@@')) return `<span class="hunk">${e}</span>`
    return e
  }).join('\n')
}
