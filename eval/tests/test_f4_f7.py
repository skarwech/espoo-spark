"""F4-F7 hard gates over stored engine outputs (data/engine/). No API calls.
Each gate also checks its own population (non-empty, complete), so an emptied or truncated output turns it red."""
import csv
import json
import random
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "eval"))
from engine import lens  # noqa: E402
from engine.extract import EVIDENCE_BOXES  # noqa: E402
from engine.tiers import tier  # noqa: E402
from pii import NATIONALITY_FORMS, identity_leaks, nationality_hits  # noqa: E402

pytestmark = pytest.mark.hard
ENGINE = ROOT / "data" / "engine"
RANK = {"none": 0, "stretch": 1, "worth": 2, "strong": 3}
ORDER = {"none": 0, "partial": 1, "supported": 2}
HEROES = ("J-001", "J-002", "J-003")
SUPPORT = {"supported", "partial", "adjacent", "none"}
PRIORITY = {"must", "nice", "learnable"}
RELATIONS = {"same skill", "narrower skill of", "broader skill of", "related skill of"}
LENS_KEYS = {"need_id", "candidate_id", "tier", "tier_label", "why", "addable", "grid", "outcome_id", "outcome", "priority", "support",
             "unit_id", "quote", "reason", "path", "box", "from", "relation", "to"}
FREE_TEXT = {"why", "outcome", "quote", "reason", "from", "to"}  # may contain numbers written by people (e.g. "40 to 12 a day")


def load(name: str):
    p = ENGINE / name
    if not p.exists():
        pytest.fail(f"{name} missing: run python -m engine.run (step 2 pipeline)")
    return json.loads(p.read_text(encoding="utf-8"))


def data(name: str):
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


def stories() -> dict:
    return {j["candidate_id"]: j["story"] for j in data("data/out/jobseekers.json")} | \
        {t["id"]: t["story"] for t in data("eval/fixtures/twins.json")}


def golden_candidates() -> list[str]:
    return sorted({k["candidate_id"] for k in data("eval/golden_key.json")} | set(HEROES))


def jobs() -> dict:
    """run_id -> (run_id, story_id, needs), exactly as the pipeline schedules them (engine/run.py)."""
    from engine import run
    return {j[0]: j for j in run.jobs(run.world())}


def outcome_keys(needs: list[dict]) -> set[str]:
    return {f"{n['need_id']}/{o['id']}" for n in needs for o in n["outcomes"]}


# ---------- F4 ----------
def test_units_are_exact_substrings_with_offsets():
    st, units = stories(), load("units.json")
    assert set(units) == set(st), "a story or twin is missing from units.json, or an unknown id is present"
    bad = []
    for sid, r in units.items():
        for u in r["units"]:
            box = st[sid].get(u["box"], "") if u["box"] in EVIDENCE_BOXES else None
            if box is None or not u["text"].strip() or not 0 <= u["start"] < u["end"] <= len(box) or box[u["start"]:u["end"]] != u["text"]:
                bad.append(u["id"])
    assert not bad, bad[:5]


def test_unit_skill_uris_exist_in_esco_cache():
    cache = data("data/esco_cache.json")["skills"]
    bad = [u["id"] for r in load("units.json").values() for u in r["units"] for s in u["skills"] if s not in cache]
    assert not bad, bad[:5]


def test_every_pool_story_extracted_and_planted_thin_flagged():
    """Registered gate: 5 planted thin stories all flagged, none dropped; plus the §6 thin rule for every story. How many units a
    non-thin story yields is a quality question for the F4 recall target, not this gate."""
    units = load("units.json")
    attrs = data("data/out/attributes.json")
    assert {a["candidate_id"] for a in attrs} <= set(units), "a pool story was dropped"
    thin = [a["candidate_id"] for a in attrs if "thin" in a["planted_tags"]]
    assert len(thin) == 5 and all(units[c]["thin"] for c in thin), [c for c in thin if not units[c]["thin"]]
    rule = [s for s, r in units.items() if r["thin"] is not (len(r["units"]) < 2)]  # method.md §6: thin = fewer than 2 units
    assert not rule, rule[:5]


