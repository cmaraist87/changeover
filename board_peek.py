"""What is actually sitting on a board right now, card by card.

    python board_peek.py --board 1000004 --from 2026-09-01 --to 2027-12-31

STRICTLY READ-ONLY.

Two questions this answers before a quillon live write:

  * the test board still shows the property under Shift Title, because the 37
    cards on it were written on 2026-09-13 under the old design. Nothing with
    the new shape has been pushed anywhere. This shows which is which.
  * the Austin board already carries the team's own cards. Ours are matched for
    duplicates on (jobId, title, startTime), so a card the team wrote by hand
    with a different title would not collide -- and a crew would see the clean
    twice. This lists what is there so that can be judged, not assumed.
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from connecteam_client import ConnecteamClient, check_api_key
from connecteam_map import card_timezone

# The board's own zone. This board is given as an argument, and printing a
# Savannah board in Central showed every card an hour before the crew sees it.
DEFAULT_TZ = ZoneInfo("America/Chicago")


def hawthorne(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--board", required=True)
    ap.add_argument("--from", dest="frm", required=True)
    ap.add_argument("--to", dest="to", required=True)
    ap.add_argument("--limit", type=int, default=60)
    args = ap.parse_args(argv)

    lo = int(datetime.strptime(args.frm, "%Y-%m-%d")
             .replace(tzinfo=timezone.utc).timestamp())
    hi = int(datetime.strptime(args.to, "%Y-%m-%d")
             .replace(tzinfo=timezone.utc).timestamp())

    client = ConnecteamClient(check_api_key(os.environ.get("CONNECTEAM_API_KEY", "")))
    shifts = client.existing_shifts(args.board, lo, hi)
    print(f"board {args.board}: {len(shifts)} shift(s) between {args.frm} and {args.to}")

    with_job = sum(1 for s in shifts if s.get("jobId"))
    titled = sum(1 for s in shifts if str(s.get("title", "")).strip())
    print(f"   carrying a jobId : {with_job}")
    print(f"   carrying a title : {titled}")

    # Colour, by name rather than hex. The client sets these, so a tally is how
    # you check what the crews will actually see: turnovers dark blue, every
    # other clean palest green, and never red -- red is the team's day-off card.
    names = {"#969696": "LIGHT GRAY (calder)",
             "#616161": "dark gray", "#3a3a3a": "near-black",
             "#548CBE": "mid blue", "#81A8CC": "light blue",
             "#4E3F75": "dark purple", "#604E8E": "mid purple",
             "#8679AA": "light purple", "#983D73": "dark magenta",
             "#A43778": "magenta", "#D178AD": "light pink",
             "#6B2E4C": "dark plum", "#925071": "mid plum",
             "#B57D9A": "light plum",
             "#85A6DA": "light royal blue (turnover)",
             "#91B282": "palest green (clean)",
             "#225A8C": "dark blue  (old turnover)",
             "#3968BB": "mid blue   (old clean)", "#AE2121": "red        (old turnover)",
             "#801A1A": "dark red", "#DC7A7A": "light red", "#D9B443": "yellow",
             "#CBA73A": "yellow", "#AE8E2D": "dark yellow", "#487037": "dark green",
             "#6F9B5C": "mid green", "#4B7AC5": "mid blue",
             "#5687B3": "mid blue", "#225A8C ": "dark blue", "#365C64": "dark teal",
             "#7C9BA2": "mid teal", "#B0712E": "dark orange", "#D4985A": "mid orange",
             "#E4B37F": "light orange"}
    cols = Counter(str(s.get("color", "")).strip().upper() or "(none)" for s in shifts)
    print("   colours in use:")
    for c, n in cols.most_common():
        print(f"      {n:>4}  {c:<9} {names.get(c, '')}")

    kinds = Counter(str(s.get("title", "")).strip() or "(no title)" for s in shifts)
    print("   titles in use:")
    for t, n in kinds.most_common(12):
        print(f"      {n:>4}  {t}")

    # WHO created each card, cross-tabulated against the three-test rule for
    # "is this ours". If one creator id lines up exactly with the cards the three
    # tests call ours, that id IS us -- and it is a far sturdier test than title
    # and colour, which the team can change by tinting a card.
    #
    # Printed as a table rather than asserted, because making createdBy the
    # ownership test on a guess would risk claiming the crews' own shifts, which
    # is the one failure the three tests exist to prevent.
    from connecteam_cards import ours as _ours
    from connecteam_map import (CANCELLED_COLOR, CARD_MINUTES, OUR_TITLES,
                                STANDARD_COLOR, TURNOVER_COLOR)
    _our_colours = (STANDARD_COLOR, TURNOVER_COLOR, CANCELLED_COLOR)

    def _three_tests(c):
        if not _ours(c, OUR_TITLES, _our_colours):
            return False
        span = int(c.get("endTime") or 0) - int(c.get("startTime") or 0)
        return span == CARD_MINUTES * 60

    tab: dict = {}
    for c in shifts:
        key = c.get("createdBy")
        row = tab.setdefault(key, {"ours": 0, "not": 0, "spots": set(),
                                   "titles": set()})
        row["ours" if _three_tests(c) else "not"] += 1
        row["spots"].add(c.get("openSpots"))
        row["titles"].add(str(c.get("title") or "")[:24])
    print()
    print("   createdBy, against the three-test rule:")
    print(f"      {'createdBy':<12}{'ours':>6}{'not ours':>10}   openSpots  titles")
    for key, row in sorted(tab.items(), key=lambda kv: -(kv[1]["ours"])):
        spots = ",".join(str(x) for x in sorted(row["spots"], key=str))
        print(f"      {str(key):<12}{row['ours']:>6}{row['not']:>10}   "
              f"{spots:<10} {len(row['titles'])} distinct")

    # Draft or published. A card in the DRAFT layer is invisible to crews until
    # somebody publishes it, which is the whole point of creating ours that way,
    # and nothing else on this board showed the difference.
    pub = sum(1 for s in shifts if s.get("isPublished"))
    unpub = sum(1 for s in shifts if not s.get("isPublished"))
    print()
    print(f"   published: {pub}   DRAFT (not published): {unpub}")
    if unpub:
        print("   the draft ones:")
        for s in sorted((x for x in shifts if not x.get("isPublished")),
                        key=lambda x: int(x.get("startTime", 0))):
            try:
                tz = ZoneInfo(card_timezone(s, board=args.board))
            except Exception:  # noqa: BLE001 - a zone lookup must not stop the dump
                tz = DEFAULT_TZ
            at = datetime.fromtimestamp(int(s.get("startTime", 0)), tz)
            print(f"      {at:%a %d %b %Y %H:%M}  "
                  f"job={str(s.get('jobId') or '-')[:8]:<8} "
                  f"{str(s.get('title', '')).strip()!r}  "
                  f"{s.get('color') or '(none)'}  id={s.get('id')}")

    print()
    print("   most recent cards:")
    for s in sorted(shifts, key=lambda x: int(x.get("startTime", 0)))[-args.limit:]:
        try:
            tz = ZoneInfo(card_timezone(s, board=args.board))
        except Exception:  # noqa: BLE001 - a zone lookup must not stop the dump
            tz = DEFAULT_TZ
        start = datetime.fromtimestamp(int(s.get("startTime", 0)), tz)
        end = datetime.fromtimestamp(int(s.get("endTime", 0)), tz)
        print(f"      {start:%a %d %b %Y %H:%M}-{end:%H:%M}  "
              f"job={str(s.get('jobId') or '-')[:8]:<8} "
              f"title={str(s.get('title', '')).strip()!r}")
    print()
    print("   Nothing was changed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(hawthorne())
