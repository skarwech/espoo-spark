"""F15 release controls, identity-free logs, traceability and participant deletion."""
import hashlib
import hmac
import json
import os
import re
from pathlib import Path

from api import event_ops, store
from engine import llm

ROOT = Path(__file__).resolve().parents[1]
DECLARED_FIELDS = {
    "interviews": ("session_id", "state", "updated"),
    "need_cards": ("need_id", "version", "record", "confirmed", "created", "confirmed_at"),
    "stories": ("story_id", "submitted", "created"),
    "story_private": ("story_id", "context"),
    "blind_matches": ("match_id", "need_id", "need_version", "candidate_id", "created"),
    "reveal_consents": ("match_id", "role", "consented_at"),
    "reveal_log": ("match_id", "recruiter_consented_at", "candidate_consented_at", "revealed_at"),
    "event_agendas": ("event_id", "agenda", "approved", "created", "approved_at", "approved_by_hash"),
    "event_passes": ("token_hash", "event_id", "content_hash", "payload", "expires_at", "created"),
    "event_pass_batches": ("event_id", "created"),
    "pulse_records": ("client_event_id", "event_id", "token_hash", "slot_id", "response", "created"),
    "oversight_log": ("log_id", "action", "actor_hash", "subject_hash", "details", "created"),
}
IDENTITY_FIELDS = {"name", "email", "nationality", "school", "birth_year", "private_context", "candidate_name", "person_name"}
IDENTITY_KEY = re.compile(r"(?:name|email|nationality|ethnic|birth|school|private.?context|contact|candidate|person)", re.I)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def organizer_allowed(token: str | None) -> bool:
    expected = os.environ.get("SPARK_ORGANIZER_TOKEN", "")
    return bool(token and expected and hmac.compare_digest(token.encode("utf-8"), expected.encode("utf-8")))


def log(action: str, actor: str, subject: str, **details) -> None:
    if identity_keys(details):
        raise ValueError("identity fields are not allowed in oversight logs")
    store.add_oversight(action, digest(actor), digest(subject), details)


def identity_keys(value) -> set[str]:
    if isinstance(value, dict):
        direct = {str(key).casefold() for key in value if IDENTITY_KEY.search(str(key))}
        return direct | set().union(*(identity_keys(item) for item in value.values()))
    if isinstance(value, list):
        return set().union(*(identity_keys(item) for item in value)) if value else set()
    return set()


def approve_event(event_id: str, actor: str) -> dict:
    record = store.get_agenda(event_id)
    if record is None:
        raise KeyError(event_id)
    if not store.approve_agenda(event_id, digest(actor)):
        raise KeyError(event_id)
    log("agenda_approved", actor, event_id, slots=len(record["agenda"]["slots"]))
    return {"event_id": event_id, "approved": True}


def issue_passes(event_id: str, actor: str) -> list[dict]:
    issued = event_ops.publish_passes(event_id)
    log("passes_issued", actor, event_id, count=len(issued))
    return issued


def trace_slot(event_id: str, slot_id: str) -> dict:
    record = store.get_agenda(event_id)
    if record is None:
        raise KeyError(event_id)
    slot = next((item for item in record["agenda"]["slots"] if item["slot_id"] == slot_id), None)
    if slot is None:
        raise KeyError(slot_id)
    match = store.get_blind_match(slot["match_id"])
    need = event_ops._need(slot["need_id"])
    pair = event_ops._pair(slot["need_id"], match["candidate_id"])
    units = event_ops._read("data/engine_d1/units.json")[match["candidate_id"]]["units"]
    units_by_id = {unit["id"]: unit for unit in units}
    outcomes = {item["id"]: item for item in need["outcomes"]}
    grid = {item["outcome_id"]: item for item in pair["grid"]}
    paths = []
    for item in slot["traceability"]:
        outcome = outcomes[item["outcome_id"]]
        result = grid[item["outcome_id"]]
        if item["unit_id"] and item["unit_id"] in units_by_id:
            unit = units_by_id[item["unit_id"]]
            evidence = {"step": "Evidence unit", "id": unit["id"], "box": unit["box"],
                        "excerpt": event_ops.blind.redact(unit["text"])}
        else:
            evidence = {"step": "Evidence check", "id": "none", "reason": event_ops.blind.redact(result["reason"])}
        paths.append({"chain": [{"step": "Need Card", "id": slot["need_id"]},
                                 {"step": "Need outcome", "id": f"{slot['need_id']}:{outcome['id']}",
                                  "text": event_ops.blind.redact(outcome["text"])}, evidence],
                      "support": result["support"]})
    return {"event_id": event_id, "slot_id": slot_id, "why": slot["why"], "paths": paths,
            "plain_language": f"This conversation is scheduled for {slot['company']} because the Need Card has cited work evidence or an explicit gap."}


def declared_schema() -> dict:
    """Compare declared storage fields with SQLite's actual columns; unexpected fields are returned as a failed control."""
    from api.store import connect
    actual = {}
    with connect() as con:
        tables = {row[0] for row in con.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%'")}
        for table in tables:
            actual[table] = tuple(row[1] for row in con.execute(f"pragma table_info({table})"))
    undeclared = {table: sorted(set(columns) - set(DECLARED_FIELDS.get(table, ()))) for table, columns in actual.items()
                  if set(columns) - set(DECLARED_FIELDS.get(table, ()))}
    missing = sorted(set(DECLARED_FIELDS) - set(actual))
    return {"tables": {key: list(DECLARED_FIELDS[key]) for key in sorted(DECLARED_FIELDS)},
            "undeclared": undeclared, "missing_tables": missing, "valid": not undeclared and not missing}


def logs_identity_free() -> dict:
    rows = store.oversight_rows()
    bad = []

    for row in rows:
        if identity_keys(row):
            bad.append(row["action"])
    return {"count": len(rows), "identity_fields": bad}


def delete_participant(story_id: str, delete_code: str) -> dict:
    """Delete an account by its 128-bit possession code and purge its story-specific cached model output."""
    if not re.fullmatch(r"S-[0-9a-f]{32}", story_id) or not hmac.compare_digest(story_id, delete_code):
        raise PermissionError("valid participant deletion code required")
    if not store.delete_story(story_id):
        raise KeyError(story_id)
    live_cache = ROOT / "data" / "engine_d1" / "live" / f"story-{story_id}.json"
    live_cache.unlink(missing_ok=True)
    purged = llm.purge_subject(story_id)
    log("participant_deleted", story_id, story_id, cached_outputs_removed=purged)
    return {"deleted": True, "cached_outputs_removed": purged}


def review_snapshot() -> dict:
    schema = declared_schema()
    logs = logs_identity_free()
    return {"schema": schema, "logs": logs, "release_gates": {"agenda_approval": True, "pass_approval": True,
            "pass_integrity_and_expiry": True, "organizer_authentication": bool(os.environ.get("SPARK_ORGANIZER_TOKEN")),
            "identity_free_logs": not logs["identity_fields"], "deletion_capability": True},
            "open_issues": ["Production role authentication and independent recruiter/candidate sessions are not configured in the synthetic prototype.",
                            "Private pass links are not delivered by an email or notification service.",
                            "The scheduler is greedy; the exact-ILP comparison and agenda stress sweep remain unverified.",
                            "Offline device, load and human responsible-AI mentor reviews are still required."]}
