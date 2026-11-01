"""HTML template for docs/mention-trend.html.

The page is static: it fetches mention_trend.json (built by mention_trend.py)
and renders the headline, weekly chart, breakdowns and committee panel
client-side. ``render`` fills a few tokens; the template is a plain string
(not an f-string) so the JavaScript braces need no escaping.
"""

import json
from html import escape

_PAGE_CSS = """
    .intro { color: #555; margin-bottom: 1rem; }
    .controls { display: flex; flex-wrap: wrap; gap: 1rem; align-items: end; margin: 1rem 0 1.5rem; }
    .controls label { display: flex; flex-direction: column; gap: 0.25rem; font-size: 0.85rem; font-weight: 600; }
    .controls select, .controls input { font: inherit; padding: 0.35rem 0.5rem; border: 1px solid var(--border); border-radius: 4px; background: white; }
    .headline { font-size: 1.1rem; line-height: 1.55; margin: 0.5rem 0 1rem; }
    .headline p + p { margin-top: 0.6rem; font-size: 1rem; color: #444; }
    .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 0.75rem; margin: 1rem 0; }
    .card { background: white; border: 1px solid var(--border); border-radius: 4px; padding: 0.8rem 1rem; }
    .card .v { font-size: 1.7rem; font-weight: 700; color: var(--primary); font-variant-numeric: tabular-nums; line-height: 1.2; }
    .card .v.down { color: #9b2c2c; }
    .card .v.up { color: #276749; }
    .card .l { font-size: 0.8rem; color: #666; margin-top: 0.2rem; }
    .chart { width: 100%; background: white; border: 1px solid var(--border); border-radius: 4px; display: block; }
    .legend { display: flex; flex-wrap: wrap; gap: 0.4rem 1.2rem; font-size: 0.82rem; color: #555; margin: 0.5rem 0 0; }
    .legend span { display: inline-flex; align-items: center; gap: 0.4rem; }
    .legend i { display: inline-block; width: 18px; height: 0; border-top-width: 3px; border-top-style: solid; }
    .legend i.box { height: 10px; border: 0; }
    .scroll { overflow-x: auto; -webkit-overflow-scrolling: touch; }
    table.t { width: 100%; border-collapse: collapse; background: white; font-size: 0.88rem; }
    table.t th, table.t td { padding: 0.5rem 0.6rem; border-bottom: 1px solid var(--border); text-align: left; vertical-align: top; }
    table.t th { color: var(--primary); font-size: 0.72rem; letter-spacing: 0.05em; text-transform: uppercase; white-space: nowrap; }
    table.t th.sortable { cursor: pointer; user-select: none; }
    table.t th.sortable:hover { text-decoration: underline; }
    table.t td.num, table.t th.num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
    table.t td.sub { color: #777; font-size: 0.8rem; }
    .neg { color: #9b2c2c; }
    .pos { color: #276749; }
    .flag { font-size: 0.72rem; background: #fdf0dc; color: #8a5a14; border-radius: 3px; padding: 0.05rem 0.35rem; margin-left: 0.35rem; white-space: nowrap; }
    .note { color: #666; font-size: 0.88rem; margin: 0.6rem 0; }
    .method { color: #555; font-size: 0.88rem; }
    .method li { margin: 0.3rem 0 0.3rem 1.2rem; }
    .error { color: #8b1e1e; }
    @media (max-width: 600px) { .card .v { font-size: 1.4rem; } header h1 { font-size: 1.8rem; } }
"""

_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Mention trends — Political Email Archive</title>
  <style>__SHARED_CSS____PAGE_CSS__</style>
  <link href="https://fonts.googleapis.com/css2?family=Libre+Baskerville:wght@400;700&display=swap" rel="stylesheet">
