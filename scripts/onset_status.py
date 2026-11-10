#!/usr/bin/env python3
"""Send one office status, or play a short demo.

    OFFICE_KEY=... python scripts/onset_status.py social working "Drafting Wed carousel"
    OFFICE_KEY=... python scripts/onset_status.py email waiting "Thu newsletter" --approval
    OFFICE_KEY=... python scripts/onset_status.py ads done "Hook test" --to social
    OFFICE=hostmate OFFICE_KEY=... python scripts/onset_status.py sales working "Vendor call"
    OFFICE_KEY=... python scripts/onset_status.py --demo

OFFICE picks the company office (default onset). OFFICE_KEY is that office's
key (ONSET_OFFICE_KEY also works for onset). Set ONSET_OFFICE_URL for a hosted
office (default http://localhost:8000), plus
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

OFFICE = os.environ.get("OFFICE", "onset").strip().lower()
BASE = os.environ.get("ONSET_OFFICE_URL", "http://localhost:8000").rstrip("/")
URL = f"{BASE}/api/v1/office/{OFFICE}/status"
KEY = os.environ.get("OFFICE_KEY") or os.environ.get("ONSET_OFFICE_KEY", "")
HEADERS = {
    "Content-Type": "application/json",
    "X-Office-Key": KEY,
    # Cloudflare's Browser Integrity Check blocks the default Python-urllib agent (error 1010).
    "User-Agent": "onset-office-bot/1.0",
}
# Hosted office behind Cloudflare Access: bots also send a service token.
if os.environ.get("CF_ACCESS_CLIENT_ID"):
    HEADERS["CF-Access-Client-Id"] = os.environ["CF_ACCESS_CLIENT_ID"]
    HEADERS["CF-Access-Client-Secret"] = os.environ.get("CF_ACCESS_CLIENT_SECRET", "")

DEMO_DETAIL = {
    ("analyst", "done", "Hook B converts best"): (
        "Weekly funnel review (demo)\n\n"
        "- Hook B ('No gym, no excuses') beat A and C on trial starts.\n"
        "- Reels with a coach voiceover kept viewers longer.\n"
        "- Suggest: Social and Ads both lead with hook B this week."
    ),
    ("email", "waiting", "Thu newsletter"): (
        "Subject: Your 10-minute arms finisher\n\n"
        "Hi {first_name},\n\n"
        "No gym? No problem. This week's finisher takes 10 minutes and a "
        "chair. Open Onset, tap Today, and hit start.\n\n"
        "Stef & Hannah"
    ),
}

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


def send(
    agent: str,
    status: str,
    task: str,
    approval: bool = False,
    to: str | None = None,
    detail: str | None = None,
    link: str | None = None,
) -> None:
    body = {
        "agent": agent,
        "status": status,
        "task": task,
        "needs_approval": approval,
        "handoff_to": to,
        "detail": detail,
        "link": link,
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
    p.add_argument(
        "agent", nargs="?", help="the office boss (e.g. onset) or a staff id (e.g. social)"
    )
    p.add_argument(
        "status", nargs="?", choices=["working", "waiting", "idle", "done"], default="working"
    )
    p.add_argument("task", nargs="?", default="")
    p.add_argument("--approval", action="store_true", help="flag that this needs Stef's OK")
    p.add_argument("--to", help="hand the work to (agent id)")
    p.add_argument("--detail", help="the finished work itself (text, or @file to read it)")
    p.add_argument("--link", help="https link to the finished work")
    p.add_argument("--demo", action="store_true", help="play a ~60s demo")
    a = p.parse_args()
    if not KEY:
        sys.exit("Set ONSET_OFFICE_KEY first")
    if a.demo and OFFICE != "onset":
        # The first status opens the office and seats its whole team.
        send(OFFICE, "working", "Morning plan")
        time.sleep(8)
        send(OFFICE, "done", "Team briefed")
    elif a.demo:
        for agent, status, task, approval, to, wait in DEMO:
            send(agent, status, task, approval, to, DEMO_DETAIL.get((agent, status, task)))
            time.sleep(wait)
    elif a.agent:
        detail = a.detail
        if detail and detail.startswith("@"):
            with open(detail[1:], encoding="utf-8") as f:
                detail = f.read()
        send(a.agent, a.status, a.task, a.approval, a.to, detail, a.link)
    else:
        p.print_help()


if __name__ == "__main__":
    main()
