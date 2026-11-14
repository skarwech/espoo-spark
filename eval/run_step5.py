"""Collect F9–F16 measurements from the frozen synthetic pool and latest evidence artifacts.

This batch audit makes no model calls. Missing browser, load, or human evidence remains pending in the release report.
"""
import hashlib
import json
import math
import os
import re
import secrets
import subprocess
import sys
import time
import uuid
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "eval" / "results" / "step5"
OUT.mkdir(parents=True, exist_ok=True)
os.environ["SPARK_ENGINE"] = "d1"
os.environ["SPARK_AMENDMENT"] = ""
os.environ["SPARK_DB"] = str(OUT / "spark_eval.db")
sys.path.insert(0, str(ROOT))

from api import blind, compliance, event_ops, scheduler, store  # noqa: E402
from engine import llm  # noqa: E402

llm.SUBJECT_INDEX = OUT / "subjects.json"
llm.CACHE = OUT / "llm_cache"
compliance.ROOT = OUT / "synthetic_project"


def read(path: str):
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def status(passed, value=None, evidence=""):
    return {"status": "PENDING" if passed is None else "PASS" if passed else "FAIL",
            "value": value, "evidence": evidence}


def identity_keys(value):
    if isinstance(value, dict):
        direct = {str(key).casefold() for key in value} & compliance.IDENTITY_FIELDS
        return direct | set().union(*(identity_keys(item) for item in value.values()))
    if isinstance(value, list):
        return set().union(*(identity_keys(item) for item in value)) if value else set()
    return set()