# ---------- F5 ----------
def test_embedding_deterministic_and_version_logged():
    from engine import embed, run
    r = load("retrieval.json")
    assert r["version"] == embed.version() and r["version"].startswith("BAAI/bge-m3@") and "unknown" not in r["version"]
    judg, units, sched = load("judgments.json"), load("units.json"), jobs()
    assert set(r["runs"]) == set(judg) == set(sched), "retrieval runs, judge runs and scheduled jobs differ"
    texts = ["Cut false sensor-fault alerts by half within 90 days", "Puolitin väärät hälytykset."]
    assert (embed.embed(texts) == embed.embed(texts)).all()
    bad = []
    for run_id, (_, sid, needs) in sched.items():  # structure of every stored run
        own = {u["id"] for u in units[sid]["units"]}
        keys = outcome_keys(needs)
        if set(r["runs"][run_id]) != keys or set(judg[run_id]) != keys:
            bad.append((run_id, "outcome keys"))
        for k, top in r["runs"][run_id].items():
            cos = [x["cosine"] for x in top]
            if len(top) != min(3, len(own)) or any(x["unit_id"] not in own for x in top) or cos != sorted(cos, reverse=True):
                bad.append((run_id, k))
    assert not bad, bad[:5]
    differ = []  # every stored run recomputes (~45 s on CPU): identical units and order; cosines are internal and, at 4 decimals,
    for run_id, (_, sid, needs) in sched.items():  # may differ by 1e-4 across BLAS thread counts (measured: 11 values at 3 threads)
        fresh = embed.retrieve(run.outcomes(needs), units[sid]["units"])
        for k, top in r["runs"][run_id].items():
            if [x["unit_id"] for x in top] != [x["unit_id"] for x in fresh[k]] or \
                    any(abs(x["cosine"] - y["cosine"]) > 2e-4 for x, y in zip(top, fresh[k])):
                differ.append((run_id, k))
    assert not differ, differ[:5]


def test_embedded_inputs_carry_no_identity():
    """Only unit texts and need-outcome texts are embedded; none may contain the owner's identity, or any nationality in en/fi/sv/ar."""
    ids = {i["candidate_id"]: i for i in data("data/out/identities.json")}
    assert {i["nationality"] for i in ids.values()} - {"Finnish", "Swedish"} <= set(NATIONALITY_FORMS), "a nationality has no en/fi/sv/ar forms"
    units = load("units.json")
    assert sum(len(r["units"]) for r in units.values()) > 0
    leaks = []
    for sid, r in units.items():
        owner = ids[sid.split("~")[0]]
        for u in r["units"]:
            leaks += [(u["id"], h) for h in identity_leaks(u["text"], owner)]
            leaks += [(u["id"], h) for n in NATIONALITY_FORMS for h in nationality_hits(u["text"], n)]
    assert not leaks, leaks[:5]


# ---------- F6 ----------
def test_judge_quotes_are_exact_substrings_of_their_unit():
    """Stored and stability judgments: a non-none label cites a unit of the same story, retrieved for that outcome, with a quote of
    at least 3 words that is an exact substring of it (the prompt asks for 4; every stored quote has at least 3)."""
    units = {u["id"]: u["text"] for r in load("units.json").values() for u in r["units"]}
    retr = load("retrieval.json")["runs"]
    bad, cited = [], 0
    for name in ("judgments.json", "stability.json"):
        for run, js in load(name).items():
            sid = run.split("@")[0]
            for k, j in js.items():
                if j["label"] == "none":
                    continue
                cited += 1
                shown = {r["unit_id"] for r in retr[run][k]}
                if len(j["quote"].split()) < 3 or not j["unit_id"].startswith(f"{sid}.") or j["unit_id"] not in shown \
                        or j["quote"] not in units.get(j["unit_id"], ""):
                    bad.append((name, run, k))
    assert cited > 0 and not bad, bad[:5]


def _injection_twins() -> list[dict]:
    inj = [t for t in data("eval/fixtures/twins.json") if t["kind"] == "injection"]
    assert len(inj) == 10
    return inj


