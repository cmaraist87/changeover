"""Has a live board drifted from the sheet, or gone silently wrong? Read-only.

    python board_watch.py
    python board_watch.py --city Savannah

STRICTLY READ-ONLY. It creates, moves, recolours and greys nothing. It exists to
turn a silent problem into an email.

WHY THIS, AND NOT AN AUTOMATIC PUSH
-----------------------------------
The watchdog beside this one asks "did the sheet build?". Nothing asked "does the
board still match it?", and on 2026-10-11 a client reported missing Savannah
cards. The board had drifted because nothing pushes it automatically and nobody
was looking.

The obvious fix is to push automatically. That was deliberately NOT done the same
day, because four separate faults were found in the push path within hours of
each other, every one caught by a human reading a preview rather than by a test:

  * pairing that would have shuffled twenty live cards to each other's times;
  * a map rebuild that orphaned 78 cards and shrank the map;
  * a map rebuild that claimed the TEAM'S cards and would have shortened
    seventeen of them from three hours to fifteen minutes;
  * a shared-board fault where a nine-card Thunderbolt push planned to grey 300
    Savannah cards.

All four are fixed and tested. But a path with that recent a history should earn
an unattended write, and the way it earns one is by producing a clean preview for
several days running -- which is what this reports.

WHAT FAILS AND WHAT DOES NOT
----------------------------
It exits non-zero only for things that are SILENTLY WRONG:

  * a property with cleans and no Job -- those cleans produce no card at all and
    nothing says so. This is what hid 3108 Norbury.
  * a stray card: one that looks like ours and is in no map entry. On a market
    board those can never be corrected again, so a calder booking among them
    keeps a green card.

Cards merely waiting to be created are NOT a failure. The window rolls forward
every day, so there is always something new at its far edge; failing on that
would email every morning and the alarm would be muted within the week.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys

from connecteam_map import CITY_SCHEDULERS, LIVE_MARKETS


def run_preview(city: str) -> tuple:
    """(returncode, output) from a real read-only preview of this city."""
    p = subprocess.run(
        [sys.executable, "-u", "connecteam_push.py", "--city", city],
        capture_output=True, text=True, timeout=900,
        env=dict(os.environ),
    )
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def read_counts(out: str) -> dict:
    """What the preview said, pulled from its own words rather than recomputed.

    Deliberately NOT a second implementation of the planner. Every wrong answer
    in this project came from a diagnostic that approximated the real thing; this
    one runs it and reads the result.
    """
    import re
    got = {"create": 0, "change": 0, "strays": 0, "no_job": 0, "wanted": 0}
    m = re.search(r"WOULD create (\d+) job", out)
    if m:
        got["create"] = int(m.group(1))
    m = re.search(r"WOULD change (\d+) card", out)
    if m:
        got["change"] = int(m.group(1))
    m = re.search(r"!! (\d+) card\(s\) on this board look like ours", out)
    if m:
        got["strays"] = int(m.group(1))
    m = re.search(r"(\d+) propertie\(s\) have NO Job", out)
    if m:
        got["no_job"] = int(m.group(1))
    m = re.search(r"(\d+) card\(s\) wanted inside the window", out)
    if m:
        got["wanted"] = int(m.group(1))
    return got


def hawthorne(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--city", default="",
                    help="just this one; default is every live market")
    args = ap.parse_args(argv)

    cities = [args.city] if args.city else sorted(LIVE_MARKETS)
    if not cities:
        print("No market is live yet. Nothing to watch.")
        return 0

    print(f"Watching {len(cities)} live market(s): {', '.join(cities)}")
    print("")
    faults = []
    for city in cities:
        rc, out = run_preview(city)
        if rc != 0:
            faults.append(f"{city}: the preview itself failed (exit {rc})")
            print(f"  {city:<14} PREVIEW FAILED (exit {rc})")
            for line in out.strip().splitlines()[-6:]:
                print(f"                 {line}")
            continue
        c = read_counts(out)
        board = CITY_SCHEDULERS.get(city, "?")
        print(f"  {city:<14} board {board}  "
              f"{c['wanted']:>4} wanted   "
              f"{c['create']:>3} to create   "
              f"{c['change']:>3} to change   "
              f"{c['strays']:>3} stray   "
              f"{c['no_job']:>2} without a Job")
        if c["no_job"]:
            faults.append(
                f"{city}: {c['no_job']} propert(ies) have cleans and no Job, so "
                f"those cleans produce no card and nothing says so")
        if c["strays"]:
            # Carry the ids through. The point of naming them is that
            # shift_delete --ids can act on exactly those and nothing beside
            # them; a count alone would send someone hunting.
            ids = re.findall(r"^\s{6}([0-9a-f]{24}:[0-9a-f-]{36})\s*$",
                             out, re.M)
            detail = ("  ids: " + ", ".join(ids)) if ids else ""
            faults.append(
                f"{city}: {c['strays']} card(s) look like ours and are in no map "
                f"entry, so they can never be corrected -- a calder booking "
                f"among them keeps a green card." + detail)

    print("")
    if not faults:
        print("Every live board is accounted for. Nothing is silently wrong.")
        print("(Cards waiting to be created are normal: the window rolls "
              "forward daily.)")
        return 0
    print("SOMETHING IS SILENTLY WRONG:", file=sys.stderr)
    for f in faults:
        print(f"  * {f}", file=sys.stderr)
    print("", file=sys.stderr)
    print("  What to do: run the Connecteam preview for that market from the "
          "Actions tab and read it. A missing Job is created with job_create; "
          "stray cards are re-adopted with REMAP-ONLY, which touches no card.",
          file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(hawthorne())
