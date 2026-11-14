"""Sensitivity analysis X1 (docs/method.md): two non-Claude annotators label the frozen golden set via Verda Inference.

Usage:
  python eval/xfamily.py models     list the model ids the key can use (no labels produced)
  python eval/xfamily.py annotate   Kimi + DeepSeek label the golden set and rate realism -> eval/labels_xfamily/
  python eval/xfamily.py score      kappas, judge accuracy per annotator, cross-family reference -> eval/results/xfamily.json

Keys: VERDA_INFERENCE_API_KEY_KIMI and VERDA_INFERENCE_API_KEY_DEEPSEEK (one per model; VERDA_INFERENCE_API_KEY as a fallback) from
the environment or spark/.env; never printed. VERDA_INFERENCE_BASE_URL, if set, must name Verda's documented host, otherwise nothing
is sent. Responses are cached by request hash in eval/labels_xfamily/cache/, so a re-run replays them. Optional VERDA_KIMI_MODEL /
VERDA_DEEPSEEK_MODEL pin the model ids.
"""
import csv
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "eval"))
from engine.tiers import tier  # noqa: E402
from golden import LABELS, cohen_kappa  # noqa: E402

BASE = "https://inference.verda.com/v1"  # pinned (Verda docs, OpenAI compatibility); never taken from the environment
OUT = ROOT / "eval" / "labels_xfamily"
CACHE = OUT / "cache"
# USD per million tokens from verda.com/inference (2026-11-14); the page shows two Kimi price sets, the higher one is used here.
PRICE = {"kimi": (3.00, 15.00), "deepseek": (0.15, 0.60)}
RANK = {"none": 0, "stretch": 1, "worth": 2, "strong": 3}


def setting(name: str) -> str:
    if os.environ.get(name):
        return os.environ[name]
    env = ROOT / ".env"
    for line in env.read_text(encoding="utf-8").splitlines() if env.exists() else []:
        key, _, value = line.strip().partition("=")
        if key.strip() == name:
            return value.strip().strip('"').strip("'")
    return ""


def api_key(family: str) -> str:
    key = setting(f"VERDA_INFERENCE_API_KEY_{family.upper()}") or setting("VERDA_INFERENCE_API_KEY")
    if not key:
        sys.exit(f"No Verda key for {family}: add VERDA_INFERENCE_API_KEY_{family.upper()}=... to spark/.env (gitignored).")
    custom = setting("VERDA_INFERENCE_BASE_URL").rstrip("/")
    if custom and custom not in (BASE, BASE.removesuffix("/v1")):
        sys.exit("VERDA_INFERENCE_BASE_URL does not name Verda's documented host (inference.verda.com); nothing was sent.")
    return key


def client(family: str) -> httpx.Client:
    return httpx.Client(base_url=BASE, headers={"Authorization": f"Bearer {api_key(family)}"}, timeout=httpx.Timeout(900.0, connect=30.0))


def model_ids(family: str) -> list[str]:
    with client(family) as c:
        r = c.get("/models")
        r.raise_for_status()
        return sorted(m["id"] for m in r.json().get("data", []))


def pick(ids: list[str], family: str, env: str, prefer: str | None = None) -> str:
    if os.environ.get(env):
        return os.environ[env]
    hits = [i for i in ids if family in i.lower()]
    if prefer in hits:
        return prefer
    if len(hits) != 1:
        sys.exit(f"{len(hits)} {family} models available ({', '.join(hits) or 'none'}); set {env} in spark/.env to choose one.")
    return hits[0]


def rubric() -> str:
    text = (ROOT / "docs" / "method.md").read_text(encoding="utf-8")
    return text[text.index("## 1."):text.index("## 2.")].strip()


def prompt_labels(items: list[dict]) -> str:
    """Task 1 of the original annotator instruction, with the files inlined (X1 protocol)."""
    return f"""You are an annotator building a golden set for "Spark", a needs-first matchmaking prototype (synthetic data).

The golden items below hold 20 pairs. Each pair = one company need (2-3 outcomes, each with a priority) and one jobseeker's
Skill Story (4 boxes, in en/fi/sv/ar). For EVERY outcome of EVERY pair, label how well the story supports that outcome:
supported / partial / none, strictly following the labelling guidelines (section 1) below. Key points:
- supported: first-person singular action that directly addresses the outcome AND a concrete method/tool or result.
- partial: relevant but (a) no personal role ("we", team, passive), (b) action without method or result, or (c) closely related work in a different context.
- none: generic self-description, aspiration, or unrelated work. The "next" box is aspiration and never evidence.
- Language, polish, grammar and spelling must NOT affect the label. Read Finnish, Swedish and Arabic stories in the original.
Give a reason of at most 20 words for each label.

Return only a JSON object with exactly this shape:
{{"labels": [{{"pair_id": "G01", "outcome_id": "o1", "label": "supported|partial|none", "reason": "..."}}]}}

LABELLING GUIDELINES (section 1):
{rubric()}

GOLDEN ITEMS (eval/golden_items.json):
{json.dumps(items, ensure_ascii=False, indent=1)}"""