</head>
<body>
  <header>
    <h1>Political <span>Email</span> Archive</h1>
    <p>Are committees mentioning a tracked person more or less than they used to?</p>
    <div class="header-links"><a href="index.html">Home</a><a href="downloads.html">All Downloads</a><a href="committees.html">Committees</a><a href="sender-mentions.html">Sender mentions</a><a href="https://github.com/dwillis/political-emails">GitHub</a></div>
  </header>
  <main>
    <h2>Mention trends</h2>
    <p class="intro">Compares the most recent weeks with the 14 weeks before them, for emails that carry a &ldquo;Paid for by&rdquo; disclaimer from an identified committee. It also separates committees changing their own messages from new committees joining the mix.</p>
    <div class="controls">
      <label>Person <select id="person"></select></label>
      <label>Party <select id="party"><option value="R">Republican</option><option value="D">Democratic</option></select></label>
      <label>Recent period <select id="win"></select></label>
      <label>Chart range <select id="period"><option value="26">Last 26 weeks</option><option value="52" selected>Last 52 weeks</option><option value="0">All history</option></select></label>
    </div>
    <div class="headline" id="headline">Loading…</div>
    <div class="cards" id="cards"></div>

    <h2>Week by week</h2>
    <svg class="chart" id="chart" viewBox="0 0 800 320" role="img" aria-label="Weekly share of emails mentioning the selected person"></svg>
    <div class="legend" id="legend"></div>

    <h2>Who is driving the change</h2>
    <p class="note">If the share falls for committees that sent email in both periods, those committees changed their messages. If it falls only because new committees mention the person less, the mix of senders changed.</p>
    <div class="scroll" id="composition"></div>
    <p class="note" id="panelcounts"></p>

    <h2>By kind of committee</h2>
    <div class="scroll" id="breakdown"></div>

    <h2>Committees in both periods</h2>
    <p class="note" id="panelnote"></p>
    <div class="controls"><label>Filter committees <input id="filter" type="search" placeholder="Name contains…"></label></div>
    <div class="scroll" id="panel"></div>

    <h2>How this is measured</h2>
    <ul class="method" id="method"></ul>
    <p class="note"><a href="mention_trend.json">Download the data (JSON)</a></p>
  </main>
  <footer>Generated __GENERATED__ UTC. Created by <a href="mailto:dpwillis@umd.edu">Derek Willis</a>. Released under the <a href="https://github.com/dwillis/political-emails/blob/main/LICENSE">MIT License</a>.</footer>
<script>
const PARTY_COLORS = __PARTY_COLORS__;
const ACCENT = "__ACCENT__";
const PARTY_NAMES = {R: 'Republican', D: 'Democratic'};
const TYPE_LABELS = {'federal-candidate': 'Candidate', 'federal-pac': 'PAC', 'joint-fundraising': 'Joint fundraising', 'party-committee': 'National party', 'state-party': 'State party', 'newsletter': 'Newsletter', 'c4': '501(c)(4)', 'other': 'Other'};
const MONTHS = ['Jan.', 'Feb.', 'March', 'April', 'May', 'June', 'July', 'Aug.', 'Sept.', 'Oct.', 'Nov.', 'Dec.'];
const COL = {week: 0, emails: 1, mentions: 2, subject: 3, count: 4, words: 5, committees: 6, affEmails: 7, affMentions: 8};
let data, sortKey = 'change_pts', sortDir = 1;

const $ = id => document.getElementById(id);
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const num = v => v == null ? '–' : Number(v).toLocaleString();
const pct = v => v == null ? '–' : `${v.toFixed(1)}%`;
const pts = v => v == null ? '–' : `${v > 0 ? '+' : v < 0 ? '−' : ''}${Math.abs(v).toFixed(1)} pts`;
const cls = v => v == null || Math.abs(v) < 0.05 ? '' : v < 0 ? 'neg' : 'pos';
const day = iso => { const [y, m, d] = iso.split('-'); return `${MONTHS[+m - 1]} ${+d}, ${y}`; };
const shortDay = iso => { const [, m, d] = iso.split('-'); return `${MONTHS[+m - 1]} ${+d}`; };
const shift = (iso, days) => { const d = new Date(iso + 'T00:00:00Z'); d.setUTCDate(d.getUTCDate() + days); return d.toISOString().slice(0, 10); };
const pValue = p => p == null ? '–' : p < 0.001 ? '<0.001' : p.toFixed(3);

function current() {
  const p = data.people[$('person').value];
  const party = $('party').value;
  return {p, party, summ: (p.summaries[party] || {})[$('win').value], weekly: p.weekly[party] || []};
}

