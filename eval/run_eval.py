"""F4-F7 targets as pre-registered in docs/method.md §6. Usage: python eval/run_eval.py -> eval/results/targets_f4_f7.json"""
import csv
import json
import math
import random
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "data"))
from engine import lens  # noqa: E402
from engine.tiers import tier  # noqa: E402
from truth import related_map  # noqa: E402

RANK = {"none": 0, "stretch": 1, "worth": 2, "strong": 3}
NOTE = "added 2026-11-14 after results; context only, not a registered measure"
# Hero claims are hand-written (not in the bank); their evidence spans for Recall@3:
HERO_SPANS = {("J-001", "iot_pipeline"): "Our team worked with data pipelines", ("J-002", "iot_alerts"): "I rebuilt the alert rules",
              ("J-002", "iot_pipeline"): "buffering on the edge gateways", ("J-003", "log_erp"): "connected our warehouse system to the ERP",
              ("J-003", "log_routes"): "re-planning routes for 120 trucks"}


def j(p: str):
    return json.loads((ROOT / p).read_text(encoding="utf-8"))


def share(hits: list[bool]) -> float | None:
    return sum(hits) / len(hits) if hits else None


def boot_ci(items: list, stat=share) -> list[float] | None:
    """95% bootstrap CI, 2,000 resamples, seed 11 (method.md §3); items are resampled independently."""
    if not items:
        return None
    rng = random.Random(11)
    vals = sorted(stat([rng.choice(items) for _ in items]) for _ in range(2000))
    return [round(vals[50], 4), round(vals[1949], 4)]


def target(value, threshold: float | None, n: int, op: str = ">=", note: str = "", kind: str = "target", mitigation: str = "",
           ci: list[float] | None = None) -> dict:
    """kind: target | proxy (reference is AI-proxy or planted labels; never shown green, method.md:11) | sensitivity (no threshold)."""
    ok = None if value is None or threshold is None else (value >= threshold if op == ">=" else value <= threshold)
    return {"value": value, "threshold": threshold, "op": op, "n": n, "pass": ok, "note": note, "kind": kind,
            "mitigation": mitigation if ok is False else "", "ci": ci}


def non_none_agreement(pairs: list[tuple[str, str]]) -> str:
    ev = [a == b for a, b in pairs if "none" not in (a, b) or a != b]
    return f"agreement where either label is non-none: {share(ev):.3f} (n = {len(ev)}; {NOTE})" if ev else ""


def random3(n_units: int, n_gold: int) -> float:
    """Chance that 3 units drawn at random from the story include a gold unit."""
    return 1.0 if n_units <= 3 else 1 - math.comb(n_units - n_gold, 3) / math.comb(n_units, 3)


