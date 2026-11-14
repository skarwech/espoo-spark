"""F2 Pool Preview: anonymised aggregates of how much of a confirmed need the registered pool covers (method.md §7).
Same engine as Match Lens (d1 tiers); counts rounded to the nearest 5, "fewer than 5" below 5, no person-level data, groups only by
company-stated constraints. Pure functions: the same need version gives the same payload apart from the timestamp."""
import math

LEVEL = {"A1": 1, "A2": 2, "B1": 3, "B2": 4, "C1": 5, "C2": 6}
PRESENCE = {"remote": 0, "hybrid": 1, "onsite": 2}
COVERED = ("strong", "worth")
KEYS = ("need_id", "version", "registered_so_far", "as_of", "framing", "engine", "counts", "sentence", "narrowing")
COUNT_KEYS = ("evidence_for_most_must_haves", "and_start_window_and_language", "and_work_mode")


def bucket(n: int) -> int | str:
    """Half-up rounding to the nearest 5; below 5 is suppressed."""
    return "fewer than 5" if n < 5 else int(5 * math.floor(n / 5 + 0.5))


def start_ok(need: dict, cons: dict) -> bool:
    return cons["start"] <= need["constraints"]["start"]


def language_ok(need: dict, cons: dict) -> bool:
    have = {l["lang"]: LEVEL[l["level"]] for l in cons["languages"]}
    return all(have.get(l["lang"], 0) >= LEVEL["B2"] for l in need["languages"] if l["required"])


def work_mode_ok(need: dict, cons: dict) -> bool:
    return PRESENCE[need["constraints"]["onsite"]] <= PRESENCE[cons["work_mode"]]


def groups(need: dict, tiers: dict, seekers: dict) -> tuple[list, list, list]:
    a = [c for c, t in tiers.items() if t in COVERED]
    b = [c for c in a if start_ok(need, seekers[c]) and language_ok(need, seekers[c])]
    return a, b, [c for c in b if work_mode_ok(need, seekers[c])]


def phrase(v: int | str) -> str:
    return "Fewer than 5" if v == "fewer than 5" else f"About {v}"


def narrowing(need: dict, tiers: dict, seekers: dict, retier) -> list[str]:
    """Names (no counts) of the two requirements whose relaxation would widen the pool most, when the pool is below 5."""
    a, b, _ = groups(need, tiers, seekers)
    if len(a) < 5:
        gain = []
        for o in (o for o in need["outcomes"] if o["priority"] == "must"):
            relaxed = {**need, "outcomes": [{**x, "priority": "nice"} if x["id"] == o["id"] else x for x in need["outcomes"]]}
            gain.append((sum(t in COVERED for t in retier(relaxed).values()), f"must-have outcome: {o['text']}"))
    elif len(b) < 5:
        base = [c for c in a]
        gain = [(sum(language_ok(need, seekers[c]) for c in base), "start window")]
        for l in (l for l in need["languages"] if l["required"]):
            relaxed = {**need, "languages": [x for x in need["languages"] if x["lang"] != l["lang"]]}
            gain.append((sum(start_ok(need, seekers[c]) and language_ok(relaxed, seekers[c]) for c in base), f"required language: {l['lang']}"))
    else:
        return []
    return [name for _, name in sorted(gain, key=lambda g: -g[0])[:2]]


def preview(need: dict, tiers: dict, seekers: dict, as_of: str, engine: str, retier) -> dict:
    a, b, c = groups(need, tiers, seekers)
    counts = dict(zip(COUNT_KEYS, (bucket(len(a)), bucket(len(b)), bucket(len(c)))))
    va, vb = counts["evidence_for_most_must_haves"], counts["and_start_window_and_language"]
    sentence = f"{phrase(va)} registered jobseekers show evidence for most of your must-haves"
    sentence += "." if va == "fewer than 5" else f"; {phrase(vb).lower()} can also start in your window and meet your language requirement."
    out = {"need_id": need["need_id"], "version": need["version"], "registered_so_far": len(seekers), "as_of": as_of,
           "framing": "Registered so far, not a promise: people register until the event and every conversation is a person's choice.",
           "engine": engine, "counts": counts, "sentence": sentence, "narrowing": narrowing(need, tiers, seekers, retier)}
    assert tuple(out) == KEYS
    return out
