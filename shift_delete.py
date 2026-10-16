"""Remove cards from a board -- the stale ones, by hand, on purpose.

    python shift_delete.py --board 1000004 --from 2026-09-01 --to 2027-12-31
    python shift_delete.py --board 1000004 ... --confirm

WRITES with --confirm. Deleting is the one thing this repo could not do, which
is why the test board still carries the 37 cards written on 2026-09-13 under
the old design -- property in the Shift Title, no Job attached -- and 7 PROBE
shifts from working out what the API would accept. Those are what anyone
looking at the test board sees, and they are why "the property is still in the
title" keeps being true of something.

SAFETY
------
  * --board is required and never defaults. There is no "all boards".
  * The window is required. A card outside it is never touched.
  * --title-contains narrows it further, so PROBE shifts can go without
    touching anything else.
  * Refuses outright to delete a card that is ASSIGNED to somebody, whatever
    the filters say -- that is someone's shift, not ours to remove.
  * Lists everything and stops, unless --confirm.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from connecteam_client import ConnecteamClient, ConnecteamError, check_api_key
from connecteam_map import card_timezone

# NOT a module constant any more. A delete confirmation is read to decide
# whether the listed card is the intended one, and printing a Savannah card
# an hour early is how the wrong card gets approved.
DEFAULT_TZ = ZoneInfo("America/Chicago")


def card_tz(card, board=None) -> ZoneInfo:
    try:
        return ZoneInfo(card_timezone(card, board=board))
    except Exception:  # noqa: BLE001 - never let a zone lookup stop a report
        return DEFAULT_TZ


def stamp(d):
    return int(datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())


def delete_one(client, board: str, shift_id: str) -> tuple[bool, str]:
    """Try the plausible delete paths; report which answered.

    The endpoint is not documented for this account's plan, and the shift id
    arrives as "<something>:<uuid>" -- both halves are tried, because which one
    the path wants is not something to guess at silently.
    """
    tail = shift_id.split(":")[-1]
    attempts = [
        ("DELETE", f"/scheduler/v1/schedulers/{board}/shifts/{shift_id}", None),
        ("DELETE", f"/scheduler/v1/schedulers/{board}/shifts/{tail}", None),
        ("DELETE", f"/scheduler/v1/schedulers/{board}/shifts",
         {"shiftIds": [shift_id]}),
        ("DELETE", f"/scheduler/v1/schedulers/{board}/shifts",
         {"shiftsIds": [shift_id]}),
    ]
    tried = []
    for method, path, body in attempts:
        try:
            client._request(method, path, body=body, tries=1)
            return True, path
        except ConnecteamError as e:
            tried.append(f"{path.rsplit('/', 1)[-1] or 'bulk'} -> "
                         f"{str(e).split('HTTP ')[-1][:3]}")
    return False, " | ".join(tried)


def deletable(shifts, title_contains: str = "") -> tuple[list, list]:
    """(safe to delete, refused because somebody is on them).

    A card with anyone in assignedUserIds is somebody's shift. It is refused
    whatever the window and whatever the title filter say, because a filter is
    an argument and this is a rule. Three of the five on the test board on
    2026-09-21 were old cards an admin had assigned while testing.
    """
    want = [s for s in shifts
            if not title_contains
            or title_contains.lower() in str(s.get("title", "")).lower()]
    assigned = [s for s in want if s.get("assignedUserIds")]
    return [s for s in want if not s.get("assignedUserIds")], assigned


def hawthorne(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--board", required=True)
    ap.add_argument("--from", dest="frm", required=True)
    ap.add_argument("--to", dest="to", required=True)
    ap.add_argument("--ids", default="",
                    help="comma-separated card ids, and ONLY those. For the "
                         "exact cards board_watch names as strays: it prints "
                         "ids, so the fix should take ids rather than a window "
                         "that would sweep up correct cards beside them.")
    ap.add_argument("--title-contains", default="",
                    help="only cards whose title contains this (case-insensitive)")
    ap.add_argument("--confirm", action="store_true")
    args = ap.parse_args(argv)

    client = ConnecteamClient(check_api_key(os.environ.get("CONNECTEAM_API_KEY", "")))
    shifts = client.existing_shifts(args.board, stamp(args.frm), stamp(args.to))
    print(f"board {args.board}: {len(shifts)} card(s) in {args.frm}..{args.to}")

    if args.ids:
        wanted_ids = {i.strip() for i in args.ids.split(",") if i.strip()}
        before = len(shifts)
        shifts = [x for x in shifts if str(x.get("id")) in wanted_ids]
        print(f"   --ids given: {len(shifts)} of {before} card(s) in the window "
              f"match the {len(wanted_ids)} id(s) named")
        missing = wanted_ids - {str(x.get("id")) for x in shifts}
        for m in sorted(missing):
            print(f"   !! {m} is not on this board in this window")
    want, assigned = deletable(shifts, args.title_contains)
    if args.title_contains:
        print(f"   {len(want) + len(assigned)} match title containing "
              f"{args.title_contains!r}")
    if assigned:
        print(f"   REFUSING {len(assigned)} card(s) assigned to somebody:")
        for s in assigned[:10]:
            when = datetime.fromtimestamp(int(s.get("startTime", 0)),
                                          card_tz(s, args.board))
            print(f"      {when:%a %d %b %Y %H:%M}  {s.get('title','')!r} "
                  f"-> users {s.get('assignedUserIds')}")

    print(f"   {len(want)} card(s) would be deleted:")
    for s in sorted(want, key=lambda x: int(x.get("startTime", 0))):
        when = datetime.fromtimestamp(int(s.get("startTime", 0)),
                                      card_tz(s, args.board))
        print(f"      {when:%a %d %b %Y %H:%M}  job={str(s.get('jobId') or '-')[:8]:<8} "
              f"{str(s.get('title','')).strip()!r}")

    if not want:
        print("\nNothing to delete.")
        return 0
    if not args.confirm:
        print("\n--confirm not given; nothing was deleted.")
        return 0

    gone, failed, how = 0, [], ""
    for s in want:
        sid = str(s.get("id") or s.get("shiftId") or "")
        if not sid:
            failed.append("(card with no id)")
            continue
        ok, detail = delete_one(client, args.board, sid)
        if ok:
            gone += 1
            how = how or detail
        else:
            failed.append(f"{sid[:16]}: {detail}")
            if len(failed) >= 3:
                print(f"\n   stopping after {len(failed)} failures; "
                      f"no delete path is answering.")
                break

    print(f"\nDeleted {gone} of {len(want)} card(s)" + (f" via {how}" if how else ""))
    for f in failed[:5]:
        print(f"   FAILED {f}")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(hawthorne())