def write_review_pack(specificity, usefulness, clusters):
    packet = {
        "instructions": "Review without candidate, recruiter, or mentor identities. Judge each question against its displayed work context.",
        "rating_guide": {
            "f9_specificity": "True when the prompt names a concrete part of the displayed work outcome and is not a generic personality question, despite the generic story.",
            "f9_usefulness": "True when the prompt could help a respectful, relevant work conversation.",
            "f9_answerable": "True when a participant could reasonably answer from work experience or describe a practical first step.",
            "f13_cluster_coherence": "True when the observed counts support the stated cluster theme and its suggested action follows from those counts.",
            "f13_concrete_action": "True when a mentor's recorded next step names a practical action and who will take it; do not enter hiring decisions.",
            "f11_device_check": "True only after the pass was opened online once and the full agenda reopened with the device offline.",
        },
        "f9_specificity": specificity,
        "f9_usefulness": usefulness,
        "f13_clusters": clusters,
        "f13_actions": [{"session_id": f"M{index:02d}",
                         "instruction": "After reviewing the dashboard summaries, record one concrete next action in your own words. Rate whether it names a practical action."}
                        for index in range(1, 4)],
        "f11_device_checks": [{"device_id": f"D{index:02d}",
                               "instruction": "Open a pass online once, switch the device offline, then reopen its pass and confirm the agenda renders."}
                              for index in range(1, 3)],
    }
    path = OUT / "review_pack.json"
    path.write_text(json.dumps(packet, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def score_human_reviews(pack_digest):
    path = OUT / "human_reviews.json"
    if not path.exists():
        return None, "Prepare and complete eval/results/step5/human_reviews.json."
    try:
        reviews = json.loads(path.read_text(encoding="utf-8"))
        packet = json.loads((OUT / "review_pack.json").read_text(encoding="utf-8"))
        if reviews.get("review_pack_sha256") != pack_digest:
            return None, "Ratings do not match the current review packet; prepare a fresh sheet and review it."
        def rows_for(key, id_key, expected):
            rows = reviews.get(key)
            if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                return None
            if [row.get(id_key) for row in rows] != [row.get(id_key) for row in expected]:
                return None
            return rows

        specificity_rows = rows_for("f9_specificity", "case_id", packet["f9_specificity"])
        usefulness_rows = rows_for("f9_usefulness", "case_id", packet["f9_usefulness"])
        cluster_rows = rows_for("f13_clusters", "case_id", packet["f13_clusters"])
        action_rows = rows_for("f13_actions", "session_id", packet["f13_actions"])
        device_rows = rows_for("f11_device_checks", "device_id", packet["f11_device_checks"])
        valid_ratings = lambda rows, field: rows is not None and all(type(row.get(field)) is bool for row in rows)
        specificity = sum(row["specific"] for row in specificity_rows) if valid_ratings(specificity_rows, "specific") else None
        useful = (sum(row["useful"] and row["answerable"] for row in usefulness_rows)
                  if usefulness_rows is not None and all(type(row.get(field)) is bool
                                                         for row in usefulness_rows for field in ("useful", "answerable")) else None)
        coherent = sum(row["coherent"] for row in cluster_rows) if valid_ratings(cluster_rows, "coherent") else None
        actions = (sum(row["concrete_action"] for row in action_rows)
                   if valid_ratings(action_rows, "concrete_action") and
                   all(isinstance(row.get("action_note"), str) and row["action_note"].strip() for row in action_rows) else None)
        devices = (sum(row["offline_pass_opened"] for row in device_rows)
                   if valid_ratings(device_rows, "offline_pass_opened") and
                   all(isinstance(row.get("device_description"), str) and row["device_description"].strip()
                       for row in device_rows) else None)
        seconds = reviews.get("demo_seconds")
        if type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds < 0:
            seconds = None
        scores = {"specificity": specificity, "useful_and_answerable": useful,
                  "coherent_clusters": coherent, "concrete_actions": actions,
                  "offline_devices": devices, "demo_seconds": seconds}
        evidence = "Ratings are scored independently from the current packet-hash-matched review sheet."
        return scores, evidence
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None, "The human review sheet is malformed."


def artifact_record(path, key):
    try:
        artifact = json.loads(path.read_text(encoding="utf-8"))
        record = artifact.get("checks", {}).get(key)
        if record is None:
            record = artifact.get("targets", {}).get(key) if isinstance(artifact.get("targets"), dict) else None
        if record is None and artifact.get("target", {}).get("id") == key:
            record = artifact["target"]
        if record is None and isinstance(artifact.get("targets"), list):
            record = next((item for item in artifact["targets"] if item.get("id") == key), None)
        if record and record.get("status") in ("PASS", "FAIL"):
            return record
    except (OSError, ValueError, AttributeError):
        pass
    return None


def cache_manifest_digest():
    cache = ROOT / "data" / "llm_cache"
    digest = hashlib.sha256()
    files = sorted(path for path in cache.rglob("*.json") if path.name != "_subjects.json")
    for path in files:
        digest.update(path.relative_to(cache).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest(), len(files)


def offline_demo_digest():
    completed = subprocess.run([sys.executable, str(ROOT / "demo.py"), "N-014"], cwd=ROOT,
                               capture_output=True, timeout=30, check=True)
    return hashlib.sha256(completed.stdout + completed.stderr).hexdigest()


def main() -> None:
    run_started = time.perf_counter()
    previous_path = OUT / "f9_f16.json"
    previous = json.loads(previous_path.read_text(encoding="utf-8")) if previous_path.exists() else {}
    release_history = list(previous.get("clean_runs", []))
    checks, targets = [], []

    def check(gate_id, passed, value=None, evidence=""):
        checks.append({"id": gate_id, **status(passed, value, evidence)})

    def target(target_id, passed, value=None, evidence=""):
        targets.append({"id": target_id, **status(passed, value, evidence)})

    people = event_ops._pool()
    needs = event_ops._needs()
    units = read("data/engine_d1/units.json")
    pairs = blind._stored_pairs()
    specificity_cases = []
    usefulness_cases = []
    fixtures = read("eval/fixtures/generic_story_controls.json")
    ordered_needs = [needs[key] for key in sorted(needs)]
    for index, fixture in enumerate(fixtures):
        need = ordered_needs[index % len(ordered_needs)]
        outcome = need["outcomes"][index % len(need["outcomes"])]
        specificity_cases.append({"case_id": fixture["case_id"], "story": fixture["story"],
                                  "work_outcome": outcome["text"],
                                  "question": event_ops.conversation_question(need, outcome, "none")})

    # F9: review 200 generated cards against the stored source IDs and the protected-question scanner.
    valid_refs = violations = yes_no = question_count = 0
    selected = sorted(pairs.items())[:200]
    all_pairs = sorted(pairs.items())
    review_selected = [all_pairs[round(index * (len(all_pairs) - 1) / 29)] for index in range(30)] if len(all_pairs) >= 30 else all_pairs
    selected_keys = {pair_key for pair_key, _pair in selected}
    all_selected = selected + [item for item in review_selected if item[0] not in selected_keys]
    match_ids = {(need_id, candidate_id): blind.match_id(need_id, needs[need_id]["version"], candidate_id)
                 for (need_id, candidate_id), _pair in all_selected}
    store.put_blind_matches([(match_id, need_id, needs[need_id]["version"], candidate_id)
                             for (need_id, candidate_id), match_id in match_ids.items()])
    match_records = store.get_blind_matches(list(match_ids.values()))
    for (need_id, candidate_id), _pair in selected:
        need = needs[need_id]
        match_id = match_ids[(need_id, candidate_id)]
        card = event_ops.conversation_card(match_id, match_records[match_id])
        outcome_ids = {item["id"] for item in need["outcomes"]}
        unit_ids = {item["id"] for item in units[candidate_id]["units"]}
        for item in card["questions"]:
            question_count += 1
            valid_refs += int(any(ref == f"need:{need_id}:outcome:{outcome_id}" for ref in item["source_ids"]
                                  for outcome_id in outcome_ids)
                              and all(not ref.startswith("unit:") or ref[5:] in unit_ids for ref in item["source_ids"]))
            violations += int(bool(event_ops.PERSONAL_ATTRIBUTE_ASK.search(item["question"]) or
                                   event_ops.ASKS_PERSONAL.search(item["question"])))
            yes_no += int(bool(re.match(r"\s*(do|does|did|is|are|can|could|would|will|have|has|should)\b", item["question"], re.I)))
    for (need_id, candidate_id), _pair in review_selected:
        need = needs[need_id]
        match_id = match_ids[(need_id, candidate_id)]
        card = event_ops.conversation_card(match_id, match_records[match_id])
        if not card["questions"]:
            continue
        prompt = card["questions"][0]
        outcome_id = next((ref.rsplit(":", 1)[-1] for ref in prompt["source_ids"]
                           if ref.startswith(f"need:{need_id}:outcome:")), None)
        outcome_text = next((row["text"] for row in need["outcomes"] if row["id"] == outcome_id), "")
        usefulness_cases.append({"case_id": f"Q{len(usefulness_cases) + 1:02d}",
                                 "work_outcome": outcome_text, "question": prompt["question"],
                                 "evidence": prompt["evidence"]})
    check("F9-IDs", len(selected) == 200 and valid_refs == question_count, f"{valid_refs}/{question_count}", "Question references resolve to stored Need outcomes and evidence units.")
    check("F9-SAFETY", violations == 0, violations)
    target("F9-YESNO", (yes_no / max(1, question_count)) <= 0.05, yes_no / max(1, question_count))

    planted_conflicts = clean_false_flags = 0
    for index in range(10):
        base_need = needs[sorted(needs)[index % len(needs)]]
        need = json.loads(json.dumps(base_need))
        if index % 3 == 0:
            need["constraints"]["start"] = "1900-01"
            constraints = {"start": "2099-01", "languages": [], "work_mode": "onsite"}
        elif index % 3 == 1:
            need["languages"] = [{"lang": "qzx", "required": True}]
            constraints = {"start": need["constraints"]["start"], "languages": [], "work_mode": "onsite"}
        else:
            need["constraints"]["onsite"] = "onsite"
            constraints = {"start": need["constraints"]["start"], "languages": [], "work_mode": "remote"}
        planted_conflicts += int(bool(event_ops._conflict_flags(need, constraints, f"control-{index}")))
        clean_need = needs[sorted(needs)[index % len(needs)]]
        clean_constraints = {"start": clean_need["constraints"]["start"],
                             "languages": [{"lang": item["lang"], "level": "B2"} for item in clean_need["languages"] if item["required"]],
                             "work_mode": "onsite"}
        clean_false_flags += int(bool(event_ops._conflict_flags(clean_need, clean_constraints, f"clean-{index}")))
    target("F9-CONFLICTS", planted_conflicts >= 9 and clean_false_flags <= 1,
           {"planted_detected": f"{planted_conflicts}/10", "clean_false_flags": f"{clean_false_flags}/10"})
    # F10: 20 seeds by five capacities; check eligibility, capacity and attendee time conflicts.
    schedule_checks = 0
    large_runtimes = []
    large_two_slot_ratios = []
    large_unscheduled_rates = []
    large_stretch_shares = []
    final_event = "step5-seed-19-size-15"
    for seed_index in range(20):
        for capacity in (1, 2, 5, 10, 15):
            event_id = f"step5-seed-{seed_index:02d}-size-{capacity}"
            started = time.perf_counter()
            agenda = event_ops.create_agenda(event_id, "2027-03-15", slots_per_company=capacity,
                                             seed=f"step5:{seed_index}:{capacity}")
            elapsed = time.perf_counter() - started
            if capacity == 15:
                large_runtimes.append(elapsed)
            company_counts = Counter()
            person_times = defaultdict(list)
            person_companies = defaultdict(set)
            links = store.get_blind_matches([slot["match_id"] for slot in agenda["slots"]])
            valid = True
            for slot in agenda["slots"]:
                match = links.get(slot["match_id"])
                if match is None:
                    valid = False
                    continue
                candidate_id = match["candidate_id"]
                need = needs[slot["need_id"]]
                company_id = need["company_id"]
                company_counts[company_id] += 1
                person_times[candidate_id].append(slot["time"])
                valid &= company_id not in person_companies[candidate_id]
                person_companies[candidate_id].add(company_id)
                valid &= event_ops._eligible(need, people[candidate_id])
                valid &= company_counts[company_id] <= min(capacity, event_ops._companies()[company_id]["capacity"])
            valid &= all(len(times) == len(set(times)) for times in person_times.values())
            valid &= all(len(companies_for_person) <= 2 for companies_for_person in person_companies.values())
            schedule_checks += int(valid)
            if capacity == 15:
                large_two_slot_ratios.append(agenda["people_with_two_slots"] / max(1, agenda["eligible_for_two_slots"]))
                viable_total = agenda["unscheduled_viable_pairs"] + len(agenda["slots"])
                large_unscheduled_rates.append(agenda["unscheduled_viable_pairs"] / max(1, viable_total))
                for need in needs.values():
                    has_eligible_stretch = any(
                        pairs[(need["need_id"], candidate_id)]["tier"] == "stretch" and event_ops._eligible(need, person)
                        for candidate_id, person in people.items())
                    if has_eligible_stretch:
                        large_stretch_shares.append(agenda["stretch_share_by_company"].get(need["company_id"], 0))
    check("F10-CONSTRAINTS", schedule_checks == 100, f"{schedule_checks}/100 agendas")

    # The final schedule is still a draft here: unapproved issuance must be blocked.
    blocked_unapproved = False
    try:
        event_ops.publish_passes(final_event)
    except PermissionError:
        blocked_unapproved = True
    check("F10-APPROVAL", blocked_unapproved, blocked_unapproved)
    draft = store.get_agenda(final_event)["agenda"]
    min_two_slot_ratio = min(large_two_slot_ratios, default=0)
    target("F10-TWO-SLOTS", bool(large_two_slot_ratios) and min_two_slot_ratio >= 0.95,
           round(min_two_slot_ratio, 4), "Worst seed among the 20 capacity-15 agendas; assigned attendees divided by attendees with at least two viable companies.")
    max_unscheduled_rate = max(large_unscheduled_rates, default=1)
    target("F10-UNSCHEDULED", bool(large_unscheduled_rates) and max_unscheduled_rate <= 0.05,
           round(max_unscheduled_rate, 4), "Worst seed among the 20 capacity-15 agendas; unselected viable pairs divided by all viable pairs.")
    target("F10-RUNTIME", bool(large_runtimes) and max(large_runtimes) < 5, round(max(large_runtimes, default=0), 4), "Maximum observed 150 × 25 run across 20 seeds, seconds.")
    stretch_ok = bool(large_stretch_shares) and all(0.18 <= share <= 0.22 for share in large_stretch_shares)
    target("F10-STRETCH", stretch_ok, {"seed_company_observations": len(large_stretch_shares),
                                        "minimum_share": round(min(large_stretch_shares, default=0), 4),
                                        "maximum_share": round(max(large_stretch_shares, default=0), 4)},
           "All companies with eligible Stretch attendees across the 20 capacity-15 agendas.")
    optimizer = scheduler.benchmark()
    target("F10-OPTIMUM", optimizer["aggregate_ratio"] >= 0.95, optimizer,
           "Aggregate objective score divided by the exact DP optimum across ten seeded small integer-assignment instances.")

    actor = "step5-evaluation-organizer"
    store.approve_agenda(final_event, compliance.digest(actor))
    passes = event_ops.publish_passes(final_event)
    pass_rows = []
    pass_tokens = []
    identities = read("data/out/identities.json")
    private_values = [str(item[field]).casefold() for item in identities
                      for field in ("name", "email", "nationality", "school") if item.get(field)]
    pass_valid = True
    max_pass_bytes = 0
    for issued in passes:
        token = issued["token"]
        pass_tokens.append(token)
        opened = event_ops.open_pass(token)
        encoded = json.dumps(opened, ensure_ascii=False, sort_keys=True)
        max_pass_bytes = max(max_pass_bytes, len(encoded.encode("utf-8")))
        pass_valid &= bool(re.fullmatch(r"[A-Za-z0-9_-]{43}", token))
        pass_valid &= opened["content_hash"] == issued["content_hash"]
        pass_valid &= opened["approved_agenda_hash"] == hashlib.sha256(
            json.dumps(draft, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()
        pass_valid &= not any(value in f"/event-pass/{token}".casefold() for value in private_values)
        pass_valid &= not any(value in encoded.casefold() for value in private_values)
        pass_valid &= not identity_keys(opened)
        pass_rows.append(opened)
    check("F11-HASH", pass_valid and len(passes) == len(people),
          f"{len(passes)}/{len(people)} passes; all content and approved-agenda hashes match")
    delivery_refs = [item.get("attendee_ref") for item in passes]
    delivery_isolated = all(isinstance(ref, str) and ref not in token
                            and ref not in json.dumps(payload, ensure_ascii=False) and "attendee_ref" not in payload
                            for ref, token, payload in
                            zip(delivery_refs, pass_tokens, pass_rows))
    check("F11-DELIVERY", len(delivery_refs) == len(people) and set(delivery_refs) == set(people) and delivery_isolated,
          "Organizer-only attendee references map all private links and stay out of pass payloads and URLs.")
    issued_hashes = {hashlib.sha256(token.encode("ascii")).hexdigest() for token in pass_tokens}
    probe_hits = sum(hashlib.sha256(secrets.token_urlsafe(32).encode("ascii")).hexdigest() in issued_hashes for _ in range(10000))
    url_valid = bool(pass_tokens) and len(set(pass_tokens)) == len(pass_tokens) and all(bool(re.fullmatch(r"[A-Za-z0-9_-]{43}", token)) and
                                          not any(value in f"/event-pass/{token}".casefold() for value in private_values)
                                          for token in pass_tokens)
    check("F11-URL", url_valid and probe_hits == 0,
          {"token_chars": sorted({len(token) for token in pass_tokens}), "random_probe_count": 10000, "hits": probe_hits})
    third_party_ref = re.compile(r"(?:src|href)\s*=\s*['\"]https?://|fetch\(\s*['\"]https?://", re.I)
    pass_sources = [ROOT / "web" / "templates" / "event_pass.html", ROOT / "web" / "templates" / "base.html",
                    ROOT / "web" / "static" / "event_pass.js", ROOT / "web" / "static" / "sw.js"]
    third_party_hits = [str(path.relative_to(ROOT)) for path in pass_sources
                        if third_party_ref.search(path.read_text(encoding="utf-8"))]
    check("F11-THIRD-PARTY-STATIC", not third_party_hits, third_party_hits)
    browser_path = OUT / "browser.json"
    offline_browser = artifact_record(browser_path, "F11-OFFLINE")
    browser_expiry = artifact_record(browser_path, "F11-EXPIRED")
    browser_network = artifact_record(browser_path, "F11-THIRD-PARTY")
    check("F11-OFFLINE", offline_browser["status"] == "PASS" if offline_browser else None,
          offline_browser.get("value") if offline_browser else None,
          offline_browser.get("evidence", "Run eval/browser_step5.py against the local app.") if offline_browser else "Run eval/browser_step5.py against the local app.")
    check("F11-THIRD-PARTY", browser_network["status"] == "PASS" if browser_network else None,
          browser_network.get("value") if browser_network else None,
          browser_network.get("evidence", "Run the browser request capture.") if browser_network else "Run the browser request capture.")
    target("F11-SIZE", max_pass_bytes <= 300_000, max_pass_bytes)
    load_record = artifact_record(OUT / "load.json", "F11-LOAD")
    target("F11-LOAD", load_record["status"] == "PASS" if load_record else None,
           load_record.get("value") if load_record else None,
           load_record.get("evidence", "Run eval/load_step5.py against the local app.") if load_record else "Run eval/load_step5.py against the local app.")
    expired_token = secrets.token_urlsafe(32)
    store.put_pass(hashlib.sha256(expired_token.encode("ascii")).hexdigest(), final_event, "expired", {}, time.time() - 1)
    try:
        event_ops.open_pass(expired_token)
        expired_blocked = False
    except TimeoutError:
        expired_blocked = True
    check("F11-EXPIRED", expired_blocked, expired_blocked)
    check("F11-EXPIRED-UI", browser_expiry["status"] == "PASS" if browser_expiry else None,
          browser_expiry.get("value") if browser_expiry else None,
          browser_expiry.get("evidence", "Run the browser expiry-state check.") if browser_expiry else "Run the browser expiry-state check.")

    # F12: 20 unique offline event IDs, with two retries, reconcile to the raw ledger.
    chosen = []
    for token, payload in zip(pass_tokens, pass_rows):
        for slot in payload["agenda"]:
            chosen.append((token, slot["slot_id"]))
            if len(chosen) == 20:
                break
        if len(chosen) == 20:
            break
    pulse_ids = []
    duplicate_posts = []
    for index, (token, slot_id) in enumerate(chosen):
        item = {"client_event_id": uuid.uuid4().hex, "slot_id": slot_id,
                "response": "useful" if index % 2 == 0 else "not_useful"}
        pulse_ids.append(item["client_event_id"])
        first = event_ops.add_pulse(token, item["client_event_id"], slot_id, item["response"])
        if index < 2:
            same_id_retry = event_ops.add_pulse(token, item["client_event_id"], slot_id, item["response"])
            fresh_id_double_tap = event_ops.add_pulse(token, uuid.uuid4().hex, slot_id,
                                                     "not_useful" if item["response"] == "useful" else "useful")
            duplicate_posts.extend([same_id_retry["duplicate"], fresh_id_double_tap["duplicate"]])
        else:
            duplicate_posts.append(not first["duplicate"])
    raw_pulses = store.pulse_rows(final_event)
    pulse_dashboard = event_ops.dashboard(final_event)
    direct_idempotency = (len(chosen) == 20 and len(raw_pulses) == 20 and len(set(pulse_ids)) == 20
                          and all(duplicate_posts))
    browser_pulses = artifact_record(OUT / "browser.json", "F12-OFFLINE-PULSES")
    if not direct_idempotency:
        idempotency_pass = False
    elif browser_pulses is None:
        idempotency_pass = None
    else:
        idempotency_pass = browser_pulses["status"] == "PASS"
    check("F12-IDEMPOTENCY", idempotency_pass,
          {"direct_api": f"{len(raw_pulses)} records from {len(chosen) + 2 * min(2, len(chosen))} posts",
           "offline_browser": browser_pulses.get("value") if browser_pulses else None},
          browser_pulses.get("evidence", "Run the 20-response offline browser pulse flow.") if browser_pulses else
          "API retry behavior checked locally; the 20-response offline browser flow remains pending.")
    pulse_fields = {"token_hash", "slot_id", "response", "created"}
    pulse_private = all(set(row) == pulse_fields and
                        re.fullmatch(r"[0-9a-f]{64}", row.get("token_hash", "")) is not None and
                        set(row).isdisjoint(compliance.IDENTITY_FIELDS) for row in raw_pulses)
    check("F12-PRIVACY", pulse_private, sorted({key for row in raw_pulses for key in row}))
    response_counts = Counter(row["response"] for row in raw_pulses)
    pulse_view = pulse_dashboard["pulse_by_response"]
    pulse_cells_reconcile = all((pulse_view.get(key) == count if count >= 5 else key not in pulse_view)
                                for key, count in response_counts.items())
    check("F12-RECONCILE", pulse_dashboard["pulse_responses"] == len(raw_pulses) and pulse_cells_reconcile,
          {"dashboard_total": pulse_dashboard["pulse_responses"], "raw_total": len(raw_pulses),
           "dashboard_cells": pulse_view, "raw_cells": response_counts})
    check("F12-SUPPRESSION", pulse_cells_reconcile, "Response groups smaller than five are absent.")
    raw_company_counts = Counter(slot["company"] for slot in draft["slots"])
    shown_company_counts = {row["company"]: row["scheduled_conversations"] for row in pulse_dashboard["company_cells"]}
    company_reconcile = pulse_dashboard["scheduled"] == len(draft["slots"]) and all(
        shown_company_counts.get(company) == count if count >= 5 else company not in shown_company_counts
        for company, count in raw_company_counts.items())
    check("F13-RECONCILE", company_reconcile,
          {"dashboard_total": pulse_dashboard["scheduled"], "agenda_total": len(draft["slots"])})
    small_cells = [company for company, count in raw_company_counts.items()
                   if count < 5 and company in shown_company_counts]
    check("F13-SUPPRESSION", not small_cells, small_cells)
    os.environ["SPARK_ORGANIZER_TOKEN"] = "step5-evaluation-token"
    check("F13-AUTH", not compliance.organizer_allowed("wrong-token"), "wrong organizer token denied")
    tap_record = artifact_record(browser_path, "F12-TAPS")
    target("F12-TAPS", tap_record["status"] == "PASS" if tap_record else None,
           tap_record.get("value") if tap_record else None,
           tap_record.get("evidence", "Run the browser pulse and sync flow.") if tap_record else "Run the browser pulse and sync flow.")

    # F14: counterfactual identity, translation/style twins and planted positive controls.
    audit = event_ops.fairness_audit()
    identity = audit["identity_twins"]
    check("F14-IDENTITY", identity["post_redaction_differences"] == 0 and identity["tier_flips"] == 0,
          identity)
    check("F14-POSITIVE", audit["positive_control"]["flagged"] == 5 and audit["positive_control"]["total"] == 5,
          audit["positive_control"])
    limits_section = (ROOT / "docs" / "method.md").read_text(encoding="utf-8")
    check("F14-LIMITS", "## 5. Limits" in limits_section, "method.md §5")
    target("F14-PERTURBATION", audit["perturbation"]["cleanliness"] >= 0.95, audit["perturbation"])
    target("F14-TRANSLATION", audit["translation_flip_rate"] <= 0.05, audit["translation_flip_rate"])
    target("F14-STYLE", audit["style_flip_rate"] <= 0.05, audit["style_flip_rate"])
    interval = audit["naive_baseline"]["mismatch_rate_95_ci"]
    target("F14-BASELINE-CI", len(interval) == 2 and interval[0] <= interval[1], interval,
           "Candidate-cluster bootstrap; the engine is a descriptive comparator, not human ground truth.")

    # F15: evidence paths, storage schema, identity-free logs, deletion and gate-bypass attempts.
    traced = [compliance.trace_slot(final_event, slot["slot_id"]) for slot in draft["slots"][:20]]
    trace_ok = len(traced) == min(20, len(draft["slots"])) and all(
        path["chain"] and len(path["chain"]) <= 3 for item in traced for path in item["paths"])
    check("F15-TRACE", trace_ok, f"{len(traced)} slots traced")
    plain_why = all(isinstance(slot.get("why"), str) and slot["why"].strip() for slot in draft["slots"])
    check("F15-WHY", plain_why, f"{sum(bool(slot.get('why', '').strip()) for slot in draft['slots'])}/{len(draft['slots'])} slots")
    schema = compliance.declared_schema()
    check("F15-SCHEMA", schema["valid"], schema)
    log_audit = compliance.logs_identity_free()
    check("F15-LOGS", not log_audit["identity_fields"], log_audit)
    deletion_ok = True
    for _ in range(5):
        story_id = "S-" + uuid.uuid4().hex
        store.put_story(story_id, {"did": "synthetic deletion control"}, "private synthetic context")
        cache_key = uuid.uuid4().hex
        cache_file = llm.CACHE / "extract" / f"{cache_key}.json"
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text("{}", encoding="utf-8")
        llm._register_subject(story_id, "extract", cache_key)
        live_file = compliance.ROOT / "data" / "engine_d1" / "live" / f"story-{story_id}.json"
        live_file.parent.mkdir(parents=True, exist_ok=True)
        live_file.write_text("{}", encoding="utf-8")
        compliance.delete_participant(story_id, story_id)
        with store.connect() as con:
            private_exists = con.execute("select 1 from story_private where story_id = ?", (story_id,)).fetchone()
        subject_index = json.loads(llm.SUBJECT_INDEX.read_text(encoding="utf-8")) if llm.SUBJECT_INDEX.exists() else {}
        deletion_ok &= store.get_story(story_id) is None and private_exists is None and not cache_file.exists() \
            and not live_file.exists() and story_id not in subject_index
    check("F15-DELETE", deletion_ok, "5 synthetic participant records deleted")
    bypass = []
    bypass.append(not compliance.organizer_allowed("invalid"))
    bypass.append(blocked_unapproved)
    try:
        compliance.approve_event("unknown-step5-event", actor)
        bypass.append(False)
    except KeyError:
        bypass.append(True)
    try:
        event_ops.add_pulse(secrets.token_urlsafe(32), "invalid-event-id", "invalid-slot", "useful")
        bypass.append(False)
    except (KeyError, ValueError):
        bypass.append(True)
    try:
        compliance.delete_participant("S-" + "0" * 32, "wrong-code")
        bypass.append(False)
    except PermissionError:
        bypass.append(True)
    check("F15-BYPASS", len(bypass) == 5 and all(bypass), bypass)
    forbidden_inference = re.compile(r"\b(?:emotion_(?:score|inference|classifier)|ai_text_detector|detect_ai_(?:text|generated))\b", re.I)
    source_files = list((ROOT / "api").glob("*.py")) + list((ROOT / "engine").glob("*.py")) + \
        list((ROOT / "web" / "static").glob("*.js"))
    forbidden_hits = [str(path.relative_to(ROOT)) for path in source_files
                      if forbidden_inference.search(path.read_text(encoding="utf-8"))]
    check("F15-NO-INFERENCE", not forbidden_hits, forbidden_hits)

    cluster_cases = [{"case_id": f"C{index:02d}", **cluster}
                     for index, cluster in enumerate(pulse_dashboard["clusters"], start=1)]
    review_digest = write_review_pack(specificity_cases, usefulness_cases, cluster_cases)
    human, human_evidence = score_human_reviews(review_digest)
    def review_note(key):
        if human is None:
            return human_evidence
        return "Based on completed human ratings." if human[key] is not None else f"Complete every rating in the {key} section of human_reviews.json."

    check("F11-OFFLINE-DEVICE", human["offline_devices"] == 2 if human and human["offline_devices"] is not None else None,
          f"{human['offline_devices']}/2" if human and human["offline_devices"] is not None else None,
          review_note("offline_devices") + " Test on two physical devices; isolated browser profiles do not count.")
    target("F9-SPECIFICITY", human["specificity"] >= 9 if human and human["specificity"] is not None else None,
           f"{human['specificity']}/10" if human and human["specificity"] is not None else None,
           review_note("specificity") + " Ten generic-story controls.")
    target("F9-USEFUL", human["useful_and_answerable"] / 30 >= 0.85 if human and human["useful_and_answerable"] is not None else None,
           f"{human['useful_and_answerable']}/30" if human and human["useful_and_answerable"] is not None else None,
           review_note("useful_and_answerable") + " Rate both usefulness and answerability.")
    target("F13-CLUSTERS", human["coherent_clusters"] >= 4 if human and human["coherent_clusters"] is not None else None,
           f"{human['coherent_clusters']}/5" if human and human["coherent_clusters"] is not None else None,
           review_note("coherent_clusters") + " Five dashboard summaries.")
    target("F13-ACTIONS", human["concrete_actions"] >= 2 if human and human["concrete_actions"] is not None else None,
           f"{human['concrete_actions']}/3" if human and human["concrete_actions"] is not None else None,
           review_note("concrete_actions") + " Record an action from each of three mentor sessions.")
    target("F16-DEMO", human["demo_seconds"] <= 240 if human and human["demo_seconds"] is not None else None,
           human["demo_seconds"] if human and human["demo_seconds"] is not None else None,
           review_note("demo_seconds") + " Time the end-to-end demonstration in seconds.")

    cache_digest, cache_files = cache_manifest_digest()
    replay_error = None
    try:
        replay_first = offline_demo_digest()
        replay_second = offline_demo_digest()
    except (OSError, subprocess.SubprocessError) as error:
        replay_first = replay_second = None
        replay_error = type(error).__name__
    replay_equal = replay_first is not None and replay_first == replay_second
    old_replay = previous.get("cache_replay", {})
    if replay_error:
        cache_pass = False
    elif not replay_equal:
        cache_pass = False
    elif old_replay.get("cache_manifest_sha256") and old_replay.get("demo_output_sha256"):
        cache_pass = (old_replay["cache_manifest_sha256"] == cache_digest
                      and old_replay["demo_output_sha256"] == replay_first)
    else:
        cache_pass = None
    cache_replay = {"same_within_run": replay_equal, "cache_manifest_sha256": cache_digest,
                    "cache_json_files": cache_files, "demo_output_sha256": replay_first,
                    "error": replay_error}
    target("F16-CACHE", cache_pass, cache_replay,
           "Two offline demo replays match; subsequent audit runs also compare the frozen model-cache manifest and output digest." if not replay_error
           else f"Offline replay failed: {replay_error}.")

    # F16 records every audit run and only counts consecutive runs with every hard gate green.
    hard_statuses = [item["status"] for item in checks]
    run_status = "FAIL" if "FAIL" in hard_statuses else "PASS" if all(value == "PASS" for value in hard_statuses) else "PENDING"
    release_history.append({"id": f"step5-{uuid.uuid4().hex[:8]}", "status": run_status,
                            "duration_seconds": round(time.perf_counter() - run_started, 3)})
    consecutive = 0
    for item in reversed(release_history):
        if item.get("status") != "PASS":
            break
        consecutive += 1
    check("F16-RUNS", True if consecutive >= 3 else None, f"{consecutive}/3 consecutive clean runs")
    open_issues = []
    if human is None or any(value is None for value in human.values()):
        open_issues.append("Human prompt and dashboard reviews, three mentor sessions, and the timed demo are pending or incomplete.")
    open_issues.append("Accessibility and responsible-AI mentor reviews remain outstanding.")
    if not offline_browser or offline_browser["status"] != "PASS" or not browser_network or browser_network["status"] != "PASS":
        open_issues.append("Browser offline and third-party request checks are missing or failed; rerun eval/browser_step5.py.")
    if human is None or human["offline_devices"] != 2:
        open_issues.append("Offline behavior still needs verification on two physical devices; isolated browser profiles are only a simulation.")
    if not load_record:
        open_issues.append("The 1,000-request load measurement is missing; run eval/load_step5.py.")
    elif load_record["status"] != "PASS":
        open_issues.append("The 1,000-request load target failed; inspect eval/results/step5/load.json.")
    if cache_pass is False:
        open_issues.append("Offline replay or cross-run cache consistency failed.")
    if optimizer["aggregate_ratio"] < 0.95:
        open_issues.append("The greedy scheduler is below the registered small-instance exact-objective threshold.")
    open_issues.append("The project’s existing d1 story-injection gate fails; recommendations are not validated hiring advice.")
    if consecutive < 3:
        open_issues.append("Three consecutive clean release runs have not been recorded.")
    result = {"created_at": time.time(), "checks": checks, "targets": targets, "clean_runs": release_history,
              "open_issues": open_issues, "cache_replay": cache_replay,
              "summary": {"agenda_schedules": schedule_checks, "question_cards": len(selected), "passes_issued": len(passes),
                          "scheduler_benchmark": optimizer,
                          "pulses": len(raw_pulses), "fairness_audit": audit,
                          "human_reviews": human}}
    (OUT / "f9_f16.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {OUT.relative_to(ROOT) / 'f9_f16.json'} ({len(checks)} checks, {len(targets)} targets)")


if __name__ == "__main__":
    main()
