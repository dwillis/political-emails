# Domains → Committees Page Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** A public `domains.html` page — the reverse of `committees.html`: pick a sender domain, see every identified committee that has sent from it. Top-50 table by default, search over all ~4,200 domains, deep-linkable selections.

**Architecture:** No new data files (design doc: `plans/2026-10-08-domains-page-design.md`, decision "Approach A"). The page fetches the two files `committees.html` already uses — `committees.json` and `committee_domains.json` (an array aligned by index with `committees.committees`; each element is `[domain, emails, first, last]` rows, sorted by emails desc) — and inverts them into `domain -> [[cIdx, emails, first, last], …]` client-side. All work is in `scripts/build_site.py` (new page template + small edits to the committees page template) and `README.md`.

**Tech Stack:** Python (stdlib only) + vanilla JS served as static HTML from GitHub Pages. Tests: pytest (`uv run pytest` from repo root; `pythonpath = ["scripts"]` is already configured).

**Conventions to match:** `build_site.py` page templates are *plain* (non-f-string) triple-quoted strings containing the full HTML+CSS+JS, with `__CSS__` / `__GENERATED__` placeholders replaced by a `generate_*_html(generated_iso)` function. Page JS relies on implicit globals from element ids (e.g. `summary`, `table`) the same way `_COMMITTEES_PAGE_BODY`'s script does. No literal `__` may appear anywhere else in a page (an existing test asserts this).

Anchors in `scripts/build_site.py` (line numbers approximate, file is ~1,966 lines):
- `COMMITTEES_MIN_EMAILS = 10` (line 48), `ACCENT = "#e89b3c"` (line 42)
- `SHARED_CSS = f"""` (line 60) — defines `:root` vars (`--primary`, `--accent`, `--border`, …), `header`, `.header-links`, `main`, `h2`
- `_COMMITTEES_PAGE_CSS = """` (line 1519), `_COMMITTEES_PAGE_BODY = """` (line 1544)
- `def generate_committees_html(generated_iso):` (line 1747), its `.replace(...)` ends at line 1755
- `def generate_downloads_html(download_info):` (line 1758)
- `def main():` (line 1892); the committees-page write block is lines 1939–1950
- committees-page nav line (1557): `<div class="header-links"><a href="index.html">Home</a><a href="downloads.html">All Downloads</a><a href="sender-mentions.html">Sender mentions</a><a href="https://github.com/dwillis/political-emails">GitHub</a></div>`
- committees-page `domainRow(i)` JS (lines 1670–1678); the domain cell is `'<tr><td>' + esc(r[0]) + '</td>…`

---

### Task 1: Page shell and data loading (fetch both JSONs, build reverse index)

**Files:**
- Modify: `scripts/build_site.py` (insert after `generate_committees_html`, ends line 1755)
- Test: `tests/test_build_site.py` (append at end)

**Step 1: Write the failing test**

Append to `tests/test_build_site.py`:

```python
def test_generate_domains_page_shell_and_data_contract():
    import build_site

    html = build_site.generate_domains_html("2026-10-08T12:00:00+00:00")

    assert html.startswith("<!DOCTYPE html>")
    assert "<title>Domains — Political Email Archive</title>" in html
    assert "fetch('committees.json')" in html
    assert "fetch('committee_domains.json')" in html
    assert "Generated 2026-10-08 12:00 UTC." in html
    assert '<a href="committees.html">Committees</a>' in html
    assert "__" not in html  # template placeholders must all be replaced
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_build_site.py::test_generate_domains_page_shell_and_data_contract -v`
Expected: FAIL with `AttributeError: module 'build_site' has no attribute 'generate_domains_html'`

**Step 3: Implement the page shell + loader**

Insert after `generate_committees_html`'s return (line 1755), before `def generate_downloads_html`:

