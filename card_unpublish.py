"""Pull our cards back into the DRAFT layer, so no crew sees them.

    python card_unpublish.py --board 1000004 --from 2026-10-01 --to 2026-12-31
    python card_unpublish.py --board 1000004 --from ... --to ... --limit 1
    python card_unpublish.py --board 1000004 --from ... --to ... --confirm

PREVIEW BY DEFAULT. Nothing is sent without --confirm.

WHY
---
`isPublished` is set when a card is CREATED, so creating in draft does nothing
for the cards already on a board. On Chris Test that left 97 published cards and
2 drafts, which is not what "save them in Draft form" means.

A published open shift with an open spot is an OFFER: Connecteam advertises it
and a cleaner can take it. That is how two cleaners ended up with shifts
nobody meant to give them -- our payload carries isOpenShift true, openSpots 1
and isPublished true, so every card we published was claimable. Pulling a card
back to draft withdraws the offer without deleting the work.

WHAT IT WILL NOT TOUCH
----------------------
  * a card with anybody on it -- assigned is read-only, always;
  * a card that is not ours on all three tests (our title, our colour, our exact
    15-minute length), so the team's own shifts are never withdrawn;
  * a card already in draft.

Whether the update endpoint even ACCEPTS isPublished is not assumed: v2 refuses
locationData outright and v1 refuses open shifts altogether. Every card is read
back after the write and the tool reports what actually changed, so --limit 1
is a real probe.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone

from connecteam_cards import is_assigned, ours
from connecteam_client import ConnecteamClient, ConnecteamError, check_api_key
from connecteam_map import (CANCELLED_COLOR, CARD_MINUTES, OUR_TITLES,
                            STANDARD_COLOR, TURNOVER_COLOR, card_timezone,
                            created_by_us)

OUR_COLOURS = (STANDARD_COLOR, TURNOVER_COLOR, CANCELLED_COLOR)


def stamp(d: str) -> int:
    return int(datetime.strptime(d, "%Y-%m-%d")
               .replace(tzinfo=timezone.utc).timestamp())


def when(card, board) -> str:
    from zoneinfo import ZoneInfo
    try:
        tz = ZoneInfo(card_timezone(card, board=board))
        return f"{datetime.fromtimestamp(int(card['startTime']), tz):%a %d %b %Y %H:%M}"
    except Exception:  # noqa: BLE001 - a bad timestamp must not stop the report
        return "?"


def mine(card) -> bool:
    """Ours on all three tests, and nobody on it."""
    if is_assigned(card):
        return False
    if not ours(card, OUR_TITLES, OUR_COLOURS):
        return False
    if created_by_us(card) is True:
        return True            # createdBy settles it; length is beside the point
    span = int(card.get("endTime") or 0) - int(card.get("startTime") or 0)
    return span == CARD_MINUTES * 60


def hawthorne(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--board", required=True)
    ap.add_argument("--from", dest="frm", required=True)
    ap.add_argument("--to", dest="to", required=True)
    ap.add_argument("--ids", default="",
                    help="comma-separated card ids, and only those")
    ap.add_argument("--limit", type=int, default=0,
                    help="act on at most this many. --limit 1 is the probe that "
                         "answers whether the endpoint accepts isPublished.")
    ap.add_argument("--confirm", action="store_true")
    args = ap.parse_args(argv)

    try:
        client = ConnecteamClient(
            check_api_key(os.environ.get("CONNECTEAM_API_KEY", "")))
    except ConnecteamError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    lo, hi = stamp(args.frm), stamp(args.to)
    try:
        on_board = client.existing_shifts(args.board, lo, hi)
    except ConnecteamError as e:
        print(f"ERROR: could not read the board: {e}", file=sys.stderr)
        return 2

    job_names = {}
    try:
        all_jobs, _how = client.list_jobs(args.board)
        for j in all_jobs:
            job_names[str(j.get("jobId") or j.get("id"))] = \
                str(j.get("name") or j.get("title") or "")
    except ConnecteamError as e:
        print(f"   (could not read Jobs: {str(e)[:80]})", file=sys.stderr)

    print(f"board {args.board}: {len(on_board)} card(s) in "
          f"{args.frm}..{args.to}")

    published = [c for c in on_board if c.get("isPublished")]
    already = len(on_board) - len(published)
    print(f"   published: {len(published)}   already draft: {already}")

    want = [c for c in published if mine(c)]
    skipped_assigned = [c for c in published if is_assigned(c)]
    print(f"   ours and unassigned: {len(want)}")
    if skipped_assigned:
        print(f"   REFUSING {len(skipped_assigned)} card(s) with somebody on "
              f"them -- assigned is read-only")

    if args.ids:
        wanted_ids = {i.strip() for i in args.ids.split(",") if i.strip()}
        before = len(want)
        want = [c for c in want if str(c.get("id")) in wanted_ids]
        print(f"   --ids given: {len(want)} of {before} match the "
              f"{len(wanted_ids)} id(s) named")

    want.sort(key=lambda c: int(c.get("startTime") or 0))
    if args.limit:
        want = want[:args.limit]
        print(f"   --limit {args.limit}: acting on {len(want)}")

    if not want:
        print("\nNothing to pull back to draft.")
        return 0

    print(f"\n   {len(want)} card(s) would move to DRAFT:")
    for c in want:
        print(f"      {when(c, args.board):<22} "
              f"{job_names.get(str(c.get('jobId') or ''), '(unknown Job)'):<26}"
              f"{str(c.get('title') or '')!r}")

    if not args.confirm:
        print("\n--confirm not given; nothing was sent.")
        return 0

    done, failed = 0, []
    for c in want:
        try:
            client.update_shift(args.board, c, {"isPublished": False})
            done += 1
        except (ConnecteamError, ValueError) as e:
            failed.append(f"{str(c.get('id'))[:24]}: {str(e)[:160]}")
            if len(failed) >= 3:
                print(f"\n   stopping after {len(failed)} failure(s).")
                break

    # READ BACK. The write reporting success says only that the call returned;
    # it does not say the field was honoured. This is the same reason the create
    # path checks isPublished on the response rather than trusting the request.
    print("")
    try:
        after = {str(c.get("id")): c
                 for c in client.existing_shifts(args.board, lo, hi)}
    except ConnecteamError as e:
        print(f"   !! sent {done} update(s) but could not read the board back "
              f"to confirm: {e}", file=sys.stderr)
        return 1

    now_draft = sum(1 for c in want
                    if not after.get(str(c.get("id")), {}).get("isPublished", True))
    still_pub = len(want) - now_draft
    print(f"Sent {done} update(s). Confirmed by reading the board back: "
          f"{now_draft} now DRAFT, {still_pub} still published.")
    if still_pub:
        print(f"   !! the endpoint did NOT honour isPublished on "
              f"{still_pub} card(s). They remain visible to crews.",
              file=sys.stderr)
    for f in failed[:5]:
        print(f"   FAILED {f}", file=sys.stderr)
    return 0 if (not failed and not still_pub) else 1


if __name__ == "__main__":
    raise SystemExit(hawthorne())
