"""Generate a self-contained HTML page for reviewing SUSPECT committee labels.

Collects the SUSPECT-tier records (via validate_committees.classify), joins in
each record's full body and unique_id, and emits one static HTML file -- no
server, double-click to open. You review with the keyboard; decisions persist in
localStorage and export to a CSV that scripts/apply_corrections.py writes back.

    uv run python scripts/build_review_site.py            # all suspects
    uv run python scripts/build_review_site.py --reason contradicts-disclaimer

Open the printed path in a browser.
"""

import argparse
import json

from committee_extract import extract_committee, looks_confident
from validate_committees import build_domain_counts, classify
from committee_utils import iter_day_files
from utils import DATA_DIR, load_jsonl

OUT_PATH = DATA_DIR.parent / "state" / "validation" / "review.html"
BODY_CAP = 16000


def cap_body(body):
    """Keep the body reviewable but bounded, preserving the disclaimer tail."""
    if len(body) <= BODY_CAP:
        return body
    half = BODY_CAP // 2
    return body[:half] + "\n\n[... truncated ...]\n\n" + body[-half:]


def collect(reason_filter, skip_fec):
    fec_exact = (lambda _c: False)
    if not skip_fec:
        from fec_match import download_fec, load_fec_index, match_name
        download_fec()
        name_index, buckets = load_fec_index()
        cache = {}

        def fec_exact(c):
            if c not in cache:
                cache[c] = match_name(c, name_index, buckets)[0] == "exact"
            return cache[c]

    print("Aggregating domain counts...")
    domain_counts = build_domain_counts()
    out = []
    for path in iter_day_files():
        for rec in load_jsonl(path):
            if not rec.get("committee"):
                continue
            tier, reason = classify(rec, domain_counts, fec_exact)
            if tier != "SUSPECT":
                continue
            if reason_filter and reason != reason_filter:
                continue
            det = extract_committee(rec.get("body") or "")
            out.append({
                "id": rec.get("unique_id"),
                "date": rec.get("date"),
                "name": rec.get("name"),
                "email": rec.get("email"),
                "domain": rec.get("domain"),
                "subject": rec.get("subject"),
                "body": cap_body(rec.get("body") or ""),
                "committee": rec.get("committee"),
                "disclaimer_says": det if (det and looks_confident(det)) else "",
                "source": rec.get("committee_source"),
                "reason": reason,
            })
    return out


def render(records):
    data = json.dumps(records, ensure_ascii=False)
    return PAGE.replace("/*__DATA__*/", data)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reason", help="Only include this SUSPECT reason")
    parser.add_argument("--skip-fec", action="store_true")
    parser.add_argument("--out", default=str(OUT_PATH))
    args = parser.parse_args()

    records = collect(args.reason, args.skip_fec)
    print(f"SUSPECT records: {len(records)}")
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(render(records))
    print(f"wrote {args.out}\n  open it in a browser; export decisions when done.")


PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Committee label review</title>
<style>
  :root { color-scheme: light dark; --bg:#fff; --fg:#111; --mut:#666; --card:#f6f7f9;
          --line:#dcdfe4; --hi:#fde68a; --hi2:#bbf7d0; --acc:#2563eb; }
  @media (prefers-color-scheme: dark) { :root {
    --bg:#0f1115; --fg:#e7e9ee; --mut:#9aa2b1; --card:#171a21; --line:#2a2f3a;
    --hi:#7c6f1f; --hi2:#1f5133; --acc:#5b8cff; } }
  * { box-sizing: border-box; }
  body { margin:0; font:15px/1.5 system-ui,sans-serif; background:var(--bg); color:var(--fg); }
  header { position:sticky; top:0; background:var(--bg); border-bottom:1px solid var(--line);
           padding:10px 16px; display:flex; gap:16px; align-items:center; flex-wrap:wrap; }
  header b { font-size:16px; }
  .bar { flex:1; height:8px; background:var(--card); border-radius:4px; overflow:hidden; min-width:120px; }
  .bar > div { height:100%; background:var(--acc); width:0; }
  button { font:inherit; padding:6px 12px; border:1px solid var(--line); background:var(--card);
           color:var(--fg); border-radius:6px; cursor:pointer; }
  button:hover { border-color:var(--acc); }
  main { max-width:920px; margin:0 auto; padding:16px; }
  .card { border:1px solid var(--line); border-radius:10px; padding:16px; }
  .meta { color:var(--mut); font-size:13px; margin-bottom:6px; }
  .subject { font-weight:600; margin-bottom:12px; }
  .labels { display:flex; gap:10px; flex-wrap:wrap; margin-bottom:12px; }
  .chip { padding:6px 10px; border-radius:8px; background:var(--card); border:1px solid var(--line); }
  .chip .k { color:var(--mut); font-size:12px; display:block; }
  .chip.stored { background:var(--hi); }
  .chip.disc { background:var(--hi2); }
  pre.body { white-space:pre-wrap; word-break:break-word; background:var(--card);
             border:1px solid var(--line); border-radius:8px; padding:12px; max-height:340px;
             overflow:auto; font-size:13px; }
  mark { background:var(--hi); padding:0 2px; }
  mark.disc { background:var(--hi2); }
  .actions { display:flex; gap:8px; flex-wrap:wrap; margin-top:14px; align-items:center; }
  .actions .done { color:var(--mut); }
  input[type=text] { font:inherit; padding:6px 10px; border:1px solid var(--line);
                     border-radius:6px; background:var(--bg); color:var(--fg); min-width:220px; }
  kbd { font:12px monospace; background:var(--card); border:1px solid var(--line);
        border-radius:4px; padding:0 4px; }
  .hint { color:var(--mut); font-size:12px; margin-top:8px; }
  .empty { text-align:center; color:var(--mut); padding:60px 0; }
</style>
</head>
<body>
<header>
  <b>Committee review</b>
  <span id="counter" class="meta"></span>
  <div class="bar"><div id="prog"></div></div>
  <select id="filter"></select>
  <button onclick="exportCSV()">Export decisions CSV</button>
</header>
<main id="main"></main>
<script>
const RECORDS = /*__DATA__*/;
const KEY = "committee_review_decisions_v1";
let decisions = JSON.parse(localStorage.getItem(KEY) || "{}");
let idx = 0, filter = "all";

function reasons() {
  const s = new Set(RECORDS.map(r => r.reason));
  return ["all", ...[...s].sort()];
}
function filtered() {
  return RECORDS.filter(r => filter === "all" || r.reason === filter);
}
function esc(s){ return (s||"").replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c])); }
function hi(body, committee, disc) {
  let h = esc(body);
  const pfb = /(paid for (and authorized )?by[^\n]{0,140})/i;
  h = h.replace(pfb, '<mark class="disc">$1</mark>');
  if (committee) { try { h = h.replace(new RegExp(esc(committee).replace(/[.*+?^${}()|[\]\\]/g,'\\$&'),'g'), m=>'<mark>'+m+'</mark>'); } catch(e){} }
  return h;
}
function save(){ localStorage.setItem(KEY, JSON.stringify(decisions)); render(); }
function decide(id, choice, corrected){ decisions[id] = {choice, corrected: corrected||""}; if (idx < filtered().length-1) idx++; save(); }

