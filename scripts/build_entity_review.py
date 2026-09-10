"""Generate a self-contained HTML page for triaging registry proposals.

Reads state/validation/registry_proposals.csv (from build_committee_registry)
and emits one static HTML file: no server, double-click to open. Keyboard
review; decisions persist in localStorage and export to a decisions CSV that
scripts/apply_registry_decisions.py merges into config/committee_registry.json
as `status: "human"`.

Decisions per proposal row:
  A  accept-fec   adopt an FEC candidate (first, or one you type) as identity
  N  new          new non-federal entity (+type)
  M  merge        alias into an existing registry entity id
  X  noise        not a committee
  S  skip         leave untriaged

    uv run python scripts/build_entity_review.py             # head 200
    uv run python scripts/build_entity_review.py --queue-cap 500

Open the printed path in a browser.
"""

import argparse
import csv
import json

from committee_registry import ENTITY_TYPES
from utils import STATE_DIR

PROPOSALS_PATH = STATE_DIR / "validation" / "registry_proposals.csv"
OUT_PATH = STATE_DIR / "validation" / "entity_review.html"


def load_rows(queue_cap, path=None):
    rows = list(csv.DictReader(open(path or PROPOSALS_PATH, encoding="utf-8")))
    rows.sort(key=lambda r: -int(r["record_count"]))
    return rows[:queue_cap]


