"""
Turn one month of one city's schedule rows into Connecteam jobs.

By default it CREATES NOTHING. It reads the sheet, works out the jobs, and prints
them. Writing needs --live, and one city at a time, because a job card that reaches
a crew's phone cannot be taken back the way a sheet cell can.

    python connecteam_push.py --city Austin
    python connecteam_push.py --city Austin --month 2026-09
    python connecteam_push.py --city Austin --live        # after reading the list

Needs CONNECTEAM_API_KEY, plus the same SHEET_ID / GOOGLE_SA_JSON the sync uses.
On the office machine HTTPS also needs the rebuilt Windows CA bundle:
    $env:REQUESTS_CA_BUNDLE = "$HOME\\win-ca-bundle.pem"
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from connecteam_client import ConnecteamClient, ConnecteamError
from connecteam_cards import (booking_activity, cards_for_tab, is_assigned,
                              created_by_us,
                              orphans,
                              ours, plan, read_map, write_map, write_snapshot)
from connecteam_jobs import build_index, resolve, usable
from connecteam_map import (CANCELLED_COLOR, CARD_MINUTES,
                            GREY_CANCELLATIONS_EVERYWHERE,
                            CITY_SCHEDULERS, LIVE_MARKETS, OUR_TITLES, STANDARD_COLOR,
                            TEST_SCHEDULER, TURNOVER_COLOR,
                            WINDOW_DAYS, card_when, months_in_window,
                            scheduler_for, timezone_for, window_bounds)
from sheet_merge import norm_city
from sheets_client import (month_worksheets, open_spreadsheet,
                           read_as_dataframe, read_row_marks)
from sync import _spanish_tab, _today_chicago, load_config, state_store


# The three colours this system sends. Named once so the alarm and the map
# rebuild cannot drift apart about what "ours" means.
OUR_COLOURS = (STANDARD_COLOR, TURNOVER_COLOR, CANCELLED_COLOR)


def _when(card, city=None) -> str:
    """A card's start, readable, for a report somebody has to act on.

    In the card's OWN zone, not the company's. This printed every Savannah and
    Thunderbolt time in Central -- an hour behind what the crew sees on the
    board -- so a stray reported at 10:00 was the 11:00 card.
    """
    return card_when(card, city=city)


def _slot_of(card) -> tuple:
    """(property, title, instant) -- what makes two cards the same job.

    The same shape connecteam_client uses to skip an occupied slot, so the two
    agree by construction about which desired card is which existing card.
    """
    return (str(card.get("jobId") or ""), str(card.get("title") or ""),
            int(card.get("startTime") or 0))


def hawthorne(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--city", required=True,
                    help="One market at a time: " + ", ".join(sorted(CITY_SCHEDULERS)))
    ap.add_argument("--month", default=None, metavar="YYYY-MM",
                    help="Pin to ONE calendar month instead of the rolling "
                         "window. For a backfill or a demo, by hand.")
    # DRAFT IS THE DEFAULT, for every board. Chris, 2026-10-13.
    #
    # A published open shift with an open spot is an OFFER: Connecteam
    # advertises it and a cleaner can take it. That is how two cleaners
    # received shifts nobody meant to give them. Draft withdraws the offer, and
    # the client publishes deliberately once they have assigned the week -- which
    # is how they already work.
    #
    # --draft is kept so older workflow routes that pass it still run; it is now
    # the default and therefore a no-op. Publishing takes an explicit --publish,
    # which nothing in any workflow passes.
    ap.add_argument("--draft", action="store_true",
                    help="(default) create cards in the DRAFT layer")
    ap.add_argument("--publish", action="store_true",
                    help="create cards PUBLISHED, visible to crews immediately "
                         "and claimable by them. Not the default, and no "
                         "workflow route passes it.")
    ap.add_argument("--remap-only", action="store_true",
                    help="Rebuild the code-to-card map from the board and write "
                         "it. Applies NOTHING: no card is created, moved, "
                         "recoloured or greyed. For repairing a map that has "
                         "drifted, before a push acts on it.")
    ap.add_argument("--start", default="", metavar="YYYY-MM-DD",
                    help="do not card anything before this date. A FLOOR, not a "
                         "fixed start: once the date has passed it has no effect, "
                         "so the window cannot keep shrinking from the front.")
    ap.add_argument("--days", type=int, default=WINDOW_DAYS, metavar="N",
                    help=f"Rolling window depth in days (default {WINDOW_DAYS}). "
                         f"Also the slice of the board that gets reconciled.")
    ap.add_argument("--test", action="store_true",
                    help="Send to the test board instead of the city's real one. "
                         "The board has no crew, so nothing reaches a phone.")
    ap.add_argument("--live", action="store_true",
                    help="Actually create the jobs. Without it, nothing is sent. "
                         "Only valid together with --test: see below.")
    args = ap.parse_args(argv)

    # --live WITHOUT --test is refused, here, in code.
    #
    # Chris' instruction, 2026-09-21: "Nothing goes anywhere else except Chris
    # Test until I sign off on testing the live boards." Earlier the same day I
    # asked whether a live Austin write was acceptable, read the answer as
    # standing permission, and pushed 38 cards to the Austin board. It was not
    # that, and they had to be deleted again.
    #
    # A rule that lives in a workflow input is one dispatch away from being
    # picked, and a rule in a comment is worth nothing at all. So the market
    # boards are unreachable from every entry point until this branch is
    # deliberately removed -- which is what "signs off" has to mean.
    # Austin was signed off on 2026-10-07; the other four are not. The gate is a
    # named set in connecteam_map, so turning one market on cannot turn the rest
    # on with it.
    if args.live and not args.test and norm_city(args.city) not in LIVE_MARKETS:
        print(f"REFUSED: {args.city} is not signed off for live writes.",
              file=sys.stderr)
        print(f"         Signed off so far: "
              f"{', '.join(sorted(LIVE_MARKETS)) or '(none)'}.", file=sys.stderr)
        print("         Everything else goes to Chris Test with --test. Adding a "
              "market to LIVE_MARKETS is the whole of a go-live; no workflow "
              "input grants it.", file=sys.stderr)
        return 2
    if args.live and not args.test:
        print(f"LIVE MARKET BOARD: {args.city} is signed off, so cards go to its "
              f"OWN board, which crews see.")

    board = scheduler_for(args.city)
    if board is None:
        print(f"ERROR: '{args.city}' is not one of the covered markets.", file=sys.stderr)
        print("       Covered: " + ", ".join(sorted(CITY_SCHEDULERS)), file=sys.stderr)
        return 2

    key = os.environ.get("CONNECTEAM_API_KEY", "").strip()
    if not key:
        print("ERROR: CONNECTEAM_API_KEY is not set.", file=sys.stderr)
        return 2

    cfg = load_config()
    if not cfg["sheet_id"]:
        print("ERROR: SHEET_ID is not set.", file=sys.stderr)
        return 2

    board_for_jobs = TEST_SCHEDULER if args.test else board
    target_board = board_for_jobs

    # THE WINDOW. Rolling 45 days from today by default; --month pins a single
    # calendar month instead, which is how a one-off backfill or a demo push is
    # done by hand.
    #
    # The same bounds are used three times over and must not diverge: which tabs
    # are read, which cards are created, and which slice of the board is
    # reconciled. Reconciling wider than we push would grey every card an earlier
    # run left outside the window; pushing wider than we reconcile would create
    # cards that nothing ever corrects again.
    today = _today_chicago()
    if args.month:
        y, m = int(args.month[:4]), int(args.month[5:7])
        quillon = date(y, m, 1)
        last = date(y + (m == 12), 1 if m == 12 else m + 1, 1) - timedelta(days=1)
        print(f"WINDOW: the whole of {args.month} ({quillon} to {last}), by --month.")
    else:
        floor = None
        if args.start:
            floor = date(int(args.start[:4]), int(args.start[5:7]),
                         int(args.start[8:10]))
        quillon, last = window_bounds(today, args.days, floor)
        print(f"WINDOW: rolling {args.days} days, {quillon} to {last} "
              f"(today is {today}).")
        if floor:
            if floor > today:
                print(f"        starting no earlier than {floor}, so the days "
                      f"the team has already carded by hand are left alone.")
            else:
                print(f"        --start {floor} has passed; it no longer "
                      f"restricts anything.")

    tz = ZoneInfo(timezone_for(args.city))
    lo = int(datetime.combine(quillon, time(0, 0), tzinfo=tz).timestamp())
    hi = int(datetime.combine(last, time(23, 59, 59), tzinfo=tz).timestamp())

    ss = open_spreadsheet(cfg["sheet_id"], cfg["sa_json"])
    tabs_by_month = month_worksheets(ss)

    client = ConnecteamClient(key)
    all_jobs, how = client.list_jobs(board_for_jobs)
    # Scoped to the board being written to, and with the soft-deleted dropped.
    # The account-wide list is a superset three times over: 506 of 1429 are
    # deleted, and the rest belong to nine different boards.
    job_index = build_index(all_jobs, board=board_for_jobs)
    print(f"{len(all_jobs)} Job(s) on the account [{how}]; "
          f"{len(usable(all_jobs, board_for_jobs))} usable on board "
          f"{board_for_jobs}; {len(job_index)} distinct name(s).")

    # Every board, live ones included -- Chris, 2026-10-08. See
    # GREY_CANCELLATIONS_EVERYWHERE for why this is no longer a per-board set.
    grey_cancellations = GREY_CANCELLATIONS_EVERYWHERE
    print(f"Calder bookings go {CANCELLED_COLOR} on board {target_board}, as "
          f"on every board.")

    # One tab at a time, because strikethrough positions are per worksheet. A
    # concatenated frame would need its indices remapped, and an off-by-one there
    # paints the wrong booking grey while leaving a real cancellation looking
    # live. Read whole here, judged below, built per tab.
    read_tabs = []
    missing = []
    for (y, m) in months_in_window(quillon, last):
        ws = tabs_by_month.get((y, m))
        if ws is None:
            missing.append(f"{y}-{m:02d} ('{_spanish_tab(f'{y}-{m:02d}')}')")
            continue
        frame, _hdr = read_as_dataframe(ws)
        # A SECOND read of the same tab, for its FORMAT. A cancellation is not in
        # any cell's value -- it is strikethrough on the row -- so the frame above
        # cannot answer "was this calder?" and never could.
        struck, _hl, _acc = read_row_marks(ws)
        read_tabs.append((ws.title, frame, struck))
        print(f"  '{ws.title}': {len(frame)} row(s), {len(struck)} struck.")
    if missing:
        print(f"  no tab for {', '.join(missing)} -- nothing read for those "
              f"month(s). A month with no tab yet is normal near the window's "
              f"far edge.")

    # Is each BOOKING live? Judged by Confirmation Code across EVERY tab read,
    # not per tab and not within the window, because a booking can move from one
    # month into the next and its live row is then somewhere this window does not
    # cover. Judged narrowly, that booking reads as calder and a crew is told
    # a job is off when it is merely later.
    active = booking_activity([(f, st) for _t, f, st in read_tabs])
    n_dead = sum(1 for v in active.values() if not v)
    print(f"{len(active)} booking(s) across those tabs, {n_dead} of them "
          f"calder (every row struck, and live nowhere else).")

    # What the sheet wants on the board: one entry per card, carrying the code it
    # belongs to so a later run can find the same card again.
    desired = []
    in_city = 0
    unmatched = set()
    for title, frame, struck in read_tabs:
        if not len(frame) or "City" not in frame.columns:
            continue
        # Boolean mask, not a rebuild: it keeps the original index, which is what
        # `struck` is expressed in.
        mine = frame[frame["City"].map(norm_city) == norm_city(args.city)]
        in_city += len(mine)
        for _i, r in mine.iterrows():
            if str(r.get("Check out - Time", "") or r.get("Check-out Time", "")).strip() \
                    and resolve(r.get("Property", ""), job_index)[0] is None:
                prop = str(r.get("Property", "")).strip()
                if prop:
                    unmatched.add(prop)
        for row, code, payload in cards_for_tab(
                mine, struck, active, job_index=job_index,
                grey_cancellations=grey_cancellations):
            if scheduler_for(row.get("City", "")) != board:
                continue
            if not (lo <= int(payload["startTime"]) <= hi):
                continue
            desired.append((row, code, payload))

    print(f"{in_city} row(s) are {args.city} across the window's tabs; "
          f"{len(desired)} card(s) wanted inside the window (a row with no "
          f"check-out is not a job, and a moved booking's old row is not either).")
    n_grey_wanted = sum(1 for _r, _c, p in desired
                        if str(p.get("color", "")).upper() == CANCELLED_COLOR.upper())
    if n_grey_wanted:
        print(f"  {n_grey_wanted} of them are calder bookings, so they are "
              f"{CANCELLED_COLOR}.")

    # Every property that produced no card, and why. These are the ones the team
    # has to create in Connecteam before their cleans can reach anybody.
    if unmatched:
        # How many CLEANS are lost, not just how many vauxbridge. "21 vauxbridge
        # have no Job" sounds like a tidy-up; "71 fairwind"
        # is the same fact and is the one that decides whether a board is fit to
        # go live.
        lost = 0
        for title, frame, struck in read_tabs:
            if not len(frame) or "City" not in frame.columns:
                continue
            mine = frame[frame["City"].map(norm_city) == norm_city(args.city)]
            for _i, r in mine.iterrows():
                if str(r.get("Property", "")).strip() not in unmatched:
                    continue
                if not str(r.get("Check out - Time", "")
                           or r.get("Check-out Time", "")).strip():
                    continue
                lost += 1
        print("")
        print(f"  {len(unmatched)} propertie(s) have NO Job on this board, so "
              f"{lost} clean(s) in this window are NOT being sent, silently:")
        for prop in sorted(unmatched):
            print(f"     {prop}")
        print("  Create these as Jobs in Connecteam and they are picked up "
              "on the next run.")

    # What each card will actually point at, so the choice can be read before it
    # is made. Where several Jobs matched, the reason says what was passed over.
    shown = {}
    for row, _code, _payload in desired:
        prop = str(row.get("Property", "")).strip()
        if prop not in shown:
            _jid, name, why = resolve(prop, job_index)
            shown[prop] = (name, why)
    if shown:
        print("")
        print(f"  {len(shown)} propertie(s) -> Job:")
        for prop, (name, why) in sorted(shown.items()):
            mark = "  " if why == "one Job matches" else " *"
            print(f"   {mark} {prop:<30} -> {name}")
            if mark == " *":
                print(f"        {why}")

    if args.test:
        # Verify before redirecting. The test board's id has already changed twice --
        # once because a group id was mistaken for it, once because the board was
        # deleted and rebuilt -- so a pinned id that has gone stale must say so here
        # rather than 404 in the middle of a write.
        boards = {str(b.get("schedulerId") or b.get("id")): (b.get("name") or "")
                  for b in client.list_schedulers()}
        if TEST_SCHEDULER not in boards:
            print(f"ERROR: the test board {TEST_SCHEDULER} is not on this account any "
                  f"more.", file=sys.stderr)
            print("       Boards that do exist:", file=sys.stderr)
            for bid, name in sorted(boards.items()):
                print(f"         {bid:<12} {name}", file=sys.stderr)
            return 2
        board = TEST_SCHEDULER
        print(f"TEST BOARD: sending {args.city}'s jobs to {board} "
              f"({boards[board]!r}), not to {args.city}'s own board.")
    print(f"\nBoard {board} ({args.city}) -- "
          + ("CREATING" if args.live else "PREVIEW, nothing will be sent") + ":\n")
    job_names = {str(j.get("jobId") or j.get("id")):
                 (j.get("name") or j.get("title") or "") for j in all_jobs}

    # The board as it is now, and what an earlier run recorded about which card
    # belongs to which booking. The map is a HINT: every id in it is checked
    # against the board below, because a card deleted in the Connecteam UI leaves
    # it stale and nothing tells us.
    store = state_store(cfg)
    card_map, generation = read_map(store, board, args.city)
    on_board = client.existing_shifts(board, lo, hi)
    print(f"   {len(on_board)} card(s) already on the board in the window; "
          f"the map knows {sum(len(v) for v in card_map.values())} of them "
          f"across {len(card_map)} booking(s).")

    # Adoption by slot is for the TEST board only. On a market board the only
    # cards we may modify are ones we created and recorded -- see plan().
    adopt_here = str(board) == str(TEST_SCHEDULER)
    if not adopt_here:
        print(f"   board {board} is a market board: adoption is OFF, so only "
              f"cards this system created and recorded can be changed.")
    # Repair the map and stop. Deliberately BEFORE the plan: a map holding the
    # wrong cards makes the plan propose changes to them, and a live run would
    # apply those changes before any rebuild could correct the map. On 2026-10-11
    # the map had claimed 95 of the team's cards and the plan wanted to shorten
    # seventeen of them -- the repair has to be able to run without that.
    if args.remap_only:
        after = client.existing_shifts(board, lo, hi)
        kept = []
        for c in after:
            if not ours(c, OUR_TITLES, OUR_COLOURS):
                continue
            # Length separated us from the team before createdBy did. Where
            # createdBy proves the card is ours, length is beside the point --
            # and insisting on it would drop a card of ours that somebody
            # resized, which is how a card becomes a stray and gets duplicated.
            if created_by_us(c) is not True and (
                    int(c.get("endTime") or 0) - int(c.get("startTime") or 0)
                    != CARD_MINUTES * 60):
                continue
            kept.append(c)
        by_slot: dict = {}
        for c in kept:
            by_slot.setdefault(_slot_of(c), []).append(str(c.get("id")))
        rebuilt: dict = {}
        claimed: set = set()
        for code, payload in [(c, p) for _r, c, p in desired]:
            for sid in by_slot.get(_slot_of(payload), ()):
                if sid in claimed:
                    continue
                rebuilt.setdefault(code, []).append(sid)
                claimed.add(sid)
                break
        was = sum(len(v) for v in card_map.values())
        now = sum(len(v) for v in rebuilt.values())
        print("")
        print(f"REMAP ONLY -- no card is created, moved, recoloured or greyed.")
        print(f"   {len(after)} card(s) on the board in the window")
        print(f"   {len(kept)} of them carry our title, our colour AND our "
              f"{CARD_MINUTES}-minute length")
        print(f"   map was {was} card(s) across {len(card_map)} booking(s)")
        print(f"   map now {now} card(s) across {len(rebuilt)} booking(s)  "
              f"({now - was:+d})")
        if not args.live:
            print("")
            print("   --live not given; the map was NOT written.")
            return 0
        if write_map(store, board, rebuilt, generation, args.city):
            print("   map written.")
        return 0

    # THE LOST-MAP ALARM. A card of ours that the map does not know can never be
    # corrected on a market board, and nothing downstream would say so.
    # The alarm looks across EVERY city sharing this board. Scoped to one city it
    # would report the other city's cards as strays -- 40 of them on the Savannah
    # board -- and an alarm that is wrong by design gets ignored.
    board_wide = dict(card_map)
    for other in sorted(CITY_SCHEDULERS):
        if str(CITY_SCHEDULERS[other]) != str(board) or other == norm_city(args.city):
            continue
        their_map, _g = read_map(store, board, other)
        for k, v in their_map.items():
            board_wide.setdefault(f"{other}:{k}", list(v))
    stranded = orphans(on_board, board_wide, OUR_TITLES, OUR_COLOURS,
                       card_seconds=CARD_MINUTES * 60)
    if stranded:
        print("")
        print(f"   !! {len(stranded)} card(s) on this board look like ours but "
              f"are in no map entry.")
        if adopt_here:
            print(f"      This board adopts by slot, so they will be recovered "
                  f"below and recorded.")
        else:
            print(f"      This board does NOT adopt, so they cannot be moved, "
                  f"recoloured or greyed -- a calder booking among them would "
                  f"keep a green card. Restore the card map, or run with the "
                  f"test board to re-derive it.")
            # The FULL id. It was clipped to 28 characters, which made the
            # alarm name cards that shift_delete --ids could not find: a card id
            # here is "<24 hex>:<full uuid>" and the clip cut the uuid in half.
            # A report exists to be acted on; one that prints an identifier you
            # cannot use is worse than one that prints none.
            # Name the PROPERTY too. The id says which card to delete; the
            # property and time are what let someone decide whether it should
            # be deleted, which is the actual question an alarm raises.
            for s2 in stranded[:10]:
                prop = job_names.get(str(s2.get("jobId") or ""), "")
                print(f"      {s2.get('id')}")
                print(f"        {prop or '(no Job -- unplaced card)'}  "
                      f"{_when(s2, args.city)}")
                print(f"        {s2.get('title')!r}  {s2.get('color')}")
            if len(stranded) > 10:
                print(f"      ... and {len(stranded) - 10} more")
        print("")

    updates, creates, greys, new_map, held = plan(
        [(c, p) for _r, c, p in desired], on_board, card_map, adopt=adopt_here,
        our_titles=OUR_TITLES,
        our_colors=(STANDARD_COLOR, TURNOVER_COLOR, CANCELLED_COLOR),
        stale_color=CANCELLED_COLOR if grey_cancellations else None)

    # ASSIGNED CARDS ARE READ-ONLY. Printed in full rather than counted: a run
    # that silently declines to create a card is exactly as confusing as one
    # that silently creates a duplicate, and this is the list that explains why
    # the board has fewer blocks than the sheet has cleans.
    if held:
        print("")
        print(f"   {len(held)} card(s)/clean(s) LEFT ALONE because a person is "
              f"on the card, or one already covers that Job that day:")
        for why, code, what in held[:25]:
            where = job_names.get(str(what.get("jobId") or ""), "(unknown Job)")
            who = [v for k, v in what.items()
                   if ("user" in k.lower() or "assign" in k.lower()) and v]
            print(f"      {where:<28} {_when(what, args.city)}  "
                  f"{(code or '-'):<12} {why}")
            if who:
                print(f"        assigned to {who[0]}")
        if len(held) > 25:
            print(f"      ... and {len(held) - 25} more")
        print("      Nothing above is created, moved, recoloured or greyed.")

    # BEFORE anything is written: the board exactly as it is. Held whole rather
    # than summarised, because a summary cannot be restored from, and the point
    # is to answer "what did it look like before?" at 6am without reading a log
    # and squinting. card_restore reads it back.
    #
    # Only on a live run, and only when there is actually something to do -- a
    # run that changes nothing has nothing to undo, and a snapshot per no-op
    # would bury the ones that matter.
    if args.live and (updates or creates or greys):
        taken = write_snapshot(
            store, board, on_board,
            {"window": f"{quillon}..{last}", "city": args.city,
             "planned": {"move_or_recolour": len(updates),
                         "create": len(creates), "grey": len(greys)}})
        if taken:
            print(f"   before-snapshot: {len(on_board)} card(s) saved as {taken}")
        else:
            print("   !! NO before-snapshot was stored. Undo would mean reading "
                  "the changes out of this log by hand.")

    # Move and recolour quillon. A booking that changed date or time keeps ITS card
    # -- Chris, 2026-10-06 -- so this is an update, not a delete and a create, and
    # it has to happen before create_shifts so the moved card is already where the
    # sheet wants it and is not created a second time.
    try:
        client.apply_card_plan(
            board, updates, greys,
            stale_color=CANCELLED_COLOR if grey_cancellations else None,
            live=args.live, job_names=job_names)
    except ConnecteamError as e:
        print("", file=sys.stderr)
        print(f"!! could not move or recolour cards: {e}", file=sys.stderr)
        return 1
    except ValueError as e:            # the unassigned gate, on an update
        print("", file=sys.stderr)
        print(f"!! REFUSED: {e}", file=sys.stderr)
        return 1

    try:
        created = client.create_shifts(
            board, [p for _c, p in creates], live=args.live,
            draft=not args.publish,
            # So the preview names the property, not thirty-eight "Clean"s.
            job_names=job_names)
    except ConnecteamError as e:
        print("", file=sys.stderr)
        print(f"!! {e}", file=sys.stderr)
        return 1
    except ValueError as e:            # the unassigned gate
        print("", file=sys.stderr)
        print(f"!! REFUSED: {e}", file=sys.stderr)
        return 1

    if not args.live:
        print("")
        print("Nothing was created, moved or recoloured. Re-run with --live once "
              "this list looks right.")
        return 0

    # Record every booking's cards by READING THE BOARD BACK and matching on the
    # slot -- property, title and instant -- not by trusting the order the create
    # response came in.
    #
    # It was a zip of the request list against the response list, which needed
    # both to be the same length AND the same order. create_shifts skips a slot
    # that is already taken, so on 2026-10-11 it sent 98 and created 78, the zip
    # was abandoned, and the map SHRANK from 301 entries to 243 -- leaving 78
    # cards on a live board that nothing could ever correct, because a market
    # board does not adopt. The old code said "the next run adopts them by their
    # slot", which has been untrue for market boards since adoption was turned
    # off there.
    #
    # Reading back is order-independent, survives a partial skip, and is
    # self-healing: a card orphaned by any earlier run is re-found here.
    if args.live:
        after = client.existing_shifts(board, lo, hi)
        by_slot: dict = {}
        for c in after:
            # ONLY cards that could be ours. Matching on the slot alone claimed
            # the TEAM'S cards: they now write our titles on their own
            # ("Turnover", "sale no entran huespedes"), many of their cards start
            # at 11:00 like ours, and the slot is only property-title-instant. On
            # 2026-10-11 the rebuild took 95 cards that way and the next push
            # proposed shortening seventeen of them from three hours to fifteen
            # minutes -- rewriting the crews' own schedule.
            #
            # Three tests, all of which must pass: our titles, our colours, and
            # our exact card length. A card the team made fails at least one.
            if not ours(c, OUR_TITLES, OUR_COLOURS):
                continue
            # Length separated us from the team before createdBy did. Where
            # createdBy proves the card is ours, length is beside the point --
            # and insisting on it would drop a card of ours that somebody
            # resized, which is how a card becomes a stray and gets duplicated.
            if created_by_us(c) is not True and (
                    int(c.get("endTime") or 0) - int(c.get("startTime") or 0)
                    != CARD_MINUTES * 60):
                continue
            by_slot.setdefault(_slot_of(c), []).append(str(c.get("id")))
        rebuilt: dict = {}
        claimed: set = set()
        for code, payload in [(c, p) for _r, c, p in desired]:
            for sid in by_slot.get(_slot_of(payload), ()):
                if sid in claimed:
                    continue
                rebuilt.setdefault(code, []).append(sid)
                claimed.add(sid)
                break
        # A card the team has ASSIGNED must stay recorded against its booking.
        # The three tests above are about not CLAIMING a stranger's card; they
        # are the wrong question for a card we already know is ours. An assigned
        # card fails them the moment somebody also recolours it, and dropping it
        # here would make it a stray -- after which the next run creates the
        # duplicate again. That is the loop the client reported on 2026-10-12.
        prior = {str(v) for vs in (card_map or {}).values() for v in vs}
        on_now = {str(c.get("id")): c for c in after}
        for code, ids in (new_map or {}).items():
            for sid in ids:
                card = on_now.get(str(sid))
                if card is None or not is_assigned(card):
                    continue
                if str(sid) not in prior:
                    continue          # not one we had recorded; do not adopt it
                if str(sid) in claimed:
                    continue
                rebuilt.setdefault(code, []).append(str(sid))
                claimed.add(str(sid))
        gained = len({s for v in rebuilt.values() for s in v})             - len({s for v in new_map.values() for s in v})
        print(f"   card map rebuilt from the board: "
              f"{sum(len(v) for v in rebuilt.values())} card(s) across "
              f"{len(rebuilt)} booking(s)"
              + (f", {gained:+d} vs pairing alone" if gained else ""))
        new_map = rebuilt

    if write_map(store, board, new_map, generation, args.city):
        print(f"   card map stored: {sum(len(v) for v in new_map.values())} "
              f"card(s) across {len(new_map)} booking(s).")

    print("")
    print(f"Created {len(created)} job(s) in {args.city}, all Unassigned.")
    return 0


if __name__ == "__main__":
    raise SystemExit(hawthorne())
