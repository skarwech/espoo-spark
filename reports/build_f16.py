"""Build the F9–F16 release page from the machine-readable offline evaluation artifact."""
import json
from pathlib import Path

from jinja2 import Environment

ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "eval" / "results" / "step5" / "f9_f16.json"
GATES = [
    ("F9-IDs", "Conversation prompts cite existing Need and evidence IDs", "100% of 200 cards"),
    ("F9-SAFETY", "Conversation prompts contain no protected-attribute questions", "0 violations"),
    ("F10-CONSTRAINTS", "Agenda constraints across seeded pool sizes", "0 violations"),
    ("F10-APPROVAL", "Unapproved agendas cannot issue passes", "blocked"),
    ("F11-OFFLINE", "Pass shell and agenda open from isolated browser profiles", "2 profiles"),
    ("F11-OFFLINE-DEVICE", "Pass opens offline on physical devices", "2 devices"),
    ("F11-HASH", "Pass content matches its approved agenda", "100%"),
    ("F11-DELIVERY", "Organizer can map each private link to its attendee reference", "150 of 150"),
    ("F11-EXPIRED", "Expired pass is rejected by the pass API", "blocked"),
    ("F11-EXPIRED-UI", "Expired pass displays its expired state in a browser", "visible"),
    ("F11-URL", "Pass URL has no attendee identity and uses a high-entropy token", "0 identity values; ≥128 bits"),
    ("F11-THIRD-PARTY-STATIC", "Pass source contains no external resource references", "0 references"),
    ("F11-THIRD-PARTY", "Browser pass flow makes no third-party requests", "0 requests"),
    ("F12-IDEMPOTENCY", "Offline pulse retries are deduplicated", "20 unique records"),
    ("F12-PRIVACY", "Pulse records contain no identity fields", "0 identity fields"),
    ("F12-RECONCILE", "Dashboard pulse total reconciles to raw records", "100%"),
    ("F12-SUPPRESSION", "Pulse groups smaller than five are hidden", "0 small groups shown"),
    ("F13-SUPPRESSION", "Dashboard hides groups smaller than five", "0 small cells shown"),
    ("F13-AUTH", "Non-organiser dashboard access is denied", "denied"),
    ("F13-RECONCILE", "Dashboard conversation totals reconcile to the agenda", "100%"),
    ("F14-IDENTITY", "Identity counterfactual leaves text and tiers unchanged", "0 differences or flips"),
    ("F14-POSITIVE", "Planted discriminatory controls are flagged", "5 of 5"),
    ("F14-LIMITS", "Registered limits section is present", "present"),
    ("F15-TRACE", "Sampled agenda slots have a complete evidence path", "20 slots, at most 3 steps"),
    ("F15-WHY", "Every agenda slot has a plain-language reason", "100%"),
    ("F15-DELETE", "Participant deletion removes stored and cached records", "0 residuals"),
    ("F15-BYPASS", "Release gates reject bypass attempts", "all blocked"),
    ("F15-LOGS", "Oversight logs contain no identity fields", "0 identity fields"),
    ("F15-SCHEMA", "Stored fields match the declared schema", "0 undeclared fields"),
    ("F15-NO-INFERENCE", "No AI-text detector or emotion inference is present", "0 occurrences"),
    ("F16-RUNS", "Consecutive clean release runs", "3 runs"),
]
TARGETS = [
    ("F9-SPECIFICITY", "Specificity on generic stories", "at least 9 of 10"),
    ("F9-CONFLICTS", "Planted conflict detection / clean false flags", "at least 9/10 / at most 1/10"),
    ("F9-YESNO", "Yes/no conversation prompts", "at most 5%"),
    ("F9-USEFUL", "Useful and answerable prompts", "at least 85% of 30 cards; human review"),
    ("F10-OPTIMUM", "Greedy objective vs exact integer optimizer on tiny instances", "at least 95%"),
    ("F10-STRETCH", "Stretch share per company where available", "20% ± 2 points"),
    ("F10-TWO-SLOTS", "Eligible attendees receiving two slots", "at least 95%"),
    ("F10-UNSCHEDULED", "Unscheduled viable pairs", "at most 5%"),
    ("F10-RUNTIME", "Agenda runtime for 150 × 25", "under 5 seconds"),
    ("F11-LOAD", "Concurrent pass requests (1,000 users)", "p95 under 1 second"),
    ("F11-SIZE", "Maximum pass payload", "300 KB"),
    ("F12-TAPS", "Feedback interactions per attendee", "at most 2 taps"),
    ("F13-CLUSTERS", "Coherent dashboard clusters", "4 of 5; mentor review"),
    ("F13-ACTIONS", "Mentors naming a concrete next action", "2 of 3; human review"),
    ("F14-PERTURBATION", "Cleanliness across translation and style twins", "at least 95%"),
    ("F14-TRANSLATION", "Translation tier flip rate", "at most 5%"),
    ("F14-STYLE", "Style tier flip rate", "at most 5%"),
    ("F14-BASELINE-CI", "Naive text-overlap baseline contrast interval", "95% cluster-bootstrap CI; descriptive"),
    ("F16-DEMO", "End-to-end demo duration", "at most 4 minutes"),
    ("F16-CACHE", "Cached outputs replay identically across release runs", "identical"),
]


def main() -> None:
    result = json.loads(RESULT.read_text(encoding="utf-8")) if RESULT.exists() else {}
    checks = {item.get("id"): item for item in result.get("checks", [])}
    measured_targets = {item.get("id"): item for item in result.get("targets", [])}
    rows = []
    for gate_id, label, threshold in GATES:
        item = checks.get(gate_id, {})
        status = str(item.get("status", "PENDING")).upper()
        if status not in ("PASS", "FAIL", "PENDING", "BLOCKED"):
            status = "PENDING"
        rows.append({"id": gate_id, "label": label, "threshold": threshold, "status": status,
                     "value": item.get("value"), "evidence": item.get("evidence", "")})
    target_rows = []
    for target_id, label, threshold in TARGETS:
        item = measured_targets.get(target_id, {})
        status = str(item.get("status", "PENDING")).upper()
        if status not in ("PASS", "FAIL", "PENDING", "BLOCKED"):
            status = "PENDING"
        target_rows.append({"id": target_id, "label": label, "threshold": threshold, "status": status,
                            "value": item.get("value"), "evidence": item.get("evidence", "")})
    runs = result.get("clean_runs", [])
    all_hard_green = bool(rows) and all(row["status"] == "PASS" for row in rows if row["id"] != "F16-RUNS") \
        and len(runs) >= 3 and all(item.get("status") == "PASS" for item in runs[-3:])
    report = Environment(autoescape=True).from_string((ROOT / "reports" / "f16.html.j2").read_text(encoding="utf-8")).render(
        css=(ROOT / "reports" / "base.css").read_text(encoding="utf-8"), rows=rows, targets=target_rows, runs=runs,
        open_issues=result.get("open_issues", ["No F9–F16 evaluation artifact has been generated yet.",
            "Human recruiter, participant, accessibility and responsible-AI mentor reviews remain outstanding.",
            "Exact-objective comparison, agenda seed sweeps, offline-device checks and 1,000-request load test are pending."]),
        generated=bool(result), all_hard_green=all_hard_green)
    (ROOT / "reports" / "f16.html").write_text(report, encoding="utf-8", newline="\n")
    print("wrote reports/f16.html")


if __name__ == "__main__":
    main()