def render(rows):
    data = json.dumps(rows, ensure_ascii=False)
    return PAGE.replace("/*__DATA__*/", data).replace(
        "__TYPES__", json.dumps(sorted(ENTITY_TYPES)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue-cap", type=int, default=200,
                        help="How many top rows to embed (500 ~= 89%% of gap)")
    parser.add_argument("--proposals", default=str(PROPOSALS_PATH))
    parser.add_argument("--out", default=str(STATE_DIR / "validation" / "entity_review.html"))
    args = parser.parse_args()

    rows = load_rows(args.queue_cap, args.proposals)
    print(f"Proposals in queue: {len(rows)} "
          f"({sum(int(r['record_count']) for r in rows):,} records)")
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(render(rows))
    print(f"wrote {args.out}\n  open it in a browser; export decisions when done.")


PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Committee entity review</title>
<style>
  :root { color-scheme: light dark; --bg:#fff; --fg:#111; --mut:#666; --card:#f6f7f9;
          --line:#dcdfe4; --acc:#2563eb; }
  @media (prefers-color-scheme: dark) { :root {
    --bg:#0f1115; --fg:#e7e9ee; --mut:#9aa2b1; --card:#171a21; --line:#2a2f3a;
    --acc:#5b8cff; } }
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
  .name { font-weight:600; font-size:17px; margin-bottom:10px; }
  .kv { display:grid; grid-template-columns:150px 1fr; gap:4px 10px; font-size:13px;
        margin-bottom:10px; }
  .kv-k { color:var(--mut); }
  pre.disc { white-space:pre-wrap; word-break:break-word; background:var(--card);
             border:1px solid var(--line); border-radius:8px; padding:12px;
             font-size:13px; max-height:200px; overflow:auto; }
  .actions { display:flex; gap:8px; flex-wrap:wrap; margin-top:14px; align-items:center; }
  kbd { font:12px monospace; background:var(--card); border:1px solid var(--line);
        border-radius:4px; padding:0 4px; }
  .hint { color:var(--mut); font-size:12px; margin-top:8px; }
  .empty { text-align:center; color:var(--mut); padding:60px 0; }
  .done { color:var(--acc); font-weight:600; }
</style>
</head>
<body>
<header>
  <b>Entity review</b>
  <span id="counter" class="meta"></span>
  <div class="bar"><div id="prog"></div></div>
  <button onclick="exportCSV()">Export decisions CSV</button>
</header>
<main id="main"></main>
<script>
const RECORDS = /*__DATA__*/;
const TYPES = __TYPES__;
const KEY = "committee_entity_decisions_v1";
let decisions = JSON.parse(localStorage.getItem(KEY) || "{}");
let idx = 0;

function esc(s){ return (s||"").replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c])); }
function save(){ localStorage.setItem(KEY, JSON.stringify(decisions)); render(); }
function decide(name, decision, target){ decisions[name] = {decision, target: target||""}; if (idx < RECORDS.length-1) idx++; save(); }

function render(){
  const done = RECORDS.filter(r => decisions[r.name]).length;
  document.getElementById("counter").textContent = `${done} / ${RECORDS.length} triaged`;
  document.getElementById("prog").style.width = RECORDS.length ? (100*done/RECORDS.length)+"%" : "0";
  const main = document.getElementById("main");
  if (!RECORDS.length){ main.innerHTML = '<div class="empty">No proposals.</div>'; return; }
  if (idx >= RECORDS.length) idx = RECORDS.length-1;
  const r = RECORDS[idx];
  const d = decisions[r.name];
  const cids = (r.fec_candidates||"").split("|").filter(Boolean);
  const cnames = (r.fec_candidate_names||"").split("|");
  const candRows = cids.map((id, i) =>
    `<div class="kv-k"><code>${esc(id)}</code></div><div>${esc(cnames[i]||"")}</div>`).join("");
  main.innerHTML = `
    <div class="card">
      <div class="meta">${esc(r.record_count)} records &middot; ${idx+1} of ${RECORDS.length}</div>
      <div class="name">${esc(r.name)}</div>
      <div class="kv">
        <div class="kv-k">variants</div><div>${esc(r.members)}</div>
        <div class="kv-k">sender domains</div><div>${esc(r.dominant_domains)}</div>
        ${candRows}
      </div>
      ${r.example_disclaimer ? `<pre class="disc">${esc(r.example_disclaimer)}</pre>` : ``}
      <div class="actions">
        ${cids.length ? `<button onclick="decide('${esc(r.name).replace(/'/g,"\\'")}','accept-fec','${cids[0]}')">Accept FEC <code>${cids[0]}</code> <kbd>A</kbd></button>` : ``}
        <button onclick="decide('${esc(r.name).replace(/'/g,"\\'")}','new')">New non-federal <kbd>N</kbd></button>
        <button onclick="mergeInto('${esc(r.name).replace(/'/g,"\\'")}')">Merge&hellip; <kbd>M</kbd></button>
        <button onclick="decide('${esc(r.name).replace(/'/g,"\\'")}','noise')">Noise <kbd>X</kbd></button>
        <button onclick="decide('${esc(r.name).replace(/'/g,"\\'")}','skip')">Skip <kbd>S</kbd></button>
        <span class="done">${d ? '&#10003; '+d.decision+(d.target?': '+esc(d.target):'') : ''}</span>
      </div>
      <div class="hint"><kbd>&larr;</kbd>/<kbd>&rarr;</kbd> navigate &middot; A adopt FEC &middot; N new &middot; M merge &middot; X noise &middot; S skip</div>
    </div>`;
}
function mergeInto(name){
  const cur = (decisions[name] && decisions[name].decision === "merge" && decisions[name].target) || "";
  const v = prompt("Existing registry entity id (FEC id or nfd:slug):", cur);
  if (v !== null && v.trim()) decide(name, "merge", v.trim());
}
function exportCSV(){
  const rows = [["name","decision","target_id","target_name","target_type","note"]];
  RECORDS.forEach(r => { const d = decisions[r.name]; if (d) rows.push([r.name, d.decision, d.target, "", r.suggested_type||""]); });
  const csv = rows.map(row => row.map(c => '"'+String(c==null?"":c).replace(/"/g,'""')+'"').join(",")).join("\n");
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([csv], {type:"text/csv"}));
  a.download = "entity_decisions.csv"; a.click();
}
document.addEventListener("keydown", e => {
  if (e.target.tagName === "INPUT") return;
  if (!RECORDS.length) return; const r = RECORDS[idx];
  const k = e.key.toLowerCase();
  if (e.key === "ArrowRight") { if (idx<RECORDS.length-1) idx++; render(); }
  else if (e.key === "ArrowLeft") { if (idx>0) idx--; render(); }
  else if (k === "a" && (r.fec_candidates||"").split("|")[0]) decide(r.name,"accept-fec",(r.fec_candidates||"").split("|")[0]);
  else if (k === "n") decide(r.name,"new");
  else if (k === "m") mergeInto(r.name);
  else if (k === "x") decide(r.name,"noise");
  else if (k === "s") decide(r.name,"skip");
});
render();
</script>
</body>
</html>"""


if __name__ == "__main__":
    main()