```python
_DOMAINS_PAGE_CSS = """
    .tracker-intro { color: #555; margin-bottom: 1rem; }
    .tracker-controls { display: flex; flex-wrap: wrap; gap: 1rem; align-items: end; margin: 1rem 0; }
    .tracker-controls label { display: flex; flex-direction: column; gap: 0.25rem; font-size: 0.85rem; font-weight: 600; }
    .search-wrap { position: relative; }
    .tracker-controls input { font: inherit; padding: 0.35rem 0.5rem; border: 1px solid var(--border); border-radius: 4px; background: white; min-width: 16rem; }
    .tracker-summary { color: #666; font-size: 0.9rem; margin: 0.5rem 0 1rem; }
    .suggest { display: none; position: absolute; top: 100%; left: 0; width: 100%; max-width: 40rem; z-index: 20; background: white; border: 1px solid var(--border); border-radius: 4px; box-shadow: 0 4px 12px rgba(0,0,0,0.08); max-height: 24rem; overflow-y: auto; }
    .suggest-item { padding: 0.4rem 0.7rem; font-size: 0.85rem; cursor: pointer; }
    .suggest-item:hover { background: #f2f6f2; }
    .suggest-meta { display: block; color: #777; font-size: 0.75rem; }
    .suggest-empty { padding: 0.4rem 0.7rem; font-size: 0.85rem; color: #777; }
    .mention-table { width: 100%; border-collapse: collapse; background: white; font-size: 0.9rem; }
    .mention-table th, .mention-table td { padding: 0.55rem 0.65rem; border-bottom: 1px solid var(--border); text-align: left; }
    .mention-table th { color: var(--primary); font-size: 0.75rem; letter-spacing: 0.05em; text-transform: uppercase; }
    .mention-table th.sortable { cursor: pointer; user-select: none; }
    .mention-table th.sortable:hover { text-decoration: underline; }
    .mention-table td.num, .mention-table th.num { text-align: right; font-variant-numeric: tabular-nums; }
    .mention-table .party { font-weight: 700; }
    .back-row { margin: 0 0 0.5rem; }
    .back-row a { color: var(--primary); font-size: 0.9rem; }
    .rank { color: #888; font-variant-numeric: tabular-nums; }
    .tracker-error { color: #8b1e1e; }
"""
```

Then `_DOMAINS_PAGE_BODY` with the shell and the load/index JS only (views come in Tasks 2–3; `render()` initially only fills the summary so the shell is coherent between tasks):

```python
_DOMAINS_PAGE_BODY = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Domains — Political Email Archive</title>
  <style>__CSS__</style>
  <link href="https://fonts.googleapis.com/css2?family=Libre+Baskerville:wght@400;700&display=swap" rel="stylesheet">
</head>
<body>
  <header>
    <h1>Political <span>Email</span> Archive</h1>
    <p>The reverse of the committees page: choose a sender domain and see every identified committee that has sent from it.</p>
    <div class="header-links"><a href="index.html">Home</a><a href="downloads.html">All Downloads</a><a href="committees.html">Committees</a><a href="https://github.com/dwillis/political-emails">GitHub</a></div>
  </header>
  <main>
    <h2 id="view-title">Top 50 domains</h2>
    <p class="back-row" id="back-row" hidden><a href="#" id="back-link">← All domains</a></p>
    <p class="tracker-intro">Counts cover committee-identified emails only, all time — the same email universe as the <a href="committees.html">committees page</a>. They will not match the dashboard's "Top 10 sender domains" chart, which counts every email, committee or not.</p>
    <p class="tracker-intro"><a href="committee_domains.json">Download the underlying per-committee data (JSON)</a></p>
    <div class="tracker-controls" id="controls"><label>Search <div class="search-wrap"><input id="search" type="search" placeholder="e.g. win.donaldjtrump.com" autocomplete="off"><div class="suggest" id="suggest"></div></div></label></div>
    <p class="tracker-summary" id="summary">Loading…</p>
    <div id="table"></div>
  </main>
  <footer>Generated __GENERATED__ UTC. Created by <a href="mailto:dpwillis@umd.edu">Derek Willis</a>. Released under the <a href="https://github.com/dwillis/political-emails/blob/main/LICENSE">MIT License</a>.</footer>
<script>
const PARTY_NAMES = {D: 'D', R: 'R', OTH: 'Other', unknown: '—'};
const byDomain = new Map();   // domain -> [[cIdx, emails, first, last], ...] (cIdx indexes into data.committees)
let domains = [];             // [domain, total_emails, first_seen, last_seen, committee_count] sorted by emails desc
let data, domainRows;
let selected = null;          // selected domain, or null for the top-50 view
let sortKey = 'total', sortDir = -1;   // reset on every view change
const esc = value => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

function extremes(rows) {
  let total = 0, first = '9999-99-99', last = '';
  rows.forEach(r => { total += r[1]; if (r[2] < first) first = r[2]; if (r[3] > last) last = r[3]; });
  return { total, first, last };
}

function render() { /* added in later tasks */ }

Promise.all([
  fetch('committees.json').then(r => r.ok ? r.json() : Promise.reject()),
  fetch('committee_domains.json').then(r => r.ok ? r.json() : Promise.reject()),
]).then(([committees, perCommittee]) => {
  data = committees; domainRows = perCommittee;
  perCommittee.forEach((rows, cIdx) => rows.forEach(row => {
    const list = byDomain.get(row[0]);
    if (list) list.push([cIdx, row[1], row[2], row[3]]);
    else byDomain.set(row[0], [[cIdx, row[1], row[2], row[3]]]);
  }));
  domains = [...byDomain.entries()].map(([domain, rows]) => {
    const x = extremes(rows);
    return [domain, x.total, x.first, x.last, rows.length];
  }).sort((a, b) => b[1] - a[1] || (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0));
  // Deep link: ?d=<domain> selects a domain on load (build lowercases domain keys).
  const want = new URL(location.href).searchParams.get('d');
  if (want) selected = want;
  render();
}).catch(() => { summary.innerHTML = '<span class="tracker-error">The domain data could not be loaded.</span>'; });
</script>
</body>
</html>"""


def generate_domains_html(generated_iso):
    """Generate the client-rendered domains page (domains.html)."""
    return (
        _DOMAINS_PAGE_BODY
        .replace("__CSS__", SHARED_CSS + _DOMAINS_PAGE_CSS)
        .replace("__GENERATED__", escape(str(generated_iso)[:16].replace("T", " ")))
    )
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_build_site.py::test_generate_domains_page_shell_and_data_contract -v`
Expected: PASS

