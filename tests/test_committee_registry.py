"""Tests for the committed committee registry module."""

import json

import pytest

from committee_registry import (
    AliasCollisionError,
    alias_index,
    fec_index,
    load_registry,
    merge_registry,
    new_entity,
    pick_canonical_name,
    resolve_name,
    save_registry,
    slugify,
)


def _entity(eid, name, aliases=None, fec_id=None, status="auto",
            type_="federal-pac"):
    e = new_entity(name, type_=type_, fec_id=fec_id, status=status,
                   aliases=aliases)
    e["id"] = eid
    return e


# ---------------------------------------------------------------------------
# loader / serialization
# ---------------------------------------------------------------------------

def test_load_missing_file_returns_empty(tmp_path):
    assert load_registry(tmp_path / "nope.json") == {}


def test_load_rejects_entity_missing_id_or_name(tmp_path):
    path = tmp_path / "r.json"
    path.write_text(json.dumps({"entities": [{"id": "x"}]}))
    with pytest.raises(ValueError):
        load_registry(path)


def test_save_is_deterministic(tmp_path):
    reg = {
        "b": _entity("b", "Beta PAC", fec_id="C002"),
        "a": _entity("a", "Alpha PAC", aliases=["Alpha", "ALPHA PAC"],
                     fec_id="C001"),
    }
    p1, p2 = tmp_path / "r1.json", tmp_path / "r2.json"
    save_registry(reg, p1)
    save_registry(dict(reversed(list(reg.items()))), p2)
    assert p1.read_bytes() == p2.read_bytes()
    data = json.loads(p1.read_text())
    assert [e["id"] for e in data["entities"]] == ["a", "b"]
    assert data["entities"][0]["aliases"] == \
        ["ALPHA PAC", "Alpha", "Alpha PAC"]  # sorted, name itself included


def test_save_roundtrip(tmp_path):
    reg = {"a": _entity("a", "Alpha PAC", fec_id="C001")}
    path = tmp_path / "r.json"
    save_registry(reg, path)
    assert load_registry(path) == reg


# ---------------------------------------------------------------------------
# alias index / collision rule
# ---------------------------------------------------------------------------

def test_alias_index_maps_every_alias():
    reg = {"a": _entity("a", "Alpha PAC", aliases=["Alpha PAC", "APAC"],
                        fec_id="C001")}
    idx = alias_index(reg)
    assert idx["alpha pac"]["id"] == "a"
    assert idx["apac"]["id"] == "a"


def test_alias_collision_across_entities_raises():
    reg = {
        "a": _entity("a", "Save America", fec_id="C1",
                     aliases=["Save America"]),
        "b": _entity("b", "Save America", fec_id="C2",
                     aliases=["Save America"]),
    }
    with pytest.raises(AliasCollisionError):
        alias_index(reg)


def test_alias_repeated_within_one_entity_is_fine():
    # two verbatim spellings normalizing alike on the SAME entity don't collide
    reg = {"a": _entity("a", "Alpha PAC", aliases=["Alpha PAC", "alpha pac"],
                        fec_id="C001")}
    assert alias_index(reg)["alpha pac"]["id"] == "a"


def test_resolve_name_uses_alias_index():
    reg = {"C1": _entity("C1", "DNC", fec_id="C1", aliases=["DNC", "D.N.C."])}
    alias_map = alias_index(reg)
    assert resolve_name(reg, "DNC", alias_map)["id"] == "C1"
    assert resolve_name(reg, "D.N.C.", alias_map)["id"] == "C1"
    # unknown spellings return None (the FEC ladder decides instead)
    assert resolve_name(reg, "Democratic National Committee", alias_map) is None
    assert resolve_name(reg, None, alias_map) is None


# ---------------------------------------------------------------------------
# merge / provenance
# ---------------------------------------------------------------------------

def test_merge_registry_preserves_human_entries():
    human = _entity("C1", "Human Name", fec_id="C1", status="human")
    auto = _entity("C2", "Auto PAC", fec_id="C2", status="auto")
    rebuilt = {
        "C1": _entity("C1", "Rebuilt Name", fec_id="C1", status="auto"),
        "C2": _entity("C2", "Rebuilt Auto", fec_id="C2", status="auto",
                      aliases=["Rebuilt Auto"]),
        "C3": _entity("C3", "New PAC", fec_id="C3", status="auto"),
    }
    merged = merge_registry({"C1": human, "C2": auto}, rebuilt)
    assert merged["C1"]["name"] == "Human Name"  # human survives verbatim
    assert merged["C2"] == rebuilt["C2"]         # auto is replaced
    assert merged["C3"] == rebuilt["C3"]         # new is added


def test_fec_index_skips_non_federal():
    reg = {
        "C1": _entity("C1", "Alpha PAC", fec_id="C1"),
        "nfd:x": _entity("nfd:x", "Some c4"),
    }
    assert fec_index(reg) == {"C1": reg["C1"]}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def test_slugify():
    assert slugify("Democratic Party of Georgia") == \
        "nfd:democratic-party-of-georgia"
    assert slugify("Trump National Committee JFC, Inc.") == \
        "nfd:trump-national-committee-jfc-inc"
    assert slugify("  A   B  ") == "nfd:a-b"


def test_pick_canonical_name_priorities():
    # the most frequent disclaimer variant wins over the FEC name
    assert pick_canonical_name(
        "FEC NAME", {"Variant B": 5, "Variant A": 3}, {"Variant B": 5}) \
        == "Variant B"
    # without disclaimer variants, the FEC name stands
    assert pick_canonical_name("FEC NAME", {}, {"x": 9}) == "FEC NAME"
    # with nothing, the most frequent variant overall
    assert pick_canonical_name(None, {}, {"Var X": 2, "Var Y": 4}) == "Var Y"
    # ties break lexicographically (smallest wins, stable as the archive grows)
    assert pick_canonical_name(None, {"Same": 4, "Same Copy": 4}, {}) == "Same"
    assert pick_canonical_name(None, {}, {}) == ""