def test_injection_never_raises_a_label():
    """Story level: the injection twin goes through extraction and judge; no outcome may score higher than on the clean story."""
    js, sched = load("judgments.json"), jobs()
    raised = []
    for t in _injection_twins():
        a, b = js[f"{t['id']}@own"], js[f"{t['source']}@own"]
        assert set(a) == set(b) == outcome_keys(sched[f"{t['id']}@own"][2]) != set(), f"{t['id']}: not every own-domain outcome judged"
        assert not any(v.get("rejected") for v in (*a.values(), *b.values())), f"{t['id']}: a rejected judgment hides the label"
        raised += [(t["id"], k) for k in a if ORDER[a[k]["label"]] > ORDER[b[k]["label"]]]
    assert not raised, raised


def test_injection_at_judge_level_never_raises_a_label():
    """Judge level (method.md §6 note 2026-11-14): the injected sentence sits inside a unit shown for every outcome."""
    res, units, sched, st = load("injection.json"), load("units.json"), jobs(), stories()
    twins = {t["id"]: t for t in _injection_twins()}
    assert sorted(res) == sorted(twins)
    raised = []
    for tid, r in res.items():
        src = twins[tid]["source"]
        suffix = st[tid]["did"][len(st[src]["did"]):].strip()
        assert st[tid]["did"].startswith(st[src]["did"]) and r["injected_text"] == suffix != "", f"{tid}: not the fixture's injected sentence"
        clean = units[src]["units"]
        want = [{**u, "text": f"{u['text']} {suffix}"} if u["id"] == r["unit_id"] else u for u in clean] if clean else None
        assert (r["injected_units"] == want and r["unit_id"] == clean[0]["id"]) if clean else \
            [u["text"] for u in r["injected_units"]] == [suffix], f"{tid}: injected units are not the clean units plus the sentence"
        assert set(r["injected"]) == set(r["clean"]) == outcome_keys(sched[f"{src}@own"][2]), f"{tid}: not every own-domain outcome judged"
        assert not any(v.get("rejected") for v in (*r["injected"].values(), *r["clean"].values())), f"{tid}: a rejected judgment hides the label"
        raised += [(tid, k) for k in r["injected"] if ORDER[r["injected"][k]["label"]] > ORDER[r["clean"][k]["label"]]]
    assert not raised, raised


# ---------- F7 ----------
def _lens_by_pair() -> dict:
    payload = load("lens.json")
    want = {(n["need_id"], c) for n in data("data/out/needs.json") for c in golden_candidates()}
    got = {(p["need_id"], p["candidate_id"]) for p in payload}
    assert len(payload) == len(want) and got == want, "lens.json does not cover every golden/hero candidate x every need exactly once"
    return {(p["need_id"], p["candidate_id"]): p for p in payload}


def _hubs() -> frozenset:
    """Hub skills per method.md A1.3 (amended run only: hubs.json marks it), recomputed from the stored pool units."""
    if not (ENGINE / "hubs.json").exists():
        return frozenset()
    return lens.hub_skills(load("units.json"), [j["candidate_id"] for j in data("data/out/jobseekers.json")])


def _rebuild(seed: int) -> list[dict]:
    units, judgments, hubs = load("units.json"), load("judgments.json"), _hubs()
    needs = data("data/out/needs.json")
    random.Random(seed).shuffle(needs)
    return sorted((lens.pair(n, c, units[c]["units"], judgments[f"{c}@all"], hubs=hubs) for c in golden_candidates() for n in needs),
                  key=lambda p: (p["candidate_id"], p["need_id"]))


def test_lens_deterministic_over_100_rebuilds():
    if (ENGINE / "hubs.json").exists():  # the stored hub list is the A1.3 rule applied to the stored units
        assert load("hubs.json")["hubs"] == sorted(lens.esco_label(s) for s in _hubs())
    ref = json.dumps([p for _, p in sorted(_lens_by_pair().items(), key=lambda kv: (kv[0][1], kv[0][0]))], sort_keys=True)
    assert all(json.dumps(_rebuild(s), sort_keys=True) == ref for s in range(100))


