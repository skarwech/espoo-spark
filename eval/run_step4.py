"""F8 offline evaluation: blind-payload safety, paired-consent reveal and planted redaction recall.

Usage: SPARK_ENGINE=d1 python eval/run_step4.py -> eval/results/step4/blind.json. It uses only stored d1 grids and the
pre-written 60-mention fixture; no model calls or network access are made.
"""
import json
import os
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "eval" / "results" / "step4"
os.environ.setdefault("SPARK_DB", str(OUT / "spark_eval.db"))
sys.path.insert(0, str(ROOT))

from api import blind, store  # noqa: E402


def data(path: str):
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def save(name: str, obj: dict) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(json.dumps(obj, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")


def main() -> None:
    need = next(item for item in data("data/out/needs.json") if item["need_id"] == "N-014")
    cards = blind.blind_cards(need)
    payload = json.dumps(cards, ensure_ascii=False).lower()
    identities = data("data/out/identities.json")
    forbidden = sorted({str(record[field]).lower() for record in identities for field in ("name", "email", "nationality", "school") if record.get(field)})
    leaked = [value for value in forbidden if value in payload]
    token = f"f8-eval-{uuid.uuid4().hex}"
    store.put_blind_match(token, need["need_id"], need["version"], "J-001")
    before = blind.reveal(token, "recruiter")
    after_recruiter = store.give_reveal_consent(token, "recruiter")
    one_sided = blind.reveal(token, "recruiter")
    after_candidate = store.give_reveal_consent(token, "candidate")
    revealed = blind.reveal(token, "recruiter")
    log = store.get_reveal_log(token)
    mentions = data("eval/fixtures/blind_mentions.json")
    redaction = [{"id": item["id"], "kind": item["kind"], "redacted": item["mention"].casefold() not in blind.redact(item["text"]).casefold()}
                 for item in mentions]
    hits = sum(item["redacted"] for item in redaction)
    result = {"blind_cards": len(cards), "identity_leaks": leaked, "one_sided_reveal": "reveal" in before or "reveal" in one_sided,
              "reveal_after_both": "reveal" in revealed and after_recruiter["state"] == "awaiting_candidate" and after_candidate["state"] == "revealed",
              "reveal_log": log, "redaction": redaction,
              "targets": {"F8 redaction recall, 60 planted mentions": {"value": hits / len(redaction), "threshold": 0.95, "n": len(redaction),
                                                              "pass": hits / len(redaction) >= 0.95, "kind": "target",
                                                              "note": "deterministic planted fixture; recruiter usability still needs people"},
                          "F8 enough to decide, 3 recruiter testers": {"value": None, "threshold": None, "n": 0, "pass": None, "kind": "pending",
                                                                           "note": "needs recruiters; not measured"}}}
    save("blind.json", result)
    print(f"blind cards: {len(cards)}; identity leaks: {len(leaked)}; redaction: {hits}/{len(redaction)}")


if __name__ == "__main__":
    main()