function render() {
  const s = current();
  if (!s.summ) { $('headline').innerHTML = '<p>Not enough data for this selection.</p>'; return; }
  renderHeadline(s);
  renderChart(s);
  renderComposition(s);
  renderBreakdown(s);
  renderPanel(s);
  renderMethod(s);
}

function renderHeadline({p, party, summ}) {
  const all = summ.rows[0], ex = summ.rows[1];
  const r = all.recent, b = all.baseline, c = all.change_pts;
  const dir = c == null ? 'compared with' : c <= -1 ? 'down from' : c >= 1 ? 'up from' : 'about the same as';
  const cont = summ.decomposition.continuing, nw = summ.decomposition.new;
  $('headline').innerHTML =
    `<p>In the ${summ.recent_weeks} weeks from ${day(summ.recent_range[0])} through ${day(summ.recent_range[1])}, <strong>${pct(r.share)}</strong> of ${num(r.emails)} ${PARTY_NAMES[party]} disclaimer emails mentioned ${esc(p.name)}. That is ${dir} ${pct(b.share)} of ${num(b.emails)} emails in the prior ${summ.baseline_weeks} weeks.</p>` +
    `<p>Among the ${num(cont.committees)} committees that sent email in both periods, the share went from ${pct(cont.baseline.share)} to ${pct(cont.recent.share)}. The ${num(nw.committees)} committees that only appear in the recent period mentioned ${esc(p.name)} in ${pct(nw.recent.share)} of their emails.</p>`;
  const card = (v, l, c) => `<div class="card"><div class="v ${c || ''}">${v}</div><div class="l">${l}</div></div>`;
  $('cards').innerHTML =
    card(pct(r.share), `Recent ${summ.recent_weeks} weeks<br>95% range ${pct(r.ci[0])}–${pct(r.ci[1])}`) +
    card(pct(b.share), `Prior ${summ.baseline_weeks} weeks<br>95% range ${pct(b.ci[0])}–${pct(b.ci[1])}`) +
    card(pts(c), `Change, all committees<br>p = ${pValue(all.p_value)}`, c <= -1 ? 'down' : c >= 1 ? 'up' : '') +
    card(pts(ex.change_pts), `Change, excluding committees named for ${esc(p.name)}<br>${pct(ex.baseline.share)} to ${pct(ex.recent.share)}`, ex.change_pts <= -1 ? 'down' : ex.change_pts >= 1 ? 'up' : '');
}

