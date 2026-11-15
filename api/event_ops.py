"""F9-F14 event workflows over the frozen synthetic Match Lens outputs."""
import hashlib
import json
import re
import secrets
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

from api import blind, pool_preview, scheduler, store
from engine import lens as engine_lens, run as engine_run

ROOT = Path(__file__).resolve().parents[1]
SUPPORT = {"supported", "partial", "adjacent", "none"}
PROTECTED = re.compile(r"\b(age|national\w*|ethnicity|race|religion|gender|sex|pregnan\w*|marital|family status|disability|health|politic\w*|union member|migration status)\b", re.I)
ASKS_PERSONAL = re.compile(r"\b(what is your|tell me your|how old|where are you from|what religion|are you married|do you have children)\b", re.I)
PERSONAL_ATTRIBUTE_ASK = re.compile(r"\b(?:how old are you|where are you from|what is your (?:age|nationality|ethnicity|race|religion|gender|sex|marital status|family status|health|disability)|tell me your (?:age|nationality|ethnicity|race|religion|gender|sex|family|health|disability)|do you have (?:children|kids|a disability)|are you (?:married|pregnant|religious|disabled))\b", re.I)
DISCRIMINATORY = re.compile(r"\b(reject|exclude|prefer|only|must be|do not interview|do not hire|do not consider|not consider)\b", re.I)


def _safe_source(value) -> str:
    """Keep work text readable while removing known direct identifiers before prompting or displaying it."""
    return blind.redact(str(value or "").strip())


def _read(path: str):
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _pool() -> dict:
    return {item["candidate_id"]: item for item in _read("data/out/jobseekers.json")}


@lru_cache(maxsize=1)
def _needs() -> dict:
    return {item["need_id"]: item for item in _read("data/out/needs.json")}


@lru_cache(maxsize=1)
def _companies() -> dict:
    return {item["company_id"]: item for item in _read("data/out/companies.json")}


def _need(need_id: str, version: int | None = None) -> dict:
    dynamic = store.get_need(need_id, version)
    return dynamic["card"] if dynamic else _needs()[need_id]


def _pair(need_id: str, candidate_id: str) -> dict:
    cached = blind._stored_pairs().get((need_id, candidate_id))
    if cached is not None:
        return cached
    card = _need(need_id)
    pairs = blind._pairs_for_need(card)
    return pairs[candidate_id]


def _conflict_flags(need: dict, constraints: dict, match_id: str) -> list[dict]:
    conflicts = []
    if not pool_preview.start_ok(need, constraints):
        conflicts.append({"kind": "start", "reason": "The stated earliest start is later than the need's start month.",
                          "source_ids": [f"need:{need['need_id']}:constraints.start", f"candidate:{match_id}:preferences.start"]})
    if not pool_preview.language_ok(need, constraints):
        conflicts.append({"kind": "language", "reason": "A required working language is below the stated B2 level or is not listed.",
                          "source_ids": [f"need:{need['need_id']}:languages", f"candidate:{match_id}:preferences.languages"]})
    if not pool_preview.work_mode_ok(need, constraints):
        conflicts.append({"kind": "work_mode", "reason": "The requested work mode differs from the person's stated preference.",
                          "source_ids": [f"need:{need['need_id']}:constraints.onsite", f"candidate:{match_id}:preferences.work_mode"]})
    return conflicts


def conversation_question(need: dict, outcome: dict, support: str, quote: str = "") -> str:
    """Build one open prompt for a Need outcome and evidence state, with identifier redaction."""
    if support not in SUPPORT:
        raise ValueError("unknown evidence label")
    outcome_text = _safe_source(outcome["text"])
    quote_text = _safe_source(quote)
    if support == "supported" and quote_text:
        prompt = f"You described “{quote_text}”. What part did you own, and how did you check the result against {outcome_text}?"
    elif support == "partial" and quote_text:
        prompt = f"You mentioned “{quote_text}”. What would you add to make your contribution to {outcome_text} measurable?"
    elif support == "adjacent" and quote_text:
        prompt = f"You have related experience in “{quote_text}”. How would you adapt that experience to {outcome_text}, and what would you check first?"
    else:
        prompt = f"What first step would you take toward {outcome_text}, and what evidence would tell you it worked?"
    if ASKS_PERSONAL.search(prompt) or PERSONAL_ATTRIBUTE_ASK.search(prompt):
        prompt = f"What work example would help you explain your approach to {outcome_text}, and what result would you look for?"
    return prompt


