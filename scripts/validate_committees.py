"""Assign a confidence tier to every labeled committee and build a review queue.

Tiers:
  CONFIRMED   committee_source == "disclaimer" (came from the "Paid for by"
              text, which is authoritative) OR an exact FEC committee match.
  CONSISTENT  matches the dominant committee on a candidate-owned domain.
  SUSPECT     high-priority for human review:
                - contradicts-disclaimer: a confident disclaimer extract exists
                  but differs from the stored label (definitionally wrong under
                  the disclaimer-priority rule) -- the sharpest error signal;
                - garbage: fails normalize_committee (should be ~0 post-sweep);
                - minority (<10%) label on an owned domain.
  UNVERIFIED  labeled but unconfirmable (backfill/llm on broker/small domains,
              no FEC hit) -- honest "we don't know", not an error claim.

    uv run python scripts/validate_committees.py
    uv run python scripts/validate_committees.py --skip-fec --queue-cap 300
"""

import argparse
import csv
import random
from collections import Counter, defaultdict

from committee_extract import extract_committee, looks_confident
from committee_utils import iter_day_files, norm_label, normalize_committee, same_committee
from utils import DATA_DIR, load_jsonl

OWNED_MIN_RECORDS = 20
OWNED_MAJORITY_SHARE = 0.75
MINORITY_SHARE = 0.10

REPORT_PATH = DATA_DIR.parent / "state" / "validation" / "report.md"
QUEUE_PATH = DATA_DIR.parent / "state" / "validation" / "review_queue.csv"


def build_domain_counts():
    """domain -> Counter(norm_label -> record_count) over labeled records."""
    counts = defaultdict(Counter)
    for path in iter_day_files():
        for rec in load_jsonl(path):
            c = rec.get("committee")
            if c:
                counts[(rec.get("domain") or "").lower()][norm_label(c)] += 1
    return counts


def is_owned(domain_counter):
    total = sum(domain_counter.values())
    if total < OWNED_MIN_RECORDS:
        return False
    top = domain_counter.most_common(1)[0][1]
    return top / total >= OWNED_MAJORITY_SHARE


def classify(rec, domain_counts, fec_exact):
    """Return (tier, reason). rec must have a non-null committee."""
    committee = rec["committee"]
    source = rec.get("committee_source")
    domain = (rec.get("domain") or "").lower()
    body = rec.get("body") or ""
    cn = norm_label(committee)

    # Garbage regression alarm (normalize should have caught it in the sweep).
    if normalize_committee(committee) is None:
        return ("SUSPECT", "garbage")

    # CONFIRMED via authoritative disclaimer or exact FEC.
    if source == "disclaimer":
        return ("CONFIRMED", "disclaimer")
    if fec_exact(committee):
        return ("CONFIRMED", "fec-exact")

    # Contradicts-disclaimer: a confident extract names a DIFFERENT committee
    # (not merely an abbreviation/truncation/variant of the stored label).
    det = extract_committee(body)
    if det and looks_confident(det) and not same_committee(det, committee):
        return ("SUSPECT", "contradicts-disclaimer")

    dom = domain_counts.get(domain, Counter())
    total = sum(dom.values())
    if is_owned(dom):
        majority = dom.most_common(1)[0][0]
        if cn == majority:
            return ("CONSISTENT", "domain-majority")
        if total and dom[cn] / total < MINORITY_SHARE:
            return ("SUSPECT", "minority-on-owned-domain")
    return ("UNVERIFIED", f"{source or 'unknown'}-unconfirmed")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-fec", action="store_true")
    parser.add_argument("--queue-cap", type=int, default=500)
    parser.add_argument("--report", default=str(REPORT_PATH))
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()

    fec_exact = (lambda _c: False)
    if not args.skip_fec:
        from fec_match import download_fec, load_fec_index, match_name
        download_fec()
        name_index, buckets = load_fec_index()
        _cache = {}

        def fec_exact(committee):
            if committee not in _cache:
                _cache[committee] = match_name(committee, name_index, buckets)[0] == "exact"
            return _cache[committee]

    print("Aggregating domain label counts...")
    domain_counts = build_domain_counts()
    owned = sum(1 for d in domain_counts.values() if is_owned(d))
    print(f"  {len(domain_counts):,} domains, {owned:,} owned")

    tiers = Counter()
    reasons = Counter()
    queue = defaultdict(list)  # reason -> [rows]
    labeled = 0

    for path in iter_day_files():
        for rec in load_jsonl(path):
            if not rec.get("committee"):
                continue
            labeled += 1
            tier, reason = classify(rec, domain_counts, fec_exact)
            tiers[tier] += 1
            reasons[reason] += 1
            if tier == "SUSPECT":
                det = extract_committee(rec.get("body") or "")
                queue[reason].append({
                    "date": rec.get("date"),
                    "email": rec.get("email"),
                    "subject": (rec.get("subject") or "")[:120],
                    "domain": rec.get("domain"),
                    "committee": rec.get("committee"),
                    "disclaimer_says": det if (det and looks_confident(det)) else "",
                    "committee_source": rec.get("committee_source"),
                    "tier": tier,
                    "reason": reason,
                })

    # Stratified sample of the review queue, proportional per reason.
    rng = random.Random(args.seed)
    total_suspect = sum(len(v) for v in queue.values())
    sampled = []
    for reason, rows in queue.items():
        take = min(len(rows), max(1, round(args.queue_cap * len(rows) / max(1, total_suspect))))
        sampled.extend(rng.sample(rows, take))
    QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(QUEUE_PATH, "w", newline="", encoding="utf-8") as f:
        cols = ["date", "email", "subject", "domain", "committee", "disclaimer_says",
                "committee_source", "tier", "reason"]
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(sampled)

    # Report.
    lines = ["# Committee validation report\n"]
    lines.append(f"Labeled records: {labeled:,}\n")
    lines.append("## Confidence tiers\n")
    for tier in ("CONFIRMED", "CONSISTENT", "UNVERIFIED", "SUSPECT"):
        n = tiers[tier]
        lines.append(f"- **{tier}**: {n:,} ({n/labeled*100:.1f}%)")
    lines.append("\n## Reasons\n")
    for reason, n in reasons.most_common():
        lines.append(f"- {reason}: {n:,}")
    lines.append(f"\nReview queue: {len(sampled):,} sampled of {total_suspect:,} SUSPECT "
                 f"-> `{QUEUE_PATH}`\n")
    report = "\n".join(lines)
    print("\n" + report)
    with open(args.report, "w", encoding="utf-8") as f:
        f.write(report)


if __name__ == "__main__":
    main()
