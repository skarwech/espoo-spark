"""F1-F3 hard gates (method.md §4, §7) over stored step-3 results (eval/results/step3/) and model-free API checks.
No model calls: stored runs are produced by eval/run_step3.py."""
import json
import os
import re
import sys
import tempfile
from pathlib import Path

import jsonschema
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("SPARK_ENGINE", "d1")  # step 3 runs on engine d1 (method.md §7); run this file in its own pytest process
pytestmark = pytest.mark.hard


def test_engine_profile_is_d1():
    from engine import llm
    assert llm.ENGINE == "d1", "run with SPARK_ENGINE=d1 in a separate pytest process (make verify does)"
RES = ROOT / "eval" / "results" / "step3"
NEED_SCHEMA = json.loads((ROOT / "data" / "schemas.json").read_text(encoding="utf-8"))["need_card"]
ESCO = json.loads((ROOT / "data" / "esco_cache.json").read_text(encoding="utf-8"))["skills"]


def load(name: str):
    p = RES / name
    if not p.exists():
        pytest.fail(f"{name} missing: run SPARK_ENGINE=d1 python eval/run_step3.py")
    return json.loads(p.read_text(encoding="utf-8"))


def data(p: str):
    return json.loads((ROOT / p).read_text(encoding="utf-8"))


@pytest.fixture()
def client(monkeypatch):
    db = Path(tempfile.mkdtemp()) / "t.db"
    monkeypatch.setenv("SPARK_DB", str(db))
    from fastapi.testclient import TestClient
    from api.main import app
    return TestClient(app)


# ---------- F1 ----------
def test_f1_ten_personas_give_schema_valid_need_cards():
    clean = load("personas.json")["clean"]
    assert sorted(clean) == [f"P{i:02d}" for i in range(1, 11)]
    bad = [p for p, r in clean.items() if not r["done"] or r["error"] or not r["record"]]
    for p, r in clean.items():
        if r["record"]:
            try:
                jsonschema.validate(r["record"]["card"], NEED_SCHEMA)
            except jsonschema.ValidationError as e:
                bad.append((p, e.message[:80]))
    assert not bad, bad


def test_f1_unconfirmed_need_card_is_blocked_downstream(client):
    from api import need_studio, store
    need = {**data("data/out/needs.json")[13], "confirmed": False}  # N-014: tiers come from stored judgments, no model call
    store.put_need({"card": need, "unmapped_skills": [], "warnings": [], "summary": ""})
    assert client.get("/api/needs/N-014/preview").status_code == 409
    with pytest.raises(PermissionError):
        need_studio.usable("N-014")
    assert client.post("/api/needs/N-014/confirm", json={"version": need["version"]}).status_code == 200
    r = client.get("/api/needs/N-014/preview")
    assert r.status_code == 200 and r.json()["need_id"] == "N-014"


def test_f1_every_skill_uri_exists_in_esco_cache():
    recs = [r["record"] for r in (*load("personas.json")["clean"].values(), *load("personas.json")["injected"]) if r["record"]]
    assert len(recs) >= 10
    uris = [s["esco_uri"] for r in recs for s in r["card"]["skills"]] + [u for r in recs for o in r["card"]["outcomes"] for u in o["skills"]]
    assert uris and all(u in ESCO for u in uris), [u for u in uris if u not in ESCO][:5]
    assert all(u.get("unmapped") is True and "esco_uri" not in u for r in recs for u in r["unmapped_skills"])


def test_f1_ten_injections_zero_successes():
    res = load("personas.json")
    injections = {i["id"]: i for i in data("eval/fixtures/need_injections.json")}
    assert len(res["injected"]) == len(injections) == 10
    from eval.step3_checks import injection_success
    succ = [(r["injection"], w) for r in res["injected"] for w in injection_success(injections[r["injection"]], r,
                                                                                 res["clean"][injections[r["injection"]]["persona"]])]
    assert not succ, succ


# ---------- F2 ----------
def _previews():
    pv = load("previews.json")
    src = {p["source"] for p in pv}
    assert len([p for p in pv if p["source"] in ("synthetic", "variant")]) == 50 and "persona" in src, "need 50 needs plus persona cards"
    return pv


def test_f2_counts_are_multiples_of_5_or_suppressed():
    bad = [(p["card"]["need_id"], p["card"]["version"], k, v) for p in _previews() for k, v in p["preview"]["counts"].items()
           if not (v == "fewer than 5" or (isinstance(v, int) and not isinstance(v, bool) and v >= 5 and v % 5 == 0))]
    assert not bad, bad[:5]


def test_f2_no_person_level_fields():
    from api.pool_preview import COUNT_KEYS, KEYS
    ids = re.compile(r"\b(J-\d{3}|S-[0-9a-f]{10})\b")
    for p in _previews():
        pr = p["preview"]
        assert tuple(pr) == KEYS and tuple(pr["counts"]) == COUNT_KEYS
        assert all(isinstance(x, str) for x in pr["narrowing"]) and isinstance(pr["registered_so_far"], int)
        assert not ids.search(json.dumps(pr)), "a person id in the payload"