def prompt_realism(items: list[dict]) -> str:
    return f"""You are a rater for "Spark", a needs-first matchmaking prototype (synthetic data).
The 20 stories below are Skill Stories. For each, decide whether a real jobseeker in Finland could plausibly have written it
(content and situation; ignore polish and language level). plausible = true/false, reason at most 20 words.

Return only a JSON object with exactly this shape:
{{"realism": [{{"story_id": "R01", "plausible": true, "reason": "..."}}]}}

STORIES (eval/realism_items.json):
{json.dumps(items, ensure_ascii=False, indent=1)}"""


def chat(model: str, family: str, task: str, prompt: str) -> dict:
    """One cached chat completion; returns the parsed JSON object from the answer."""
    key = hashlib.sha256(json.dumps([BASE, model, task, prompt], ensure_ascii=False).encode()).hexdigest()
    path = CACHE / f"{family}-{task}-{key[:16]}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))["output"]
    body = {"model": model, "messages": [{"role": "user", "content": prompt}], "max_tokens": 32000,
            "response_format": {"type": "json_object"}}
    t0, resp = time.monotonic(), None
    with client(family) as c:
        for attempt in range(3):
            try:
                r = c.post("/chat/completions", json=body)
                if r.status_code == 400 and "response_format" in body:  # JSON mode not offered by this model: ask in the prompt only
                    body.pop("response_format")
                    continue
                r.raise_for_status()
                resp = r.json()
                break
            except (httpx.TimeoutException, httpx.TransportError, httpx.HTTPStatusError) as e:
                if attempt == 2 or (isinstance(e, httpx.HTTPStatusError) and e.response.status_code in (401, 403, 404)):
                    sys.exit(f"{family} {task}: {type(e).__name__} {getattr(getattr(e, 'response', None), 'status_code', '')}")
                time.sleep(5 * (attempt + 1))
    msg = resp["choices"][0]["message"]
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", (msg.get("content") or "").strip())
    output = json.loads(text[text.index("{"):text.rindex("}") + 1])
    usage = resp.get("usage", {})
    usd = (usage.get("prompt_tokens", 0) * PRICE[family][0] + usage.get("completion_tokens", 0) * PRICE[family][1]) / 1e6
    CACHE.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"model": resp.get("model", model), "task": task, "usage": usage, "usd_estimate": round(usd, 5),
                                "seconds": round(time.monotonic() - t0, 1), "output": output}, indent=1, ensure_ascii=False) + "\n",
                    encoding="utf-8", newline="\n")
    return output


def annotate() -> None:
    items = json.loads((ROOT / "eval" / "golden_items.json").read_text(encoding="utf-8"))
    realism = json.loads((ROOT / "eval" / "realism_items.json").read_text(encoding="utf-8"))
    want = {(p["pair_id"], o["id"]) for p in items for o in p["outcomes"]}
    for family, env, prefer in (("kimi", "VERDA_KIMI_MODEL", "kimi-k3-1-pro"), ("deepseek", "VERDA_DEEPSEEK_MODEL", None)):
        model = pick(model_ids(family), family, env, prefer)
        got = {}
        for task, p in (("labels", prompt_labels(items)), ("labels-retry", None)):
            missing = sorted(want - set(got))
            if not missing:
                break
            if p is None:  # re-ask once, only for what is missing or invalid
                p = prompt_labels(items) + f"\n\nLabel ONLY these (pair_id, outcome_id) items: {json.dumps(missing)}"
            for x in chat(model, family, task, p).get("labels", []):
                k = (x.get("pair_id"), x.get("outcome_id"))
                if k in want and k not in got and x.get("label") in LABELS:
                    got[k] = {"pair_id": k[0], "outcome_id": k[1], "label": x["label"], "reason": str(x.get("reason", ""))}
        rated = {r["story_id"]: r for r in chat(model, family, "realism", prompt_realism(realism)).get("realism", [])
                 if isinstance(r.get("plausible"), bool)}
        OUT.mkdir(parents=True, exist_ok=True)
        doc = {"annotator": family, "model": model, "labels": [got[k] for k in sorted(got)],
               "missing": [f"{p}/{o}" for p, o in sorted(want - set(got))],
               "realism": [rated[s["story_id"]] for s in realism if s["story_id"] in rated]}
        (OUT / f"{family}.json").write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
        print(f"{family} ({model}): {len(got)}/{len(want)} labels, {len(doc['realism'])}/{len(realism)} realism ratings")


