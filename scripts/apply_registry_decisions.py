"""Merge triage decisions into config/committee_registry.json as `status: "human"`.

Two input forms:
  --decisions CSV   entity_review.html export (name, decision, target_id,
                    target_name, target_type, note)
  --seed JSON       a list of authored entity dicts (the high-value hand-seed)

Every applied entry is upgraded to `status: "human"` so auto-rebuilds preserve
it. The script refuses to write when the merged registry would collide on
norm_label aliases (the loader's hard rule) so a bad decision can't get
committed silently.

    uv run python scripts/apply_registry_decisions.py --decisions entity_decisions.csv
    uv run python scripts/apply_registry_decisions.py --seed hand_seeds.json
"""

import argparse
import csv
import json

from committee_registry import (
    AliasCollisionError,
    alias_index,
    load_registry,
    save_registry,
    slugify,
)
from build_committee_registry import _federal_type


def _upgrade(entity, aliases, note=None):
    """Force human status, union aliases, append note."""
    entity = dict(entity)
    entity["aliases"] = sorted(
        {*(entity.get("aliases") or []), *aliases} - {""})
    entity["status"] = "human"
    if note:
        parts = list(dict.fromkeys(p for p in (entity.get("note"), note) if p))
        entity["note"] = "; ".join(parts)
    return entity


def entry_from_decision(row, registry, index):
    """A registry entity (or None) for one decisions-CSV row."""
    decision = (row.get("decision") or "").strip()
    name = (row.get("name") or "").strip()
    if not name or decision in ("", "skip"):
        return None

    if decision == "accept-fec":
        fid = (row.get("target_id") or "").strip()
        if not fid.startswith("C"):
            raise ValueError(f"{name}: accept-fec needs an FEC id, got {fid!r}")
        existing = registry.get(fid) or {}
        meta = index["meta"].get(fid, {})
        return _upgrade(
            {
                "id": fid,
                "fec_id": fid,
                "name": existing.get("name") or meta.get("name") or name,
                "type": existing.get("type") or _federal_type(meta),
                "party": existing.get("party") or meta.get("party") or None,
                "aliases": existing.get("aliases") or [],
            },
            [name], note=row.get("note") or None)

    if decision == "noise":
        return _upgrade(
            {
                "id": slugify(name),
                "fec_id": None,
                "name": name,
                "type": "noise",
                "party": None,
                "aliases": [],
            },
            [name], note=row.get("note") or None)

    if decision == "new":
        type_ = ((row.get("target_type") or "").strip()
                 or (row.get("suggested_type") or "").strip() or "other")
        return _upgrade(
            {
                "id": slugify(name),
                "fec_id": None,
                "name": name,
                "type": type_,
                "party": None,
                "aliases": [],
            },
            [name], note=row.get("note") or None)

    raise ValueError(f"unknown decision {decision!r} for {name!r}")


def apply_seed(merged, seed_entities):
    """Authored entity dicts: forced to human, aliases unioned into base."""
    for seed in seed_entities:
        if "id" not in seed or "name" not in seed:
            raise ValueError(f"seed entity missing id/name: {seed}")
        existing = merged.get(seed["id"]) or {}
        # explicit seed values win; base aliases/fields survive the upgrade
        entity = dict(existing)
        entity.update({k: v for k, v in seed.items() if v is not None})
        entity["status"] = "human"
        seed_aliases = list(seed.get("aliases") or []) + [seed["name"]]
        merged[seed["id"]] = _upgrade(entity, seed_aliases)
    return merged


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--decisions", help="Decisions CSV from entity_review.html")
    parser.add_argument("--seed", help="JSON file with a list of authored entities")
    args = parser.parse_args()
    if not (args.decisions or args.seed):
        parser.error("one of --decisions / --seed is required")

    from fec_match import download_fec, load_fec_index

    download_fec()
    index, _buckets = load_fec_index()

    merged = load_registry()
    applied = 0
    if args.seed:
        with open(args.seed) as f:
            seeds = json.load(f)
        merged = apply_seed(merged, seeds)
        applied += len(seeds)
    if args.decisions:
        with open(args.decisions, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        for row in rows:
            if (row.get("decision") or "").strip() == "merge":
                target = (row.get("target_id") or "").strip()
                name = (row.get("name") or "").strip()
                if target not in merged:
                    raise ValueError(f"merge target {target!r} not in registry")
                merged[target] = _upgrade(merged[target], [name],
                                          note=row.get("note") or None)
            else:
                entity = entry_from_decision(row, merged, index)
                if entity:
                    merged[entity["id"]] = entity
            if (row.get("decision") or "").strip():
                applied += 1

    # hard load-error discipline: never commit an alias-colliding registry
    try:
        alias_index(merged)
    except AliasCollisionError as e:
        raise SystemExit(f"refusing to write registry: {e}") from None

    from committee_registry import REGISTRY_PATH

    save_registry(merged, REGISTRY_PATH)
    print(f"applied {applied:,} entries; registry now has {len(merged):,} "
          f"entities ({sum(1 for e in merged.values() if e['status'] == 'human'):,} human)")
    print(f"wrote {REGISTRY_PATH} -- commit it for git review")


if __name__ == "__main__":
    main()