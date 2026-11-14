"""SQLite storage for interviews, Need Cards, Skill Stories and event workflows. Path: SPARK_DB or data/spark.db.
Text is stored as submitted: JSON keeps every string byte for byte (method.md §7, own-words integrity)."""
import json
import os
import sqlite3
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = """
create table if not exists interviews (session_id text primary key, state text not null, updated real not null);
create table if not exists need_cards (need_id text not null, version integer not null, record text not null,
  confirmed integer not null default 0, created real not null, confirmed_at real, primary key (need_id, version));
create table if not exists stories (story_id text primary key, submitted text not null, created real not null);
create table if not exists story_private (story_id text primary key, context text not null);
create table if not exists blind_matches (
  match_id text primary key, need_id text not null, need_version integer not null, candidate_id text not null, created real not null
);
create table if not exists reveal_consents (
  match_id text not null, role text not null check(role in ('recruiter', 'candidate')), consented_at real not null,
  primary key (match_id, role), foreign key (match_id) references blind_matches(match_id)
);
create table if not exists reveal_log (
  match_id text primary key, recruiter_consented_at real not null, candidate_consented_at real not null, revealed_at real not null,
  foreign key (match_id) references blind_matches(match_id)
);
create table if not exists event_agendas (
  event_id text primary key, agenda text not null, approved integer not null default 0, created real not null,
  approved_at real, approved_by_hash text
);
create table if not exists event_passes (
  token_hash text primary key, event_id text not null, content_hash text not null, payload text not null,
  expires_at real not null, created real not null
);
create table if not exists event_pass_batches (event_id text primary key, created real not null);
create table if not exists pulse_records (
  client_event_id text primary key, event_id text not null, token_hash text not null, slot_id text not null,
  response text not null, created real not null
);
create table if not exists oversight_log (
  log_id integer primary key autoincrement, action text not null, actor_hash text not null, subject_hash text not null,
  details text not null, created real not null
);
"""


def connect() -> sqlite3.Connection:
    database = Path(os.environ.get("SPARK_DB") or ROOT / "data" / "spark.db")
    database.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(database)
    con.execute("pragma foreign_keys = on")
    con.executescript(SCHEMA)
    return con


def put_interview(session_id: str, state: dict) -> None:
    with connect() as con:
        con.execute("insert or replace into interviews values (?, ?, ?)", (session_id, json.dumps(state, ensure_ascii=False), time.time()))


def get_interview(session_id: str) -> dict | None:
    with connect() as con:
        row = con.execute("select state from interviews where session_id = ?", (session_id,)).fetchone()
    return json.loads(row[0]) if row else None


def next_id(prefix: str, table: str, column: str, start: int) -> str:
    """Next free id like N-101 / C-101 (new records start above the synthetic world's ids)."""
    with connect() as con:
        used = {r[0] for r in con.execute(f"select {column} from {table}")}
    n = start
    while f"{prefix}-{n:03d}" in used:
        n += 1
    return f"{prefix}-{n:03d}"


def put_need(record: dict) -> None:
    card = record["card"]
    with connect() as con:
        con.execute("insert into need_cards values (?, ?, ?, 0, ?, null)",
                    (card["need_id"], card["version"], json.dumps(record, ensure_ascii=False), time.time()))


def get_need(need_id: str, version: int | None = None) -> dict | None:
    """Latest (or given) version: {record fields..., confirmed, confirmed_at}."""
    q = "select record, confirmed, confirmed_at from need_cards where need_id = ?" + (" and version = ?" if version else "") + \
        " order by version desc limit 1"
    with connect() as con:
        row = con.execute(q, (need_id, version) if version else (need_id,)).fetchone()
    if not row:
        return None
    rec = json.loads(row[0])
    rec["card"]["confirmed"] = bool(row[1])
    return {**rec, "confirmed_at": row[2]}


def confirm_need(need_id: str, version: int) -> bool:
    with connect() as con:
        cur = con.execute("update need_cards set confirmed = 1, confirmed_at = ? where need_id = ? and version = ?",
                          (time.time(), need_id, version))
    return cur.rowcount == 1


def put_story(story_id: str, submitted: dict, private_context: str | None) -> None:
    with connect() as con:
        con.execute("insert into stories values (?, ?, ?)", (story_id, json.dumps(submitted, ensure_ascii=False), time.time()))
        if private_context:
            con.execute("insert into story_private values (?, ?)", (story_id, private_context))


def get_story(story_id: str) -> dict | None:
    with connect() as con:
        row = con.execute("select submitted from stories where story_id = ?", (story_id,)).fetchone()
    return json.loads(row[0]) if row else None


