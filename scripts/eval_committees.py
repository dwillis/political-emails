"""Score committee extraction against the hand-labeled gold set.

Gold set (1,000 rows, Nov 2024) lives in the LLM-Extraction-Challenge repo. We
join it back to the archive by (email, subject, y/m/d/h/minute) to recover
pristine bodies, then score three predictors against the gold `committee`:

  1. stored     -- the label already in the archive
  2. regex      -- the deterministic committee_extract.extract_committee()
  3. llm        -- the full IdentifyCommitteeModule (needs --model + Ollama)

    uv run python scripts/eval_committees.py                 # stored + regex
    uv run --group enrich python scripts/eval_committees.py --model qwen3:4b

Caveats printed in the report: gold is a single month, skewed toward
disclaimer-bearing mail, so accuracy here is optimistic for the disclaimer path
and thin for the pure-LLM path.
"""

import argparse
import csv
import sys
import urllib.request
from collections import Counter
from datetime import datetime
from pathlib import Path

from committee_extract import extract_committee, looks_confident
from committee_utils import norm_label
from utils import DATA_DIR, load_jsonl

GOLD_URL = (
    "https://raw.githubusercontent.com/dwillis/LLM-Extraction-Challenge/"
    "refs/heads/main/fundraising-emails/training.csv"
)
GOLD_PATH = DATA_DIR.parent / "state" / "gold" / "training.csv"


def download_gold(refresh=False):
    if GOLD_PATH.exists() and not refresh:
        return
    GOLD_PATH.parent.mkdir(parents=True, exist_ok=True)
    print(f"Downloading gold set -> {GOLD_PATH}")
    urllib.request.urlretrieve(GOLD_URL, GOLD_PATH)


def parse_gold_date(s):
    """Parse a gold date like '11/1/24 0:02'. Returns datetime or None."""
    try:
        return datetime.strptime(s.strip(), "%m/%d/%y %H:%M")
    except (ValueError, AttributeError):
        return None


def gold_key(row):
    """Join key from a gold row using its explicit y/m/d/h/minute columns."""
    try:
        return (
            (row["email"] or "").strip().lower(),
            (row["subject"] or "").strip().lower(),
            int(row["year"]), int(row["month"]), int(row["day"]),
            int(row["hour"]), int(row["minute"]),
        )
    except (ValueError, KeyError):
        return None


def record_key(rec):
    return (
        (rec.get("email") or "").strip().lower(),
        (rec.get("subject") or "").strip().lower(),
        rec.get("year"), rec.get("month"), rec.get("day"),
        rec.get("hour"), rec.get("minute"),
    )


def build_archive_index():
    """Index Nov 2024 archive records by join key (gold is all Nov 2024)."""
    index = {}
    nov = DATA_DIR / "2024" / "11"
    for path in sorted(nov.glob("*.jsonl")):
        for rec in load_jsonl(path):
            index.setdefault(record_key(rec), rec)
    return index


def regex_predict(body):
    """What the module's deterministic pass would use: name if confident else None."""
    name = extract_committee(body)
    return name if looks_confident(name) else None


def score(pairs, predict, label):
    """pairs: list of (gold_committee, input_text). predict: text -> pred|None."""
    n = len(pairs)
    exact = norm = covered = 0
    misses = []
    for gold, text in pairs:
        pred = predict(text)
        if pred:
            covered += 1
        if pred and pred == gold:
            exact += 1
        if pred and norm_label(pred) == norm_label(gold):
            norm += 1
        elif len(misses) < 25:
            misses.append((gold, pred))
    print(f"\n=== {label} (n={n}) ===")
    print(f"  coverage (non-null pred): {covered}/{n} ({covered/n*100:.1f}%)")
    print(f"  exact match:              {exact}/{n} ({exact/n*100:.1f}%)")
    print(f"  normalized match:         {norm}/{n} ({norm/n*100:.1f}%)")
    if covered:
        print(f"  normalized match on covered rows: {norm}/{covered} ({norm/covered*100:.1f}%)")
    return misses


def score_party(pairs):
    """Score stored archive party against gold party (rows with a gold party)."""
    n = len(pairs)
    if not n:
        return
    covered = sum(1 for _g, p in pairs if p)
    correct = sum(1 for g, p in pairs if p and p == g)
    print(f"\n=== party: stored vs gold (n={n}) ===")
    print(f"  coverage (non-null stored): {covered}/{n} ({covered/n*100:.1f}%)")
    print(f"  accuracy on covered:        {correct}/{covered} "
          f"({correct/covered*100:.1f}%)" if covered else "  (no coverage)")
    grid = Counter((g, p or "None") for g, p in pairs)
    print("  gold -> stored:")
    for (g, p), c in sorted(grid.items()):
        flag = "" if (p == g or p == "None") else "  <-- mismatch"
        print(f"    {g} -> {p}: {c}{flag}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh", action="store_true", help="Re-download the gold set")
    parser.add_argument("--model", help="Also score this Ollama model via the DSPy module")
    parser.add_argument("--ollama-url", default="http://localhost:11434")
    parser.add_argument("--show-misses", type=int, default=10, help="Confusion examples per predictor")
    args = parser.parse_args()

    download_gold(args.refresh)
    with open(GOLD_PATH, encoding="utf-8") as f:
        gold_rows = list(csv.DictReader(f))
    print(f"Gold rows: {len(gold_rows)}")

    index = build_archive_index()
    print(f"Archive Nov-2024 records indexed: {len(index)}")

    joined = []      # (gold_committee, archive_record)
    party_pairs = []  # (gold_party, stored_party) for rows with a gold party
    unjoined = 0
    for row in gold_rows:
        k = gold_key(row)
        rec = index.get(k) if k else None
        if rec is None:
            unjoined += 1
            continue
        joined.append((row["committee"].strip(), rec))
        gp = (row.get("party") or "").strip().upper()
        if gp:
            party_pairs.append((gp, rec.get("party")))
    jr = len(joined)
    print(f"Joined to archive: {jr}/{len(gold_rows)} ({jr/len(gold_rows)*100:.1f}%); "
          f"unjoined: {unjoined}")
    print("\nCAVEAT: gold is single-month (Nov 2024), skewed to disclaimer-bearing "
          "mail; treat as directional, not a corpus-wide error rate.")

    # stored label predictor
    stored_pairs = [(g, rec.get("committee")) for g, rec in joined]
    m1 = score([(g, c) for g, c in stored_pairs], lambda x: x, "stored archive label")

    # deterministic regex predictor (pristine archive bodies)
    regex_pairs = [(g, rec.get("body") or "") for g, rec in joined]
    m2 = score(regex_pairs, regex_predict, "regex extractor (fixed)")

    for label, misses in (("stored", m1), ("regex", m2)):
        if args.show_misses:
            print(f"\n  {label} confusion (gold | pred):")
            for gold, pred in misses[: args.show_misses]:
                print(f"    {gold!r:40} | {pred!r}")

    score_party(party_pairs)

    if args.model:
        import dspy
        from enrich_committees import configure_dspy
        from identify_committee import IdentifyCommitteeModule
        configure_dspy(args.model, args.ollama_url, disable_thinking=True)
        module = IdentifyCommitteeModule()

        def llm_predict(body):
            try:
                return module(email_body=body).committee or None
            except Exception:
                return None

        m3 = score(regex_pairs, llm_predict, f"full module (llm={args.model})")
        if args.show_misses:
            print(f"\n  llm confusion (gold | pred):")
            for gold, pred in m3[: args.show_misses]:
                print(f"    {gold!r:40} | {pred!r}")


if __name__ == "__main__":
    main()
