"""Cross-reference committee names against the FEC committee master file.

Downloads the FEC bulk committee master (cm.txt) for the requested cycles,
builds a normalized-name index, and matches extracted committee names (exact
normalized, then fuzzy via difflib). A committee that matches an FEC record is a
strong confidence signal (and yields a canonical FEC ID); a near-miss can flag a
misspelling. This is a VALIDATION signal only -- nothing here writes to data/.

    uv run python scripts/fec_match.py --download          # refresh the cache
    uv run python scripts/fec_match.py                     # match archive values
"""

import argparse
import csv
import difflib
import urllib.request
import zipfile
from collections import Counter
from pathlib import Path

from committee_utils import iter_day_files, norm_label
from utils import DATA_DIR, load_jsonl

FEC_DIR = DATA_DIR.parent / "state" / "fec"
CYCLES = list(range(2016, 2028, 2))
CMTE_ID, CMTE_NM = 0, 1  # cm.txt field positions
# 0.92 produced false positives on one-surname-swap names ("Harder for Congress"
# -> "HARPER FOR CONGRESS"). 0.95 keeps accent/"US Senate" normalizations only.
# Fuzzy matches are a review hint, NEVER a confirmation signal (see validate).
FUZZY_CUTOFF = 0.95


def cm_url(year):
    return f"https://www.fec.gov/files/bulk-downloads/{year}/cm{str(year)[2:]}.zip"


def cm_path(year):
    return FEC_DIR / f"cm{str(year)[2:]}.zip"


def download_fec(years=CYCLES, refresh=False):
    FEC_DIR.mkdir(parents=True, exist_ok=True)
    for year in years:
        path = cm_path(year)
        if path.exists() and not refresh:
            continue
        try:
            print(f"Downloading FEC cm{str(year)[2:]}.zip ...")
            urllib.request.urlretrieve(cm_url(year), path)
        except Exception as e:  # noqa: BLE001
            print(f"  [warn] {year} failed: {e} (continuing with available cycles)")


def load_fec_index(years=CYCLES):
    """Return (name_index, buckets).

    name_index: {norm_name: (fec_id, original_name, latest_cycle)}
    buckets:    {(first_char, len//5): [norm_name, ...]} for fuzzy candidate lookup
    """
    name_index = {}
    buckets = {}
    loaded = 0
    for year in years:
        path = cm_path(year)
        if not path.exists():
            continue
        loaded += 1
        with zipfile.ZipFile(path) as z:
            with z.open("cm.txt") as f:
                for raw in f:
                    parts = raw.decode("utf-8", "replace").rstrip("\n").split("|")
                    if len(parts) <= CMTE_NM:
                        continue
                    fid, name = parts[CMTE_ID], parts[CMTE_NM]
                    norm = norm_label(name)
                    if not norm:
                        continue
                    prev = name_index.get(norm)
                    if prev is None or year > prev[2]:
                        name_index[norm] = (fid, name, year)
                    if prev is None:
                        buckets.setdefault((norm[0], len(norm) // 5), []).append(norm)
    print(f"FEC index: {len(name_index):,} distinct names from {loaded} cycle file(s)")
    return name_index, buckets


def match_name(value, name_index, buckets):
    """Match a committee value against the FEC index.

    Returns (match_type, fec_id, matched_name, score). match_type is
    "exact" | "fuzzy" | "none".
    """
    norm = norm_label(value)
    if not norm:
        return ("none", "", "", 0.0)
    hit = name_index.get(norm)
    if hit:
        return ("exact", hit[0], hit[1], 1.0)
    # Fuzzy within the same first-char + length bucket (keeps it tractable).
    candidates = buckets.get((norm[0], len(norm) // 5), [])
    close = difflib.get_close_matches(norm, candidates, n=1, cutoff=FUZZY_CUTOFF)
    if close:
        best = close[0]
        score = difflib.SequenceMatcher(None, norm, best).ratio()
        fid, name, _ = name_index[best]
        return ("fuzzy", fid, name, round(score, 3))
    return ("none", "", "", 0.0)


def archive_value_counts():
    counts = Counter()
    for path in iter_day_files():
        for rec in load_jsonl(path):
            c = rec.get("committee")
            if c:
                counts[c] += 1
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download", action="store_true", help="Download/refresh the FEC cache and exit")
    parser.add_argument("--refresh", action="store_true", help="Force re-download")
    parser.add_argument("--out", type=Path, default=FEC_DIR / "fec_matches.csv")
    args = parser.parse_args()

    if args.download:
        download_fec(refresh=True)
        return

    download_fec(refresh=args.refresh)
    name_index, buckets = load_fec_index()
    if not name_index:
        print("No FEC data cached. Run with --download first.")
        return

    counts = archive_value_counts()
    print(f"Matching {len(counts):,} distinct archive committee values...")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    exact = fuzzy = none = 0
    rec_exact = 0
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["committee", "record_count", "match_type", "fec_id", "matched_name", "score"])
        for value, n in counts.most_common():
            mt, fid, mname, score = match_name(value, name_index, buckets)
            exact += mt == "exact"
            fuzzy += mt == "fuzzy"
            none += mt == "none"
            if mt == "exact":
                rec_exact += n
            w.writerow([value, n, mt, fid, mname, score])

    print(f"  exact: {exact:,} values  fuzzy: {fuzzy:,}  none: {none:,}  (of {len(counts):,})")
    print(f"  records under an exact FEC match: {rec_exact:,}")
    print(f"  wrote {args.out}")


if __name__ == "__main__":
    main()