# ---------- F8 blind matching and two-sided reveal ----------
def put_blind_match(match_id: str, need_id: str, need_version: int, candidate_id: str) -> None:
    """Persist the server-only link behind an opaque blind-card id.

    The client receives ``match_id`` but never the candidate id. Reusing a deterministic id for a different pair is treated as a
    programming error rather than silently changing the person behind an existing consent record.
    """
    put_blind_matches([(match_id, need_id, need_version, candidate_id)])


def put_blind_matches(matches: list[tuple[str, str, int, str]]) -> None:
    """Validate and persist many opaque handles with one database connection."""
    now = time.time()
    with connect() as con:
        for match_id, need_id, need_version, candidate_id in matches:
            row = con.execute("select need_id, need_version, candidate_id from blind_matches where match_id = ?", (match_id,)).fetchone()
            if row is None:
                con.execute("insert into blind_matches values (?, ?, ?, ?, ?)",
                            (match_id, need_id, need_version, candidate_id, now))
            elif tuple(row) != (need_id, need_version, candidate_id):
                raise ValueError("blind match id is already linked to a different pair")


def get_blind_match(match_id: str) -> dict | None:
    with connect() as con:
        row = con.execute("select need_id, need_version, candidate_id from blind_matches where match_id = ?", (match_id,)).fetchone()
    return {"need_id": row[0], "need_version": row[1], "candidate_id": row[2]} if row else None


def get_blind_matches(match_ids: list[str]) -> dict[str, dict]:
    """Fetch several server-only blind-handle mappings with one connection."""
    if not match_ids:
        return {}
    found = {}
    with connect() as con:
        for match_id in set(match_ids):
            row = con.execute("select need_id, need_version, candidate_id from blind_matches where match_id = ?",
                              (match_id,)).fetchone()
            if row:
                found[match_id] = {"need_id": row[0], "need_version": row[1], "candidate_id": row[2]}
    return found


def give_reveal_consent(match_id: str, role: str) -> dict:
    """Record one party's affirmative opt-in, then create an immutable reveal log only when both have opted in."""
    if role not in ("recruiter", "candidate"):
        raise ValueError("role must be recruiter or candidate")
    with connect() as con:
        if con.execute("select 1 from blind_matches where match_id = ?", (match_id,)).fetchone() is None:
            raise KeyError(match_id)
        con.execute("insert or ignore into reveal_consents values (?, ?, ?)", (match_id, role, time.time()))
        rows = dict(con.execute("select role, consented_at from reveal_consents where match_id = ?", (match_id,)))
        if {"recruiter", "candidate"} <= rows.keys():
            con.execute("insert or ignore into reveal_log values (?, ?, ?, ?)",
                        (match_id, rows["recruiter"], rows["candidate"], time.time()))
    return reveal_status(match_id)


def reveal_status(match_id: str) -> dict:
    """Return consent state without exposing either party's identity or contact details."""
    with connect() as con:
        if con.execute("select 1 from blind_matches where match_id = ?", (match_id,)).fetchone() is None:
            raise KeyError(match_id)
        consents = dict(con.execute("select role, consented_at from reveal_consents where match_id = ?", (match_id,)))
        logged = con.execute("select revealed_at from reveal_log where match_id = ?", (match_id,)).fetchone()
    both = {"recruiter", "candidate"} <= consents.keys()
    state = "revealed" if both else "awaiting_candidate" if "recruiter" in consents else "awaiting_recruiter" if "candidate" in consents else "not_requested"
    return {"state": state, "revealed": both, "revealed_at": logged[0] if logged else None}


def get_reveal_log(match_id: str) -> dict | None:
    """Auditable, identity-free record created only after both parties consent."""
    with connect() as con:
        row = con.execute("select recruiter_consented_at, candidate_consented_at, revealed_at from reveal_log where match_id = ?",
                          (match_id,)).fetchone()
    if row is None:
        return None
    return {"match_id": match_id, "recruiter_consented_at": row[0], "candidate_consented_at": row[1], "revealed_at": row[2]}


# ---------- F10-F15 event, pass, pulse and oversight storage ----------
def save_agenda(event_id: str, agenda: dict) -> None:
    with connect() as con:
        # A replacement schedule invalidates old private passes and their event-specific feedback.
        con.execute("delete from pulse_records where event_id = ?", (event_id,))
        con.execute("delete from event_passes where event_id = ?", (event_id,))
        con.execute("delete from event_pass_batches where event_id = ?", (event_id,))
        con.execute("insert or replace into event_agendas(event_id, agenda, approved, created, approved_at, approved_by_hash) "
                    "values (?, ?, 0, ?, null, null)", (event_id, json.dumps(agenda, ensure_ascii=False), time.time()))