**Step 5: Commit**

```bash
git add scripts/build_site.py tests/test_build_site.py
git commit -m "Add domains.html page shell with reverse-index data loading"
```

---

### Task 2: Top-50 default view (sortable table)

**Files:**
- Modify: `scripts/build_site.py` (`_DOMAINS_PAGE_BODY` script + CSS unchanged)
- Test: `tests/test_build_site.py` (append)

**Step 1: Write the failing test**

Append to `tests/test_build_site.py`:

```python
def test_generate_domains_page_renders_top_fifty():
    import build_site

    html = build_site.generate_domains_html("2026-10-08T12:00:00+00:00")

    assert "const TOP_N = 50;" in html
    assert "Top 50 domains" in html
    assert 'data-sort="committees"' in html
    assert "slice(0, TOP_N)" in html
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_build_site.py::test_generate_domains_page_renders_top_fifty -v`
Expected: FAIL (`TOP_N` not in html)

**Step 3: Implement the top-50 view**

In `_DOMAINS_PAGE_BODY`'s script, replace:

```js
function render() { /* added in later tasks */ }
```

with:

```js
const TOP_N = 50;
const SUGGEST_MAX = 15;

function th(key, label, cls) {
  return '<th class="sortable ' + (cls || '') + '" data-sort="' + key + '">' + label
    + (sortKey === key ? (sortDir < 0 ? ' ▼' : ' ▲') : '') + '</th>';
}

function renderTop() {
  viewTitle.textContent = 'Top 50 domains';
  backRow.hidden = true; controls.hidden = false;
  const grand = domains.reduce((s, d) => s + d[1], 0);
  summary.textContent = grand.toLocaleString() + ' committee-identified emails across '
    + domains.length.toLocaleString() + ' sender domains from ' + data.committees.length.toLocaleString()
    + ' committees. The list shows the top ' + TOP_N + ' by volume; search covers every domain.';
  const keyFns = { domain: d => d[0], committees: d => d[4], total: d => d[1], first_seen: d => d[2], last_seen: d => d[3] };
  const sorted = domains.slice().sort((a, b) => {
    const x = keyFns[sortKey](a), y = keyFns[sortKey](b);
    return (x < y ? -1 : x > y ? 1 : 0) * sortDir || b[1] - a[1];
  });
  const body = sorted.slice(0, TOP_N).map((d, i) =>
    '<tr><td class="rank">' + (i + 1) + '</td><td><a href="#" class="domain-link" data-domain="' + esc(d[0]) + '">' + esc(d[0]) + '</a></td>'
    + '<td class="num">' + d[4].toLocaleString() + '</td><td class="num">' + d[1].toLocaleString() + '</td>'
    + '<td>' + esc(d[2]) + '</td><td>' + esc(d[3]) + '</td></tr>'
  ).join('');
  table.innerHTML = '<table class="mention-table"><thead><tr><th>Rank</th>' + th('domain', 'Domain')
    + th('committees', 'Committees', 'num') + th('total', 'Emails', 'num')
    + th('first_seen', 'First seen') + th('last_seen', 'Last seen')
    + '</tr></thead><tbody>' + body + '</tbody></table>';
}

function render() {
  if (selected) renderDomain(); else renderTop();
}

function renderDomain() { /* added in Task 3 */ }
```

