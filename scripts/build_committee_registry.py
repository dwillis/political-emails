"""Build/refresh the committee registry from one archive scan.

Seeds (the plan's Phase 2):
  a) archive names the tier ladder resolves to exactly ONE FEC ID become
     aliases of a `status: auto` federal entity (existing `status: human`
     entries are never overwritten);
  b) unresolved names are clustered by same_committee into proposed
     non-federal entities and emitted to a frequency-ordered review CSV;
  c) names whose resolution is multi-FEC (e.g. "SAVE AMERICA", registered by
     two different committees) are never auto-seeded -- they go to proposals
     so the registry's alias-collision rule can't be tripped by them.

Artifacts:
  config/committee_registry.json            entities (merged with existing)
  state/validation/registry_proposals.csv   non-federal/multi-fid clusters

    uv run python scripts/build_committee_registry.py            # build
    uv run python scripts/build_committee_registry.py --no-save  # report only
"""

import argparse
import csv
import re
from collections import defaultdict

from committee_registry import (
    REGISTRY_PATH,
    load_registry,
    merge_registry,
    new_entity,
    pick_canonical_name,
    save_registry,
)
from committee_utils import iter_day_files, norm_label
from utils import STATE_DIR, load_jsonl

PROPOSALS_PATH = STATE_DIR / "validation" / "registry_proposals.csv"

_DISCLAIMER_SAMPLE_MAX = 300


def _federal_type(meta):
    """Registry type for a resolved FEC committee from cm.txt fields."""
    if meta["tp"] in {"H", "S", "P"}:
        return "federal-candidate"
    if meta["dsgn"] == "J":
        return "joint-fundraising"
    return "federal-pac"


def _state_party_suggestion(label):
    """Rough type hint for the review loop; the human decides."""
    if re.search(r"\b(democratic|republican) party\b", label):
        return "state-party"
    if label.endswith("news") or label.endswith("newsletter"):
        return "newsletter"
    return "other"


def make_resolver(index):
    """resolve(committee, rec) -> (tier, fec_id, candidates), memoized."""
    from fec_match import record_cycle, resolve_committee

    cache = {}

    def resolve(committee, rec):
        key = (committee, record_cycle(rec))
        if key not in cache:
            cache[key] = resolve_committee(committee, rec, index)
        return cache[key]

    return resolve


def scan_archive(resolve=None):
    """One pass over the archive collecting per-name facts.

    Returns {name: {"count", "disclaimer_count", "domains": Counter,
                    "example": str, "fids": Counter, "cands": set}}.
    `resolve` (optional) is called as resolve(committee, rec) for each record
    (make_resolver builds the real one); injected in tests.
    """
    facts = {}
    for path in iter_day_files():
        for rec in load_jsonl(path):
            committee = rec.get("committee")
            if not committee:
                continue
            f = facts.setdefault(committee, {
                "count": 0, "disclaimer_count": 0,
                "domains": defaultdict(int), "example": "",
                "fids": defaultdict(int), "cands": set(),
            })
            f["count"] += 1
            if rec.get("committee_source") == "disclaimer":
                f["disclaimer_count"] += 1
            if rec.get("domain"):
                f["domains"][(rec["domain"] or "").lower()] += 1
            if not f["example"] and rec.get("disclaimer_text"):
                f["example"] = rec["disclaimer_text"][:_DISCLAIMER_SAMPLE_MAX]
            if resolve is not None:
                _tier, fid, cands = resolve(committee, rec)
                if fid:
                    f["fids"][fid] += 1
                elif cands:
                    f["cands"].update(cands)
    return facts


def _cluster_unresolved(rows, facts):
    """Union-find over unresolved norm_label groups (label, names, fids).

    Merge requires same_committee AND a shared sender domain carrying >=5
    records on both sides; returns a list of clusters (lists of rows).
    """
    from committee_utils import same_committee

    domains = {}
    for label, names, _fids in rows:
        agg = defaultdict(int)
        for name in names:
            for d, n in facts[name]["domains"].items():
                agg[d] += n
        domains[label] = agg

    parent = {label: label for label, _n, _f in rows}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    labels = [label for label, _n, _f in rows]
    for i, a in enumerate(labels):
        for b in labels[i + 1:]:
            if not same_committee(a, b):
                continue
            shared = set(domains[a]) & set(domains[b])
            if max((min(domains[a][d], domains[b][d])
                    for d in shared), default=0) >= 5:
                parent[find(b)] = find(a)

    clusters = defaultdict(list)
    for row in rows:
        clusters[find(row[0])].append(row)
    return list(clusters.values())