def get_agenda(event_id: str) -> dict | None:
    with connect() as con:
        row = con.execute("select agenda, approved, approved_at from event_agendas where event_id = ?", (event_id,)).fetchone()
    if not row:
        return None
    return {"agenda": json.loads(row[0]), "approved": bool(row[1]), "approved_at": row[2]}


def approve_agenda(event_id: str, actor_hash: str) -> bool:
    with connect() as con:
        cur = con.execute("update event_agendas set approved = 1, approved_at = ?, approved_by_hash = ? where event_id = ?",
                          (time.time(), actor_hash, event_id))
    return cur.rowcount == 1


def put_pass(token_hash: str, event_id: str, content_hash: str, payload: dict, expires_at: float) -> None:
    with connect() as con:
        con.execute("insert or replace into event_passes values (?, ?, ?, ?, ?, ?)",
                    (token_hash, event_id, content_hash, json.dumps(payload, ensure_ascii=False), expires_at, time.time()))


def put_pass_batch(event_id: str, approved_agenda_hash: str, passes: list[tuple[str, str, dict, float]]) -> bool:
    """Atomically issue one pass batch, only if the same agenda is still approved."""
    import hashlib

    with connect() as con:
        con.execute("begin immediate")
        row = con.execute("select agenda, approved from event_agendas where event_id = ?", (event_id,)).fetchone()
        if row is None or not row[1]:
            return False
        canonical = json.dumps(json.loads(row[0]), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        current_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        if current_hash != approved_agenda_hash:
            return False
        if con.execute("select 1 from event_pass_batches where event_id = ?", (event_id,)).fetchone() \
                or con.execute("select 1 from event_passes where event_id = ? limit 1", (event_id,)).fetchone():
            return False
        con.execute("insert into event_pass_batches values (?, ?)", (event_id, time.time()))
        created = time.time()
        con.executemany("insert into event_passes values (?, ?, ?, ?, ?, ?)",
                        [(token_hash, event_id, content_hash, json.dumps(payload, ensure_ascii=False), expires_at, created)
                         for token_hash, content_hash, payload, expires_at in passes])
    return True


def get_pass(token_hash: str) -> dict | None:
    with connect() as con:
        row = con.execute("select event_id, content_hash, payload, expires_at from event_passes where token_hash = ?", (token_hash,)).fetchone()
    return {"event_id": row[0], "content_hash": row[1], "payload": json.loads(row[2]), "expires_at": row[3]} if row else None


def event_pass_count(event_id: str) -> int:
    with connect() as con:
        return con.execute("select count(*) from event_passes where event_id = ?", (event_id,)).fetchone()[0]


def record_pulse(client_event_id: str, event_id: str, token_hash: str, slot_id: str, response: str) -> bool:
    """Store at most one pulse per pass and meeting, even when a double tap gets a new event id."""
    with connect() as con:
        # Serialize the existence check with the insert so concurrent taps cannot create duplicate feedback.
        con.execute("begin immediate")
        if con.execute("select 1 from pulse_records where token_hash = ? and slot_id = ?", (token_hash, slot_id)).fetchone():
            return False
        cur = con.execute("insert or ignore into pulse_records values (?, ?, ?, ?, ?, ?)",
                          (client_event_id, event_id, token_hash, slot_id, response, time.time()))
    return cur.rowcount == 1


def pulse_rows(event_id: str) -> list[dict]:
    with connect() as con:
        rows = con.execute("select token_hash, slot_id, response, created from pulse_records where event_id = ?", (event_id,)).fetchall()
    return [{"token_hash": row[0], "slot_id": row[1], "response": row[2], "created": row[3]} for row in rows]


def add_oversight(action: str, actor_hash: str, subject_hash: str, details: dict) -> None:
    with connect() as con:
        con.execute("insert into oversight_log(action, actor_hash, subject_hash, details, created) values (?, ?, ?, ?, ?)",
                    (action, actor_hash, subject_hash, json.dumps(details, ensure_ascii=False), time.time()))


def oversight_rows() -> list[dict]:
    with connect() as con:
        rows = con.execute("select action, actor_hash, subject_hash, details, created from oversight_log order by log_id").fetchall()
    return [{"action": row[0], "actor_hash": row[1], "subject_hash": row[2], "details": json.loads(row[3]), "created": row[4]}
            for row in rows]


def delete_story(story_id: str) -> bool:
    """Delete the submitted story and its separately stored private context."""
    with connect() as con:
        exists = con.execute("select 1 from stories where story_id = ?", (story_id,)).fetchone()
        if exists is None:
            return False
        con.execute("delete from story_private where story_id = ?", (story_id,))
        con.execute("delete from stories where story_id = ?", (story_id,))
    return True
