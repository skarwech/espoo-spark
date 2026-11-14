"""Build the step-3 proof page (reports/f3.html): F1 Need Studio, F2 Pool Preview, F3 Skill Story, and the engine d1 re-validation.
Every number comes from eval/results/ (step3/*.json, targets_f4_f7*.json, hard*.xml)."""
import json
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

from jinja2 import Environment

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from eval.step3_checks import injection_success  # noqa: E402

RES = ROOT / "eval" / "results"
F13_TESTS = {
    "test_engine_profile_is_d1": "Gates ran on engine d1",
    "test_f1_ten_personas_give_schema_valid_need_cards": "F1 10 personas: schema-valid Need Card (≤ 1 auto-retry per call)",
    "test_f1_unconfirmed_need_card_is_blocked_downstream": "F1 unconfirmed Need Card blocked downstream (409), usable once confirmed",
    "test_f1_every_skill_uri_exists_in_esco_cache": "F1 every skill URI exists in the ESCO cache; unmapped skills flagged, never given a URI",
    "test_f1_ten_injections_zero_successes": "F1 10 injection answers: 0 successes",
    "test_f2_counts_are_multiples_of_5_or_suppressed": "F2 every count a multiple of 5 or “fewer than 5” (50 needs + persona cards)",
    "test_f2_no_person_level_fields": "F2 no person-level field, list or id in any preview",
    "test_f2_no_protected_breakdowns": "F2 groups only by company-stated constraints",
    "test_f2_preview_equals_recomputed_match_lens_tiers": "F2 preview equals independently recomputed Match Lens tiers (rounded)",
    "test_f3_stored_text_equals_submitted_50_submissions": "F3 50 submissions: stored text equals submitted, byte for byte",
    "test_f3_clarifying_questions_zero_protected_violations": "F3 50 clarifying questions: 0 protected-attribute or company mentions",
    "test_f3_each_supported_language_through_full_flow": "F3 en, fi, sv, ar stories through submit → store → d1 extraction → judge → tiers",
    "test_f3_candidate_never_sees_a_score": "F3 candidate-facing payloads and page carry no tier, score or rank",
}


def opt(p: Path, default=None):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default


def junit(name: str) -> dict:
    p = RES / name
    out = {}
    if p.exists():
        for tc in ET.parse(p).getroot().iter("testcase"):
            out[(tc.get("classname", "").split(".")[-1], tc.get("name"))] = not any(tc.find(k) is not None for k in ("failure", "error", "skipped"))
    return out


def status(results: dict, module: str, name: str) -> str:
    v = results.get((module, name))
    return "pending" if v is None else "pass" if v else "fail"


def build():
    d1 = junit("hard_d1.xml")
    gates = [(label, status(d1, "test_f1_f3", n)) for n, label in F13_TESTS.items()]
    f47 = sorted({n for (m, n) in d1 if m == "test_f4_f7"})
    engine_gates = [(n.removeprefix("test_").replace("_", " "), status(d1, "test_f4_f7", n)) for n in f47]
    v1t, d1t = opt(RES / "targets_f4_f7.json", {"targets": {}}), opt(RES / "targets_f4_f7_d1.json", {"targets": {}, "meta": {}})
    engine_rows = [{"name": k, "v1": v1t["targets"].get(k), "d1": t} for k, t in d1t["targets"].items()]
    tg = opt(RES / "step3" / "targets.json", {"targets": {}, "meta": {}})
    pr = opt(RES / "step3" / "personas.json", {"clean": {}, "injected": []})
    ps = {p["id"]: p for p in opt(ROOT / "eval" / "fixtures" / "personas.json", [])}
    personas = [{"id": pid, "style": ps[pid]["style"], "lang": ps[pid]["lang"], "q": r["questions_asked"], "done": r["done"],
                 "outcomes": len(r["record"]["card"]["outcomes"]) if r["record"] else 0, "warnings": len(r["record"]["warnings"]) if r["record"] else 0,
                 "unmapped": len(r["record"]["unmapped_skills"]) if r["record"] else 0, "need_id": r["record"]["card"]["need_id"] if r["record"] else ""}
                for pid, r in sorted(pr["clean"].items())]
    injections = {i["id"]: i for i in opt(ROOT / "eval" / "fixtures" / "need_injections.json", [])}
    inj = [{"id": r["injection"], "persona": injections[r["injection"]]["persona"], "stage": injections[r["injection"]]["stage"],
            "lang": injections[r["injection"]]["lang"], "kind": injections[r["injection"]]["asks_for"]["kind"],
            "detail": injections[r["injection"]]["asks_for"]["detail"],
            "why": injection_success(injections[r["injection"]], r, pr["clean"][injections[r["injection"]]["persona"]])}
           for r in pr["injected"]]
    hero = pr["clean"].get("P10")
    previews = opt(RES / "step3" / "previews.json", [])
    hero_preview = next((p["preview"] for p in previews if hero and hero["record"] and p["card"]["need_id"] == hero["record"]["card"]["need_id"]), None)
    narrow = next((p for p in previews if p["preview"]["narrowing"]), None)
    dist = Counter("fewer than 5" if p["preview"]["counts"]["evidence_for_most_must_haves"] == "fewer than 5" else "5 or more" for p in previews)
    st = opt(RES / "step3" / "stories.json", {"submissions": [], "questions": [], "identifiers": [], "coverage": []})
    idcat = sorted(Counter((f["category"], f["lang"]) for f in st["identifiers"]).items())
    idrows = [{"category": c, "lang": l, "n": n, "flagged": sum(f["flagged"] for f in st["identifiers"] if f["category"] == c and f["lang"] == l)}
              for (c, l), n in idcat]
    html = Environment(autoescape=True).from_string((ROOT / "reports" / "f3.html.j2").read_text(encoding="utf-8")).render(
        css=(ROOT / "reports" / "base.css").read_text(encoding="utf-8"), gates=gates, engine_gates=engine_gates, engine_rows=engine_rows,
        d1meta=d1t.get("meta", {}), targets=tg["targets"], meta=tg["meta"], personas=personas, inj=inj, hero=hero, hero_preview=hero_preview,
        narrow=narrow, dist=dict(dist), n_previews=len(previews), st=st, idrows=idrows,
        n_identical=sum(s["identical"] for s in st["submissions"]), n_nfc=sum(s["nfc_would_change"] for s in st["submissions"]),
        engine_failed=[g for g in engine_gates if g[1] != "pass"])
    (ROOT / "reports" / "f3.html").write_text(html, encoding="utf-8", newline="\n")
    print("wrote reports/f3.html")


if __name__ == "__main__":
    build()
