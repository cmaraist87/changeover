"""A calder booking is struck even when its slot is taken by a live one.

Run: python test_occupied_slot.py

On 2026-09-20 nine (Property, Date) slots held two bookings each. Guesty called
one of each pair calder and the other live -- every one a cancellation plus a
replacement, not a double booking -- and the calder rows had no line, because
the gate asked "is any live booking at this slot?" rather than "does this slot
hold more rows than bookings?".

1123 Jessamine 2026-10-01 is the case in hand: Tess Varga HMSAMPLE04 calder on
2026-09-02, Omar Said HMSAMPLE06 live on the same dates.

The counting matters in both directions, so both directions are pinned here: one
row and one booking is a MATCH however the codes differ, and the row is reused,
not struck.
"""
import pandas as pd

from sheet_merge import merge_reservations_into_sheet

HEADER = ["City", "Day", "Date", "Confirmation Code", "Guest", "assigned",
          "Property", "Verified", "Check out - Time", "OUT", "Check-in Time",
          "IN", "T/O", "Adjustments"]
OCT = ("2026-10-01", "2026-10-31")


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
    return merge_reservations_into_sheet(
        pd.DataFrame(candidates or [], columns=CAND_COLS),
        pd.DataFrame(sheet_rows, columns=HEADER), cancel_window=OCT, **kw)


def test_the_cancelled_half_of_a_shared_slot_is_struck():
    """1123 Jessamine, 2026-10-01. The replacement holds the slot; Gosseen is gone."""
    sheet = [row("2026-10-01", "1123 Jessamine", "Tess Varga", "HMSAMPLE04"),
             row("2026-10-01", "1123 Jessamine", "Omar Said", "HMSAMPLE06")]
    candidates = [cand("2026-10-01", "1123 Jessamine", "Omar Said", "HMSAMPLE06")]

    _full, stats, changes = merge(candidates, sheet)
    struck = [r["Confirmation Code"] for r in changes["calder"]]
    assert struck == ["HMSAMPLE04"], struck
    assert stats["calder"] == 1, stats
    print("OK 1123 Jessamine: the calder booking is struck, the live one is not")


def test_the_live_half_is_never_the_one_struck():
    """Order must not decide it -- put the live booking second and re-run."""
    sheet = [row("2026-10-01", "1123 Jessamine", "Omar Said", "HMSAMPLE06"),
             row("2026-10-01", "1123 Jessamine", "Tess Varga", "HMSAMPLE04")]
    candidates = [cand("2026-10-01", "1123 Jessamine", "Omar Said", "HMSAMPLE06")]

    _full, _stats, changes = merge(candidates, sheet)
    assert [r["Confirmation Code"] for r in changes["calder"]] == ["HMSAMPLE04"]
    print("OK: the row whose code is still live is kept, whichever order they sit in")


def test_one_row_and_one_booking_is_a_match_even_with_a_different_code():
    """The counting rule's other edge. A lone row at a slot a live booking occupies
    is REUSED -- it becomes that booking's row. Striking it would be wrong, and it
    is what broke Quinn Ashford's move onto an occupied slot."""
    sheet = [row("2026-10-01", "1022 Inglewood", "Old Guest", "HMSTALE001")]
    candidates = [cand("2026-10-01", "1022 Inglewood", "Jordan Vale", "HMNEW00001")]

    _full, stats, _changes = merge(candidates, sheet)
    assert stats["calder"] == 0, stats
    print("OK: one row, one booking -- reused, not struck, whatever the codes are")


def test_both_halves_struck_when_both_bookings_are_gone():
    """1718 St Haverford 2026-11-25 had both of its bookings calder."""
    sheet = [row("2026-10-05", "1718 St Haverford", "Ivy Chen", "HA-Smpl001"),
             row("2026-10-05", "1718 St Haverford", "Pavel Kovac", "HMSAMPLE13")]

    _full, stats, changes = merge([], sheet)
    assert stats["calder"] == 2, stats
    assert {r["Confirmation Code"] for r in changes["calder"]} == {
        "HA-Smpl001", "HMSAMPLE13"}
    print("OK: a slot whose bookings have both gone loses both rows to the line")


def test_three_rows_two_live_strikes_only_the_leftover():
    """Combined listings put several real bookings on one slot; only the excess
    beyond what Guesty still holds is a cancellation."""
    sheet = [row("2026-10-02", "422 Pembrook 101", "A", "HMAAA"),
             row("2026-10-02", "422 Pembrook 101", "B", "HMBBB"),
             row("2026-10-02", "422 Pembrook 101", "C", "HMCCC")]
    candidates = [cand("2026-10-02", "422 Pembrook 101", "A", "HMAAA"),
                  cand("2026-10-02", "422 Pembrook 101", "C", "HMCCC")]

    _full, stats, changes = merge(candidates, sheet)
    assert [r["Confirmation Code"] for r in changes["calder"]] == ["HMBBB"], changes
    assert stats["calder"] == 1, stats
    print("OK: two live of three rows -- only the one Guesty lost is struck")


def test_an_already_struck_row_does_not_shield_a_live_one():
    """A struck row is not competing for the slot, so the live row beside it must
    not be counted as the surplus and struck in its place."""
    sheet = [row("2026-10-03", "3513 Elmsworth", "Gone", "HMGONE"),
             row("2026-10-03", "3513 Elmsworth", "Here", "HMHERE")]
    candidates = [cand("2026-10-03", "3513 Elmsworth", "Here", "HMHERE")]

    _full, stats, _changes = merge(candidates, sheet, struck_rows=frozenset({0}))
    assert stats["calder"] == 0, ("the live row must stay clean", stats)
    print("OK: a row already struck does not push the live row into the surplus")


if __name__ == "__main__":
    test_the_cancelled_half_of_a_shared_slot_is_struck()
    test_the_live_half_is_never_the_one_struck()
    test_one_row_and_one_booking_is_a_match_even_with_a_different_code()
    test_both_halves_struck_when_both_bookings_are_gone()
    test_three_rows_two_live_strikes_only_the_leftover()
    test_an_already_struck_row_does_not_shield_a_live_one()
    print("\nALL OCCUPIED-SLOT TESTS PASSED")
