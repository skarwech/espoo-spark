"""Create a synthetic agenda and measure 1,000 concurrent pass fetches against a running local app."""
import argparse
import asyncio
import json
import math
import os
import time
import uuid
from datetime import date, timedelta
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "eval" / "results" / "step5" / "load.json"


async def run(base_url: str, organizer_token: str, event_date: str) -> dict:
    headers = {"X-Organizer-Token": organizer_token}
    event_id = f"load-{uuid.uuid4().hex[:12]}"
    limits = httpx.Limits(max_connections=1000, max_keepalive_connections=1000)
    timeout = httpx.Timeout(30.0, connect=10.0)
    async with httpx.AsyncClient(base_url=base_url.rstrip("/"), headers=headers, limits=limits, timeout=timeout) as client:
        draft = await client.post(f"/api/events/{event_id}/agenda", json={"date": event_date, "slots_per_company": 15,
                                                                         "seed": f"load:{event_id}"})
        draft.raise_for_status()
        approved = await client.post(f"/api/events/{event_id}/approve")
        approved.raise_for_status()
        issued = await client.post(f"/api/events/{event_id}/passes")
        issued.raise_for_status()
        passes = issued.json().get("passes", [])
        if not passes:
            raise RuntimeError("the agenda produced no passes")

        latencies = [0.0] * 1000
        errors = []

        async def fetch(index: int) -> None:
            token = passes[index % len(passes)]["token"]
            started = time.perf_counter()
            try:
                response = await client.get(f"/api/passes/{token}")
                latencies[index] = time.perf_counter() - started
                if response.status_code != 200:
                    errors.append({"status": response.status_code})
            except Exception as error:  # collect transport errors without storing request tokens
                latencies[index] = time.perf_counter() - started
                errors.append({"error": type(error).__name__})

        await asyncio.gather(*(fetch(index) for index in range(1000)))

    ordered = sorted(latencies)
    p95 = ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]
    passed = not errors and p95 < 1.0
    return {"created_at": time.time(), "event_id": event_id,
            "target": {"id": "F11-LOAD", "status": "PASS" if passed else "FAIL",
                       "value": {"requests": len(latencies), "p95_seconds": round(p95, 4), "errors": len(errors)},
                       "evidence": "1,000 concurrent GETs against the local app; pass tokens are omitted."}}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=os.environ.get("SPARK_LOAD_BASE_URL", "http://127.0.0.1:8778"))
    parser.add_argument("--organizer-token", default=os.environ.get("SPARK_ORGANIZER_TOKEN", ""))
    parser.add_argument("--date", default=(date.today() + timedelta(days=30)).isoformat())
    args = parser.parse_args()
    if not args.organizer_token:
        parser.error("provide --organizer-token or SPARK_ORGANIZER_TOKEN")
    result = asyncio.run(run(args.base_url, args.organizer_token, args.date))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {OUT.relative_to(ROOT)} · {result['target']['status']} · {result['target']['value']}")


if __name__ == "__main__":
    main()
