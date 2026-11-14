"""Engine pipeline: extract -> retrieve -> judge -> lens. Outputs in data/engine/. Usage: python -m engine.run <step>

  extract     all 150 pool stories and all twins (F4)
  judge       golden + hero candidates against all 25 needs; twins and their sources against own-domain needs (F5, F6)
  lens        coverage grid + tier for every judged candidate x all 25 needs (F7)
  stability   golden candidates re-judged with the cache bypassed; nothing stored in the cache (F6 stability)
  injection   judge-level injection fixture: clean vs injected unit, shown for every own-domain outcome (F6, method.md §6 note)
"""
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from engine import embed, extract, judge, lens, llm

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / ("engine" if llm.ENGINE == "v1" else f"engine_{llm.ENGINE}")  # d1, k1 -> data/engine_d1, data/engine_k1 (§7)
WORKERS = int(__import__("os").environ.get("SPARK_WORKERS", "6"))
HEROES = ("J-001", "J-002", "J-003")


def read(name: str, default=None):
    p = OUT / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default


def write(name: str, obj) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(json.dumps(obj, indent=1, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def world() -> dict:
    load = lambda p: json.loads((ROOT / p).read_text(encoding="utf-8"))
    js, twins = load("data/out/jobseekers.json"), load("eval/fixtures/twins.json")
    arch = {a["id"]: a["domain"] for a in load("data/archetypes.json")["archetypes"]}
    planted = load("data/out/planted.json")
    return {"stories": {j["candidate_id"]: j["story"] for j in js} | {t["id"]: t["story"] for t in twins},
            "twins": twins, "needs": load("data/out/needs.json"),
            "need_domain": {nid: arch[o["o1"]] for nid, o in planted["outcomes"].items()},
            "origin": {a["candidate_id"]: a["origin_domain"] for a in load("data/out/attributes.json")},
            "pool": sorted(j["candidate_id"] for j in js),
            "golden": sorted({k["candidate_id"] for k in load("eval/golden_key.json")} | set(HEROES))}


def matched(w: dict) -> list[str]:
    """Candidates judged against all needs: golden + heroes for v1; every registered person for d1 and k1 (Pool Preview, §7)."""
    return w["golden"] if llm.ENGINE == "v1" else w["pool"]


def outcomes(needs: list[dict]) -> list[dict]:
    return [{"key": f"{n['need_id']}/{o['id']}", "text": o["text"]} for n in needs for o in n["outcomes"]]


def parallel(fn, items) -> list:
    """Results in input order; a failed item yields its LLMError instead of aborting the whole run."""
    def safe(x):
        try:
            return fn(x)
        except Exception as e:  # recorded as a failure of this item; the run keeps going and exits non-zero
            return e if isinstance(e, llm.LLMError) else llm.LLMError(f"{type(e).__name__}: {e}")
    with ThreadPoolExecutor(WORKERS) as ex:
        return list(ex.map(safe, items))


def keep(results: dict, pairs) -> list[str]:
    """Store successful results; return the ids that failed."""
    failed = []
    for key, res in pairs:
        (failed.append(f"{key}: {res}") if isinstance(res, Exception) else results.__setitem__(key, res))
    return failed


def step_extract(w: dict) -> None:
    units = read("units.json", {})
    todo = sorted(s for s in w["stories"] if s not in units)
    print(f"extract: {len(todo)} stories to do, {len(units)} done")
    failed = keep(units, zip(todo, parallel(lambda s: extract.extract(s, w["stories"][s]), todo)))
    write("units.json", units)
    report(failed)


def own_needs(w: dict, source: str) -> list[dict]:
    return [n for n in w["needs"] if w["need_domain"][n["need_id"]] == w["origin"][source]]


def jobs(w: dict) -> list[tuple[str, str, list[dict]]]:
    """(run_id, story_id, needs). run_id = '<story>@all' or '<story>@own'."""
    out = [(f"{c}@all", c, w["needs"]) for c in matched(w)]
    for t in w["twins"]:
        own = own_needs(w, t["source"])
        out += [(f"{t['id']}@own", t["id"], own), (f"{t['source']}@own", t["source"], own)]
    return sorted({j[0]: j for j in out}.values())


def step_judge(w: dict, *, stability: bool = False) -> None:
    units = read("units.json", {})
    todo = [j for j in jobs(w) if j[0].endswith("@all") and j[1] in w["golden"]] if stability else jobs(w)
    retrieval = read("retrieval.json", {"version": embed.version(), "runs": {}})
    for run_id, sid, needs in todo:  # embedding is sequential (CPU model, deterministic)
        if run_id not in retrieval["runs"]:
            retrieval["runs"][run_id] = embed.retrieve(outcomes(needs), units[sid]["units"])
    retrieval["version"] = embed.version()
    write("retrieval.json", retrieval)
    done = {} if stability else read("judgments.json", {})
    todo = [j for j in todo if j[0] not in done]
    print(f"{'stability' if stability else 'judge'}: {len(todo)} runs")
    call = lambda j: judge.judge(units[j[1]]["units"], outcomes(j[2]), retrieval["runs"][j[0]],
                                 use_cache=not stability, store=not stability)
    failed = keep(done, zip((j[0] for j in todo), parallel(call, todo)))
    write("stability.json" if stability else "judgments.json", done)
    report(failed)


def report(failed: list[str]) -> None:
    if failed:
        print(f"{len(failed)} failed (re-run this step to retry; cached calls replay for free):", *failed[:5], sep="\n  ")
        sys.exit(1)


def step_lens(w: dict) -> None:
    units, judgments = read("units.json"), read("judgments.json")
    hubs = lens.hub_skills(units, w["pool"]) if llm.A1 else frozenset()  # A1.3, opt-in
    if llm.A1:
        write("hubs.json", {"rule": "tagged on more than 1/8 of the pool stories' evidence units (method.md A1.3)",
                            "hubs": sorted(lens.esco_label(s) for s in hubs)})
    out = [lens.pair(n, c, units[c]["units"], judgments[f"{c}@all"], hubs=hubs) for c in matched(w) for n in w["needs"]]
    write("lens.json", out)
    print(f"lens: {len(out)} pairs; hub skills: {', '.join(sorted(lens.esco_label(s) for s in hubs)) or 'none'}")


def injection_case(w: dict, units: dict, retrieval: dict, t: dict) -> dict:
    """The injected sentence goes into the source's first evidence unit (or forms the only unit), shown first for every outcome."""
    src, did = t["source"], w["stories"][t["source"]]["did"]
    if not t["story"]["did"].startswith(did):
        raise ValueError(f"{t['id']}: twin did box does not extend its source")
    inj = t["story"]["did"][len(did):].strip()
    clean = units[src]["units"]
    uid = clean[0]["id"] if clean else f"{src}.inj"
    injected = [{**u, "text": f"{u['text']} {inj}"} if u["id"] == uid else u for u in clean] or \
        [{"id": uid, "box": "did", "start": -1, "end": -1, "text": inj, "skills": []}]
    outs = outcomes(own_needs(w, src))
    base = retrieval["runs"][f"{src}@own"]
    shown = {o["key"]: [{"unit_id": uid}] + [r for r in base.get(o["key"], []) if r["unit_id"] != uid] for o in outs}
    return {"source": src, "unit_id": uid, "injected_text": inj, "injected_units": injected,
            "clean": judge.judge(clean, outs, shown if clean else {}), "injected": judge.judge(injected, outs, shown)}


def step_injection(w: dict) -> None:
    units, retrieval = read("units.json"), read("retrieval.json")
    todo = [t for t in w["twins"] if t["kind"] == "injection"]
    print(f"injection: {len(todo)} fixtures")
    out = {}
    failed = keep(out, zip((t["id"] for t in todo), parallel(lambda t: injection_case(w, units, retrieval, t), todo)))
    write("injection.json", out)
    report(failed)


if __name__ == "__main__":
    step = sys.argv[1] if len(sys.argv) > 1 else ""
    steps = {"extract": step_extract, "judge": step_judge, "lens": step_lens,
             "stability": lambda w: step_judge(w, stability=True), "injection": step_injection}
    if step not in steps:
        sys.exit(__doc__)
    try:
        steps[step](world())
    finally:
        print(f"LLM spend this run: ${llm.spent():.2f}")