function render(){
  const list = filtered();
  const done = list.filter(r => decisions[r.id]).length;
  document.getElementById("counter").textContent = `${done} / ${list.length} reviewed`;
  document.getElementById("prog").style.width = list.length ? (100*done/list.length)+"%" : "0";
  const main = document.getElementById("main");
  if (!list.length){ main.innerHTML = '<div class="empty">No records for this filter.</div>'; return; }
  if (idx >= list.length) idx = list.length-1;
  const r = list[idx];
  const d = decisions[r.id];
  main.innerHTML = `
    <div class="card">
      <div class="meta">${esc(r.date)} &middot; ${esc(r.domain)} &middot; reason: <b>${esc(r.reason)}</b> &middot; source: ${esc(r.source)}</div>
      <div class="subject">${esc(r.subject)}</div>
      <div class="meta">${esc(r.name)} &lt;${esc(r.email)}&gt;</div>
      <div class="labels">
        <div class="chip stored"><span class="k">stored committee</span>${esc(r.committee)}</div>
        ${r.disclaimer_says ? `<div class="chip disc"><span class="k">disclaimer says</span>${esc(r.disclaimer_says)}</div>` : ``}
      </div>
      <pre class="body">${hi(r.body, r.committee, r.disclaimer_says)}</pre>
      <div class="actions">
        <button onclick="decide('${r.id}','keep')">Keep stored <kbd>K</kbd></button>
        ${r.disclaimer_says ? `<button onclick="decide('${r.id}','disclaimer','${esc(r.disclaimer_says).replace(/'/g,"\\'")}')">Use disclaimer <kbd>D</kbd></button>` : ``}
        <button onclick="editVal('${r.id}')">Other&hellip; <kbd>E</kbd></button>
        <button onclick="decide('${r.id}','skip')">Skip <kbd>S</kbd></button>
        <span class="done">${d ? '✓ '+d.choice+(d.corrected?': '+esc(d.corrected):'') : ''}</span>
      </div>
      <div class="hint"><kbd>&larr;</kbd>/<kbd>&rarr;</kbd> navigate &middot; ${idx+1} of ${list.length}</div>
    </div>`;
}
function editVal(id){
  const cur = (decisions[id] && decisions[id].corrected) || "";
  const v = prompt("Correct committee name:", cur);
  if (v !== null && v.trim()) decide(id, "correct", v.trim());
}
function exportCSV(){
  const rows = [["unique_id","date","choice","corrected_committee","stored_committee"]];
  RECORDS.forEach(r => { const d = decisions[r.id]; if (d) rows.push([r.id, r.date, d.choice, d.corrected, r.committee]); });
  const csv = rows.map(row => row.map(c => '"'+String(c==null?"":c).replace(/"/g,'""')+'"').join(",")).join("\n");
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([csv], {type:"text/csv"}));
  a.download = "committee_decisions.csv"; a.click();
}
document.addEventListener("keydown", e => {
  if (e.target.tagName === "INPUT") return;
  const list = filtered(); if (!list.length) return; const r = list[idx];
  if (e.key === "ArrowRight") { if (idx<list.length-1) idx++; render(); }
  else if (e.key === "ArrowLeft") { if (idx>0) idx--; render(); }
  else if (e.key.toLowerCase() === "k") decide(r.id,"keep");
  else if (e.key.toLowerCase() === "d" && r.disclaimer_says) decide(r.id,"disclaimer",r.disclaimer_says);
  else if (e.key.toLowerCase() === "e") editVal(r.id);
  else if (e.key.toLowerCase() === "s") decide(r.id,"skip");
});
const sel = document.getElementById("filter");
reasons().forEach(x => { const o = document.createElement("option"); o.value=x; o.textContent=x; sel.appendChild(o); });
sel.onchange = () => { filter = sel.value; idx = 0; render(); };
render();
</script>
</body>
</html>"""


if __name__ == "__main__":
    main()
