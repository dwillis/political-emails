"""Re-normalize existing committee values across the archive.

Applies the current normalize_committee() rules to every record's committee
field, nulling values that are now rejected (rambling model essays, DSPy adapter
leakage, over-long text). Nulled records become eligible for re-enrichment.

    uv run python scripts/clean_committees.py --dry-run
    uv run python scripts/clean_committees.py
"""

import argparse

from committee_utils import iter_day_files, normalize_committee
from utils import load_jsonl, save_jsonl


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Report without writing")
    args = parser.parse_args()

    nulled = 0
    files_changed = 0
    examples = []

    for path in iter_day_files():
        records = load_jsonl(path)
        changed = False
        for rec in records:
            current = rec.get("committee")
            if current is None:
                continue
            cleaned = normalize_committee(current)
            if cleaned != current:
                if len(examples) < 15:
                    examples.append(current[:70])
                rec["committee"] = cleaned
                if cleaned is None and "committee_source" in rec:
                    # keep invariant: source is null iff committee is null
                    rec["committee_source"] = None
                nulled += 1
                changed = True
        if changed:
            files_changed += 1
            if not args.dry_run:
                save_jsonl(path, records)

    action = "would null" if args.dry_run else "nulled"
    print(f"{action} {nulled} garbage committee values across {files_changed} files")
    for ex in examples:
        print(f"  - {ex!r}")


if __name__ == "__main__":
    main()
