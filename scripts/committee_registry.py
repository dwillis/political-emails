"""Committed committee registry: the source of truth for canonical entities.

`config/committee_registry.json` holds one entity per real-world committee,
federal or not:

    {"id": "C00010603" | "nfd:<slug>",       FEC ID, else a synthetic id
     "fec_id": "C00010603" | null,           identity key (null for non-federal)
     "name": "Democratic National Committee", canonical display name
     "type": "federal-candidate" | "federal-pac" | "party-committee" |
             "joint-fundraising" | "state-party" | "state-candidate" |
             "newsletter" | "c4" | "other" | "noise",
     "party": "D" | "R" | "I" | "" | null,   optional
     "status": "auto" | "human",             provenance: tiers vs review
     "aliases": [verbatim archive/FEC committee strings]}

Why it exists: backfill recomputes FEC matches from `committee` on every run,
so human decisions (colloquial names, non-federal entities, noise) must live
somewhere committed and durable, consulted BEFORE the FEC tier ladder.

Loader contract: `{norm_label(alias): entity}`; two aliases whose norm_label
collides across DIFFERENT entities are a hard load error (test-enforced) --
that collision is exactly the "SAVE AMERICA" two-committees case, which must
stay ambiguous instead of silently picking one. Auto-rebuilds never overwrite
`status: "human"` entries.

Serialization is deterministic (sorted entities/aliases, sort_keys, indent=2)
so registry-only edits produce no spurious diffs.
"""

import json
import re

from committee_utils import norm_label
from utils import CONFIG_DIR

REGISTRY_PATH = CONFIG_DIR / "committee_registry.json"

ENTITY_TYPES = {
    "federal-candidate", "federal-pac", "party-committee", "joint-fundraising",
    "state-party", "state-candidate", "newsletter", "c4", "other", "noise",
}

_ALPHANUM_RE = re.compile(r"[^a-z0-9]+")


class AliasCollisionError(ValueError):
    """Two aliases with equal norm_label but different entities."""


def slugify(name):
    """Stable synthetic-id slug ("Democratic Party of Georgia" -> "nfd:democratic-party-of-georgia")."""
    slug = _ALPHANUM_RE.sub("-", str(name).casefold()).strip("-")
    slug = re.sub(r"-+", "-", slug)[:60]
    return f"nfd:{slug}"


def new_entity(name, *, type_="other", fec_id=None, status="human", aliases=None,
               party=None):
    """Build a registry entity dict (aliases default to the name itself)."""
    return {
        "id": fec_id or slugify(name),
        "fec_id": fec_id,
        "name": name,
        "type": type_,
        "party": party,
        "status": status,
        "aliases": sorted({*aliases, name}) if aliases else [name],
    }


def load_registry(path=None):
    """{id: entity} from the committed JSON; {} when the file doesn't exist."""
    path = path or REGISTRY_PATH
    if not path.exists():
        return {}
    with open(path) as f:
        data = json.load(f)
    registry = {}
    for entity in data.get("entities", []):
        if "id" not in entity or "name" not in entity:
            raise ValueError(f"registry entity missing id/name: {entity}")
        registry[entity["id"]] = entity
    return registry


def save_registry(registry, path=None):
    """Deterministic serialization: entities by id, aliases sorted, 2-space indent."""
    path = path or REGISTRY_PATH
    entities = []
    for eid in sorted(registry):
        e = registry[eid]
        e = dict(e)
        e["aliases"] = sorted(set(e.get("aliases") or []))
        entities.append(e)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"entities": entities}, f, sort_keys=True, indent=2)
        f.write("\n")


def alias_index(registry):
    """{norm_label(alias): entity} over every alias in the registry.

    Raises AliasCollisionError when the same normalized label would resolve to
    two different entities -- an ambiguity the loader refuses to guess.
    """
    index = {}
    for eid, entity in registry.items():
        for alias in entity.get("aliases") or []:
            key = norm_label(alias)
            if not key:
                continue
            if key in index and index[key]["id"] != eid:
                raise AliasCollisionError(
                    f"alias {alias!r} (norm_label {key!r}) maps to both "
                    f"{index[key]['id']!r} and {eid!r}")
            index[key] = entity
    return index


def fec_index(registry):
    """{fec_id: entity} for canonical-name lookup after an FEC tier match."""
    return {e["fec_id"]: e for e in registry.values() if e.get("fec_id")}


def resolve_name(registry, committee, alias_map=None):
    """Registry entity for a verbatim committee string, or None.

    Keyed by norm_label, the same convention as party_utils.load_party_overrides.
    """
    if not committee:
        return None
    if alias_map is None:
        alias_map = alias_index(registry)
    return alias_map.get(norm_label(committee))


def merge_registry(base, incoming, preserve_human=True):
    """Merge auto-rebuilt entries into a registry without clobbering humans.

    Entries with status "human" in `base` are kept verbatim (including their
    aliases); "auto" entries are replaced by the rebuilt version.
    """
    merged = dict(base)
    for eid, entity in incoming.items():
        if preserve_human and eid in merged and merged[eid].get("status") == "human":
            continue
        merged[eid] = entity
    return merged


def pick_canonical_name(fec_name, disclaimer_variants, all_variants):
    """Frozen display name for an entity (the confirmed canonical-name rule).

    Priority: the most frequent variant seen on the entity's own
    disclaimer/human-sourced records; then the FEC CMTE_NM; then the most
    frequent variant overall. Ties break lexicographically (smallest wins) so
    the result is stable as the archive grows.
    """
    def most_frequent(counter):
        if not counter:
            return None
        top = max(counter.values())
        return min(v for v, n in counter.items() if n == top)

    return (most_frequent(disclaimer_variants) or fec_name
            or most_frequent(all_variants) or "")