def conversation_card(match_id: str, match_record: dict | None = None) -> dict:
    """Create 2–3 open questions grounded in need/outcome/evidence ids and practical conflicts."""
    match = match_record or store.get_blind_match(match_id)
    if match is None:
        raise KeyError(match_id)
    need = _need(match["need_id"], match["need_version"])
    pair = _pair(match["need_id"], match["candidate_id"])
    candidate = _pool()[match["candidate_id"]]
    questions = []
    for grid in pair["grid"]:
        prompt = conversation_question(need, {"text": grid["outcome"]}, grid["support"], grid["quote"])
        refs = [f"need:{need['need_id']}:outcome:{grid['outcome_id']}"]
        if grid["unit_id"]:
            refs.append(f"unit:{grid['unit_id']}")
        questions.append({"question": prompt, "source_ids": refs, "evidence": grid["support"]})
    cons = candidate["constraints"]
    output = {"match_id": match_id, "questions": questions[:3], "conflicts": _conflict_flags(need, cons, match_id),
              "why": blind.redact(pair["why"]),
              "framing": "Conversation prompts are optional starting points. Discuss the work together; they are not an interview score."}
    if any(ASKS_PERSONAL.search(item["question"]) or PERSONAL_ATTRIBUTE_ASK.search(item["question"])
           for item in output["questions"]):
        raise ValueError("a conversation prompt contains a protected-attribute question")
    return output


def _eligible(need: dict, seeker: dict) -> bool:
    return pool_preview.start_ok(need, seeker["constraints"]) and pool_preview.language_ok(need, seeker["constraints"]) \
        and pool_preview.work_mode_ok(need, seeker["constraints"])


def _time_at(start_time: str, index: int, interval: int) -> str:
    hour, minute = (int(x) for x in start_time.split(":"))
    total = hour * 60 + minute + index * interval
    return f"{total // 60:02d}:{total % 60:02d}"