Sorting the full list before the `slice(0, TOP_N)` means any column sort shows the top 50 *of that ordering* (e.g. sort by Committees to surface shared broadcast domains).

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_build_site.py::test_generate_domains_page_renders_top_fifty -v`
Expected: PASS (the `renderDomain` stub keeps the shell test passing)

**Step 5: Commit**

```bash
git add scripts/build_site.py tests/test_build_site.py
git commit -m "Add top-50 domains table to domains.html"
```

---

### Task 3: Search, domain detail view, deep links

**Files:**
- Modify: `scripts/build_site.py` (`_DOMAINS_PAGE_BODY` script)
- Test: `tests/test_build_site.py` (append)

**Step 1: Write the failing test**

Append to `tests/test_build_site.py`:

```python
def test_generate_domains_page_search_detail_and_deeplink():
    import build_site

    html = build_site.generate_domains_html("2026-10-08T12:00:00+00:00")

    assert "suggest-item" in html
    assert "history.replaceState" in html
    assert "searchParams.get('d')" in html
    assert "No committee-linked emails found for" in html
    assert "% of committee's total" in html
    assert "fec.gov/data/committee/" in html
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_build_site.py::test_generate_domains_page_search_detail_and_deeplink -v`
Expected: FAIL

**Step 3: Implement search, detail view, deep links**

In `_DOMAINS_PAGE_BODY`'s script, replace the `renderDomain` stub with the real view, and add the search/suggestion/selection handlers:

```js
function renderDomain() {
  viewTitle.textContent = selected;
  backRow.hidden = false; controls.hidden = true;
  const rows = selected ? byDomain.get(selected.toLowerCase()) : null;
  if (!rows || !rows.length) {
    summary.innerHTML = 'No committee-linked emails found for <strong>' + esc(selected) + '</strong>.';
    table.innerHTML = '';
    return;
  }
  const x = extremes(rows);
  summary.textContent = x.total.toLocaleString() + ' committee-identified emails from '
    + rows.length.toLocaleString() + (rows.length === 1 ? ' committee' : ' committees')
    + ', first seen ' + x.first + ', last seen ' + x.last + '.';
  const keyFns = { name: r => r.c.name.toLowerCase(), total: r => r.n, pct: r => r.n / r.c.total, first_seen: r => r.first, last_seen: r => r.last };
  const shown = rows.map(r => ({ c: data.committees[r[0]], n: r[1], first: r[2], last: r[3] }))
    .sort((a, b) => {
      const x1 = keyFns[sortKey](a), y1 = keyFns[sortKey](b);
      return (x1 < y1 ? -1 : x1 > y1 ? 1 : 0) * sortDir || b.n - a.n;
    });
  const body = shown.map(r => {
    const name = r.c.fec_id
      ? '<a href="https://www.fec.gov/data/committee/' + encodeURIComponent(r.c.fec_id) + '/">' + esc(r.c.name) + '</a>'
      : esc(r.c.name);
    const pct = r.c.total ? 100 * r.n / r.c.total : 0;
    return '<tr><td>' + name + '</td><td class="party">' + esc(PARTY_NAMES[r.c.party] || r.c.party) + '</td>'
      + '<td class="num">' + r.n.toLocaleString() + '</td><td class="num">' + pct.toFixed(1) + '%</td>'
      + '<td>' + esc(r.first) + '</td><td>' + esc(r.last) + '</td></tr>';
  }).join('');
  table.innerHTML = '<table class="mention-table"><thead><tr>' + th('name', 'Committee') + '<th>Party</th>'
    + th('total', 'Emails', 'num') + th('pct', "% of committee's total", 'num')
    + th('first_seen', 'First seen') + th('last_seen', 'Last seen')
    + '</tr></thead><tbody>' + body + '</tbody></table>';
}

function setUrlParam(domain) {
  const url = new URL(location.href);
  if (domain) url.searchParams.set('d', domain); else url.searchParams.delete('d');
  history.replaceState(null, '', url);
}

