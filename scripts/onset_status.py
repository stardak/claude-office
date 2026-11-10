#!/usr/bin/env python3
"""Send one Onset office status, or play a short demo.

    ONSET_OFFICE_KEY=... python scripts/onset_status.py social working "Drafting Wed carousel"
    ONSET_OFFICE_KEY=... python scripts/onset_status.py email waiting "Thu newsletter" --approval
    ONSET_OFFICE_KEY=... python scripts/onset_status.py ads done "Hook test" --to social
    ONSET_OFFICE_KEY=... python scripts/onset_status.py --demo

Set ONSET_OFFICE_URL for a hosted office (default http://localhost:8000), plus
CF_ACCESS_CLIENT_ID / CF_ACCESS_CLIENT_SECRET when it sits behind Cloudflare Access.
Uses only the standard library so bots can copy it as-is.
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

try:  # use the OS certificate store when available (uv's Python lacks it on macOS)
    import truststore

    truststore.inject_into_ssl()
except ImportError:
    pass

URL = (
    os.environ.get("ONSET_OFFICE_URL", "http://localhost:8000").rstrip("/") + "/api/v1/onset/status"
)
KEY = os.environ.get("ONSET_OFFICE_KEY", "")
HEADERS = {
    "Content-Type": "application/json",
    "X-Onset-Key": KEY,
    # Cloudflare's Browser Integrity Check blocks the default Python-urllib agent (error 1010).
    "User-Agent": "onset-office-bot/1.0",
}
# Hosted office behind Cloudflare Access: bots also send a service token.
if os.environ.get("CF_ACCESS_CLIENT_ID"):
    HEADERS["CF-Access-Client-Id"] = os.environ["CF_ACCESS_CLIENT_ID"]
    HEADERS["CF-Access-Client-Secret"] = os.environ.get("CF_ACCESS_CLIENT_SECRET", "")

DEMO = [
    ("onset", "working", "Planning this week", False, None, 4),
    ("analyst", "working", "Weekly funnel review", False, None, 5),
    ("analyst", "done", "Hook B converts best", False, "onset", 4),
    ("social", "working", "Drafting Wed carousel", False, None, 3),
    ("email", "working", "Writing Thu newsletter", False, None, 3),
    ("creators", "working", "Reviewing new Trybe videos", False, None, 3),
    ("ads", "working", "Testing 3 new hooks", False, None, 6),
    ("ads", "done", "Hook B won", False, "social", 4),
    ("creators", "waiting", "Top creator bonus", True, None, 6),
    ("social", "working", "Rewriting carousel with hook B", False, None, 4),
    ("email", "waiting", "Thu newsletter", True, None, 8),
    ("onset", "waiting", "Two things need Stef", False, None, 6),
    ("email", "working", "Scheduling Thu newsletter", False, None, 4),
    ("creators", "done", "Chased overdue videos", False, None, 3),
    ("social", "done", "Wed carousel queued", False, "onset", 4),
    ("onset", "done", "Week planned", False, None, 0),
]


def send(agent: str, status: str, task: str, approval: bool = False, to: str | None = None) -> None:
    body = {
        "agent": agent,
        "status": status,
        "task": task,
        "needs_approval": approval,
        "handoff_to": to,
    }
    req = urllib.request.Request(
        URL,
        data=json.dumps(body).encode(),
        headers=HEADERS,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            print(f"{agent:6} {status:8} {task}  -> {r.status}")
    except urllib.error.HTTPError as e:
        print(
            f"{agent:6} {status:8} {task}  -> {e.code} {e.read().decode()[:200]}", file=sys.stderr
        )
    except TimeoutError:
        print(f"{agent:6} {status:8} {task}  -> timed out (office slow to answer)", file=sys.stderr)
    except urllib.error.URLError as e:
        sys.exit(f"Can't reach the office at {URL} ({e.reason}). Is it running?")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("agent", nargs="?", help="onset, or a staff id from ONSET_STAFF (e.g. social)")
    p.add_argument(
        "status", nargs="?", choices=["working", "waiting", "idle", "done"], default="working"
    )
    p.add_argument("task", nargs="?", default="")
    p.add_argument("--approval", action="store_true", help="flag that this needs Stef's OK")
    p.add_argument("--to", help="hand the work to (agent id)")
    p.add_argument("--demo", action="store_true", help="play a ~60s demo")
    a = p.parse_args()
    if not KEY:
        sys.exit("Set ONSET_OFFICE_KEY first")
    if a.demo:
        for agent, status, task, approval, to, wait in DEMO:
            send(agent, status, task, approval, to)
            time.sleep(wait)
    elif a.agent:
        send(a.agent, a.status, a.task, a.approval, a.to)
    else:
        p.print_help()


if __name__ == "__main__":
    main()
