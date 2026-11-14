"""Prepare a human-rating sheet for the latest F9–F16 evaluation packet."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "eval" / "results" / "step5"
PACK = OUT / "review_pack.json"
REVIEWS = OUT / "human_reviews.json"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prepare", choices=("prepare",), help="create a blank rating sheet without replacing existing ratings")
    args = parser.parse_args()
    if not PACK.exists():
        parser.error("run `make step5` first to generate the review packet")
    packet = json.loads(PACK.read_text(encoding="utf-8"))
    digest = hashlib.sha256(PACK.read_bytes()).hexdigest()
    if REVIEWS.exists():
        current = json.loads(REVIEWS.read_text(encoding="utf-8"))
        if current.get("review_pack_sha256") != digest:
            parser.error("the packet changed since this sheet was prepared; archive human_reviews.json and run this command again")
        print(f"ratings already exist: {REVIEWS.relative_to(ROOT)} (left unchanged)")
        return

    template = {
        "review_pack_sha256": digest,
        "instructions": "Use the rating_guide in review_pack.json. Rate without adding names or other personal data; use true/false for each judgement.",
        "f9_specificity": [{"case_id": item["case_id"], "specific": None} for item in packet["f9_specificity"]],
        "f9_usefulness": [{"case_id": item["case_id"], "useful": None, "answerable": None}
                          for item in packet["f9_usefulness"]],
        "f13_clusters": [{"case_id": item["case_id"], "coherent": None} for item in packet["f13_clusters"]],
        "f13_actions": [{"session_id": f"M{index:02d}", "concrete_action": None, "action_note": ""}
                         for index in range(1, 4)],
        "f11_device_checks": [{"device_id": f"D{index:02d}", "device_description": "",
                               "offline_pass_opened": None}
                              for index in range(1, 3)],
        "demo_seconds": None,
    }
    REVIEWS.parent.mkdir(parents=True, exist_ok=True)
    REVIEWS.write_text(json.dumps(template, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    print(f"created {REVIEWS.relative_to(ROOT)} from {len(packet['f9_specificity'])} generic-story cases, "
          f"{len(packet['f9_usefulness'])} prompt cards, and {len(packet['f13_clusters'])} dashboard clusters")


if __name__ == "__main__":
    main()