function renderChart({p, party, summ, weekly}) {
  const svg = $('chart');
  const byWeek = new Map(weekly.map(r => [r[COL.week], r]));
  let rows = weekly.filter(r => r[COL.week] <= data.last_complete_week);
  const range = +$('period').value;
  if (range) rows = rows.filter(r => r[COL.week] > shift(data.last_complete_week, -7 * range));
  if (!rows.length) { svg.innerHTML = '<text x="400" y="160" text-anchor="middle">No emails in this range.</text>'; return; }
  // fill missing weeks so gaps show as gaps, not as straight lines
  const weeks = [];
  for (let w = rows[0][COL.week]; w <= rows[rows.length - 1][COL.week]; w = shift(w, 7)) weeks.push(w);
  const share = r => r && r[COL.emails] ? 100 * r[COL.mentions] / r[COL.emails] : null;
  const subj = r => r && r[COL.emails] ? 100 * r[COL.subject] / r[COL.emails] : null;
  const raw = weeks.map(w => share(byWeek.get(w)));
  const rolling = weeks.map((w, i) => {
    let m = 0, e = 0;
    for (let k = Math.max(0, i - 2); k <= i; k++) { const r = byWeek.get(weeks[k]); if (r) { m += r[COL.mentions]; e += r[COL.emails]; } }
    return e ? 100 * m / e : null;
  });
  const subject = weeks.map(w => subj(byWeek.get(w)));
  const overlays = [];
  if (range) {
    for (const yearsBack of [1, 4]) {
      const vals = weeks.map(w => {
        // rolling 3-week share for the aligned week N years earlier (52 weeks/year keeps weekdays aligned)
        let m = 0, e = 0;
        for (let k = 0; k < 3; k++) { const r = byWeek.get(shift(w, -364 * yearsBack - 7 * k)); if (r) { m += r[COL.mentions]; e += r[COL.emails]; } }
        return e >= 30 ? 100 * m / e : null;
      });
      if (vals.some(v => v != null)) overlays.push({label: `Same weeks in ${shift(weeks[weeks.length - 1], -364 * yearsBack).slice(0, 4)}`, vals, dash: yearsBack === 1 ? '6 4' : '2 4'});
    }
  }
  const all = [...raw, ...rolling, ...subject, ...overlays.flatMap(o => o.vals)].filter(v => v != null);
  const max = Math.max(10, Math.ceil(Math.max(...all) / 10) * 10);
  const L = 44, T = 18, W = 736, H = 236;
  const x = i => L + (weeks.length === 1 ? W / 2 : i * W / (weeks.length - 1));
  const y = v => T + H - (v / max) * H;
  const path = vals => { let d = '', pen = false; vals.forEach((v, i) => { if (v == null) { pen = false; return; } d += `${pen ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`; pen = true; }); return d; };
  const color = PARTY_COLORS[party] || ACCENT;
  const idx = w => weeks.indexOf(w);
  const band = (from, to, fill) => {
    let a = idx(from), b = idx(to);
    if (a < 0 && from < weeks[0]) a = 0;
    if (b < 0 || a < 0) return '';
    const half = weeks.length > 1 ? W / (weeks.length - 1) / 2 : 10;
    return `<rect x="${Math.max(L, x(a) - half)}" y="${T}" width="${Math.min(L + W, x(b) + half) - Math.max(L, x(a) - half)}" height="${H}" fill="${fill}"/>`;
  };
  let grid = '';
  for (let v = 0; v <= max; v += max > 40 ? 20 : 10) grid += `<line x1="${L}" x2="${L + W}" y1="${y(v)}" y2="${y(v)}" stroke="#eee"/><text x="${L - 6}" y="${y(v) + 4}" font-size="11" text-anchor="end" fill="#666">${v}%</text>`;
  const ticks = Math.min(weeks.length, 8);
  let xl = '';
  const seen = new Set();
  for (let k = 0; k < ticks; k++) {
    const i = ticks === 1 ? 0 : Math.round(k * (weeks.length - 1) / (ticks - 1));
    if (seen.has(i)) continue; seen.add(i);
    const w = weeks[i], label = weeks.length > 60 ? `${MONTHS[+w.slice(5, 7) - 1]} ${w.slice(0, 4)}` : shortDay(w);
    const anchor = weeks.length > 1 && i === weeks.length - 1 ? 'end' : i === 0 && weeks.length > 1 ? 'start' : 'middle';
    xl += `<text x="${x(i)}" y="${T + H + 18}" font-size="11" text-anchor="${anchor}" fill="#666">${label}</text>`;
  }
  const dots = weeks.length <= 60 ? raw.map((v, i) => v == null ? '' : `<circle cx="${x(i)}" cy="${y(v)}" r="3" fill="${color}" opacity="0.45"><title>Week of ${day(weeks[i])}: ${pct(v)} of ${num(byWeek.get(weeks[i])[COL.emails])} emails</title></circle>`).join('') : '';
  svg.innerHTML =
    band(summ.baseline_range[0], shift(summ.baseline_range[1], -6), '#f1f1f1') +
    band(summ.recent_range[0], shift(summ.recent_range[1], -6), '#fdf0dc') +
    grid + xl +
    overlays.map(o => `<path d="${path(o.vals)}" fill="none" stroke="#999" stroke-width="1.5" stroke-dasharray="${o.dash}"/>`).join('') +
    `<path d="${path(subject)}" fill="none" stroke="${color}" stroke-width="1.5" stroke-dasharray="4 3" opacity="0.6"/>` +
    (weeks.length <= 60 ? `<path d="${path(raw)}" fill="none" stroke="${color}" stroke-width="1" opacity="0.35"/>` : '') +
    `<path d="${path(rolling)}" fill="none" stroke="${color}" stroke-width="3"/>` + dots;
  $('legend').innerHTML =
    `<span><i style="border-top-color:${color}"></i>Share of emails mentioning ${esc(p.name)} (3-week average)</span>` +
    (weeks.length <= 60 ? `<span><i style="border-top-color:${color};border-top-width:1px;opacity:.5"></i>Single week</span>` : '') +
    `<span><i style="border-top-color:${color};border-top-style:dashed;opacity:.6"></i>Mentioned in subject line</span>` +
    overlays.map(o => `<span><i style="border-top-color:#999;border-top-style:${o.dash === '6 4' ? 'dashed' : 'dotted'}"></i>${o.label}</span>`).join('') +
    `<span><i class="box" style="background:#f1f1f1"></i>Prior period</span><span><i class="box" style="background:#fdf0dc"></i>Recent period</span>`;
}

