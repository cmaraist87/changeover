"""Which card belongs to a booking, and where is it? Read-only.

    python card_lookup.py --code HMSAMPLE14
    python card_lookup.py --property "224 Oakmead 3"
    python card_lookup.py --code HMSAMPLE14 --board 1000002

STRICTLY READ-ONLY. Nothing is written anywhere.

Answers the three questions a sheet row cannot:

  * "this row looks wrong on the board -- which card is it?"
  * "Connecteam support want an id for this job" -> prints it, in both the forms
    the API needs.
  * "did this booking ever get a card at all?" -> says so plainly when not,
    which is the answer that matters, because a property with no Job produces no
    card and says nothing.

A card id on this account is COMPOUND -- "<ObjectId>:<UUID>" -- and the two
halves are not interchangeable. DELETE wants the whole string; the v2 update
wants only the half before the colon and answers "shift id is invalid" for
anything else. Both are printed, labelled, so neither has to be guessed at.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone

from connecteam_cards import map_name, read_map
from connecteam_client import ConnecteamClient, ConnecteamError, check_api_key
from connecteam_map import (CITY_SCHEDULERS, DEFAULT_TIMEZONE,
                            TEST_SCHEDULER, card_timezone)
from sync import load_config, state_store

# Every board worth looking on, test board included.
ALL_BOARDS = sorted(set(CITY_SCHEDULERS.values()) | {TEST_SCHEDULER})


def when(ts, tz=None) -> str:
    """A time in the zone it belongs to. `tz` comes from the card or its board;
    defaulting it to Central showed every Savannah card an hour early."""
    try:
        from zoneinfo import ZoneInfo
        at = datetime.fromtimestamp(int(ts), ZoneInfo(tz or DEFAULT_TIMEZONE))
        return f"{at:%a %d %b %Y %H:%M}"
    except Exception:  # noqa: BLE001 - a bad timestamp is not worth a crash here
        return str(ts)


def hawthorne(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--code", default="",
                    help="a Guesty confirmation code, as it appears in the sheet")
    ap.add_argument("--property", dest="prop", default="",
                    help="a property name; matches the Job the card points at")
    ap.add_argument("--board", default="",
                    help="only this board. Default: every board.")
    args = ap.parse_args(argv)

    if not args.code and not args.prop:
        print("ERROR: give --code or --property.", file=sys.stderr)
        return 2
    code = args.code.strip().upper()

    try:
        client = ConnecteamClient(
            check_api_key(os.environ.get("CONNECTEAM_API_KEY", "")))
    except ConnecteamError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    store = state_store(load_config())
    boards = [args.board] if args.board else ALL_BOARDS

    found_any = False
    # Boards whose read FAILED. Tracked because the clean negative printed
    # at the bottom of this tool is only true if every board was actually
    # read. On 2026-10-11 six lookups hit Connecteam's rate limit, every
    # board read failed, and all six printed "No card anywhere ... that is
    # a real answer, not a failure" -- which was the opposite of the truth.
    unread = []
    for board in boards:
        # EVERY city on this board. The map is per board AND city, because two
        # markets share a board, and a lookup must not miss a card merely because
        # the booking belongs to the other one.
        cities = [c for c, b in CITY_SCHEDULERS.items() if str(b) == str(board)]
        if str(board) == TEST_SCHEDULER:
            cities = sorted(CITY_SCHEDULERS)      # the test board takes any city
        card_map: dict = {}
        found_in: dict = {}
        for c in cities:
            m, _gen = read_map(store, board, c)
            for k, v in m.items():
                card_map.setdefault(k, []).extend(str(x) for x in v)
                for x in v:
                    found_in[str(x)] = c
        ids = [str(i) for i in card_map.get(code, ())] if code else []

        # The Job, so --property can be answered and so an id can be named.
        job_names = {}
        try:
            all_jobs, _how = client.list_jobs(board)
            for j in all_jobs:
                jid = str(j.get("jobId") or j.get("id"))
                job_names[jid] = str(j.get("name") or j.get("title") or "")
        except ConnecteamError as e:
            print(f"board {board}: could not read Jobs ({str(e)[:200]})",
                  file=sys.stderr)
            unread.append(f"{board} (Jobs)")

        want_jobs = set()
        if args.prop:
            low = args.prop.strip().lower()
            want_jobs = {jid for jid, nm in job_names.items()
                         if low in nm.lower()}

        if not ids and not want_jobs:
            continue

        # A wide read, because a card may have been moved since it was recorded.
        lo = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp())
        hi = int(datetime(2028, 1, 1, tzinfo=timezone.utc).timestamp())
        try:
            on_board = client.existing_shifts(board, lo, hi)
        except ConnecteamError as e:
            print(f"board {board}: could not read cards ({str(e)[:200]})",
                  file=sys.stderr)
            unread.append(f"{board} (cards)")
            continue
        by_id = {str(s.get("id")): s for s in on_board}

        hits = []
        for sid in ids:
            hits.append((sid, by_id.get(sid)))
        if want_jobs:
            for s in on_board:
                if str(s.get("jobId")) in want_jobs:
                    sid = str(s.get("id"))
                    if sid not in [h[0] for h in hits]:
                        hits.append((sid, s))
        if not hits:
            continue

        found_any = True
        label = f"board {board}"
        for city, b in sorted(CITY_SCHEDULERS.items()):
            if str(b) == str(board):
                label += f" ({city})"
                break
        if str(board) == TEST_SCHEDULER:
            label += " (Chris Test)"
        print("")
        print(f"=== {label} ===")
        for sid, card in hits:
            print("")
            if card is None:
                print(f"  {sid}")
                print(f"    THE MAP HAS THIS ID BUT THE BOARD DOES NOT. The card "
                      f"was deleted in Connecteam, or the map is stale.")
                continue
            jid = str(card.get("jobId") or "")
            print(f"  property   {job_names.get(jid, '(unknown Job)')}")
            ctz = card_timezone(card, board=board)
            print(f"  when       {when(card.get('startTime'), ctz)}"
                  f" - {when(card.get('endTime'), ctz)[-5:]}  ({ctz})")
            print(f"  title      {card.get('title')!r}")
            print(f"  colour     {card.get('color')}")
            print(f"  assigned   "
                  f"{card.get('assignedUserIds') or 'nobody (open shift)'}")
            print(f"  card id    {sid}")
            print(f"    for DELETE          use the whole id above")
            print(f"    for the v2 UPDATE   use {sid.split(':', 1)[0]}")
            print(f"  job id     {jid}")
            if store is not None:
                owner = found_in.get(sid)
                if owner:
                    print(f"  recorded in {map_name(board, owner)}")
                else:
                    print(f"  NOT in any card map for this board -- found by "
                          f"property only, so nothing can correct it")

    # An incomplete lookup must never read as an empty one.
    if unread:
        print("")
        print(f"LOOKUP INCOMPLETE: {len(unread)} board read(s) FAILED "
              f"-- {', '.join(unread)}.", file=sys.stderr)
        print("  Nothing below is a negative answer: a card at one of those "
              "boards would not have been seen.", file=sys.stderr)
        if found_any:
            print("  The cards printed above are real; others may be missing.",
                  file=sys.stderr)
        print("  A 429 means too many calls at once -- re-run this one alone.",
              file=sys.stderr)
        return 2

    if not found_any:
        print("")
        if code:
            print(f"No card anywhere for {code!r}.")
            print("That is a real answer, not a failure. It happens when:")
            print("  * the booking has no check-out, so there is nothing to clean;")
            print("  * its property has no Job on the board, so no card is made;")
            print("  * the booking is outside every pushed window;")
            print("  * or the market is not live yet.")
        else:
            print(f"No card found for a property matching {args.prop!r}.")
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(hawthorne())
