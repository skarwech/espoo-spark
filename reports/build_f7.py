"""Build the step-2 proof page (reports/f7.html): Match Lens for the heroes, golden tier agreement, F4-F7 gates and targets.
Every number comes from data/engine/, eval/results/ and the pytest JUnit file. Candidates appear by id only (no identity)."""
import csv
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from jinja2 import Environment

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from engine.lens import TIER_TEXT  # noqa: E402
from engine.tiers import tier  # noqa: E402

RANK = {"none": 0, "stretch": 1, "worth": 2, "strong": 3}
# Running example from the challenge spec (F7 "Expect"); personas, not identities.
HEROES = (("J-002", "rough but concrete story", "strong"), ("J-003", "career changer from logistics", "worth"),
          ("J-001", "polished, generic story", "stretch"))


def opt(p: str, default=None):
    f = ROOT / p
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else default


def gate_labels(units: dict, inj: dict) -> dict:
    twins = [t for t in opt("eval/fixtures/twins.json", []) if t["kind"] == "injection"]
    kept = sum(any(r.get("injected_text", "\0") in u["text"] for u in units.get(t["id"], {}).get("units", []))
               for t, r in ((t, inj.get(t["id"], {})) for t in twins))
    clean_none = all(v["label"] == "none" for r in inj.values() for v in r["clean"].values()) if inj else False
    return {
        "test_units_are_exact_substrings_with_offsets": "F4 units are exact substrings of an evidence box, offsets in range, every story present",
        "test_unit_skill_uris_exist_in_esco_cache": "F4 skill URIs exist in the ESCO cache",
        "test_every_pool_story_extracted_and_planted_thin_flagged": "F4 5 planted thin stories flagged, none dropped; thin rule holds for all",
        "test_embedding_deterministic_and_version_logged": "F5 embeddings deterministic, version matches; all 156 stored retrieval runs recompute to the same units in the same order",
        "test_embedded_inputs_carry_no_identity": "F5 embedded inputs carry no identity (names incl. Finnish case forms, nationality in en/fi/sv/ar, school)",
        "test_judge_quotes_are_exact_substrings_of_their_unit": "F6 every quote is exact, ≥ 3 words, from a unit retrieved for that outcome",
        "test_injection_never_raises_a_label": f"F6 injection, story level: no label raised (extraction kept {kept} of {len(twins)} injected sentences)",
        "test_injection_at_judge_level_never_raises_a_label":
            f"F6 injection, judge level: injected sentence inside a unit shown for every outcome, no label raised, none rejected"
            f"{' (all clean labels were none: tests none → raised only)' if clean_none else ''}",
        "test_lens_deterministic_over_100_rebuilds": "F7 determinism over 100 rebuilds, all 500 pairs present",
        "test_no_numeric_score_or_rank_in_lens_payload": "F7 no numeric score or rank in the payload (key allowlist, fixed vocabularies)",
        "test_no_auto_exclusion_when_everything_is_none": "F7 no auto-exclusion: every pair addable, also when all are none",
        "test_every_tier_is_explained": "F7 every tier carries a cited unit or a stated reason",
        "test_no_strong_where_consensus_is_stretch_or_none": "F7 no Strong where consensus is Stretch or none",
        "test_proof_page_carries_no_candidate_identity": "This page carries no candidate identity",
    }


def stale() -> bool:
    """True when the JUnit file is older than the engine outputs, the targets or the tests it is meant to describe."""
    p = ROOT / "eval" / "results" / "hard.xml"
    inputs = [*(ROOT / "data" / "engine").glob("*.json"), ROOT / "eval" / "results" / "targets_f4_f7.json", ROOT / "eval" / "tests" / "test_f4_f7.py"]
    return p.exists() and any(f.exists() and f.stat().st_mtime > p.stat().st_mtime for f in inputs)


def gates(labels: dict):
    p = ROOT / "eval" / "results" / "hard.xml"
    status = {}
    if p.exists():
        for tc in ET.parse(p).getroot().iter("testcase"):
            status[tc.get("name")] = not any(tc.find(k) is not None for k in ("failure", "error", "skipped"))
    return [(label, "pending" if name not in status else "pass" if status[name] else "fail") for name, label in labels.items()]


def marked(unit_text: str, quote: str) -> tuple[str, str, str]:
    i = unit_text.find(quote) if quote else -1
    return (unit_text, "", "") if i < 0 else (unit_text[:i], quote, unit_text[i + len(quote):])