def test_f2_no_protected_breakdowns():
    """Groups are only the company-stated constraints; narrowing names only must-have outcomes, the start window or a required language."""
    from api.pool_preview import COUNT_KEYS
    protected = {"nationality", "gender", "age", "ethnicity", "religion", "origin", "background", "country", "school", "birth", "name"}
    assert not any(protected & set(k.split("_")) for k in COUNT_KEYS)
    assert set(COUNT_KEYS) == {"evidence_for_most_must_haves", "and_start_window_and_language", "and_work_mode"}
    bad = [n for p in _previews() for n in p["preview"]["narrowing"]
           if not (n.startswith("must-have outcome: ") or n == "start window" or re.fullmatch(r"required language: [a-z]{2}", n))]
    assert not bad, bad[:5]


def test_f2_preview_equals_recomputed_match_lens_tiers():
    """Independent recomputation: tiers from stored d1 judgments via lens.pair, constraints straight from the pool records."""
    os.environ["SPARK_ENGINE"] = "d1"
    from engine import lens
    from api.pool_preview import bucket
    units = data("data/engine_d1/units.json")
    stored = data("data/engine_d1/judgments.json")
    seekers = {x["candidate_id"]: x["constraints"] for x in data("data/out/jobseekers.json")}
    level = {"A1": 1, "A2": 2, "B1": 3, "B2": 4, "C1": 5, "C2": 6}
    presence = {"remote": 0, "hybrid": 1, "onsite": 2}
    synthetic = {n["need_id"] for n in data("data/out/needs.json")}
    bad = []
    for p in _previews():
        card = p["card"]
        if card["need_id"] in synthetic:
            judg = {c: stored[f"{c}@all"] for c in seekers}
        else:
            live_file = ROOT / "data" / "engine_d1" / "live" / f"{card['need_id']}-v{card['version']}.json"
            judg = json.loads(live_file.read_text(encoding="utf-8"))["judgments"]
        a = [c for c in seekers if lens.pair(card, c, units[c]["units"], judg[c])["tier"] in ("strong", "worth")]
        req = [l["lang"] for l in card["languages"] if l["required"]]
        b = [c for c in a if seekers[c]["start"] <= card["constraints"]["start"]
             and all(max([level[x["level"]] for x in seekers[c]["languages"] if x["lang"] == r], default=0) >= 4 for r in req)]
        cc = [c for c in b if presence[card["constraints"]["onsite"]] <= presence[seekers[c]["work_mode"]]]
        want = [bucket(len(a)), bucket(len(b)), bucket(len(cc))]
        if list(p["preview"]["counts"].values()) != want:
            bad.append((card["need_id"], card["version"], list(p["preview"]["counts"].values()), want))
    assert not bad, bad[:5]


# ---------- F3 ----------
def test_f3_stored_text_equals_submitted_50_submissions():
    subs = load("stories.json")["submissions"]
    assert len(subs) == 50 and all(s["status"] == 200 for s in subs)
    assert all(s["identical"] for s in subs), [s["i"] for s in subs if not s["identical"]]


def test_f3_clarifying_questions_zero_protected_violations():
    from api.skill_story import BLOCK
    qs = load("stories.json")["questions"]
    assert len(qs) == 50
    companies = [c["name"].lower() for c in data("data/out/companies.json")]
    viol = [q["shown"] for q in qs if BLOCK.search(q["shown"]) or any(n in q["shown"].lower() for n in companies)]
    assert not viol, viol[:5]


def test_f3_each_supported_language_through_full_flow():
    cov = load("stories.json")["coverage"]
    needs = {n["need_id"] for n in data("data/out/needs.json")}
    assert sorted(c["lang"] for c in cov) == ["ar", "en", "fi", "sv"]
    assert all(c["units"] >= 1 and set(c["tiers"]) == needs and c["stored_equals_extraction_input"] for c in cov), cov


def test_f3_candidate_never_sees_a_score(client):
    body = {"lang": "en", "problem": "Late shipments.", "did": "I built a tracking sheet; delays fell by half.", "differently": "", "next": "",
            "consent": True}
    r = client.post("/api/stories", json=body)
    assert r.status_code == 200 and set(r.json()) == {"story_id"}
    v = client.get(f"/api/stories/{r.json()['story_id']}/recruiter-view").json()
    assert set(v) <= {"lang", "problem", "did", "differently", "next", "constraints", "mini_case"}
    check = client.post("/api/stories/check", json=body).json()
    banned = re.compile(r'"(tier|score|rank|match|cosine|similarity)"', re.I)
    assert not banned.search(json.dumps(r.json()) + json.dumps(v) + json.dumps(check))
    page = client.get("/skill-story").text
    assert not re.search(r"\b(tier|score:|ranking:)", page, re.I) or "never see a score" in page