def create_agenda(event_id: str, event_date: str, start_time: str = "09:00", interval_minutes: int = 15,
                  slots_per_company: int = 15, seed: str = "spark-event-1", *, persist: bool = True) -> dict:
    """Build a deterministic greedy agenda; persist blind links and the draft unless this is a read-only preview."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{3,48}", event_id):
        raise ValueError("event_id must be 3–48 letters, digits, '_' or '-'")
    datetime.strptime(event_date, "%Y-%m-%d")
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", start_time):
        raise ValueError("start_time must use HH:MM")
    if interval_minutes not in (10, 15, 20, 30) or not 1 <= slots_per_company <= 30:
        raise ValueError("unsupported interval or company capacity")

    needs = list(_needs().values())
    lens = blind._stored_pairs()
    people = _pool()
    companies = _companies()
    need_by_company = {n["company_id"]: n for n in needs}
    max_slots = max((min(slots_per_company, company["capacity"]) for company in companies.values()), default=0)
    hour, minute = (int(x) for x in start_time.split(":"))
    if hour * 60 + minute + max(0, max_slots - 1) * interval_minutes > 23 * 60 + 59:
        raise ValueError("the configured agenda would extend past midnight")
    available = defaultdict(list)
    pair_by = {}
    for company_id, need in need_by_company.items():
        for candidate_id, seeker in people.items():
            pair = lens[(need["need_id"], candidate_id)]
            if pair["tier"] != "none" and _eligible(need, seeker):
                available[candidate_id].append((company_id, need, pair))
                pair_by[(company_id, candidate_id)] = (need, pair)

    per_company = defaultdict(list)
    capacities = {company_id: min(slots_per_company, company["capacity"])
                  for company_id, company in companies.items()}
    edge_tiers = {(candidate_id, company_id): pair["tier"]
                  for (company_id, candidate_id), (_need, pair) in pair_by.items()}
    assignments = scheduler.greedy_assign(edge_tiers, capacities, seed)
    chosen_by_person = defaultdict(set)
    for assignment in assignments:
        candidate_id, company_id = assignment["candidate_id"], assignment["company_id"]
        need, pair = pair_by[(company_id, candidate_id)]
        chosen_by_person[candidate_id].add(company_id)
        per_company[company_id].append({"candidate_id": candidate_id, "need": need, "pair": pair,
                                        "index": assignment["index"]})

    slots = []
    blind_links = []
    per_candidate_slots = defaultdict(list)
    for company_id in sorted(per_company):
        company = companies[company_id]
        need = need_by_company[company_id]
        for item in sorted(per_company[company_id], key=lambda row: row["index"]):
            candidate_id, pair, index = item["candidate_id"], item["pair"], item["index"]
            match_id = blind.match_id(need["need_id"], need["version"], candidate_id)
            slot_id = f"{event_id}:{company_id}:{index + 1:02d}"
            sources = [{"need_id": need["need_id"], "outcome_id": row["outcome_id"], "unit_id": row["unit_id"],
                        "support": row["support"], "quote": blind.redact(row["quote"]), "reason": blind.redact(row["reason"])}
                       for row in pair["grid"]]
            slot = {"slot_id": slot_id, "time": _time_at(start_time, index, interval_minutes), "company": company["name"],
                    "industry": company["industry"], "need_id": need["need_id"], "match_id": match_id,
                    "tier": pair["tier"], "why": blind.redact(pair["why"]), "traceability": sources[:3]}
            slots.append(slot)
            blind_links.append((match_id, need["need_id"], need["version"], candidate_id))
            per_candidate_slots[candidate_id].append(slot)

    output = {"event_id": event_id, "date": event_date, "start_time": start_time, "interval_minutes": interval_minutes,
              "slots_per_company": slots_per_company, "slots": sorted(slots, key=lambda x: (x["time"], x["company"], x["slot_id"])),
              "eligible_people": sum(bool(available[c]) for c in people),
              "eligible_for_two_slots": sum(len({entry[0] for entry in available[c]}) >= 2 for c in people),
              "people_with_any_slot": sum(bool(per_candidate_slots[c]) for c in people),
              "people_with_two_slots": sum(len(per_candidate_slots[c]) >= 2 for c in people),
              "eligible_people_missing_second_slot": sum(len({entry[0] for entry in available[c]}) >= 2 and len(per_candidate_slots[c]) < 2
                                                         for c in people),
              "unscheduled_viable_pairs": sum(1 for candidate_id, options in available.items()
                                               for company_id, _need, _pair in options
                                               if company_id not in chosen_by_person[candidate_id]),
              "stretch_share_by_company": {company_id: round(sum(x["pair"]["tier"] == "stretch" for x in rows) / len(rows), 3)
                                           for company_id, rows in per_company.items() if rows},
              "why": "Slots respect each company's capacity, company stated language/start/work mode constraints, and attendee time clashes. "
                     "Each attendee receives at most two distinct company meetings; capacity may leave some eligible people unscheduled."}
    if any(len({s["time"] for s in by_person}) != len(by_person) for by_person in per_candidate_slots.values()):
        raise ValueError("agenda contains an attendee time overlap")
    if persist:
        store.put_blind_matches(blind_links)
        store.save_agenda(event_id, output)
    return output


def publish_passes(event_id: str, ttl_days: int = 14) -> list[dict]:
    record = store.get_agenda(event_id)
    if not record:
        raise KeyError(event_id)
    if not record["approved"]:
        raise PermissionError("agenda approval is required before pass generation")
    if store.event_pass_count(event_id):
        raise PermissionError("passes have already been issued for this agenda; rebuild and approve the agenda to replace them")
    if not 1 <= ttl_days <= 90:
        raise ValueError("pass expiry must be 1–90 days")
    agenda = record["agenda"]
    canonical_agenda = json.dumps(agenda, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    agenda_hash = hashlib.sha256(canonical_agenda.encode("utf-8")).hexdigest()
    grouped = defaultdict(list, {candidate_id: [] for candidate_id in _pool()})
    match_links = store.get_blind_matches([slot["match_id"] for slot in agenda["slots"]])
    for slot in agenda["slots"]:
        match = match_links.get(slot["match_id"])
        if match is None:
            raise ValueError("agenda contains an unknown blind match handle")
        grouped[match["candidate_id"]].append(slot)
    issued = []
    stored = []
    expiry = (datetime.strptime(agenda["date"], "%Y-%m-%d").replace(tzinfo=timezone.utc) + timedelta(days=ttl_days)).timestamp()
    for candidate_id, slots in sorted(grouped.items()):
        token = secrets.token_urlsafe(32)
        payload_slots = []
        for slot in sorted(slots, key=lambda value: value["time"]):
            card = conversation_card(slot["match_id"], match_links[slot["match_id"]])
            payload_slots.append({"slot_id": slot["slot_id"], "time": slot["time"], "company": slot["company"],
                                  "industry": slot["industry"], "need_id": slot["need_id"], "questions": card["questions"],
                                  "conflicts": card["conflicts"], "why": card["why"]})
        payload = {"event_id": event_id, "date": agenda["date"], "venue": "Espoo event venue", "agenda": payload_slots,
                   "approved_agenda_hash": agenda_hash,
                   "pulse_choices": ["useful", "not_useful"]}
        canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        if len(canonical.encode("utf-8")) > 300_000:
            raise ValueError("a pass exceeds the 300 KB payload limit")
        content_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        token_hash = hashlib.sha256(token.encode("ascii")).hexdigest()
        stored.append((token_hash, content_hash, payload, expiry))
        # The attendee reference is returned only to the authenticated organizer for private link delivery.
        # It is never included in the pass payload or URL.
        issued.append({"attendee_ref": candidate_id, "token": token, "content_hash": content_hash, "expires_at": expiry,
                       "slot_ids": [slot["slot_id"] for slot in sorted(slots, key=lambda value: value["time"])]})
    if not store.put_pass_batch(event_id, agenda_hash, stored):
        raise PermissionError("the approved agenda changed or passes were issued while this batch was being prepared")
    return issued


def open_pass(token: str) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
        raise KeyError("unknown pass")
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    record = store.get_pass(token_hash)
    if record is None:
        raise KeyError("unknown pass")
    if record["expires_at"] <= datetime.now(timezone.utc).timestamp():
        raise TimeoutError("Expired")
    agenda_record = store.get_agenda(record["event_id"])
    if not agenda_record or not agenda_record["approved"]:
        raise ValueError("pass is no longer attached to an approved agenda")
    if record["payload"].get("event_id") != record["event_id"]:
        raise ValueError("pass event id does not match its stored record")
    canonical_agenda = json.dumps(agenda_record["agenda"], sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    agenda_hash = hashlib.sha256(canonical_agenda.encode("utf-8")).hexdigest()
    if record["payload"].get("approved_agenda_hash") != agenda_hash:
        raise ValueError("pass no longer matches its approved agenda")
    canonical = json.dumps(record["payload"], sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    actual = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    if actual != record["content_hash"]:
        raise ValueError("pass content no longer matches its approved hash")
    return {**record["payload"], "content_hash": record["content_hash"], "expires_at": record["expires_at"]}


def add_pulse(token: str, client_event_id: str, slot_id: str, response: str) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
        raise KeyError("unknown pass")
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    payload = open_pass(token)
    record = store.get_pass(token_hash)
    if record is None:
        raise KeyError("unknown or expired pass")
    valid_slots = {item["slot_id"] for item in payload["agenda"]}
    if slot_id not in valid_slots or response not in ("useful", "not_useful"):
        raise ValueError("invalid slot or pulse response")
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,80}", client_event_id):
        raise ValueError("client_event_id must be a random 16–80 character id")
    inserted = store.record_pulse(client_event_id, record["event_id"], token_hash, slot_id, response)
    return {"recorded": True, "duplicate": not inserted}


def dashboard(event_id: str) -> dict:
    record = store.get_agenda(event_id)
    if not record or not record["approved"]:
        raise PermissionError("only a published agenda can appear on the organiser dashboard")
    agenda = record["agenda"]
    pulses = store.pulse_rows(event_id)
    by_company = Counter(slot["company"] for slot in agenda["slots"])
    rows = [{"company": name, "scheduled_conversations": count} for name, count in sorted(by_company.items()) if count >= 5]
    response_counts = Counter(p["response"] for p in pulses)
    pulse_summary = {key: value for key, value in sorted(response_counts.items()) if value >= 5}
    safe_count = lambda count: str(count) if count >= 5 else "fewer than 5"

    support_counts = Counter(item["support"] for slot in agenda["slots"] for item in slot["traceability"])
    tier_counts = Counter(slot["tier"] for slot in agenda["slots"])
    conflict_counts = Counter()
    matches = store.get_blind_matches([slot["match_id"] for slot in agenda["slots"]])
    for slot in agenda["slots"]:
        match = matches.get(slot["match_id"])
        if match:
            need = _need(slot["need_id"])
            conflict_counts.update(flag["kind"] for flag in _conflict_flags(need, _pool()[match["candidate_id"]]["constraints"], slot["match_id"]))

    def grouped(counter: Counter, keys: tuple[str, ...]) -> str:
        return "; ".join(f"{key}: {safe_count(counter[key])}" for key in keys)

    eligible_two = agenda.get("eligible_for_two_slots", 0)
    assigned_two = agenda["people_with_two_slots"]
    max_capacity = sum(min(agenda["slots_per_company"], company["capacity"]) for company in _companies().values())
    companies_at_capacity = sum(
        by_company.get(company["name"], 0) >= min(agenda["slots_per_company"], company["capacity"])
        for company in _companies().values()
    )
    clusters = [
        {"theme": "Evidence coverage", "observed": grouped(support_counts, ("supported", "partial", "adjacent", "none")),
         "action": "Review examples marked partial or related and keep the participant in control of the discussion."},
        {"theme": "Practical constraints", "observed": grouped(conflict_counts, ("start", "language", "work_mode")),
         "action": "Resolve stated start, language or work-mode conflicts before confirming a meeting."},
        {"theme": "Conversation mix", "observed": grouped(tier_counts, ("strong", "worth", "stretch")),
         "action": "Review the evidence and capacity mix; tiers are discussion aids, not a hiring rank."},
        {"theme": "Schedule coverage", "observed": f"{safe_count(agenda['people_with_any_slot'])} attendees have a meeting; "
         f"{safe_count(assigned_two)} of {safe_count(eligible_two)} eligible for two meetings received both. "
         f"{len(agenda['slots'])} of {max_capacity} available slots are filled; {safe_count(companies_at_capacity)} companies are at capacity.",
         "action": "Review remaining capacity where eligible attendees could not receive a second distinct meeting."},
        {"theme": "Participant pulse", "observed": grouped(response_counts, ("useful", "not_useful")) if pulses else "No feedback has arrived yet.",
         "action": "Discuss recurring feedback with event mentors; response groups smaller than five are withheld."},
    ]
    return {"event_id": event_id, "scheduled": len(agenda["slots"]), "people_with_two_slots": safe_count(assigned_two),
            "pulse_responses": len(pulses), "pulse_by_response": pulse_summary, "company_cells": rows,
            "clusters": clusters, "framing": "Small groups are suppressed. Counts describe this event's registered activity, not hiring outcomes."}


def fairness_audit() -> dict:
    """Counterfactual check of registered identity-separated stories and saved translation/style twins."""
    people = _pool()
    identities = {item["candidate_id"]: item for item in _read("data/out/identities.json")}
    lens = blind._stored_pairs()
    identity_checks = 0
    identity_differences = 0
    identity_flips = 0
    identity_fields_changed = 0
    judgments = _read("data/engine_d1/judgments.json")
    units = _read("data/engine_d1/units.json")
    needs = _needs()
    for candidate_id, person in people.items():
        original_identity = identities[candidate_id]
        birth_year = original_identity.get("birth_year", 1999)
        changed_birth_year = int(birth_year) + 7 if str(birth_year).isdigit() else 1999
        changed_identity = {**original_identity, "name": "QZX-Counterfactual-Name-8841", "nationality": "QZX-Nationality-8841",
                            "school": "QZX-School-8841", "birth_year": changed_birth_year,
                            "email": "qzx-counterfactual-8841@example.test"}
        identity_fields_changed += sum(original_identity.get(key) != changed_identity.get(key)
                                       for key in ("name", "nationality", "school", "birth_year", "email"))
        source_text = "\n".join(person["story"][box] for box in ("problem", "did", "differently"))
        identity_fields = ("name", "email", "nationality", "school")
        original_values = tuple(str(original_identity[key]) for key in identity_fields if original_identity.get(key))
        counterfactual_values = tuple(str(changed_identity[key]) for key in identity_fields if changed_identity.get(key))
        # Inject the two identity variants into otherwise identical work text, then compare after redaction.
        def identity_twin(identity: dict) -> str:
            return (f"{source_text}\nPersonal details: name {identity['name']}; nationality {identity['nationality']}; "
                    f"school {identity['school']}; born in {identity['birth_year']}; email {identity['email']}.")

        twin_values = tuple(dict.fromkeys((*original_values, *counterfactual_values)))
        original_text = blind.redact(identity_twin(original_identity), identity_values=twin_values)
        counterfactual_text = blind.redact(identity_twin(changed_identity), identity_values=twin_values)
        identity_checks += 1
        identity_differences += int(original_text != counterfactual_text)
        for need_id, pair_candidate in lens:
            if pair_candidate != candidate_id:
                continue
            cached = lens[(need_id, candidate_id)]
            recomputed = engine_lens.pair(needs[need_id], candidate_id, units[candidate_id]["units"],
                                          judgments[f"{candidate_id}@all"])
            identity_flips += int(cached["tier"] != recomputed["tier"])
    world = _read("eval/fixtures/twins.json")
    attributes = {item["candidate_id"]: item for item in _read("data/out/attributes.json")}
    need_domain = engine_run.world()["need_domain"]
    tier_flips = Counter()
    tier_total = Counter()
    for twin in world:
        if twin["kind"] not in ("translation", "style"):
            continue
        source, twin_id = twin["source"], twin["id"]
        own = [need for need in needs.values() if need_domain[need["need_id"]] == attributes[source]["origin_domain"]]
        src_run, twin_run = f"{source}@own", f"{twin_id}@own"
        if src_run not in judgments or twin_run not in judgments:
            continue
        for need in own:
            original_tier = blind._stored_pairs()[(need["need_id"], source)]["tier"]
            derived = engine_lens.pair(need, twin_id, units[twin_id]["units"], judgments[twin_run])
            kind = twin["kind"]
            tier_total[kind] += 1
            tier_flips[kind] += original_tier != derived["tier"]
    controls = _read("eval/fixtures/bias_controls.json") if (ROOT / "eval/fixtures/bias_controls.json").exists() else []
    flagged = sum(bool(PROTECTED.search(item["text"]) and DISCRIMINATORY.search(item["text"])) for item in controls)
    naive, engine = [], []
    by_candidate = defaultdict(list)
    for need in needs.values():
        for candidate_id, candidate in people.items():
            pair = lens[(need["need_id"], candidate_id)]
            text = " ".join([out["text"] for out in need["outcomes"]]).casefold()
            story = " ".join(candidate["story"][box] for box in ("problem", "did", "differently")).casefold()
            overlap = len({word for word in re.findall(r"[a-z]{4,}", text) if word in story})
            naive_value = overlap >= 2
            engine_value = pair["tier"] in ("strong", "worth")
            naive.append(naive_value)
            engine.append(engine_value)
            by_candidate[candidate_id].append((naive_value, engine_value))
    both = sum(a and b for a, b in zip(naive, engine)) / max(1, sum(naive))
    import random
    rng = random.Random(20271114)
    candidate_ids = sorted(by_candidate)
    bootstrap = []
    for _ in range(2000):
        sample = [rng.choice(candidate_ids) for _ in candidate_ids]
        records = [row for candidate_id in sample for row in by_candidate[candidate_id]]
        bootstrap.append(sum(a != b for a, b in records) / max(1, len(records)))
    bootstrap.sort()
    mismatch = sum(a != b for a, b in zip(naive, engine)) / max(1, len(naive))
    return {"identity_twins": {"n": identity_checks, "post_redaction_differences": identity_differences,
                                "identity_fields_perturbed": identity_fields_changed, "tier_flips": identity_flips,
                                "note": "The identity-only counterfactual preserves the stored work story; the Match Lens is recomputed from the same work-only inputs."},
            "positive_control": {"flagged": flagged, "total": len(controls)},
            "translation_flip_rate": tier_flips["translation"] / max(1, tier_total["translation"]),
            "style_flip_rate": tier_flips["style"] / max(1, tier_total["style"]),
            "perturbation": {"twins_checked": sum(tier_total.values()), "tier_flips": sum(tier_flips.values()),
                             "cleanliness": 1 - sum(tier_flips.values()) / max(1, sum(tier_total.values()))},
            "naive_baseline": {"overlap_selected": sum(naive), "engine_covered": sum(engine), "engine_positive_rate_among_overlap": round(both, 4),
                               "naive_positive_rate": round(sum(naive) / max(1, len(naive)), 4),
                               "engine_positive_rate": round(sum(engine) / max(1, len(engine)), 4),
                               "agreement_with_engine": round(1 - mismatch, 4),
                               "mismatch_rate_95_ci": [round(bootstrap[int(0.025 * len(bootstrap))], 4),
                                                       round(bootstrap[min(len(bootstrap) - 1, int(0.975 * len(bootstrap)))], 4)],
                               "n_pairs": len(naive),
                               "note": "Descriptive candidate-cluster bootstrap; the engine is a comparator, not human ground truth."}}
