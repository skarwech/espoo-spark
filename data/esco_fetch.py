"""Resolve archetype skill labels to ESCO URIs and cache them with their relations.

Network is used here only. Output (data/esco_cache.json) is frozen and committed;
`make data` never touches the network.
"""
import json
import sys
from pathlib import Path

import httpx

API = "https://ec.europa.eu/esco/api"
HERE = Path(__file__).parent
RELS = ["broaderSkill", "hasEssentialSkill", "hasOptionalSkill", "isEssentialForOccupation", "isOptionalForOccupation"]


def resolve(client, label):
    r = client.get(f"{API}/search", params={"text": label, "type": "skill", "language": "en", "limit": 10})
    r.raise_for_status()
    for hit in r.json()["_embedded"]["results"]:
        if hit["title"].lower() == label.lower():
            return hit["uri"]
    raise LookupError(f"no exact ESCO match for {label!r}")


def fetch(client, uri):
    r = client.get(f"{API}/resource/skill", params={"uri": uri, "language": "en"})
    r.raise_for_status()
    d, links = r.json(), r.json()["_links"]
    rel = {k: sorted(x["uri"] for x in links.get(k, [])) for k in RELS}
    return {"uri": uri, "label": d["title"], **rel}


def main():
    arch = json.loads((HERE / "archetypes.json").read_text(encoding="utf-8"))
    labels = sorted({s for a in arch["archetypes"] for s in a["skills"]} | set(arch["extra_skills"]))
    with httpx.Client(timeout=30) as c:
        skills = {}
        for label in labels:
            uri = resolve(c, label)
            skills[uri] = fetch(c, uri)
            print(f"ok  {label}")
    out = {"source": "ESCO API v1 (ec.europa.eu/esco/api), fetched 2026-11-13", "skills": skills}
    (HERE / "esco_cache.json").write_text(json.dumps(out, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    print(f"cached {len(skills)} skills")


if __name__ == "__main__":
    sys.exit(main())
