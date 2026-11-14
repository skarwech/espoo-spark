"""F8 blind candidate cards and two-sided identity reveal (docs/method.md §8).

Evidence comes from the cached d1 Match Lens. Identity records are deliberately not loaded while blind cards are built; they are
looked up only after both sides have separately opted in. Every excerpt passes the deterministic redactor before leaving the server.
"""
import hashlib
import hmac
import json
import os
import re
import secrets
from functools import lru_cache
from pathlib import Path

from api import store
from engine import lens, live, llm, run

ROOT = Path(__file__).resolve().parents[1]
TOKEN_SECRET = (os.environ.get("SPARK_BLIND_TOKEN_SECRET") or secrets.token_hex(32)).encode("utf-8")
BLIND_CARD_KEYS = ("match_id", "tier", "tier_label", "why", "coverage", "practical_preferences", "reveal_state")

_EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
_PHONE = re.compile(r"(?<!\w)(?:\+|00)?\d[\d\s().-]{6,}\d(?!\w)")
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?:[\s-]?[A-Z0-9]){11,30}\b", re.I)
_BIRTH = re.compile(r"\b(?:born|birth(?:date)?|syntynyt|född)\s*(?:on|:)?\s*\d{1,2}[./-]\d{1,2}[./-]\d{2,4}\b|\b(?:born|syntynyt|född)\s*(?:in|:)?\s*(?:19|20)\d{2}\b", re.I)
_AGE = re.compile(r"\b(?:i am|i'm|age(?:\s+is)?|ikä|jag är)\s+\d{1,3}\s*(?:years? old|vuotta|år)?\b", re.I)
_NAME = re.compile(r"\b(?:my name is|i am|i'm|nimeni on|minä olen|jag heter|jag är)\s+[A-ZÅÄÖ][\w'’-]+(?:\s+[A-ZÅÄÖ][\w'’-]+){0,2}\b", re.I)
_AR_NAME = re.compile(r"(?:اسمي|أنا)\s+[\u0621-\u064a]{2,}(?:\s+[\u0621-\u064a]{2,}){0,2}")
_NATIONALITY = re.compile(r"\b(?:my nationality is|i am|i'm|nationality|olen|kansalaisuuteni on|jag är|nationalitet)\s+"
                         r"(?:finnish|swedish|estonian|ukrainian|somali|iraqi|syrian|suomalainen|ruotsalainen|virolainen|"
                         r"finländare|svensk|estnisk)\b|(?:جنسيتي|أنا)\s+(?:فنلندي|سويدي|إستوني|أوكراني|صومالي|عراقي|سوري)", re.I)
_SCHOOL = re.compile(r"\b(?:aalto university|university of [A-Z][\w -]+|helsinki university|metropolia(?: university)?|"
                      r"stud(?:ied|y) at [A-Z][\w -]+|opiskelin [A-ZÅÄÖ][\w -]+|jag studerade vid [A-ZÅÄÖ][\w -]+)\b", re.I)


def _replace(pattern: re.Pattern, text: str) -> str:
    return pattern.sub("[redacted personal detail]", text)


@lru_cache(maxsize=1)
def _known_identity_values() -> tuple[str, ...]:
    """Synthetic identity values that must never survive redaction even in unusual phrasing."""
    records = json.loads((ROOT / "data" / "out" / "identities.json").read_text(encoding="utf-8"))
    values = [str(record[field]) for record in records for field in ("name", "email", "nationality", "school") if record.get(field)]
    return tuple(sorted(set(values), key=len, reverse=True))


def redact(text: str, *, identity_values: tuple[str, ...] | None = None) -> str:
    """Remove direct identifiers from a blind excerpt while retaining the work evidence around it."""
    out = str(text)
    for pattern in (_EMAIL, _PHONE, _IBAN, _BIRTH, _AGE, _NAME, _AR_NAME, _NATIONALITY, _SCHOOL):
        out = _replace(pattern, out)
    values = _known_identity_values() if identity_values is None else tuple(sorted(set(identity_values), key=len, reverse=True))
    for value in values:
        out = re.sub(re.escape(value), "[redacted personal detail]", out, flags=re.I)
    return out


def match_id(need_id: str, version: int, candidate_id: str) -> str:
    """Opaque, stable client handle. The candidate id is never encoded in or returned with it."""
    data = f"{need_id}:{version}:{candidate_id}".encode("utf-8")
    return "m_" + hmac.new(TOKEN_SECRET, data, hashlib.sha256).hexdigest()[:32]