function renderComposition({p, summ}) {
  const d = summ.decomposition, all = summ.rows[0], cw = summ.committee_weighted;
  const row = (label, sub, b, r, bn, rn) => {
    const ch = b != null && r != null ? r - b : null;
    return `<tr><td>${label}<div class="sub">${sub}</div></td><td class="num">${pct(b)}<div class="sub">${bn}</div></td><td class="num">${pct(r)}<div class="sub">${rn}</div></td><td class="num ${cls(ch)}">${pts(ch == null ? null : Math.round(ch * 10) / 10)}</td></tr>`;
  };
  $('composition').innerHTML = `<table class="t"><thead><tr><th>Group</th><th class="num">Prior</th><th class="num">Recent</th><th class="num">Change</th></tr></thead><tbody>` +
    row('All committees', 'Every email counts once', all.baseline.share, all.recent.share, `${num(all.baseline.emails)} emails`, `${num(all.recent.emails)} emails`) +
    row('Typical committee', `Median committee share, committees with ${data.min_emails}+ emails`, cw.baseline.median_share, cw.recent.median_share, `${num(cw.baseline.committees)} committees`, `${num(cw.recent.committees)} committees`) +
    row('Committees in both periods', `${num(d.continuing.committees)} committees`, d.continuing.baseline.share, d.continuing.recent.share, `${num(d.continuing.baseline.emails)} emails`, `${num(d.continuing.recent.emails)} emails`) +
    row('Only in prior period', `${num(d.exited.committees)} committees that stopped sending`, d.exited.baseline.share, null, `${num(d.exited.baseline.emails)} emails`, '') +
    row('New in recent period', `${num(d.new.committees)} committees that started sending`, null, d.new.recent.share, '', `${num(d.new.recent.emails)} emails`) +
    `</tbody></table>`;
  const c = summ.panel_counts, n = c.fell + c.flat + c.rose;
  $('panelcounts').textContent = n ? `Of ${n} committees with at least ${data.min_emails} emails in both periods, ${c.fell} mention ${p.name} less (down more than ${data.flat_band_pts} points), ${c.flat} are about the same and ${c.rose} mention ${p.name} more.` : '';
}

function renderBreakdown({summ}) {
  $('breakdown').innerHTML = `<table class="t"><thead><tr><th>Committees</th><th class="num">Prior</th><th class="num">Recent</th><th class="num">Change</th><th class="num">p</th><th class="num">In subject</th><th class="num">Per 1,000 words</th></tr></thead><tbody>` +
    summ.rows.map(r => `<tr><td>${esc(r.label)}</td><td class="num">${pct(r.baseline.share)}<div class="sub">${num(r.baseline.emails)} emails</div></td><td class="num">${pct(r.recent.share)}<div class="sub">${num(r.recent.emails)} emails</div></td><td class="num ${cls(r.change_pts)}">${pts(r.change_pts)}</td><td class="num">${pValue(r.p_value)}</td><td class="num">${pct(r.baseline.subject_share)} → ${pct(r.recent.subject_share)}</td><td class="num">${r.baseline.per_1k_words ?? '–'} → ${r.recent.per_1k_words ?? '–'}</td></tr>`).join('') +
    `</tbody></table>`;
}

const PANEL_COLS = [
  ['committee', 'Committee', false], ['type', 'Kind', false],
  ['baseline_share', 'Prior share', true], ['recent_share', 'Recent share', true],
  ['change_pts', 'Change', true], ['recent_emails', 'Recent emails', true],
];