def score() -> None:
    rows = list(csv.DictReader((ROOT / "eval" / "golden.csv").open(encoding="utf-8")))
    xf = {f: json.loads((OUT / f"{f}.json").read_text(encoding="utf-8")) for f in ("kimi", "deepseek")}
    lab = {f: {(x["pair_id"], x["outcome_id"]): x["label"] for x in d["labels"]} for f, d in xf.items()}
    ref = {"consensus": "consensus", "A (Claude)": "annotator_a", "B (Claude)": "annotator_b"}
    keys = [(r["pair_id"], r["outcome_id"]) for r in rows]
    col = {name: {(r["pair_id"], r["outcome_id"]): r[c] for r in rows} for name, c in ref.items()}
    col |= {"Kimi": lab["kimi"], "DeepSeek": lab["deepseek"]}
    names = list(col)

    def kappa(a: str, b: str) -> dict:
        both = [k for k in keys if k in col[a] and k in col[b]]
        return {"kappa": round(cohen_kappa([col[a][k] for k in both], [col[b][k] for k in both]), 3), "n": len(both)}

    res = {"models": {f: d["model"] for f, d in xf.items()}, "missing": {f: d["missing"] for f, d in xf.items()},
           "kappa": {f"{a} vs {b}": kappa(a, b) for i, a in enumerate(names) for b in names[i + 1:]}}
    by_key = {(r["pair_id"], r["outcome_id"]): r for r in rows}
    agree = {k: lab["kimi"][k] for k in keys if k in lab["kimi"] and lab["kimi"].get(k) == lab["deepseek"].get(k)}
    res["cross_family_reference"] = {"n": len(agree), "excluded_disagreements": len(keys) - len(agree)}
    runs = {"v1 (registered)": ROOT / "data" / "engine_v1"}
    if (ROOT / "data" / "engine" / "hubs.json").exists():
        runs["v2 (amendment A1)"] = ROOT / "data" / "engine"
    for run, d in runs.items():
        judg = json.loads((d / "judgments.json").read_text(encoding="utf-8"))
        jl = {k: judg[f"{by_key[k]['candidate_id']}@all"][f"{by_key[k]['need_id']}/{k[1]}"]["label"] for k in keys}
        acc = {}
        for name in names[1:] + ["cross-family reference"]:
            ref_l = agree if name == "cross-family reference" else col[name]
            ks = [k for k in keys if k in ref_l]
            acc[name] = {"accuracy": round(sum(jl[k] == ref_l[k] for k in ks) / len(ks), 3) if ks else None, "n": len(ks)}
        sup = [k for k in agree if jl[k] == "supported"]
        acc["cross-family reference"]["false_supported"] = round(sum(agree[k] == "none" for k in sup) / len(sup), 3) if sup else None
        acc["cross-family reference"]["false_supported_n"] = len(sup)
        lens_t = {(p["need_id"], p["candidate_id"]): p["tier"] for p in json.loads((d / "lens.json").read_text(encoding="utf-8"))}
        pairs = {}
        for r in rows:
            pairs.setdefault((r["need_id"], r["candidate_id"], r["pair_id"]), []).append((r["priority"], (r["pair_id"], r["outcome_id"])))
        full = {k: v for k, v in pairs.items() if all(o in agree for _, o in v)}
        d_ = [abs(RANK[lens_t[k[:2]]] - RANK[tier([(p, agree[o]) for p, o in v])]) for k, v in full.items()]
        acc["F7 vs cross-family tiers"] = {"exact": round(sum(x == 0 for x in d_) / len(d_), 3) if d_ else None,
                                           "within_one": round(sum(x <= 1 for x in d_) / len(d_), 3) if d_ else None, "n_pairs": len(d_)}
        res[f"judge {run}"] = acc
    res["realism_plausible"] = {f: {"share": round(sum(r["plausible"] for r in d["realism"]) / len(d["realism"]), 3) if d["realism"] else None,
                                    "n": len(d["realism"])} for f, d in xf.items()}
    calls = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(CACHE.glob("*.json"))]
    res["cost"] = {"calls": len(calls), "usd_estimate": round(sum(c["usd_estimate"] for c in calls), 3),
                   "tokens": {k: sum(c["usage"].get(k, 0) for c in calls) for k in ("prompt_tokens", "completion_tokens")}}
    out = ROOT / "eval" / "results" / "xfamily.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(res, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "models":
        for fam in ("kimi", "deepseek"):
            print(f"{fam} key:", ", ".join(model_ids(fam)))
    elif cmd in ("annotate", "score"):
        {"annotate": annotate, "score": score}[cmd]()
    else:
        sys.exit(__doc__)
