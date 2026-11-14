"""Build the offline F8 proof page from the redaction/reveal evaluation and hard-gate JUnit result."""
import json
import xml.etree.ElementTree as ET
from pathlib import Path

from jinja2 import Environment

ROOT = Path(__file__).resolve().parents[1]
RES = ROOT / "eval" / "results"
GATES = {
    "test_f8_blind_payloads_have_no_identity_fields_or_values": "F8 blind payloads for all 150 candidates contain no identity field or value",
    "test_f8_one_sided_opt_in_never_reveals_then_logs_both_timestamps": "F8 one-sided opt-in reveals nothing; released log has both timestamps",
}


def optional(path: Path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def statuses() -> dict:
    path = RES / "hard_f8.xml"
    if not path.exists():
        return {}
    return {case.get("name"): not any(case.find(tag) is not None for tag in ("failure", "error", "skipped"))
            for case in ET.parse(path).getroot().iter("testcase") if case.get("classname", "").endswith("test_f8")}


def main() -> None:
    result = optional(RES / "step4" / "blind.json", {"blind_cards": 0, "identity_leaks": [], "redaction": [], "targets": {}})
    target = result["targets"].get("F8 redaction recall, 60 planted mentions")
    kinds = {}
    for item in result.get("redaction", []):
        kinds.setdefault(item["kind"], [0, 0])
        kinds[item["kind"]][0] += 1
        kinds[item["kind"]][1] += int(item["redacted"])
    html = Environment(autoescape=True).from_string((ROOT / "reports" / "f8.html.j2").read_text(encoding="utf-8")).render(
        css=(ROOT / "reports" / "base.css").read_text(encoding="utf-8"), gates=[(label, statuses().get(name)) for name, label in GATES.items()],
        result=result, target=target, kinds=sorted((kind, total, redacted) for kind, (total, redacted) in kinds.items()))
    (ROOT / "reports" / "f8.html").write_text(html, encoding="utf-8", newline="\n")
    print("wrote reports/f8.html")


if __name__ == "__main__":
    main()