def test_no_numeric_score_or_rank_in_lens_payload():
    """Allowlisted keys only; no number, and no number-looking string outside free-text fields."""
    payload = list(_lens_by_pair().values())
    keys = {k for p in payload for k in _walk_keys(p)}
    assert keys <= LENS_KEYS, keys - LENS_KEYS
    numbers = [v for p in payload for v in _walk_values(p) if isinstance(v, (int, float)) and not isinstance(v, bool)]
    numeric_strings = [(k, v) for p in payload for k, v in _walk_items(p)
                       if k not in FREE_TEXT and isinstance(v, str) and re.fullmatch(r"\s*[-+]?\d+([.,]\d+)?\s*%?\s*", v)]
    assert not numbers and not numeric_strings, (numbers[:3], numeric_strings[:3])
    bad = [(p["need_id"], p["candidate_id"]) for p in payload
           if p["tier"] not in RANK or p["tier_label"] != lens.TIER_TEXT[p["tier"]] or not p["why"].startswith(lens.TIER_TEXT[p["tier"]])
           or re.search(r"\d\s*%", p["why"] + p["tier_label"])
           or any(g["support"] not in SUPPORT or g["priority"] not in PRIORITY or (g["path"] and g["path"]["relation"] not in RELATIONS)
                  for g in p["grid"])]
    assert not bad, bad[:5]


def test_no_auto_exclusion_when_everything_is_none():
    needs = data("data/out/needs.json")
    nothing = {f"{n['need_id']}/{o['id']}": {"label": "none", "unit_id": "", "quote": "", "reason": "x"} for n in needs for o in n["outcomes"]}
    pairs = [lens.pair(n, "J-999", [], nothing) for n in needs]
    assert len(pairs) == len(needs) and all(p["tier"] == "none" and p["addable"] and p["why"] for p in pairs)
    stored = _lens_by_pair()  # and in the stored payload: every pair present (checked above) and addable, whatever its tier
    assert all(p["addable"] is True for p in stored.values())


def test_every_tier_is_explained():
    bad = []
    for p in _lens_by_pair().values():
        cited = [r for r in p["grid"] if r["support"] != "none" and (r["quote"].strip() or r["path"])]
        if (p["tier"] != "none" and not cited) or not p["why"].strip() or not all(r["reason"].strip() for r in p["grid"]):
            bad.append((p["need_id"], p["candidate_id"]))
    assert not bad, bad[:5]


def test_no_strong_where_consensus_is_stretch_or_none():
    rows = list(csv.DictReader((ROOT / "eval" / "golden.csv").open(encoding="utf-8")))
    pairs = {}
    for r in rows:
        pairs.setdefault((r["need_id"], r["candidate_id"]), []).append((r["priority"], r["consensus"]))
    engine = {k: p["tier"] for k, p in _lens_by_pair().items()}
    assert len(pairs) == 20 and set(pairs) <= set(engine)
    severe = [k for k, labels in pairs.items() if engine[k] == "strong" and RANK[tier(labels)] <= RANK["stretch"]]
    assert not severe, severe


def test_proof_page_carries_no_candidate_identity():
    """The matching-side proof page shows candidate ids only: no name, nationality or school of any candidate."""
    page = (ROOT / "reports" / "f7.html").read_text(encoding="utf-8")
    assert all(f"{h} · " in page for h in HEROES) and "G20" in page, "the page has no hero cards or golden table: build it first"
    leaks = [(i["candidate_id"], h) for i in data("data/out/identities.json") for h in identity_leaks(page, i)]
    assert not leaks, leaks[:5]


def _walk_items(o):
    if isinstance(o, dict):
        for k, v in o.items():
            yield k, v
            yield from _walk_items(v)
    elif isinstance(o, list):
        for v in o:
            yield from _walk_items(v)


def _walk_values(o):
    return (v for _, v in _walk_items(o) if not isinstance(v, (dict, list)))


def _walk_keys(o):
    return (k for k, _ in _walk_items(o))
