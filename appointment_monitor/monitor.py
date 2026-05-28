#!/usr/bin/env python3
"""Monitor the Greek Consulate NY visa SuperSaas schedule and email when
appointments open up on dates strictly before a cutoff (default 2026-08-01)."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import re
import smtplib
import sys
import time
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import urlencode

import requests
from bs4 import BeautifulSoup

SCHEDULE_URL = "https://www.supersaas.com/schedule/Greek_Consulate_New_York/Visa_Department"
DEFAULT_CUTOFF = dt.date(2026, 8, 1)
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Classes that indicate a day cell is NOT bookable.
UNAVAILABLE_TOKENS = {
    "full", "busy", "closed", "disabled", "past", "unavailable",
    "blocked", "cal_dim", "cal_past", "cal_full", "off", "holiday",
}
AVAILABLE_TOKENS = {"free", "available", "open", "cal_free"}

DATE_IN_HREF = re.compile(r"day=(\d{4}-\d{2}-\d{2})")

log = logging.getLogger("appt_monitor")


def fetch_month(session: requests.Session, year: int, month: int,
                debug_dir: Path | None) -> str:
    params = {"view": "month", "day": f"{year:04d}-{month:02d}-01"}
    url = f"{SCHEDULE_URL}?{urlencode(params)}"
    log.debug("GET %s", url)
    resp = session.get(url, timeout=30)
    resp.raise_for_status()
    if debug_dir:
        debug_dir.mkdir(parents=True, exist_ok=True)
        (debug_dir / f"{year:04d}-{month:02d}.html").write_text(resp.text)
    return resp.text


def parse_available_dates(html: str) -> set[str]:
    """Return ISO dates that look bookable in a SuperSaas month view.

    SuperSaas markup varies by schedule type, so try several heuristics and
    union the results. The state file dedupes alerts, so a false positive
    only costs one extra email per date.
    """
    soup = BeautifulSoup(html, "html.parser")
    found: set[str] = set()

    def cell_unavailable(cell) -> bool:
        classes = set(cell.get("class") or [])
        return bool(classes & UNAVAILABLE_TOKENS)

    # 1. Anchors explicitly tagged as a free slot.
    for a in soup.find_all("a", href=True):
        classes = set(a.get("class") or [])
        if classes & AVAILABLE_TOKENS:
            m = DATE_IN_HREF.search(a["href"])
            if m:
                found.add(m.group(1))

    # 2. Calendar cells with a data-date attribute and at least one link.
    for cell in soup.find_all(attrs={"data-date": True}):
        if cell_unavailable(cell):
            continue
        if cell.find("a", href=True):
            found.add(cell["data-date"])

    # 3. <td>/<div> day cells whose link carries day=YYYY-MM-DD.
    for cell in soup.find_all(["td", "div"]):
        classes = cell.get("class") or []
        if not any("day" in c or "cal" in c for c in classes):
            continue
        if cell_unavailable(cell):
            continue
        for a in cell.find_all("a", href=True):
            m = DATE_IN_HREF.search(a["href"])
            if m:
                found.add(m.group(1))

    return found


def find_open_appointments(session: requests.Session, cutoff: dt.date,
                           debug_dir: Path | None) -> set[str]:
    today = dt.date.today()
    months: list[tuple[int, int]] = []
    y, m = today.year, today.month
    while dt.date(y, m, 1) < cutoff:
        months.append((y, m))
        m += 1
        if m == 13:
            m, y = 1, y + 1

    found: set[str] = set()
    for year, month in months:
        try:
            html = fetch_month(session, year, month, debug_dir)
        except requests.RequestException as e:
            log.warning("fetch %d-%02d failed: %s", year, month, e)
            continue
        dates = parse_available_dates(html)
        log.info("%d-%02d: %d available day(s) detected", year, month, len(dates))
        for d in dates:
            try:
                parsed = dt.date.fromisoformat(d)
            except ValueError:
                continue
            if today <= parsed < cutoff:
                found.add(d)
        time.sleep(1)
    return found


def load_state(path: Path) -> set[str]:
    if not path.exists():
        return set()
    try:
        return set(json.loads(path.read_text()))
    except (json.JSONDecodeError, OSError) as e:
        log.warning("state file %s unreadable (%s); starting fresh", path, e)
        return set()


def save_state(path: Path, seen: set[str]) -> None:
    path.write_text(json.dumps(sorted(seen)))


def send_email(new_dates: set[str]) -> None:
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ["SMTP_USER"]
    password = os.environ["SMTP_PASS"]
    to = os.environ.get("TO_EMAIL", "annie.liang94@gmail.com")

    dates = sorted(new_dates)
    body = "\n".join([
        "New Greek Consulate NY visa appointment day(s) available before the cutoff:",
        "",
        *(f"  - {d}  ->  {SCHEDULE_URL}?day={d}" for d in dates),
        "",
        f"Source: {SCHEDULE_URL}",
        "",
        "(Book fast — these go quickly.)",
    ])

    msg = EmailMessage()
    msg["From"] = user
    msg["To"] = to
    msg["Subject"] = f"[Greek Consulate] {len(dates)} appointment day(s) available"
    msg.set_content(body)

    log.info("emailing %s about %d new date(s)", to, len(dates))
    with smtplib.SMTP(host, port, timeout=30) as s:
        s.starttls()
        s.login(user, password)
        s.send_message(msg)


def run_once(cutoff: dt.date, state_path: Path, debug_dir: Path | None,
             dry_run: bool) -> int:
    session = requests.Session()
    session.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.9",
    })

    open_dates = find_open_appointments(session, cutoff, debug_dir)
    log.info("currently open before %s: %s", cutoff, sorted(open_dates) or "none")

    seen = load_state(state_path)
    new = open_dates - seen
    if new:
        log.info("NEW openings: %s", sorted(new))
        if dry_run:
            print("DRY RUN — would email about:", ", ".join(sorted(new)))
        else:
            try:
                send_email(new)
            except Exception:
                log.exception("email send failed; state not updated, will retry next run")
                return 1
        save_state(state_path, seen | new)
    else:
        log.info("no new openings")

    # Forget dates that have closed again so they re-alert if they reopen.
    pruned = (seen | new) & open_dates
    if pruned != (seen | new):
        save_state(state_path, pruned)
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cutoff", default=DEFAULT_CUTOFF.isoformat(),
                   help=f"ISO date; alert only on slots strictly before this "
                        f"(default {DEFAULT_CUTOFF})")
    p.add_argument("--interval", type=int, default=0,
                   help="If >0, loop forever sleeping this many seconds between "
                        "checks. Default: run once (use cron for scheduling).")
    p.add_argument("--state-file", type=Path,
                   default=Path(os.environ.get("MONITOR_STATE_FILE",
                                               "seen_slots.json")),
                   help="Where to remember already-alerted dates.")
    p.add_argument("--debug-dir", type=Path, default=None,
                   help="Save raw HTML of each fetched month for inspection.")
    p.add_argument("--dry-run", action="store_true",
                   help="Don't send email; just print what would be sent.")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    cutoff = dt.date.fromisoformat(args.cutoff)

    if args.interval <= 0:
        return run_once(cutoff, args.state_file, args.debug_dir, args.dry_run)

    while True:
        try:
            run_once(cutoff, args.state_file, args.debug_dir, args.dry_run)
        except Exception:
            log.exception("run failed; will retry after interval")
        log.info("sleeping %ds", args.interval)
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
