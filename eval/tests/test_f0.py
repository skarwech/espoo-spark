"""F0 hard gates: schema validity, no real PII (with positive control), reproducibility, quotas, planted cases, golden set."""
import csv
import json
import re
import subprocess
import sys
from pathlib import Path

import jsonschema
import pytest

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "out"
sys.path.insert(0, str(ROOT / "eval"))
from pii import identity_leaks, scan, walk_strings  # noqa: E402

pytestmark = pytest.mark.hard
FILES = {"companies.json": "company", "needs.json": "need_card", "jobseekers.json": "jobseeker",
         "identities.json": "identity", "attributes.json": "attributes"}
LANG_QUOTA = {"en": 60, "fi": 45, "sv": 23, "ar": 22}
BG_QUOTA = {"international_professional": 45, "local_graduate": 30, "career_changer": 30, "returner": 23, "vocational": 22}


def load(name: str):
    return json.loads((OUT / name).read_text(encoding="utf-8"))


def stories():
    for j in load("jobseekers.json"):
        yield j["candidate_id"], j["story"]


@pytest.mark.parametrize("fname,schema", FILES.items())
def test_schema_validity(fname, schema):
    validator = jsonschema.Draft202012Validator(json.loads((ROOT / "data" / "schemas.json").read_text(encoding="utf-8"))[schema])
    records = load(fname)
    assert records, f"{fname} is empty"
    errors = [f"{r.get('candidate_id') or r.get('need_id') or r.get('company_id')}: {e.message}"
              for r in records for e in validator.iter_errors(r)]
    assert not errors, errors[:5]


def test_story_boxes_within_120_words():
    over = [(cid, box) for cid, s in stories() for box in ("problem", "did", "differently", "next") if len(s[box].split()) > 120]
    assert not over


def test_need_skill_uris_exist_in_esco_cache():
    cache = json.loads((ROOT / "data" / "esco_cache.json").read_text(encoding="utf-8"))["skills"]
    missing = [s["esco_uri"] for n in load("needs.json") for s in n["skills"] if s["esco_uri"] not in cache]
    assert not missing


def test_pii_scanner_positive_control():
    fx = json.loads((ROOT / "eval" / "fixtures" / "pii_positive.json").read_text(encoding="utf-8"))
    missed = [p["text"] for p in fx["planted"] if not any(k == p["kind"] for k, _ in scan(p["text"]))]
    assert not missed and {p["kind"] for p in fx["planted"]} == {"email", "phone", "hetu", "iban"}
    assert not [c["text"] for c in fx["clean"] if scan(c["text"])]


def test_no_real_pii_anywhere():
    paths = sorted(OUT.glob("*.json")) + sorted((ROOT / "data" / "bank").glob("*.json")) + \
        [ROOT / "data" / "heroes.json", ROOT / "data" / "common.json"]
    hits = [(p.name, h) for p in paths for s in walk_strings(json.loads(p.read_text(encoding="utf-8"))) for h in scan(s)]
    assert not hits, hits[:5]


def test_identity_leak_detector_positive_control():
    fx = json.loads((ROOT / "eval" / "fixtures" / "pii_positive.json").read_text(encoding="utf-8"))
    assert all(identity_leaks(p["text"], p) for p in fx["identity_planted"])


def test_matching_records_carry_no_identity():
    """Whole jobseeker record (story, constraints, industry) is the matching input; none of the owner's identity may appear."""
    ids = {i["candidate_id"]: i for i in load("identities.json")}
    leaks = [(j["candidate_id"], h) for j in load("jobseekers.json")
             for h in identity_leaks(" ".join(walk_strings(j)), ids[j["candidate_id"]])]
    assert not leaks, leaks[:5]


def test_reproducible(tmp_path):
    for run in ("a", "b"):
        subprocess.run([sys.executable, str(ROOT / "data" / "generate.py"), "--seed", "42", "--out", str(tmp_path / run)], check=True)
    names = sorted(p.name for p in (tmp_path / "a").glob("*.json"))
    assert names == sorted(p.name for p in OUT.glob("*.json"))
    for n in names:
        a, b, committed = ((tmp_path / "a" / n).read_bytes(), (tmp_path / "b" / n).read_bytes(), (OUT / n).read_bytes())
        assert a == b, f"{n} differs between two runs"
        assert a == committed, f"{n} in data/out is stale; run make data"


def test_quotas_match_preregistration():
    langs, bgs, styles = {}, {}, {}
    for _, s in stories():
        langs[s["lang"]] = langs.get(s["lang"], 0) + 1
    for a in load("attributes.json"):
        bgs[a["background"]] = bgs.get(a["background"], 0) + 1
        styles[a["style"]] = styles.get(a["style"], 0) + 1
    assert langs == LANG_QUOTA and bgs == BG_QUOTA and styles == {"polished": 75, "rough": 75}
    assert len(load("companies.json")) == len(load("needs.json")) == 25


def test_planted_cases_exist():
    attrs = {a["candidate_id"]: a for a in load("attributes.json")}
    assert {"J-001", "J-002", "J-003"} <= {c for c, a in attrs.items() if "hero" in a["planted_tags"]}
    assert sum("generic" in a["planted_tags"] for a in attrs.values()) >= 10
    assert sum("thin" in a["planted_tags"] for a in attrs.values()) == 5
    assert any(n["need_id"] == "N-014" for n in load("needs.json"))
    planted = load("planted.json")
    assert set(planted["claims"]) == set(attrs)


def test_golden_set_complete():
    rows = list(csv.DictReader((ROOT / "eval" / "golden.csv").open(encoding="utf-8")))
    labels = {"supported", "partial", "none"}
    assert len({r["pair_id"] for r in rows}) == 20
    assert {("N-014", c) for c in ("J-001", "J-002", "J-003")} <= {(r["need_id"], r["candidate_id"]) for r in rows}
    bad = [r["pair_id"] + r["outcome_id"] for r in rows
           if not {r["planted"], r["annotator_a"], r["annotator_b"], r["consensus"]} <= labels]
    assert not bad


def test_golden_set_frozen_to_its_items():
    """Labels are only valid for the exact items annotated; a re-selection must not silently re-key them."""
    sys.path.insert(0, str(ROOT / "eval"))
    from golden import FROZEN, digests
    assert FROZEN.exists(), "run: python eval/golden.py freeze"
    assert json.loads(FROZEN.read_text(encoding="utf-8"))["sha256"] == digests()
