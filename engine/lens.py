"""F7: coverage grid and tier per (need, candidate). Pure functions over stored units and judgments; no numbers in the output."""
import json
from functools import lru_cache
from pathlib import Path

from engine.tiers import tier

ROOT = Path(__file__).resolve().parents[1]
TIER_TEXT = {"strong": "Strong", "worth": "Worth a conversation", "stretch": "Stretch", "none": "No evidence yet"}


@lru_cache(maxsize=1)
def esco() -> dict:
    return json.loads((ROOT / "data" / "esco_cache.json").read_text(encoding="utf-8"))["skills"]


HUB_SHARE = 1 / 8  # method.md A1.3: the pool spans 8 domains


def hub_skills(units: dict, pool_ids) -> frozenset[str]:
    """Skills tagged on more than 1/8 of the pool stories' evidence units (method.md A1.3)."""
    pool = [u for c in pool_ids for u in units[c]["units"]]
    count = {}
    for u in pool:
        for s in u["skills"]:
            count[s] = count.get(s, 0) + 1
    return frozenset(s for s, n in count.items() if n > HUB_SHARE * len(pool))


def esco_label(uri: str) -> str:
    return esco()[uri]["label"]


def relation(c: str, s: str, occupation: bool = False) -> str:
    """Direct ESCO link from candidate skill c to outcome skill s, or ''. method.md §6: shared-occupation links are not adjacency;
    occupation=True adds them for the registered sensitivity analysis only."""
    sk = esco()
    if c == s:
        return "same skill"
    a, b = sk.get(c, {}), sk.get(s, {})
    if s in a.get("broaderSkill", []):
        return "narrower skill of"
    if c in b.get("broaderSkill", []):
        return "broader skill of"
    if s in a.get("hasEssentialSkill", []) + a.get("hasOptionalSkill", []) or c in b.get("hasEssentialSkill", []) + b.get("hasOptionalSkill", []):
        return "related skill of"
    occ = lambda x: set(x.get("isEssentialForOccupation", []) + x.get("isOptionalForOccupation", []))
    if occupation and occ(a) & occ(b):
        return "shares an occupation with"
    return ""


def adjacency(units: list[dict], outcome_skills: list[str], occupation: bool = False, hubs: frozenset = frozenset()) -> dict | None:
    """First (unit, candidate skill, outcome skill) path, in stable order, or None. Hub candidate skills never form a path (A1.3)."""
    for u in units:
        for c in u["skills"]:
            if c in hubs:
                continue
            for s in outcome_skills:
                rel = relation(c, s, occupation)
                if rel:
                    return {"unit_id": u["id"], "from": esco()[c]["label"], "relation": rel, "to": esco()[s]["label"]}
    return None


def pair(need: dict, cid: str, units: list[dict], judgments: dict[str, dict], *, use_adjacency: bool = True,
         occupation: bool = False, hubs: frozenset = frozenset()) -> dict:
    """Coverage grid + tier for one pair. judgments are keyed '<need_id>/<outcome_id>'."""
    by_id = {u["id"]: u for u in units}
    grid = []
    for o in need["outcomes"]:
        j = judgments.get(f"{need['need_id']}/{o['id']}", {"label": "none", "unit_id": "", "quote": "", "reason": "Not judged."})
        row = {"outcome_id": o["id"], "outcome": o["text"], "priority": o["priority"], "support": j["label"],
               "unit_id": j["unit_id"], "quote": j["quote"], "reason": j["reason"], "path": None}
        if j["label"] == "none" and use_adjacency:
            path = adjacency(units, o["skills"], occupation, hubs)
            if path:
                row.update(support="adjacent", unit_id=path["unit_id"], path=path,
                           reason=f"No direct evidence; adjacent skill path: {path['from']} ({path['relation']}) {path['to']}.")
        if row["unit_id"]:
            row["box"] = by_id[row["unit_id"]]["box"]
        grid.append(row)
    t = tier((r["priority"], r["support"]) for r in grid)
    cited = [r for r in grid if r["support"] != "none"]
    why = (f"{TIER_TEXT[t]}: " + "; ".join(f"{r['outcome_id']} {r['support']}" for r in cited)) if cited else \
        "No evidence yet: no unit addresses this need's outcomes. Any person can still add this conversation."
    return {"need_id": need["need_id"], "candidate_id": cid, "tier": t, "tier_label": TIER_TEXT[t], "why": why,
            "addable": True, "grid": grid}
