"""Build the F0 proof page (reports/f0.html) and verification_report.md from data files + pytest JUnit XML.

Every number on the page is computed here from data/out, eval/ and eval/results/hard.xml. Nothing is hardcoded.
"""
import csv
import json
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

from jinja2 import Environment

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "data"))
sys.path.insert(0, str(ROOT / "eval"))
from golden import cohen_kappa  # noqa: E402
from truth import load, precheck  # noqa: E402
sys.path.insert(0, str(ROOT))
from engine.tiers import tier  # noqa: E402

TIER_NAMES = {"strong": "Strong", "worth": "Worth a conversation", "stretch": "Stretch", "none": "No evidence yet"}


def junit():
    p = ROOT / "eval" / "results" / "hard.xml"
    if not p.exists():
        return []
    out = []
    for tc in ET.parse(p).getroot().iter("testcase"):
        failed = any(tc.find(k) is not None for k in ("failure", "error", "skipped"))  # a skipped hard gate is not green
        out.append({"name": tc.get("name"), "ok": not failed})
    return out


def gate(tests, prefix):
    hits = [t for t in tests if t["name"].startswith(prefix)]
    if not hits:
        return "pending", "not run"
    bad = [t["name"] for t in hits if not t["ok"]]
    return ("pass" if not bad else "fail"), f"{len(hits) - len(bad)}/{len(hits)} tests green"


def bars(counter: Counter, order=None, names=None):
    order = order or [k for k, _ in counter.most_common()]
    total = sum(counter.values()) or 1
    top = max(counter.values()) if counter else 1
    return [{"label": (names or {}).get(k, k), "n": counter[k], "pct": 100 * counter[k] / total, "w": 100 * counter[k] / top} for k in order]


def golden_rows():
    p = ROOT / "eval" / "golden.csv"
    return list(csv.DictReader(p.open(encoding="utf-8"))) if p.exists() else []


def realism():
    p = ROOT / "eval" / "labels"
    items = json.loads((ROOT / "eval" / "realism_items.json").read_text(encoding="utf-8")) if (ROOT / "eval" / "realism_items.json").exists() else []
    ratings = {}
    for who in ("annotator_a", "annotator_b"):
        f = p / f"{who}.json"
        if f.exists():
            for r in json.loads(f.read_text(encoding="utf-8")).get("realism", []):
                ratings.setdefault(r["story_id"], {})[who] = r
    return items, ratings