function renderPanel({p, summ}) {
  const q = $('filter').value.trim().toLowerCase();
  const rows = summ.panel.filter(r => !q || r.committee.toLowerCase().includes(q)).slice().sort((a, b) => {
    const va = a[sortKey] ?? '', vb = b[sortKey] ?? '';
    return (va < vb ? -1 : va > vb ? 1 : 0) * sortDir || a.committee.localeCompare(b.committee);
  });
  $('panelnote').textContent = `${summ.panel.length} committees sent at least ${data.min_emails} emails in each period. Click a column heading to sort.`;
  const head = PANEL_COLS.map(([k, label, isNum]) => `<th class="sortable${isNum ? ' num' : ''}" data-k="${k}">${label}${sortKey === k ? (sortDir > 0 ? ' ▲' : ' ▼') : ''}</th>`).join('');
  $('panel').innerHTML = rows.length ? `<table class="t"><thead><tr>${head}</tr></thead><tbody>` + rows.map(r =>
    `<tr><td>${esc(r.committee)}${r.affiliated ? `<span class="flag">named for ${esc(p.name)}</span>` : ''}</td><td>${esc(TYPE_LABELS[r.type] || '–')}</td>` +
    `<td class="num">${pct(r.baseline_share)}<div class="sub">${num(r.baseline_emails)} emails</div></td><td class="num">${pct(r.recent_share)}<div class="sub">${num(r.recent_emails)} emails</div></td>` +
    `<td class="num ${cls(r.change_pts)}">${pts(r.change_pts)}</td><td class="num">${num(r.recent_emails)}</td></tr>`).join('') + `</tbody></table>` : '<p class="note">No committees match.</p>';
  $('panel').querySelectorAll('th.sortable').forEach(th => th.addEventListener('click', () => {
    const k = th.dataset.k;
    if (sortKey === k) sortDir = -sortDir; else { sortKey = k; sortDir = k === 'committee' || k === 'type' ? 1 : -1; }
    renderPanel(current());
  }));
}

function renderMethod({p, summ}) {
  $('method').innerHTML = [
    `Emails count when they carry a &ldquo;Paid for by&rdquo; disclaimer and the sending committee has been identified. Party is the committee&rsquo;s party.`,
    `An email mentions ${esc(p.name)} when the subject or campaign copy matches one of the names in the project&rsquo;s tracked-people list. The surname alone counts. Text in the disclaimer and unsubscribe footer is ignored, so a committee&rsquo;s own name in its disclaimer is not a mention.`,
    `The recent period is the last ${summ.recent_weeks} complete Monday-to-Sunday weeks, ending ${day(summ.recent_range[1])}. The prior period is the ${summ.baseline_weeks} weeks before that, ${day(summ.baseline_range[0])} through ${day(summ.baseline_range[1])}. The current partial week is left out.`,
    `The 95% range is a Wilson interval. The p value comes from a two-proportion test; small values mean the change is unlikely to be chance alone.`,
    `&ldquo;Committees named for ${esc(p.name)}&rdquo; are those whose own name matches the same patterns, such as joint fundraising committees carrying the name.`,
    `Indirect references such as &ldquo;the President&rdquo; or &ldquo;MAGA&rdquo; are not counted.`,
  ].map(t => `<li>${t}</li>`).join('');
}

fetch('mention_trend.json').then(r => r.ok ? r.json() : Promise.reject()).then(d => {
  data = d;
  Object.entries(d.people).forEach(([slug, p]) => $('person').add(new Option(p.name, slug)));
  d.recent_windows.forEach(n => $('win').add(new Option(`Last ${n} weeks`, String(n), n === 7, n === 7)));
  ['person', 'party', 'win', 'period'].forEach(id => $(id).addEventListener('change', render));
  $('filter').addEventListener('input', () => renderPanel(current()));
  render();
}).catch(() => { $('headline').innerHTML = '<p class="error">The trend data could not be loaded.</p>'; });
</script>
</body>
</html>"""


def render(generated_iso, shared_css, party_colors, accent):
    generated = escape(str(generated_iso or "")[:16].replace("T", " "))
    return (_TEMPLATE
            .replace("__SHARED_CSS__", shared_css)
            .replace("__PAGE_CSS__", _PAGE_CSS)
            .replace("__PARTY_COLORS__", json.dumps(party_colors))
            .replace("__ACCENT__", accent)
            .replace("__GENERATED__", generated))
