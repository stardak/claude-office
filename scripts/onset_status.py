#!/usr/bin/env python3
"""Send one Onset office status, or play a short demo.

    ONSET_OFFICE_KEY=... python scripts/onset_status.py social working "Drafting Wed carousel"
    ONSET_OFFICE_KEY=... python scripts/onset_status.py email waiting "Thu newsletter" --approval
    ONSET_OFFICE_KEY=... python scripts/onset_status.py ads done "Hook test" --to social
    ONSET_OFFICE_KEY=... python scripts/onset_status.py --demo

Set ONSET_OFFICE_URL for a hosted office (default http://localhost:8000).
Uses only the standard library so bots can copy it as-is.
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

URL = (
    os.environ.get("ONSET_OFFICE_URL", "http://localhost:8000").rstrip("/") + "/api/v1/onset/status"
)
KEY = os.environ.get("ONSET_OFFICE_KEY", "")

DEMO = [
    ("onset", "working", "Planning this week", False, None, 4),
    ("social", "working", "Drafting Wed carousel", False, None, 3),
    ("email", "working", "Writing Thu newsletter", False, None, 3),
    ("ads", "working", "Testing 3 new hooks", False, None, 6),
    ("ads", "done", "Hook B won", False, "social", 4),
    ("social", "working", "Rewriting carousel with hook B", False, None, 4),
    ("email", "waiting", "Thu newsletter", True, None, 8),
    ("onset", "waiting", "Email needs Stef", False, None, 6),
    ("email", "working", "Scheduling Thu newsletter", False, None, 4),
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
        headers={"Content-Type": "application/json", "X-Onset-Key": KEY},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            print(f"{agent:6} {status:8} {task}  -> {r.status}")
    except urllib.error.HTTPError as e:
        print(
            f"{agent:6} {status:8} {task}  -> {e.code} {e.read().decode()[:200]}", file=sys.stderr
        )
    except urllib.error.URLError:
        sys.exit(f"Can't reach the office at {URL}. Is `make dev` running?")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("agent", nargs="?", choices=["onset", "social", "email", "ads"])
    p.add_argument(
        "status", nargs="?", choices=["working", "waiting", "idle", "done"], default="working"
    )
    p.add_argument("task", nargs="?", default="")
    p.add_argument("--approval", action="store_true", help="flag that this needs Stef's OK")
    p.add_argument("--to", choices=["onset", "social", "email", "ads"], help="hand the work to")
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
