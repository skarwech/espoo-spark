"""Planted (by-construction) truth: per-outcome support and tiers. Usage: python data/truth.py  (prints the precheck)

Direct support only: a claim supports its own archetype at its level; a *supported* claim gives *partial* to related
archetypes (method.md §1, rule c). ESCO adjacency is an engine feature and is not planted here.
"""
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from engine.tiers import tier  # noqa: E402

RANK = {"none": 0, "partial": 1, "supported": 2}


def load(out: Path = ROOT / "data" / "out"):
    return {n: json.loads((out / f"{n}.json").read_text(encoding="utf-8")) for n in ("needs", "jobseekers", "planted", "attributes", "companies")}


def related_map(pairs) -> dict[str, set[str]]:
    rel = {}
    for a, b in pairs:
        rel.setdefault(a, set()).add(b)
        rel.setdefault(b, set()).add(a)
    return rel


def support(claims: list[dict], archetype: str, rel: dict | None) -> str:
    """rel=None: direct claims only (no related-archetype partials)."""
    rel = rel or {}
    best = "none"
    for c in claims:
        lvl = c["level"] if c["archetype"] == archetype else \
            "partial" if c["level"] == "supported" and archetype in rel.get(c["archetype"], ()) else "none"
        best = max(best, lvl, key=RANK.get)
    return best


def pair_truth(w: dict, use_related: bool = True) -> dict:
    """{(need_id, candidate_id): {"labels": {oid: level}, "tier": str}} for every pair.

    use_related=True is the generation-time rule (golden selection is frozen on it); False is direct-only evidence.
    """
    rel = related_map(w["planted"]["related"]) if use_related else None
    out = {}
    for n in w["needs"]:
        arch = w["planted"]["outcomes"][n["need_id"]]
        for cid, claims in w["planted"]["claims"].items():
            labels = {o["id"]: support(claims, arch[o["id"]], rel) for o in n["outcomes"]}
            out[(n["need_id"], cid)] = {"labels": labels, "tier": tier((o["priority"], labels[o["id"]]) for o in n["outcomes"])}
    return out


def precheck(w: dict, use_related: bool = True) -> dict:
    t = pair_truth(w, use_related)
    per_need = {n["need_id"]: Counter(v["tier"] for (nid, _), v in t.items() if nid == n["need_id"]) for n in w["needs"]}
    viable = Counter()
    for (_, cid), v in t.items():
        viable[cid] += v["tier"] != "none"
    return {
        "tier_mix_total": dict(Counter(v["tier"] for v in t.values())),
        "per_need": {k: dict(v) for k, v in per_need.items()},
        "nordsense_strong_or_worth": per_need["N-014"]["strong"] + per_need["N-014"]["worth"],
        "jobseekers_with_ge2_viable": sum(v >= 2 for v in viable.values()),
        "jobseekers_with_0_viable": sum(v == 0 for v in viable.values()),
        "capacity_total": sum(c["capacity"] for c in w["companies"]),
        "needs_without_stretch": [k for k, v in per_need.items() if not v["stretch"]],
        "heroes": {cid: t[("N-014", cid)] for cid in ("J-001", "J-002", "J-003")},
    }


if __name__ == "__main__":
    for mode in (True, False):
        p = precheck(load(), mode)
        p.pop("per_need")
        print("with related rule" if mode else "direct only", json.dumps(p, default=str))
