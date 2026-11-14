"""Fetch the ESCO description of every cached skill (amendment A1.1). Network is used here only; the output
data/esco_scope.json is frozen and committed. Usage: python data/esco_scope.py"""
import json
import re
from pathlib import Path

import httpx

API = "https://ec.europa.eu/esco/api"
HERE = Path(__file__).parent
MAX_WORDS = 30


def first_sentence(text: str) -> str:
    s = re.split(r"(?<=[.!?])\s+", " ".join(text.split()), maxsplit=1)[0]
    words = s.split()
    return s if len(words) <= MAX_WORDS else " ".join(words[:MAX_WORDS]) + " …"


def main() -> None:
    cache = json.loads((HERE / "esco_cache.json").read_text(encoding="utf-8"))["skills"]
    out = {}
    with httpx.Client(timeout=30) as c:
        for uri in sorted(cache):
            r = c.get(f"{API}/resource/skill", params={"uri": uri, "language": "en"})
            r.raise_for_status()
            d = r.json()
            desc = (d.get("description") or {}).get("en", {}).get("literal", "") or (d.get("scopeNote") or {}).get("en", {}).get("literal", "")
            if not desc.strip():
                raise LookupError(f"no English description for {cache[uri]['label']}")
            out[uri] = {"label": cache[uri]["label"], "description": first_sentence(desc)}
    doc = {"source": "ESCO API v1 (ec.europa.eu/esco/api), descriptions fetched 2026-11-14, first sentence, max 30 words", "skills": out}
    (HERE / "esco_scope.json").write_text(json.dumps(doc, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {len(out)} descriptions")


if __name__ == "__main__":
    main()