def build():
    w = load()
    pc, pc_direct = precheck(w), precheck(w, use_related=False)
    tests = junit()
    bank = {d: json.loads((ROOT / "data" / "bank" / f"{d}.json").read_text(encoding="utf-8"))
            for d in json.loads((ROOT / "data" / "archetypes.json").read_text(encoding="utf-8"))["domains"]}
    claims = [c for b in bank.values() for c in b["claims"]]
    unique_texts = {s for c in claims for l in c["text"].values() for s in l.values()}
    used_claims = {c["claim_id"] for cs in w["planted"]["claims"].values() for c in cs}
    attrs = {a["candidate_id"]: a for a in w["attributes"]}
    js = {j["candidate_id"]: j for j in w["jobseekers"]}
    langs = Counter(j["story"]["lang"] for j in w["jobseekers"])
    bgs = Counter(a["background"] for a in w["attributes"])
    levels = Counter(c["level"] for cs in w["planted"]["claims"].values() for c in cs)
    edu = Counter(a["education"] for a in w["attributes"])
    gap = Counter("career gap" if a["career_gap"] else "no gap" for a in w["attributes"])
    shares = {**{f"lang {k}": v / 150 for k, v in langs.items()}, **{k: v / 150 for k, v in bgs.items()}}
    min_share = min(shares.items(), key=lambda kv: kv[1])

    rows = golden_rows()
    for r in rows:  # which planting rule produced the planted label (direct claim vs related-archetype rule)
        arch = w["planted"]["outcomes"][r["need_id"]][r["outcome_id"]]
        r["via"] = "direct" if any(c["archetype"] == arch for c in w["planted"]["claims"][r["candidate_id"]]) else "related"
    diffs = [r for r in rows if r["consensus"] != r["planted"]]
    related_rows = [r for r in rows if r["via"] == "related" and r["planted"] == "partial"]
    pairs = {}
    for r in rows:
        pairs.setdefault(r["pair_id"], []).append(r)
    tier_pairs = [(tier((r["priority"], r["consensus"]) for r in rs), rs[0]["planted_tier"]) for rs in pairs.values()]
    golden_tiers = {"consensus": Counter(c for c, _ in tier_pairs), "planted": Counter(p for _, p in tier_pairs)}
    kappa = cohen_kappa([r["annotator_a"] for r in rows], [r["annotator_b"] for r in rows]) if rows else None
    agree_planted = sum(r["consensus"] == r["planted"] for r in rows) / len(rows) if rows else None
    r_items, r_ratings = realism()
    rated = [v for v in r_ratings.values() if len(v) == 2]
    plausible = sum(x["plausible"] for v in rated for x in v.values()) / (2 * len(rated)) if rated else None

    hard = [
        ("Schema validity of all records", *gate(tests, "test_schema_validity")),
        ("Story boxes ≤ 120 words", *gate(tests, "test_story_boxes")),
        ("ESCO URIs exist in cache", *gate(tests, "test_need_skill_uris")),
        ("PII scanner positive control (7 planted: email, 4 phone formats, ID, IBAN)", *gate(tests, "test_pii_scanner_positive_control")),
        ("No real PII anywhere (0 hits)", *gate(tests, "test_no_real_pii_anywhere")),
        ("Identity-leak detector positive control", *gate(tests, "test_identity_leak_detector")),
        ("No identity in matching records", *gate(tests, "test_matching_records_carry_no_identity")),
        ("Reproducibility: regenerate twice, byte-identical", *gate(tests, "test_reproducible")),
        ("Quotas match pre-registration", *gate(tests, "test_quotas")),
        ("Planted cases exist and are labelled", *gate(tests, "test_planted_cases")),
        ("Golden set complete (20 pairs, all labels)", *gate(tests, "test_golden_set_complete")),
        ("Golden set frozen to its items (sha256)", *gate(tests, "test_golden_set_frozen")),
    ]
    targets = [
        ("Diversity: no language/background < 10%", "pass" if min_share[1] >= 0.10 else "fail",
         f"smallest: {min_share[0]} = {100 * min_share[1]:.1f}%"),
        ("Label reliability: Cohen's κ ≥ 0.6", "proxy" if kappa is not None else "pending",
         f"κ = {kappa:.2f} between two AI proxies sharing one rubric; says little about human reliability. Human labels pending." if kappa is not None else "annotation not run"),
        ("Realism: ≥ 90% of 20 stories plausible", "proxy" if plausible is not None else "pending",
         f"{100 * plausible:.0f}% (AI proxy raters; human rating pending)" if plausible is not None else "rating not run"),
    ]

    heroes = []
    nordsense = next(n for n in w["needs"] if n["need_id"] == "N-014")
    for cid in ("J-001", "J-002", "J-003"):
        t = pc["heroes"][cid]
        heroes.append({"cid": cid, "story": js[cid]["story"], "attrs": attrs[cid], "labels": t["labels"], "tier": TIER_NAMES[t["tier"]]})
    twin = next(c for c in claims if c["id"] == "iot_alerts.supported.1")

    env = Environment(autoescape=True)
    env.filters["pct"] = lambda x: f"{100 * x:.0f}%"
    html = env.from_string((ROOT / "reports" / "f0.html.j2").read_text(encoding="utf-8")).render(
        css=(ROOT / "reports" / "base.css").read_text(encoding="utf-8"),
        hard=hard, targets=targets, tests=tests, pc=pc, tier_names=TIER_NAMES,
        n_companies=len(w["companies"]), n_needs=len(w["needs"]), n_people=len(w["jobseekers"]),
        n_claims=len(claims), n_unique=len(unique_texts), n_used=len(used_claims),
        lang_bars=bars(langs, ["en", "fi", "sv", "ar"]), bg_bars=bars(bgs), level_bars=bars(levels, ["supported", "partial", "none"]),
        tier_bars=bars(Counter(pc["tier_mix_total"]), list(TIER_NAMES), TIER_NAMES), pc_direct=pc_direct,
        tier_bars_direct=bars(Counter(pc_direct["tier_mix_total"]), list(TIER_NAMES), TIER_NAMES),
        edu_bars=bars(edu), gap_bars=bars(gap), golden_tiers=golden_tiers,
        related_n=len(related_rows), related_kept=sum(r["consensus"] == "partial" for r in related_rows),
        style=Counter(a["style"] for a in w["attributes"]), generic=sum("generic" in a["planted_tags"] for a in w["attributes"]),
        thin=sum("thin" in a["planted_tags"] for a in w["attributes"]),
        nordsense=nordsense, heroes=heroes, twin=twin, rows=rows, kappa=kappa, agree_planted=agree_planted, diffs=diffs, diffs_related=sum(r["via"] == "related" for r in diffs),
        r_items=r_items, r_ratings=r_ratings,
    )
    (ROOT / "reports" / "f0.html").write_text(html, encoding="utf-8", newline="\n")
    md = ["# Verification report — F0", "", "| Check | Kind | Status | Detail |", "|---|---|---|---|"]
    md += [f"| {n} | hard | {s.upper()} | {d} |" for n, s, d in hard]
    md += [f"| {n} | target | {s.upper()} | {d} |" for n, s, d in targets]
    (ROOT / "verification_report.md").write_text("\n".join(md) + "\n", encoding="utf-8", newline="\n")
    print("wrote reports/f0.html and verification_report.md")


if __name__ == "__main__":
    build()