def build_entities(facts, index):
    """(entities, proposals) from scanned name facts + the FEC index.

    entities: new/refreshed `status: auto` entries keyed by id.
    proposals: list of dict rows for the review CSV (multi-fid groups,
    unresolved clusters).
    """
    # Group verbatim names by norm_label: every spelling that normalizes
    # alike must agree on an entity or the alias index would collide.
    groups = defaultdict(list)
    for name, f in facts.items():
        groups[norm_label(name)].append(name)

    entities = {}
    unresolved = []
    for label, names in sorted(groups.items()):
        fids = defaultdict(int)
        for name in names:
            for fid, n in facts[name]["fids"].items():
                fids[fid] += n
        if len(fids) == 1:
            fid = next(iter(fids))
            disclaimer_variants, all_variants = {}, {}
            for name in names:
                f = facts[name]
                all_variants[name] = f["count"]
                if f["disclaimer_count"]:
                    disclaimer_variants[name] = f["disclaimer_count"]
            meta = index["meta"].get(fid, {})
            entities[fid] = new_entity(
                pick_canonical_name(
                    meta.get("name"), disclaimer_variants, all_variants),
                type_=_federal_type(meta), fec_id=fid, status="auto",
                aliases=names, party=meta.get("party") or None,
            )
        else:
            unresolved.append((label, names, fids))

    # Cluster unresolved groups by same_committee PLUS a shared sender domain
    # (>=5 records on both sides). Bare same_committee transitively chains
    # unrelated committees into one unusable mega-cluster (verified: 170k
    # records); the domain gate keeps chains to names actually co-mailed.
    clusters = _cluster_unresolved(unresolved, facts)

    proposals = []
    for members in clusters:
        members.sort(key=lambda r: -sum(facts[n]["count"] for n in r[1]))
        names = [n for _l, ns, _f in members for n in ns]
        disclaimer_variants, all_variants = {}, {}
        domains = defaultdict(int)
        example = ""
        for name in names:
            f = facts[name]
            all_variants[name] = f["count"]
            if f["disclaimer_count"]:
                disclaimer_variants[name] = f["disclaimer_count"]
            domains.update(f["domains"])
            if not example and f["example"]:
                example = f["example"]
        fids = sorted({fid for _l, _ns, ff in members for fid in ff},
                      key=lambda f: -sum(ff.get(f, 0) for _l, _ns, ff in members))
        cands = sorted({c for _l, ns, _f in members for n in ns
                        for c in facts[n]["cands"]} - set(fids))
        chosen = (fids + cands)[:5]
        suggested = pick_canonical_name(None, disclaimer_variants, all_variants)
        proposals.append({
            "name": suggested,
            "record_count": sum(facts[n]["count"] for n in names),
            "members": " | ".join(sorted(names)),
            "fec_candidates": "|".join(chosen),
            "fec_candidate_names": " | ".join(
                index["meta"][f]["name"] for f in chosen if f in index["meta"]),
            "dominant_domains": " | ".join(
                f"{d}:{n}" for d, n in
                sorted(domains.items(), key=lambda kv: -kv[1])[:3]),
            "example_disclaimer": example,
            "suggested_type": _state_party_suggestion(suggested.casefold()),
        })
    proposals.sort(key=lambda p: -p["record_count"])
    return entities, proposals


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-save", action="store_true",
                        help="Report without writing the registry or CSV")
    args = parser.parse_args()

    from fec_match import download_fec, load_fec_index

    download_fec()
    index, _buckets = load_fec_index()
    registry = load_registry()
    existing_aliases = {norm_label(a) for e in registry.values()
                        for a in e.get("aliases") or []}
    if registry:
        print(f"Existing registry: {len(registry):,} entities "
              f"({sum(1 for e in registry.values() if e['status'] == 'human'):,} human)")

    print("Scanning archive (resolving every name via the tier ladder)...")
    facts = scan_archive(make_resolver(index))
    print(f"  {len(facts):,} distinct committee names")

    # Names the registry already resolves are skipped: the committed decisions
    # win, and the builder must not re-propose them.
    facts = {name: f for name, f in facts.items()
             if norm_label(name) not in existing_aliases}
    print(f"  {len(facts):,} after excluding registry-alias names")

    entities, proposals = build_entities(facts, index)
    merged = merge_registry(registry, entities, preserve_human=True)

    print(f"Seeded {len(entities):,} federal entities (status auto); "
          f"{len(proposals):,} proposals covering "
          f"{sum(p['record_count'] for p in proposals):,} records")
    for p in proposals[:10]:
        print(f"  {p['record_count']:>6}  {p['name']}")

    if not args.no_save:
        # belt and braces: refuse to commit an alias-colliding registry
        from committee_registry import alias_index

        alias_index(merged)
        save_registry(merged, REGISTRY_PATH)
        PROPOSALS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(PROPOSALS_PATH, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=[
                "name", "record_count", "members", "fec_candidates",
                "fec_candidate_names", "dominant_domains", "example_disclaimer",
                "suggested_type"])
            w.writeheader()
            w.writerows(proposals)
        print(f"Wrote {REGISTRY_PATH} and {PROPOSALS_PATH}")


if __name__ == "__main__":
    main()