"""Golden set (method.md §2). Usage:
  python eval/golden.py select   -> eval/golden_items.json (blinded, for annotators), eval/realism_items.json, eval/golden_key.json
  python eval/golden.py merge    -> eval/golden.csv from eval/labels/{annotator_a,annotator_b,adjudicator}.json
  python eval/golden.py freeze   -> eval/labels/FROZEN.json (sha256 of items + key); select refuses once frozen
"""
import csv
import hashlib
import json
import random
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "data"))
from truth import load, pair_truth  # noqa: E402

EVAL = ROOT / "eval"
HEROES = [("N-014", "J-001"), ("N-014", "J-002"), ("N-014", "J-003")]
TARGET = {"strong": 5, "worth": 5, "stretch": 5, "none": 5}
LABELS = ("supported", "partial", "none")


def cohen_kappa(a: list[str], b: list[str]) -> float:
    n = len(a)
    po = sum(x == y for x, y in zip(a, b)) / n
    ca, cb = Counter(a), Counter(b)
    pe = sum(ca[k] * cb[k] for k in LABELS) / n ** 2
    return 1.0 if pe == 1 else (po - pe) / (1 - pe)


FROZEN = EVAL / "labels" / "FROZEN.json"
FROZEN_FILES = ("golden_items.json", "golden_key.json", "realism_items.json", "golden.csv",
                "labels/annotator_a.json", "labels/annotator_b.json", "labels/adjudicator.json")


def digests() -> dict[str, str]:
    return {f: hashlib.sha256((EVAL / f).read_bytes()).hexdigest() for f in FROZEN_FILES}