function selectDomain(domain) {
  selected = domain; sortKey = 'total'; sortDir = -1;
  setUrlParam(domain);
  search.value = ''; hideSuggest();
  render(); window.scrollTo(0, 0);
}

function deselect() {
  selected = null; sortKey = 'total'; sortDir = -1;
  setUrlParam(null);
  render(); window.scrollTo(0, 0);
}

function hideSuggest() { suggest.style.display = 'none'; }

function showSuggest(q) {
  if (!domains || !q) { hideSuggest(); return; }
  const matches = domains.filter(d => d[0].indexOf(q) !== -1).slice(0, SUGGEST_MAX);
  suggest.innerHTML = matches.length ? matches.map(d =>
    '<div class="suggest-item" data-domain="' + esc(d[0]) + '">' + esc(d[0])
    + '<span class="suggest-meta">' + d[1].toLocaleString() + ' emails · ' + d[4].toLocaleString() + ' committees</span></div>'
  ).join('') : '<div class="suggest-empty">No matching domains.</div>';
  suggest.style.display = 'block';
}

search.addEventListener('input', () => showSuggest(search.value.trim().toLowerCase()));
search.addEventListener('keydown', e => {
  if (e.key === 'Enter') {
    const first = suggest.querySelector('.suggest-item');
    if (first) selectDomain(first.dataset.domain);
  } else if (e.key === 'Escape') {
    hideSuggest();
  }
});
suggest.addEventListener('mousedown', e => {
  const item = e.target.closest && e.target.closest('.suggest-item');
  if (item) { e.preventDefault(); selectDomain(item.dataset.domain); }
});
document.addEventListener('click', e => {
  if (!e.target.closest || !e.target.closest('.search-wrap')) hideSuggest();
});
backRow.addEventListener('click', e => {
  if (e.target.id === 'back-link') { e.preventDefault(); deselect(); }
});
table.addEventListener('click', e => {
  if (e.target.dataset && e.target.dataset.domain) { e.preventDefault(); selectDomain(e.target.dataset.domain); return; }
  const key = e.target.dataset && e.target.dataset.sort;
  if (!key) return;
  if (sortKey === key) sortDir = -sortDir;
  else { sortKey = key; sortDir = (key === 'domain' || key === 'name') ? 1 : -1; }
  render();
});
```

Note: this same `table.addEventListener('click', …)` is the only sort handler — it already exists? No: in this plan the sort handler appears here for the first time (Task 2's code has no listeners). If the click handler was already added in Task 2, merge rather than duplicate.

Note: `e.target.closest` guard keeps SVG/text targets from throwing. The `mousedown` handler selects before the input's blur can close the dropdown.

**Step 4: Run all page tests to verify they pass**

Run: `uv run pytest tests/test_build_site.py -v`
Expected: PASS (all, including the Task 1–2 tests)

**Step 5: Commit**

```bash
git add scripts/build_site.py tests/test_build_site.py
git commit -m "Add search, domain detail view, and deep links to domains.html"
```

---

### Task 4: Cross-link from committees.html

**Files:**
- Modify: `scripts/build_site.py` (`_COMMITTEES_PAGE_BODY`: nav line and `domainRow`)
- Test: `tests/test_build_site.py` (append)

**Step 1: Write the failing test**

Append to `tests/test_build_site.py`:

```python
def test_committees_page_links_to_domains_page():
    import build_site

    html = build_site.generate_committees_html("2026-10-08T12:00:00+00:00")

    assert '<a href="domains.html">Domains</a>' in html
    assert "domains.html?d=" in html
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_build_site.py::test_committees_page_links_to_domains_page -v`
Expected: FAIL

**Step 3: Implement the two links**

Edit 1 — nav (in `_COMMITTEES_PAGE_BODY`, replace the whole header-links div):

```html
<div class="header-links"><a href="index.html">Home</a><a href="downloads.html">All Downloads</a><a href="sender-mentions.html">Sender mentions</a><a href="domains.html">Domains</a><a href="https://github.com/dwillis/political-emails">GitHub</a></div>
```

Edit 2 — in `domainRow(i)`, wrap the domain cell in a link (replace):

```js
else inner = '<table class="domain-table"><thead><tr><th>Domain</th><th class="num">Emails</th><th>First seen</th><th>Last seen</th></tr></thead><tbody>'
    + rows.map(r => '<tr><td>' + esc(r[0]) + '</td><td class="num">' + r[1].toLocaleString() + '</td><td>' + esc(r[2]) + '</td><td>' + esc(r[3]) + '</td></tr>').join('') + '</tbody></table>';
