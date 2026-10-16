"""A booking that moved must never be counted as a cancellation.

Run: python test_moved_not_cancelled.py

Both cases here are real, from the 2026-09-09 run. That run's own snapshot counted
2 calder reservations; the tabs struck 3 rows more than that, and the same log
listed both extra bookings under "edited in place":

    edited  Quinn Ashford [HMSAMPLE01]  '31 Tam 301 V3' -> '406 W Stourton V U V1'
    edited  Sofia Lindqvist [HMSAMPLE05]  checkout: '2026-09-30' -> '2026-10-01'

The old test asked whether ANY ROW existed at the destination, rather than whether
THIS BOOKING was already there, and it only ever looked inside one month.

The third case is the one the guard exists for and must keep failing closed: a
multi-unit listing shares one confirmation code, so a single unit cancelling leaves
the code "live" without anything having moved.
"""
import pandas as pd

from sheet_merge import merge_reservations_into_sheet

HEADER = ["City", "Day", "Date", "Confirmation Code", "Guest", "assigned",
          "Property", "Verified", "Check out - Time", "OUT", "Check-in Time",
          "IN", "T/O", "Adjustments"]
SEP = ("2026-09-01", "2026-09-30")


def row(date, prop, guest, code):
    return {"City": "New Quintrell", "Day": "", "Date": date, "Confirmation Code": code,
            "Guest": guest, "assigned": "", "Property": prop, "Verified": "",
            "Check out - Time": "", "OUT": "", "Check-in Time": "", "IN": "",
            "T/O": "", "Adjustments": ""}


def cand(date, prop, guest, code):
    return {"City": "New Quintrell", "Day": "", "Date": date, "Confirmation Code": code,
            "Guest": guest, "Property": prop, "Check-out Time": "",
            "Check-in Time": "", "T/O": "", "Adjustments": ""}


CAND_COLS = list(cand("", "", "", "").keys())


def merge(candidates, sheet_rows, **kw):
    # An empty candidate list still has to carry the column names -- a month where
    # every booking has gone is exactly the case this file is about.
    cands = pd.DataFrame(candidates or [], columns=CAND_COLS)
    return merge_reservations_into_sheet(
        cands, pd.DataFrame(sheet_rows, columns=HEADER), cancel_window=SEP, **kw)


def test_a_move_into_an_occupied_slot_is_not_a_cancellation():
    """Quinn Ashford. He moved to 406 W Stourton on 09-13 -- a slot that ALREADY held
    Mira Holt's row, which is why the move went unseen and his old rows were
    struck. The destination existing says nothing; what matters is that HE was
    not there before."""
    sheet = [row("2026-09-13", "31 Tamarisk 301", "Quinn Ashford", "HMSAMPLE01"),
             row("2026-09-17", "31 Tamarisk 301", "Quinn Ashford", "HMSAMPLE01"),
             row("2026-09-13", "406 W Stourton", "Mira Holt", "HMSAMPLE19")]
    candidates = [cand("2026-09-13", "406 W Stourton", "Quinn Ashford", "HMSAMPLE01"),
                  cand("2026-09-17", "406 W Stourton", "Quinn Ashford", "HMSAMPLE01")]

    _full, stats, changes = merge(candidates, sheet)
    assert stats["calder"] == 0, f"struck a live booking: {changes['calder']}"
    assert stats["moved"] == 2, changes["moved"]
    print("OK Quinn Ashford: 0 calder, 2 moved (was 2 calder, 0 moved)")


def test_a_move_across_a_month_boundary_is_not_a_cancellation():
    """Sofia Lindqvist. Her checkout slid 09-30 -> 10-01, so her booking left
    September's candidates entirely and the month it left saw only an absence."""
    sheet = [row("2026-09-30", "1401 Glenmoor A", "Sofia Lindqvist",
                 "HMSAMPLE05")]
    october = [("1401 Glenmoor A", "2026-10-01")]

    _full, stats, changes = merge([], sheet)
    assert stats["calder"] == 1, "precondition: invisible without the wider view"

    _full, stats, changes = merge(
        [], sheet, live_by_code_all={"HMSAMPLE05": october})
    assert stats["calder"] == 0, f"struck a live booking: {changes['calder']}"
    assert stats["moved"] == 1, changes["moved"]
    print("OK Sofia Lindqvist: 0 calder, 1 moved (was 1 calder, 0 moved)")


def test_one_unit_of_a_multi_unit_booking_cancelling_still_counts():
    """The case the guard exists for, and the one a looser test would break.
    402 and 404 W Stourton share HMSAMPLE02. 402 cancels, 404 stands -- the code is
    still live, but every slot it holds is one it already held. Nothing moved."""
    sheet = [row("2026-09-20", "402 W Stourton", "Lena Marsh", "HMSAMPLE02"),
             row("2026-09-20", "404 W Stourton", "Lena Marsh", "HMSAMPLE02")]
    candidates = [cand("2026-09-20", "404 W Stourton", "Lena Marsh", "HMSAMPLE02")]

    _full, stats, changes = merge(
        candidates, sheet,
        live_by_code_all={"HMSAMPLE02": [("404 W Stourton", "2026-09-20")]})
    assert stats["calder"] == 1, changes["calder"]
    assert stats["moved"] == 0, f"a real cancellation was written off as a move: {changes['moved']}"
    assert changes["calder"][0]["Property"] == "402 W Stourton"
    print("OK multi-unit: 402 stays struck, 404 stands -- still 1 cancellation")


def test_a_booking_that_is_truly_gone_is_still_struck():
    sheet = [row("2026-09-20", "1022 Nettlebed", "Gone Entirely", "HMGONE1234")]
    _full, stats, _changes = merge([], sheet, live_by_code_all={})
    assert stats["calder"] == 1 and stats["moved"] == 0, stats
    print("OK a genuinely calder booking is still struck")


if __name__ == "__main__":
    test_a_move_into_an_occupied_slot_is_not_a_cancellation()
    test_a_move_across_a_month_boundary_is_not_a_cancellation()
    test_one_unit_of_a_multi_unit_booking_cancelling_still_counts()
    test_a_booking_that_is_truly_gone_is_still_struck()
    print("\nALL MOVED-NOT-CALDER TESTS PASSED")