def freeze():
    if FROZEN.exists():
        sys.exit("already frozen; a frozen golden set is never re-blessed")
    FROZEN.write_text(json.dumps({"frozen": "2026-11-13", "sha256": digests()}, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(f"froze {FROZEN_FILES}")


def select():
    if FROZEN.exists():
        sys.exit("golden set is frozen (eval/labels/FROZEN.json); labels are tied to these exact items")
    w, rng = load(), random.Random("golden:7")
    truth = pair_truth(w)
    arch = json.loads((ROOT / "data" / "archetypes.json").read_text(encoding="utf-8"))
    dom_of = {a["id"]: a["domain"] for a in arch["archetypes"]}
    need_dom = {nid: dom_of[o["o1"]] for nid, o in w["planted"]["outcomes"].items()}
    attrs = {a["candidate_id"]: a for a in w["attributes"]}
    lang = {j["candidate_id"]: j["story"]["lang"] for j in w["jobseekers"]}
    chosen = list(HEROES)
    count = lambda f: Counter(f(p) for p in chosen)
    for t in TARGET:
        pool = sorted(p for p, v in truth.items() if v["tier"] == t and p[1] not in {"J-001", "J-002", "J-003"})
        rng.shuffle(pool)
        # harder 'none'/'stretch' pairs: same domain as the candidate's experience
        pool.sort(key=lambda p: t in ("none", "stretch") and need_dom[p[0]] != attrs[p[1]]["origin_domain"])
        while count(lambda p: truth[p]["tier"])[t] < TARGET[t]:
            used_c, used_n = {c for _, c in chosen}, count(lambda p: p[0])
            ok = [p for p in pool[:40] if p[1] not in used_c and used_n[p[0]] < 2]
            langs, styles = count(lambda p: lang[p[1]]), count(lambda p: attrs[p[1]]["style"])
            chosen.append(min(ok, key=lambda p: (langs[lang[p[1]]], styles[attrs[p[1]]["style"]])))
    langs, styles = count(lambda p: lang[p[1]]), count(lambda p: attrs[p[1]]["style"])
    assert all(langs[l] >= 3 for l in ("en", "fi", "sv", "ar")) and min(styles.values()) >= 8, (langs, styles)
    rng.shuffle(chosen)
    needs = {n["need_id"]: n for n in w["needs"]}
    stories = {j["candidate_id"]: j["story"] for j in w["jobseekers"]}
    items, key = [], []
    for i, (nid, cid) in enumerate(chosen, 1):
        pid = f"G{i:02d}"
        items.append({"pair_id": pid, "outcomes": [{k: o[k] for k in ("id", "text", "priority")} for o in needs[nid]["outcomes"]],
                      "story": {k: stories[cid][k] for k in ("lang", "problem", "did", "differently", "next")}})
        key.append({"pair_id": pid, "need_id": nid, "candidate_id": cid, "story_lang": lang[cid], "style": attrs[cid]["style"],
                    "planted_tier": truth[(nid, cid)]["tier"], "planted": truth[(nid, cid)]["labels"]})
    realism = random.Random("realism:7").sample(sorted(stories), 20)
    dump(EVAL / "golden_items.json", items)
    dump(EVAL / "golden_key.json", key)
    dump(EVAL / "realism_items.json", [{"story_id": f"R{i:02d}", "candidate_id": c, "story": stories[c]} for i, c in enumerate(realism, 1)])
    print(f"selected {len(items)} pairs, {sum(len(i['outcomes']) for i in items)} labels; langs {dict(langs)} styles {dict(styles)}")


def merge():
    if FROZEN.exists():
        sys.exit("golden set is frozen; merge would overwrite golden.csv")
    key = {k["pair_id"]: k for k in json.loads((EVAL / "golden_key.json").read_text(encoding="utf-8"))}
    items = json.loads((EVAL / "golden_items.json").read_text(encoding="utf-8"))
    want = {(it["pair_id"], o["id"]) for it in items for o in it["outcomes"]}

    def lab(who):
        rows = json.loads((EVAL / "labels" / f"{who}.json").read_text(encoding="utf-8"))["labels"]
        out = {(x["pair_id"], x["outcome_id"]): x for x in rows}
        assert len(out) == len(rows), f"{who}: duplicate labels"
        assert set(out) == want, f"{who}: labels do not cover exactly the golden items"
        return out

    a, b = lab("annotator_a"), lab("annotator_b")
    adj = {(x["pair_id"], x["outcome_id"]): x for x in json.loads((EVAL / "labels" / "adjudicator.json").read_text(encoding="utf-8"))["decisions"]}
    assert set(adj) == {k for k in want if a[k]["label"] != b[k]["label"]}, "adjudicator must decide exactly the disagreements"
    rows = []
    for it in items:
        k = key[it["pair_id"]]
        for o in it["outcomes"]:
            ab = (it["pair_id"], o["id"])
            la, lb = a[ab]["label"], b[ab]["label"]
            cons = la if la == lb else adj[ab]["label"]
            rows.append({"pair_id": it["pair_id"], "need_id": k["need_id"], "candidate_id": k["candidate_id"], "outcome_id": o["id"],
                         "priority": o["priority"], "story_lang": k["story_lang"], "style": k["style"], "planted_tier": k["planted_tier"],
                         "planted": k["planted"][o["id"]], "annotator_a": la, "annotator_b": lb, "consensus": cons,
                         "adjudicated": la != lb, "note": adj[ab]["reason"] if la != lb else ""})
    with (EVAL / "golden.csv").open("w", newline="", encoding="utf-8") as f:
        wr = csv.DictWriter(f, fieldnames=list(rows[0]))
        wr.writeheader()
        wr.writerows(rows)
    k = cohen_kappa([r["annotator_a"] for r in rows], [r["annotator_b"] for r in rows])
    print(f"wrote {len(rows)} rows; kappa A-B = {k:.3f}")


def dump(path: Path, obj):
    path.write_text(json.dumps(obj, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    assert cohen_kappa(["supported", "none", "partial"], ["supported", "none", "partial"]) == 1.0
    {"select": select, "merge": merge, "freeze": freeze}[sys.argv[1]]()
