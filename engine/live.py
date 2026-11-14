"""Live engine d1 for step 3: judge a new Need Card over the whole registered pool, and run a newly captured Skill Story
through extraction, retrieval, judge and tiers. Same functions as the batch pipeline (method.md §7); results are cached on disk."""
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from engine import embed, extract, judge, lens, llm, run

LIVE = run.OUT / "live"


def _need_ready() -> None:
    if llm.ENGINE == "v1":
        raise RuntimeError("the live engine runs on a step-3 profile (SPARK_ENGINE=k1, method.md §7)")


def _read(p: Path):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def _write(p: Path, obj) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, indent=1, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def pool_units() -> dict:
    return run.read("units.json")


def judge_need(card: dict) -> dict:
    """{candidate_id: judgments} for every registered person against this need version (cached per need id and version)."""
    _need_ready()
    path = LIVE / f"{card['need_id']}-v{card['version']}.json"
    if (cached := _read(path)) is not None:
        return cached["judgments"]
    units, w = pool_units(), run.world()
    outs = run.outcomes([card])

    retrieved = {cid: embed.retrieve(outs, units[cid]["units"]) for cid in w["pool"]}  # embedding is sequential (CPU model)
    with ThreadPoolExecutor(run.WORKERS) as ex:
        res = dict(ex.map(lambda cid: (cid, judge.judge(units[cid]["units"], outs, retrieved[cid])), w["pool"]))
    _write(path, {"need_id": card["need_id"], "version": card["version"], "embedding": embed.version(), "judgments": res})
    return res


def tiers_for(card: dict, judgments: dict | None = None) -> dict:
    """{candidate_id: tier} for a need over the pool. Stored d1 judgments serve the 25 synthetic needs and their variants."""
    units, w = pool_units(), run.world()
    if judgments is None:
        stored = run.read("judgments.json")
        judgments = {c: stored[f"{c}@all"] for c in w["pool"]} if card["need_id"] in {n["need_id"] for n in w["needs"]} else judge_need(card)
    return {c: lens.pair(card, c, units[c]["units"], judgments[c])["tier"] for c in w["pool"]}


def process_story(story_id: str, story: dict) -> dict:
    """A new Skill Story through d1: evidence units, then a tier for every one of the 25 needs (cached per story id)."""
    _need_ready()
    path = LIVE / f"story-{story_id}.json"
    if (cached := _read(path)) is not None:
        return cached
    w = run.world()
    ex = extract.extract(story_id, story)
    outs = run.outcomes(w["needs"])
    retrieved = embed.retrieve(outs, ex["units"])
    judgments = judge.judge(ex["units"], outs, retrieved)
    pairs = [lens.pair(n, story_id, ex["units"], judgments) for n in w["needs"]]
    out = {"story_id": story_id, "lang": story["lang"], "units": ex["units"], "thin": ex["thin"],
           "tiers": {p["need_id"]: p["tier"] for p in pairs}, "judgments": judgments}
    _write(path, out)
    return out