def consensus() -> tuple[dict, dict]:
    """(pair -> [(priority, label)], (need, cand, outcome) -> label) from the frozen golden set."""
    pairs, labels = {}, {}
    p = ROOT / "eval" / "golden.csv"
    for r in (csv.DictReader(p.open(encoding="utf-8")) if p.exists() else []):
        pairs.setdefault((r["pair_id"], r["need_id"], r["candidate_id"], r["story_lang"], r["style"]), []).append((r["priority"], r["consensus"]))
        labels[(r["need_id"], r["candidate_id"], r["outcome_id"])] = r["consensus"]
    return pairs, labels


def agreement(engine: str | None, ref: str) -> str:
    if engine is None:
        return "not built"
    d = RANK[engine] - RANK[ref]
    return "agrees" if d == 0 else f"engine {abs(d)} tier{'s' if abs(d) > 1 else ''} {'higher' if d > 0 else 'lower'}"


def build():
    lens_pairs, units = opt("data/engine/lens.json", []), opt("data/engine/units.json", {})
    judgments, inj = opt("data/engine/judgments.json", {}), opt("data/engine/injection.json", {})
    unit_text = {u["id"]: u["text"] for r in units.values() for u in r["units"]}
    gpairs, glabels = consensus()
    engine = {(p["need_id"], p["candidate_id"]): p["tier"] for p in lens_pairs}
    gtier = {(k[1], k[2]): tier(v) for k, v in gpairs.items()}
    heroes = []
    for cid, persona, expected in HEROES:
        p = next((x for x in lens_pairs if x["need_id"] == "N-014" and x["candidate_id"] == cid), None)
        if not p:
            continue
        rows = [{**r, "parts": marked(unit_text.get(r["unit_id"], ""), r["quote"]),
                 "consensus": glabels.get(("N-014", cid, r["outcome_id"]), "—"),
                 "judge_reason": judgments.get(f"{cid}@all", {}).get(f"N-014/{r['outcome_id']}", {}).get("reason", "")} for r in p["grid"]]
        heroes.append({"cid": cid, "persona": persona, "pair": p, "rows": rows, "thin": units[cid]["thin"], "n_units": len(units[cid]["units"]),
                       "expected": expected, "consensus": gtier.get(("N-014", cid)),
                       "vs_spec": agreement(p["tier"], expected), "vs_consensus": agreement(p["tier"], gtier[("N-014", cid)]) if ("N-014", cid) in gtier else ""})
    golden = [{"pair_id": k[0], "need": k[1], "cand": k[2], "lang": k[3], "style": k[4], "consensus": tier(v),
               "engine": engine.get((k[1], k[2])), "agree": agreement(engine.get((k[1], k[2])), tier(v))}
              for k, v in sorted(gpairs.items())] if lens_pairs else []
    res = opt("eval/results/targets_f4_f7.json", {"targets": {}, "meta": {}})
    v1 = opt("data/engine_v1/targets_f4_f7.json", {}) if (ROOT / "data" / "engine" / "hubs.json").exists() else {}  # amended run: compare
    v1_tier = {(p["need_id"], p["candidate_id"]): p["tier"] for p in opt("data/engine_v1/lens.json", [])} if v1 else {}
    for h in heroes:
        h["v1"] = v1_tier.get(("N-014", h["cid"]))
    for g in golden:
        g["v1"] = v1_tier.get((g["need"], g["cand"]))
    env = Environment(autoescape=True)
    html = env.from_string((ROOT / "reports" / "f7.html.j2").read_text(encoding="utf-8")).render(
        css=(ROOT / "reports" / "base.css").read_text(encoding="utf-8"), gates=gates(gate_labels(units, inj)), stale=stale(), targets=res["targets"],
        missed={k: t for k, t in res["targets"].items() if t["pass"] is False}, meta=res["meta"], heroes=heroes, golden=golden,
        v1=v1.get("targets", {}), v1_missed=[k for k, t in v1.get("targets", {}).items() if t["pass"] is False],
        hubs=opt("data/engine/hubs.json", {}).get("hubs"), a1=opt("data/engine_a1_partial/summary.json"),
        xf=opt("eval/results/xfamily.json"),
        tier_text=TIER_TEXT, n_units=sum(len(r["units"]) for r in units.values()), n_stories=len(units))
    (ROOT / "reports" / "f7.html").write_text(html, encoding="utf-8", newline="\n")
    print("wrote reports/f7.html")


if __name__ == "__main__":
    build()
