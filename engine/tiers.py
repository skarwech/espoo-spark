"""Fixed, transparent tier rules (docs/method.md §3). Pure functions; thresholds live in RULES."""
from collections.abc import Iterable

RULES = {"strong_supported_share": 0.5, "worth_covered_share": 0.6}
TIERS = ("strong", "worth", "stretch", "none")
COVERED = {"supported", "partial", "adjacent"}
PRIORITIES = {"must", "learnable", "nice"}
SUPPORTS = COVERED | {"none"}


def tier(outcomes: Iterable[tuple[str, str]]) -> str:
    """outcomes: (priority, support) pairs; support in supported|partial|adjacent|none."""
    outcomes = list(outcomes)
    bad = [o for o in outcomes if o[0] not in PRIORITIES or o[1] not in SUPPORTS]
    if bad:
        raise ValueError(f"unknown priority/support values: {bad}")
    musts = [s for p, s in outcomes if p == "must"]
    if not musts:
        raise ValueError("a need must have at least one must-have outcome")
    supported = sum(s == "supported" for s in musts) / len(musts)
    covered = sum(s in COVERED for s in musts) / len(musts)
    if all(s in ("supported", "partial") for s in musts) and supported >= RULES["strong_supported_share"]:
        return "strong"
    if covered >= RULES["worth_covered_share"]:
        return "worth"
    if any(s in COVERED for _, s in outcomes):
        return "stretch"
    return "none"


if __name__ == "__main__":
    assert tier([("must", "supported"), ("must", "supported"), ("learnable", "none")]) == "strong"  # Karim
    assert tier([("must", "adjacent"), ("must", "partial")]) == "worth"  # Elena
    assert tier([("must", "none"), ("must", "partial")]) == "stretch"  # Aino
    assert tier([("must", "none"), ("must", "none"), ("nice", "none")]) == "none"
    assert tier([("must", "partial"), ("must", "partial")]) == "worth"  # no supported -> not strong
    try:
        tier([("must", "supported"), ("Must", "none")])
        raise AssertionError("typo accepted")
    except ValueError:
        pass
    print("tiers ok")
