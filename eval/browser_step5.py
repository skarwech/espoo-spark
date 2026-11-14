"""Browser audit for pass caching, API-down recovery, expiry, pulse sync and third-party requests.

Requires a running local app, Playwright Python, and Chromium. It creates a uniquely named synthetic event and writes only an
aggregate artifact to eval/results/step5/browser.json. Pass tokens are never written to the artifact.
"""
import argparse
import hashlib
import json
import os
import secrets
import sys
import time
import uuid
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urljoin, urlsplit

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "eval" / "results" / "step5" / "browser.json"
sys.path.insert(0, str(ROOT))


def main() -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as error:
        raise SystemExit("Install the optional browser dependencies: pip install -r requirements-browser.txt && python -m playwright install chromium") from error

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=os.environ.get("SPARK_LOAD_BASE_URL", "http://127.0.0.1:8778"))
    parser.add_argument("--organizer-token", default=os.environ.get("SPARK_ORGANIZER_TOKEN", ""))
    parser.add_argument("--date", default=(date.today() + timedelta(days=30)).isoformat())
    args = parser.parse_args()
    if not args.organizer_token:
        parser.error("provide --organizer-token or SPARK_ORGANIZER_TOKEN")

    from api import store

    base_url = args.base_url.rstrip("/")
    base_parts = urlsplit(base_url)
    base_origin = f"{base_parts.scheme}://{base_parts.netloc}"
    event_id = f"browser-{uuid.uuid4().hex[:12]}"
    external_requests = set()
    result = {"created_at": time.time(), "event_id": event_id, "checks": {}, "targets": {}}

    def track_request(request) -> None:
        parts = urlsplit(request.url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin != base_origin:
            external_requests.add(origin)

    def record_check(key: str, passed: bool, value, evidence: str = "") -> None:
        result["checks"][key] = {"status": "PASS" if passed else "FAIL", "value": value, "evidence": evidence}

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        organizer = browser.new_context()
        organizer_page = organizer.new_page()
        organizer_page.on("request", track_request)
        organizer_page.goto(urljoin(base_url + "/", "event-console"))
        organizer_page.locator("#organizer-token").fill(args.organizer_token)
        organizer_page.locator("#event-id").fill(event_id)
        organizer_page.locator("#event-date").fill(args.date)
        organizer_page.get_by_role("button", name="Build draft agenda").click()
        organizer_page.get_by_text("Draft saved.").wait_for()
        organizer_page.get_by_role("button", name="Approve agenda").click()
        organizer_page.get_by_text("Agenda approved.").wait_for()
        organizer_page.get_by_role("button", name="Issue event passes").click()
        organizer_page.get_by_text("private pass(es) issued.").wait_for()
        links = organizer_page.locator("#pass-links a").all()
        pass_urls = [urljoin(base_url + "/", link.get_attribute("href")) for link in links]
        tokens = [url.rsplit("/", 1)[-1] for url in pass_urls]
        api = organizer.request
        usable = []
        available_slots = 0
        for token, pass_url in zip(tokens, pass_urls):
            response = api.get(f"{base_url}/api/passes/{token}")
            payload = response.json() if response.ok else {}
            if payload.get("agenda"):
                usable.append((token, pass_url, payload))
                available_slots += len(payload["agenda"])
            if len(usable) >= 10 and available_slots >= 20:
                break
        if len(links) != 150 or len(usable) < 10 or available_slots < 20:
            browser.close()
            raise RuntimeError(f"expected 150 links, ten assigned passes, and 20 meeting slots; got {len(links)}, {len(usable)}, and {available_slots}")

        device_results = []
        for token, pass_url, _payload in usable[:2]:
            context = browser.new_context()
            page = context.new_page()
            page.on("request", track_request)
            page.goto(pass_url)
            page.locator("#pass-content article").first.wait_for()
            page.wait_for_function("async () => !!(await caches.match(location.href))")
            context.set_offline(True)
            page.reload()
            page.locator("#pass-content article").first.wait_for()
            device_results.append(bool(page.locator("#pass-content article").count()))
            context.close()

        # Verify ten distinct passes render from browser storage after the API is disconnected.
        offline_context = browser.new_context()
        offline_page = offline_context.new_page()
        offline_page.on("request", track_request)
        ten_cached = []
        for _token, pass_url, _payload in usable:
            offline_page.goto(pass_url)
            offline_page.locator("#pass-content article").first.wait_for()
            offline_page.wait_for_function("async () => !!(await caches.match(location.href))")
            ten_cached.append(True)
        offline_context.set_offline(True)
        for _token, pass_url, _payload in usable:
            offline_page.goto(pass_url)
            offline_page.locator("#pass-content article").first.wait_for()
            ten_cached.append(True)

        pulse_slots = []
        for token, pass_url, payload in usable:
            for slot_index, slot in enumerate(payload["agenda"]):
                pulse_slots.append((token, pass_url, slot_index, slot["slot_id"]))
                if len(pulse_slots) == 20:
                    break
            if len(pulse_slots) == 20:
                break
        tap_counts = {}
        for token, pass_url, slot_index, _slot_id in pulse_slots:
            offline_page.goto(pass_url)
            articles = offline_page.locator("#pass-content article")
            articles.first.wait_for()
            articles.nth(slot_index).get_by_role("button", name="Useful", exact=True).click()
            tap_counts[token] = tap_counts.get(token, 0) + 1
        offline_context.set_offline(False)
        for token in tap_counts:
            pass_url = next(url for candidate, url, _payload in usable if candidate == token)
            offline_page.goto(pass_url)
            offline_page.locator("#pass-content article").first.wait_for()
            offline_page.wait_for_function("document.getElementById('pulse-state').textContent === 'Feedback synced.'",
                                           timeout=15000)
        pulse_dashboard = api.get(f"{base_url}/api/organizer/events/{event_id}/dashboard",
                                  headers={"X-Organizer-Token": args.organizer_token})
        pulse_total = pulse_dashboard.json().get("pulse_responses", -1) if pulse_dashboard.ok else -1
        pulse_synced = len(pulse_slots) == 20 and pulse_total == 20
        max_taps = max(tap_counts.values(), default=0)
        offline_context.close()

        expired_token = secrets.token_urlsafe(32)
        store.put_pass(hashlib.sha256(expired_token.encode("ascii")).hexdigest(), event_id, "expired-control", {}, time.time() - 1)
        expired_context = browser.new_context()
        expired_page = expired_context.new_page()
        expired_page.on("request", track_request)
        expired_page.goto(f"{base_url}/event-pass/{expired_token}")
        expired_page.locator("#expired").wait_for(state="visible")
        expired_context.close()
        organizer.close()
        browser.close()

    record_check("F11-OFFLINE", all(device_results) and len(ten_cached) == 20,
                 {"separate_contexts": sum(device_results), "fresh_passes_reopened_offline": len(ten_cached) // 2},
                 "Pass content rendered offline in two isolated browser contexts and ten cached pass URLs.")
    record_check("F11-EXPIRED", True, "Expired", "An expired token displayed the pass page's expired state.")
    record_check("F11-THIRD-PARTY", not external_requests, sorted(external_requests),
                 "Observed browser requests across organizer and pass pages.")
    result["checks"]["F12-SYNC"] = {"status": "PASS" if pulse_synced else "FAIL", "value": pulse_synced,
                                     "evidence": f"{pulse_total} offline-queued responses were present in the organizer dashboard after reconnect."}
    result["checks"]["F12-OFFLINE-PULSES"] = {"status": "PASS" if pulse_synced else "FAIL", "value": pulse_total,
                                               "evidence": "20 unique meeting feedback taps were queued offline and synced after reconnect."}
    result["targets"]["F12-TAPS"] = {"status": "PASS" if pulse_synced and max_taps <= 2 else "FAIL",
                                     "value": max_taps if pulse_synced else None,
                                     "evidence": "Maximum feedback taps by one pass holder in the 20-response offline flow; syncing was automatic after reconnect."}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {OUT.relative_to(ROOT)} · offline={result['checks']['F11-OFFLINE']['status']} · expired={result['checks']['F11-EXPIRED']['status']}")


if __name__ == "__main__":
    main()
