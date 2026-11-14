"""Offline "oracle" demo of Spark's Match Lens: replays the running example from stored engine outputs.

No API key, no network, no model call: every answer was recorded when the engine ran (data/engine/, data/llm_cache/).
Usage: python demo.py [need_id]   (default N-014, NordSense)
"""
import json
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HEROES = {"J-002": "rough but concrete story", "J-003": "career changer from logistics", "J-001": "polished, generic story"}
EXPECTED = {"J-002": "Strong", "J-003": "Worth a conversation", "J-001": "Stretch"}  # the challenge spec's expectation
LABEL = {"must": "must-have", "learnable": "can learn", "nice": "nice to have"}


def load(p: str):
    return json.loads((ROOT / p).read_text(encoding="utf-8"))


def wrap(text: str, indent: int = 6) -> str:
    return textwrap.fill(text, width=100, initial_indent=" " * indent, subsequent_indent=" " * indent)


def main() -> None:
    need_id = sys.argv[1] if len(sys.argv) > 1 else "N-014"
    need = next(n for n in load("data/out/needs.json") if n["need_id"] == need_id)
    lens = {(p["need_id"], p["candidate_id"]): p for p in load("data/engine/lens.json")}
    units = {u["id"]: u["text"] for r in load("data/engine/units.json").values() for u in r["units"]}
    print(f"\nSPARK · Match Lens demo (offline replay) · need {need_id}\n")
    print("Company need (confirmed Need Card):")
    for o in need["outcomes"]:
        print(f"  {o['id']} [{LABEL[o['priority']]}] {o['text']}")
    print("  Reality: " + " ".join(need["company_reality_notes"]))
    cands = [c for c in HEROES if (need_id, c) in lens] if need_id == "N-014" else sorted(c for n, c in lens if n == need_id)
    for cid in cands:
        p = lens[(need_id, cid)]
        print(f"\n{'─' * 100}\n{cid} · {HEROES.get(cid, 'golden-set candidate')}  →  tier: {p['tier_label'].upper()}"
              + (f"   (spec expects {EXPECTED[cid]})" if cid in EXPECTED and need_id == "N-014" else ""))
        print(f"  why: {p['why']}")
        for r in p["grid"]:
            print(f"  {r['outcome_id']} [{r['support']}] {r['outcome']}")
            if r["quote"]:
                print(wrap(f"quote: “{r['quote']}”"))
            elif r["path"]:
                print(wrap(f"adjacent ESCO path: {r['path']['from']} ({r['path']['relation']}) {r['path']['to']} "
                           f"— from: “{units.get(r['path']['unit_id'], '')}”"))
            print(wrap(f"reason: {r['reason']}"))
    print(f"\n{'─' * 100}\nNo match score is shown to anyone; tiers come from fixed rules over quoted evidence, and a person decides.")
    print("Proof pages: make serve, then http://127.0.0.1:8777/reports/f7.html (step 2) and /reports/f0.html (step 1).\n")


if __name__ == "__main__":
    main()