@lru_cache(maxsize=1)
def _pool() -> dict:
    return {item["candidate_id"]: item for item in json.loads((ROOT / "data" / "out" / "jobseekers.json").read_text(encoding="utf-8"))}


@lru_cache(maxsize=1)
def _stored_pairs() -> dict:
    pairs = json.loads((ROOT / "data" / "engine_d1" / "lens.json").read_text(encoding="utf-8"))
    return {(pair["need_id"], pair["candidate_id"]): pair for pair in pairs}


def _pairs_for_need(card: dict) -> dict:
    """Use the frozen d1 grid when available; new confirmed Need Cards reuse the F2 live path."""
    pool = _pool()
    stored = _stored_pairs()
    if all((card["need_id"], candidate_id) in stored for candidate_id in pool):
        return {candidate_id: stored[(card["need_id"], candidate_id)] for candidate_id in pool}
    if llm.ENGINE == "v1":
        raise RuntimeError("blind cards for a new Need Card require a step-3 engine profile")
    judgments = live.judge_need(card)
    units = run.read("units.json")
    return {candidate_id: lens.pair(card, candidate_id, units[candidate_id]["units"], judgments[candidate_id]) for candidate_id in pool}


def _coverage(grid: list[dict]) -> list[dict]:
    return [{"outcome": redact(item["outcome"]), "priority": item["priority"], "evidence": item["support"],
             "excerpt": redact(item["quote"]), "reason": redact(item["reason"])} for item in grid]


def blind_cards(card: dict) -> list[dict]:
    """One non-ranked, redacted blind card for every registered candidate, including people with no evidence yet."""
    candidates, pairs = _pool(), _pairs_for_need(card)
    cards = []
    for candidate_id in sorted(candidates):
        pair = pairs[candidate_id]
        opaque_id = match_id(card["need_id"], card["version"], candidate_id)
        store.put_blind_match(opaque_id, card["need_id"], card["version"], candidate_id)
        constraints = candidates[candidate_id]["constraints"]
        blind = {"match_id": opaque_id, "tier": pair["tier"], "tier_label": pair["tier_label"], "why": redact(pair["why"]),
                 "coverage": _coverage(pair["grid"]),
                 "practical_preferences": {"start": constraints["start"], "work_mode": constraints["work_mode"]},
                 "reveal_state": store.reveal_status(opaque_id)["state"]}
        if tuple(blind) != BLIND_CARD_KEYS:
            raise ValueError("blind-card payload changed without an allowlist review")
        cards.append(blind)
    tier_order = {"strong": 0, "worth": 1, "stretch": 2, "none": 3}
    return sorted(cards, key=lambda item: (tier_order[item["tier"]], item["match_id"]))


@lru_cache(maxsize=1)
def _identities() -> dict:
    return {item["candidate_id"]: item for item in json.loads((ROOT / "data" / "out" / "identities.json").read_text(encoding="utf-8"))}


@lru_cache(maxsize=1)
def _companies() -> dict:
    return {item["company_id"]: item for item in json.loads((ROOT / "data" / "out" / "companies.json").read_text(encoding="utf-8"))}


def _company(match: dict) -> dict:
    record = store.get_need(match["need_id"], match["need_version"])
    if record and record.get("company"):
        company = record["company"]
        return {"name": company.get("name", "The company"), "industry": company.get("industry", "")}
    needs = {item["need_id"]: item for item in json.loads((ROOT / "data" / "out" / "needs.json").read_text(encoding="utf-8"))}
    need = needs.get(match["need_id"])
    company = _companies().get(need["company_id"], {}) if need else {}
    return {"name": company.get("name", "The company"), "industry": company.get("industry", "")}


def reveal(match_id_: str, role: str) -> dict:
    """Return only consent state until both choices exist; after that, reveal the minimum contact information required to connect."""
    if role not in ("recruiter", "candidate"):
        raise ValueError("role must be recruiter or candidate")
    status = store.reveal_status(match_id_)
    response = {"match_id": match_id_, **status}
    if not status["revealed"]:
        return response
    match = store.get_blind_match(match_id_)
    if role == "recruiter":
        identity = _identities()[match["candidate_id"]]
        response["reveal"] = {"name": identity["name"], "email": identity["email"]}
    else:
        response["reveal"] = _company(match)
    return response
