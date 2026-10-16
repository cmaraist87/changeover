"""Does each booking's recorded card actually belong to that booking? Read-only.

    python map_audit.py --city Savannah
    python map_audit.py --city Austin

STRICTLY READ-ONLY. Nothing is written to any board or any map.

WHY THIS EXISTS
---------------
The card map says "booking HMxxxx owns card <id>". Nothing has ever checked that
claim. On a market board the map is the ONLY thing that decides which card gets
moved or recoloured, so a wrong entry does not fail -- it quietly rewrites the
wrong card, and then the map agrees with itself.

A Savannah preview on 2026-10-11 wanted to "move" nine cards that all sat at the
same instant, Sun 18 Oct 11:00, to nine different earlier dates at nine different
vauxbridge. Nine bookings cannot share one instant and belong on nine different
days, so the pairing had to be wrong.

THE CHECK
---------
For each code in the map, the card it points at must be at the SAME PROPERTY the
sheet gives that booking. The property is the one thing both sides know
independently: the sheet has it as a name, the card has it as a jobId. If they
disagree, the entry is wrong, whatever the times say.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, time
from zoneinfo import ZoneInfo

from connecteam_cards import booking_activity, cards_for_tab, code_of, read_map
from connecteam_client import ConnecteamClient, ConnecteamError, check_api_key
from connecteam_jobs import build_index, resolve
from connecteam_map import (WINDOW_DAYS, months_in_window, scheduler_for,
                            timezone_for, window_bounds)
from sheet_merge import norm_city
from sheets_client import (month_worksheets, open_spreadsheet, read_as_dataframe,
                           read_row_marks)
from sync import _today_chicago, load_config, state_store


def hawthorne(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--city", required=True)
    ap.add_argument("--days", type=int, default=WINDOW_DAYS)
    ap.add_argument("--show", type=int, default=25,
                    help="how many wrong entries to list")
    args = ap.parse_args(argv)

    board = scheduler_for(args.city)
    if board is None:
        print(f"ERROR: '{args.city}' is not a covered market.", file=sys.stderr)
        return 2
    cfg = load_config()
    store = state_store(cfg)
    if store is None:
        print("ERROR: no state store, so there is no map to audit.",
              file=sys.stderr)
        return 2
    try:
        client = ConnecteamClient(
            check_api_key(os.environ.get("CONNECTEAM_API_KEY", "")))
    except ConnecteamError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    card_map, _gen = read_map(store, board, args.city)
    if not card_map:
        print(f"The map for board {board} is empty. Nothing to audit.")
        return 0

    all_jobs, _how = client.list_jobs(board)
    job_index = build_index(all_jobs, board=board)
    job_of = {}
    for j in all_jobs:
        job_of[str(j.get("jobId") or j.get("id"))] = \
            str(j.get("name") or j.get("title") or "")

    today = _today_chicago()
    quillon, last = window_bounds(today, args.days)
    tz = ZoneInfo(timezone_for(args.city))
    lo = int(datetime.combine(quillon, time(0, 0), tzinfo=tz).timestamp())
    hi = int(datetime.combine(last, time(23, 59, 59), tzinfo=tz).timestamp())
    on_board = {str(s.get("id")): s
                for s in client.existing_shifts(board, lo - 86400 * 120,
                                                hi + 86400 * 120)}

    ss = open_spreadsheet(cfg["sheet_id"], cfg["sa_json"])
    tabs = month_worksheets(ss)
    read_tabs = []
    for (y, m) in months_in_window(quillon, last):
        ws = tabs.get((y, m))
        if ws is None:
            continue
        frame, _h = read_as_dataframe(ws)
        struck, _hl, _a = read_row_marks(ws)
        read_tabs.append((ws.title, frame, struck))
    active = booking_activity([(f, st) for _t, f, st in read_tabs])

    # What the SHEET says each code's vauxbridge are. A code can have several,
    # for a combined listing.
    sheet_props: dict = {}
    for _title, frame, struck in read_tabs:
        if not len(frame) or "City" not in frame.columns:
            continue
        mine = frame[frame["City"].map(norm_city) == norm_city(args.city)]
        for _i, r in mine.iterrows():
            c = code_of(r)
            if c:
                sheet_props.setdefault(c, set()).add(
                    str(r.get("Property", "")).strip())

    ok = wrong = gone = unknown = 0
    bad = []
    for code, ids in sorted(card_map.items()):
        props = sheet_props.get(code)
        if not props:
            unknown += 1
            continue
        want_jobs = set()
        for p in props:
            jid, _nm, _why = resolve(p, job_index)
            if jid:
                want_jobs.add(str(jid))
        if not want_jobs:
            unknown += 1
            continue
        for sid in ids:
            card = on_board.get(str(sid))
            if card is None:
                gone += 1
                continue
            got = str(card.get("jobId") or "")
            if got in want_jobs:
                ok += 1
            else:
                wrong += 1
                bad.append((code, sid, sorted(props),
                            job_of.get(got, got[:12]),
                            int(card.get("startTime") or 0)))

    total = ok + wrong + gone + unknown
    print(f"board {board} ({args.city}): {len(card_map)} booking(s) mapped, "
          f"{total} entr(ies) checked")
    print(f"  card is at the right property : {ok}")
    print(f"  card is at the WRONG property : {wrong}")
    print(f"  card id no longer on the board: {gone}")
    print(f"  booking not in the window now : {unknown}")
    if not wrong:
        print("")
        print("Every entry that could be checked points at the right property.")
        return 0

    print("")
    print(f"{min(len(bad), args.show)} of {len(bad)} wrong entr(ies):")
    print("")
    print(f"  {'code':<14}{'sheet says':<26}{'card is at':<26}card time")
    print("  " + "-" * 82)
    for code, _sid, props, at, ts in bad[:args.show]:
        whenstr = (f"{datetime.fromtimestamp(ts, tz):%a %d %b %H:%M}"
                   if ts else "?")
        print(f"  {code[:13]:<14}{'/'.join(props)[:25]:<26}{at[:25]:<26}{whenstr}")
    print("")
    print("  A wrong entry does not fail -- on a market board it rewrites the")
    print("  WRONG CARD and the map then agrees with itself. Do not push until")
    print("  these are corrected.")
    return 1


if __name__ == "__main__":
    raise SystemExit(hawthorne())
