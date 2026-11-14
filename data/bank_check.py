"""Structural checker for a content-bank domain file. Usage: python data/bank_check.py data/bank/iot.json

Semantic checks (label correctness, translation fidelity) are done by reviewers; this catches
everything a script can: shape, counts, word limits, number consistency, script, PII.
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "eval"))
from pii import scan  # noqa: E402

LANGS = ("en", "fi", "sv", "ar")
STYLES = ("polished", "rough")
LEVELS = ("supported", "partial", "none")
MAX_WORDS = {"contexts": 45, "claims": 40, "differently": 35, "next": 35}
NUM = re.compile(r"\d+(?:[.,]\d+)?")
ARABIC = re.compile(r"[؀-ۿ]")
ARABIC_DIGITS = re.compile(r"[٠-٩۰-۹]")


def numbers(s: str) -> list[str]:
    return sorted(n.replace(",", ".") for n in NUM.findall(s))


def check_text(where: str, text: dict, max_words: int) -> list[str]:
    errs = []
    if set(text) != set(LANGS):
        return [f"{where}: languages {sorted(text)} != {list(LANGS)}"]
    ref = numbers(text["en"]["polished"])
    for lang in LANGS:
        if set(text[lang]) != set(STYLES):
            errs.append(f"{where}.{lang}: styles {sorted(text[lang])}")
            continue
        for style in STYLES:
            s, at = text[lang][style], f"{where}.{lang}.{style}"
            if not isinstance(s, str) or not s.strip():
                errs.append(f"{at}: empty")
                continue
            if len(s.split()) > max_words:
                errs.append(f"{at}: {len(s.split())} words > {max_words}")
            if numbers(s) != ref:
                errs.append(f"{at}: numbers {numbers(s)} != en.polished {ref}")
            if ARABIC_DIGITS.search(s):
                errs.append(f"{at}: use Western digits")
            if (lang == "ar") != bool(ARABIC.search(s)):
                errs.append(f"{at}: wrong script for {lang}")
            errs += [f"{at}: PII {h}" for h in scan(s)]
    if text["en"]["polished"].strip() == text["en"]["rough"].strip():
        errs.append(f"{where}: polished and rough are identical")
    return errs


def check(path: Path) -> list[str]:
    bank = json.loads(path.read_text(encoding="utf-8"))
    arch = json.loads((Path(__file__).parent / "archetypes.json").read_text(encoding="utf-8"))
    dom = bank.get("domain")
    my_arch = [a["id"] for a in arch["archetypes"] if a["domain"] == dom]
    errs = [] if my_arch else [f"unknown domain {dom!r}"]
    want = {"contexts": 2, "differently": 2, "next": 2}
    for key, n in want.items():
        items = bank.get(key, [])
        if len(items) != n:
            errs.append(f"{key}: {len(items)} items, want {n}")
        for i, it in enumerate(items):
            errs += check_text(f"{key}[{i}]", it.get("text", {}), MAX_WORDS[key])
    expect_ids = {f"{a}.{lvl}.{n}" for a in my_arch for lvl in LEVELS for n in (1, 2)}
    claims = bank.get("claims", [])
    got_ids = [c.get("id") for c in claims]
    if set(got_ids) != expect_ids or len(got_ids) != len(expect_ids):
        errs.append(f"claims ids mismatch: missing {sorted(expect_ids - set(got_ids))} extra {sorted(set(got_ids) - expect_ids)}")
    for c in claims:
        a, lvl, _ = (c.get("id") or "..").split(".")
        if c.get("archetype") != a or c.get("level") != lvl:
            errs.append(f"{c.get('id')}: archetype/level fields disagree with id")
        errs += check_text(c.get("id", "?"), c.get("text", {}), MAX_WORDS["claims"])
        en = c.get("text", {}).get("en", {})
        for style in STYLES:
            s = en.get(style, "")
            if lvl == "supported" and not re.search(r"\bI\b", s):
                errs.append(f"{c['id']}.en.{style}: supported claim lacks first-person 'I'")
    notes = bank.get("reality_notes", [])
    if len(notes) != 3 or not all(isinstance(n, str) and n.strip() for n in notes):
        errs.append("reality_notes: want 3 non-empty English strings")
    errs += [f"reality_notes: PII {h}" for n in notes for h in scan(n)]
    return errs


if __name__ == "__main__":
    bad = 0
    for p in sys.argv[1:]:
        errs = check(Path(p))
        bad += len(errs)
        print(f"{p}: {'OK' if not errs else f'{len(errs)} issue(s)'}")
        for e in errs:
            print("  -", e)
    sys.exit(1 if bad else 0)