def main():
    units, judg, retr = j("data/engine/units.json"), j("data/engine/judgments.json"), j("data/engine/retrieval.json")["runs"]
    planted, bank = j("data/out/planted.json"), {c["id"]: c for d in j("data/archetypes.json")["domains"]
                                                 for c in j(f"data/bank/{d}.json")["claims"]}
    arch_skills = {a["id"]: a["skills"] for a in j("data/archetypes.json")["archetypes"]}
    label_uri = {s["label"].lower(): u for u, s in j("data/esco_cache.json")["skills"].items()}
    attrs = {a["candidate_id"]: a for a in j("data/out/attributes.json")}
    js = {x["candidate_id"]: x["story"] for x in j("data/out/jobseekers.json")}
    twins = j("eval/fixtures/twins.json")
    needs = {n["need_id"]: n for n in j("data/out/needs.json")}
    rel = related_map(planted["related"])
    rows = list(csv.DictReader((ROOT / "eval" / "golden.csv").open(encoding="utf-8")))
    tags = lambda sid: {s for u in units[sid]["units"] for s in u["skills"]}
    res = {}

    # F4 tag precision / recall (planted reference, proxy)
    sample = random.Random("eval:f4:7").sample(sorted(c for c in attrs if "hero" not in attrs[c]["planted_tags"]), 30)
    per = []  # (tp, fp, fn) per story
    for c in sample:
        ref = {label_uri[l.lower()] for cl in planted["claims"][c] if cl["level"] != "none" for l in arch_skills[cl["archetype"]]}
        pred = tags(c)
        per.append((len(pred & ref), len(pred - ref), len(ref - pred)))
    tp, fp, fn = map(sum, zip(*per))
    micro = lambda i: (lambda b: sum(x[0] for x in b) / max(1, sum(x[0] + x[i] for x in b)))  # i = 1: precision, 2: recall
    res["F4 tag precision (planted reference)"] = target(
        share([True] * tp + [False] * fp), 0.80, 30, kind="proxy", ci=boot_ci(per, micro(1)),
        note=f"tp {tp} / fp {fp} / fn {fn} over {tp + fp} predicted tags; CI resamples the 30 stories",
        mitigation="Diagnosis (added 2026-11-14 after results; reviewer judgement, not a registered measure): every false positive sits on a "
                   "did-box unit that overlaps a supported or partial planted claim. About 23 of 30 are skills the unit text does state but the "
                   "2–3-skill archetype reference omits (for example Python, data analysis), so precision against the planted reference is a lower "
                   "bound. The remaining ~7 are over-broad or wrong tags, for example ICT system integration (×3) on work that only uses a system "
                   "as a data source. Mitigation: proposed extraction amendment (ESCO scope notes in the prompt's skill list; a system only named "
                   "as a data source is not evidence of integrating it), pending approval and a full disclosed re-run. Threshold unchanged.")
    res["F4 tag recall (planted reference)"] = target(share([True] * tp + [False] * fn), 0.70, 30, kind="proxy", ci=boot_ci(per, micro(2)))
    jac, empty = [], 0
    for t in (t for t in twins if t["kind"] == "translation"):
        a, b = tags(t["id"]), tags(t["source"])
        empty += not (a | b)
        jac.append(len(a & b) / len(a | b) if a | b else 1.0)
    rest = [x for t, x in zip((t for t in twins if t["kind"] == "translation"), jac) if tags(t["id"]) | tags(t["source"])]
    mean = lambda v: sum(v) / len(v)
    res["F4 cross-language skill Jaccard (mean)"] = target(
        mean(jac) if jac else None, 0.80, len(jac), ci=boot_ci(jac, mean),
        note=f"{empty} pairs have no tags on either side and count as 1.0" +
             (f"; mean over the other {len(rest)}: {mean(rest):.3f} ({NOTE})" if rest else ""))

    # F5 Recall@3 on golden outcomes with consensus support
    def span(cid: str, claim: dict, lang: str, style: str) -> tuple[int, int] | None:
        text = HERO_SPANS.get((cid, claim["archetype"])) if claim["claim_id"].startswith("hero.") else \
            bank[claim["claim_id"]]["text"][lang][style]
        story = j_story(cid, lang)
        s = story["did"].find(text) if text else -1
        return (s, s + len(text)) if s >= 0 else None

    def j_story(sid: str, lang: str) -> dict:
        return js.get(sid) or next(t["story"] for t in twins if t["id"] == sid)

    def gold_hit(sid: str, cid: str, run: str, key: str, archetype: str, lang: str, style: str) -> tuple[str, float]:
        """(hit | miss | extraction miss, random-3 chance of a hit). §6: claim of the outcome's archetype or a related one."""
        claims = [cl for cl in planted["claims"][cid] if cl["archetype"] == archetype or archetype in rel.get(cl["archetype"], ())]
        spans = [sp for cl in claims if (sp := span(sid, cl, lang, style))]
        overlap = lambda u: u["box"] == "did" and any(u["start"] < e and s < u["end"] for s, e in spans)
        gold = {u["id"] for u in units[sid]["units"] if overlap(u)}
        if not gold:
            return "extraction miss", 0.0
        chance = random3(len(units[sid]["units"]), len(gold))
        return ("hit" if gold & {r["unit_id"] for r in retr[run][key]} else "miss"), chance

    def f5(out: list[tuple[str, float]], kind: str) -> dict:
        scored = [(o, c) for o, c in out if o != "extraction miss"]
        hits = [o == "hit" for o, _ in scored]
        small = sum(c == 1.0 for _, c in scored)
        chance = f"; in {small} of {len(scored)} cases any 3 of the story's units include an evidence unit; random-3 baseline " \
                 f"{sum(c for _, c in scored) / len(scored):.3f} ({NOTE})" if scored else ""
        return target(share(hits), 0.90, len(scored), kind=kind, ci=boot_ci(hits),
                      note=f"{len(out) - len(scored)} extraction misses excluded" + chance)

    out = []
    for r in rows:
        if r["consensus"] == "none":
            continue
        arch = planted["outcomes"][r["need_id"]][r["outcome_id"]]
        out.append(gold_hit(r["candidate_id"], r["candidate_id"], f"{r['candidate_id']}@all", f"{r['need_id']}/{r['outcome_id']}",
                            arch, js[r["candidate_id"]]["lang"], attrs[r["candidate_id"]]["style"]))
    res["F5 Recall@3 on golden supported/partial"] = f5(out, "proxy")  # population = AI-proxy consensus
    out = []
    for t in (t for t in twins if t["kind"] == "translation"):
        src = t["source"]
        for key in retr[f"{t['id']}@own"]:
            nid, oid = key.split("/")
            arch = planted["outcomes"][nid][oid]
            if needs[nid]["lang"] == "en" and any(cl["archetype"] == arch and cl["level"] != "none" for cl in planted["claims"][src]):
                out.append(gold_hit(t["id"], src, f"{t['id']}@own", key, arch, t["story"]["lang"], attrs[src]["style"]))
    res["F5 cross-language correct unit in top 3 (English outcome, translated story)"] = f5(out, "proxy")  # planted-span reference

    # F6 vs golden consensus
    lab = [(judg[f"{r['candidate_id']}@all"][f"{r['need_id']}/{r['outcome_id']}"]["label"], r["consensus"]) for r in rows]
    acc = [a == b for a, b in lab]
    res["F6 accuracy vs consensus (3-class)"] = target(share(acc), 0.80, len(lab), kind="proxy", ci=boot_ci(acc))
    fs = [b == "none" for a, b in lab if a == "supported"]
    res["F6 false-supported rate"] = target(share(fs), 0.10, len(fs), op="<=", kind="proxy", ci=boot_ci(fs))
    for kind, name in (("translation", "F6 language invariance"), ("style", "F6 style invariance")):
        pairs_ = [(judg[f"{t['id']}@own"][k]["label"], judg[f"{t['source']}@own"][k]["label"])
                  for t in twins if t["kind"] == kind for k in judg[f"{t['id']}@own"]]
        same = [a == b for a, b in pairs_]
        res[name] = target(share(same), 0.90, len(pairs_), note=non_none_agreement(pairs_), ci=boot_ci(same))
    stab_p = ROOT / "data" / "engine" / "stability.json"
    if stab_p.exists():
        stab = json.loads(stab_p.read_text(encoding="utf-8"))
        pairs_ = [(judg[run][k]["label"], v["label"]) for run, js_ in stab.items() for k, v in js_.items()]
        same = [a == b for a, b in pairs_]
        res["F6 stability (uncached re-run)"] = target(share(same), 0.95, len(pairs_), note=non_none_agreement(pairs_), ci=boot_ci(same))

    # F7 tier agreement with consensus tiers
    lens_pairs = {(p["need_id"], p["candidate_id"]): p for p in j("data/engine/lens.json")}
    pairs = {}
    for r in rows:
        pairs.setdefault((r["need_id"], r["candidate_id"]), []).append((r["priority"], r["consensus"]))
    direct = {k: lens.pair(needs[k[0]], k[1], units[k[1]]["units"], judg[f"{k[1]}@all"], use_adjacency=False)["tier"] for k in pairs}
    eng = {k: lens_pairs[k]["tier"] for k in pairs}
    cons = {k: tier(v) for k, v in pairs.items()}
    miss = [k for k in pairs if eng[k] != cons[k]]
    label = {(r["need_id"], r["candidate_id"], r["outcome_id"]): r["consensus"] for r in rows}
    judge_eq = [k for k in miss if all(judg[f"{k[1]}@all"][f"{k[0]}/{g['outcome_id']}"]["label"] == label[(k[0], k[1], g["outcome_id"])]
                                       for g in lens_pairs[k]["grid"])]
    pool_tags = Counter(lens.esco_label(s) for c in js for u in units[c]["units"] for s in u["skills"])
    via = Counter(g["path"]["from"] for k in miss for g in lens_pairs[k]["grid"] if g["path"])
    decomposition = (f"{len(miss)} of {len(pairs)} pairs disagree; engine above consensus in {sum(RANK[eng[k]] > RANK[cons[k]] for k in miss)}; "
                     f"adjacency changed the tier in {sum(eng[k] != direct[k] for k in miss)}; judge labels equal consensus on every outcome in "
                     f"{len(judge_eq)}. Adjacent paths in the disagreeing pairs (all priorities) start from: "
                     + ", ".join(f"{s} ×{n} (tagged on {pool_tags[s]} pool units)" for s, n in via.most_common()) + f" ({NOTE})")
    exact, within = (lambda e: [RANK[e[k]] == RANK[cons[k]] for k in pairs]), (lambda e: [abs(RANK[e[k]] - RANK[cons[k]]) <= 1 for k in pairs])
    res["F7 tier agreement exact (with adjacency, registered)"] = target(
        share(exact(eng)), 0.80, len(pairs), kind="proxy", note=decomposition, ci=boot_ci(exact(eng)),
        mitigation="Consensus labels exclude adjacency by design (method.md §1). Adjacency can only raise the engine tier, never lower it, so "
                   "where the judge matches consensus (6 of the 7 disagreements) every adjacent path puts the engine above consensus; this target "
                   "partly measures that design choice. Mitigation: proposed adjacency amendment (no same-skill adjacency on hub skills tagged "
                   "across many outcomes; an adjacent path alone cannot lift a pair from none to worth), pending approval and a full disclosed "
                   "re-run. Threshold unchanged.")
    res["F7 tier agreement within one (with adjacency, registered)"] = target(share(within(eng)), 0.95, len(pairs), kind="proxy",
                                                                              ci=boot_ci(within(eng)))
    hubs = lens.hub_skills(units, js) if (ROOT / "data" / "engine" / "hubs.json").exists() else frozenset()  # A1.3 applies to the amended run
    occupation = {k: lens.pair(needs[k[0]], k[1], units[k[1]]["units"], judg[f"{k[1]}@all"], occupation=True, hubs=hubs)["tier"] for k in pairs}
    for label_, e, why in (("direct labels only", direct, "registered as also reported without adjacency (method.md §6); no threshold"),
                           ("adjacency incl. shared occupation", occupation,
                            "registered sensitivity analysis (method.md §6: shared-occupation links are not adjacency); no threshold")):
        res[f"F7 tier agreement exact ({label_})"] = target(share(exact(e)), None, len(pairs), kind="sensitivity", note=why, ci=boot_ci(exact(e)))
        res[f"F7 tier agreement within one ({label_})"] = target(share(within(e)), None, len(pairs), kind="sensitivity", note=why,
                                                                ci=boot_ci(within(e)))

    lp = ROOT / "data" / "llm_cache" / "_ledger.jsonl"
    ledger = [json.loads(l) for l in lp.read_text(encoding="utf-8").splitlines() if l] if lp.exists() else []
    meta = {"llm_calls": len(ledger), "llm_usd": round(sum(x["usd"] for x in ledger), 2),
            "served_by": sorted({x["served_by"] for x in ledger}), "refusals": sum(x["stop"] == "refusal" for x in ledger),
            "units_invalid_dropped": sum(u["invalid_dropped"] for u in units.values()),
            "judge_rejected": sum(bool(v.get("rejected")) for r in judg.values() for v in r.values()),
            "embedding": j("data/engine/retrieval.json")["version"],
            "hubs": sorted(lens.esco_label(s) for s in hubs),
            "tokens": {k: sum(x["usage"].get(k) or 0 for x in ledger) for k in
                       ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")}}
    out_p = ROOT / "eval" / "results" / "targets_f4_f7.json"
    out_p.parent.mkdir(parents=True, exist_ok=True)
    out_p.write_text(json.dumps({"targets": res, "meta": meta}, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    for k, v in res.items():
        val = "n/a" if v["value"] is None else f"{v['value']:.3f}"
        ci = f" CI {v['ci'][0]:.2f}-{v['ci'][1]:.2f}" if v["ci"] else ""
        print(f"{'PASS' if v['pass'] else 'MISS' if v['pass'] is False else '----'}  [{v['kind']}] {k}: {val}{ci} ({v['op']} {v['threshold']}, n={v['n']}) {v['note'][:90]}")
    print(json.dumps(meta))


if __name__ == "__main__":
    main()