```

with:

```js
else inner = '<table class="domain-table"><thead><tr><th>Domain</th><th class="num">Emails</th><th>First seen</th><th>Last seen</th></tr></thead><tbody>'
    + rows.map(r => '<tr><td><a href="domains.html?d=' + encodeURIComponent(r[0]) + '">' + esc(r[0]) + '</a></td><td class="num">' + r[1].toLocaleString() + '</td><td>' + esc(r[2]) + '</td><td>' + esc(r[3]) + '</td></tr>').join('') + '</tbody></table>';
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_build_site.py::test_committees_page_links_to_domains_page tests/test_build_site.py::test_generate_committees_page_links_data_and_controls -v`
Expected: PASS (both — the existing committees-page test still passes)

**Step 5: Commit**

```bash
git add scripts/build_site.py tests/test_build_site.py
git commit -m "Link committees.html domain rows to domains.html and add nav link"
```

---

### Task 5: Wire the page into `main()` and build locally

**Files:**
- Modify: `scripts/build_site.py` (`main()`, after the committees-page write block at lines 1939–1950)
- Test: end-to-end verification (no unit test — `main()` writes to `DOCS_DIR`)

**Step 1: Wire into `main()`**

After the `committees_page.write_text(...)` + print block (lines 1948–1950), insert:

```python
    domains_page = DOCS_DIR / "domains.html"
    domains_page.write_text(generate_domains_html(committees["generated_at"]))
    print(f"  Wrote {domains_page}")
```

**Step 2: Run the test suite**

Run: `uv run pytest -v`
Expected: PASS (all existing tests plus the five new ones)

**Step 3: Build locally (end-to-end)**

Run: `cd scripts && uv run python build_site.py && cd ..`
Expected: build completes; output includes `Wrote docs/domains.html` and `Wrote docs/committees.html ...`. This reads years of local `data/`, so it takes a while; the same run in CI is the daily deploy.

**Step 4: Inspect the built page**

Run: `grep -c "mention-table" docs/domains.html`
Expected: a count ≥ 1 (JS template strings included once)

**Step 5: Serve and eyeball the real pages**

Run: `uv run python -m http.server 8000 -d docs` (keep running; open in browser)

Check at `http://localhost:8000/domains.html`:
1. Top-50 table renders with e.americanactionnews.com first (~10k emails).
2. Sorting by **Committees** puts shared broadcast domains on top.
3. Typing "win.donald" suggests `win.donaldjtrump.com`; Enter selects it; the detail table shows Trump committees near 100% of their total.
4. URL becomes `?d=win.donaldjtrump.com`; reloading lands straight in the detail view.
5. A bogus URL `?d=notadomain.com` shows the "No committee-linked emails" message.
6. Back link returns to the top 50 and clears `?d=`.
7. On `committees.html`, expand a committee's Domains row — a domain cell links through to the matching detail view.

Kill the server when done (Ctrl-C in the terminal running it).

**Step 6: Commit**

```bash
git add scripts/build_site.py
git commit -m "Write domains.html from build_site main()"
```

---

### Task 6: README documentation

**Files:**
- Modify: `README.md` (the "The build also writes `committees.html`..." paragraph ends line 72)

**Step 1: Add the doc paragraph**

Insert after the committees-page paragraph (line 72, before `### Committee Enrichment`):

```markdown
The build also writes `domains.html`: the reverse lookup — for a given sender
domain, the identified committees that have sent from it. It reuses
`committees.json` and `committee_domains.json` client-side (no new data file).
The default view lists the top 50 domains by volume; search covers every
committee-linked domain, and each selection deep-links as
`domains.html?d=<domain>`. Counts cover committee-identified emails only.
```

Note: `README.md` currently has uncommitted changes (`M README.md` in git status) — add only this paragraph, do not revert or reflow anything else, and leave the unrelated edits untouched in the commit (stage just this file as-is; the other edits remain the author's).

**Step 2: Commit**

```bash
git add README.md
git commit -m "Document domains.html in the README"
```

---

### Final verification (after Task 6)

Run: `uv run pytest -v`
Expected: full suite PASS.

Manual checklist from Task 5 Step 5 repeated once more against the rebuilt site (the deploy workflow picks everything up automatically; no `.github/workflows/` changes needed).