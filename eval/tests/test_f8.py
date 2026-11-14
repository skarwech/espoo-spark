"""F8 Blind View and Reveal hard gates (docs/method.md §8)."""
import json
import os
import re
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("SPARK_ENGINE", "d1")


def data(path: str):
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("SPARK_DB", str(Path(tempfile.mkdtemp()) / "f8.db"))
    from fastapi.testclient import TestClient
    from api.main import app
    return TestClient(app)


def seed_confirmed_need():
    from api import store
    need = next(item for item in data("data/out/needs.json") if item["need_id"] == "N-014")
    store.put_need({"card": need, "unmapped_skills": [], "warnings": [], "summary": ""})
    assert store.confirm_need(need["need_id"], need["version"])
    return need


@pytest.mark.hard
def test_f8_blind_payloads_have_no_identity_fields_or_values(client):
    seed_confirmed_need()
    response = client.get("/api/needs/N-014/blind-candidates")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["candidates"]) == 150
    forbidden_keys = {"candidate_id", "name", "email", "nationality", "school", "birth_year", "private_context"}

    def keys(value):
        if isinstance(value, dict):
            return set(value) | set().union(*(keys(item) for item in value.values()))
        if isinstance(value, list):
            return set().union(*(keys(item) for item in value)) if value else set()
        return set()

    assert not forbidden_keys & keys(payload)
    shown = json.dumps(payload, ensure_ascii=False).casefold()
    identities = data("data/out/identities.json")
    values = {str(item[field]).casefold() for item in identities for field in ("name", "email", "nationality", "school") if item.get(field)}
    assert not [value for value in values if value in shown]
    assert not re.search(r"\bJ-\d{3}\b", shown)


@pytest.mark.hard
def test_f8_one_sided_opt_in_never_reveals_then_logs_both_timestamps(client):
    seed_confirmed_need()
    match = client.get("/api/needs/N-014/blind-candidates").json()["candidates"][0]["match_id"]
    recruiter = client.post(f"/api/blind-matches/{match}/opt-in/recruiter")
    assert recruiter.status_code == 200
    assert recruiter.json()["state"] == "awaiting_candidate" and "reveal" not in recruiter.json()
    waiting = client.get(f"/api/blind-matches/{match}/reveal?role=recruiter")
    assert waiting.status_code == 200 and not waiting.json()["revealed"] and "reveal" not in waiting.json()
    candidate = client.post(f"/api/blind-matches/{match}/opt-in/candidate")
    assert candidate.status_code == 200 and candidate.json()["revealed"]
    assert set(candidate.json()["reveal"]) == {"name", "industry"}
    revealed = client.get(f"/api/blind-matches/{match}/reveal?role=recruiter")
    assert revealed.status_code == 200 and set(revealed.json()["reveal"]) == {"name", "email"}
    from api import store
    log = store.get_reveal_log(match)
    assert log and log["recruiter_consented_at"] and log["candidate_consented_at"] and log["revealed_at"]


def test_f8_redaction_recall_on_sixty_planted_mentions():
    from api import blind
    fixtures = data("eval/fixtures/blind_mentions.json")
    assert len(fixtures) == 60
    missed = [item["id"] for item in fixtures if item["mention"].casefold() in blind.redact(item["text"]).casefold()]
    assert len(missed) <= 3, missed
