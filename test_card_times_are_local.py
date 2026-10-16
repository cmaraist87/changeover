"""A card's time is reported in the zone the crew reads it in.

Run: python test_card_times_are_local.py

Every report printed card times in America/Chicago, hardcoded. For Austin that
is right. For Savannah and Thunderbolt it is an hour early, so the stray-card
alarm named a card at "Wed 18 Nov 10:00" that the crew sees at 11:00 -- and
shift_delete's confirmation, the output somebody reads to decide whether the
listed card is the one they meant, had the same hour missing.

Nothing looked broken, which is the point: the stored epoch was always correct
and only the rendering was wrong, so no amount of checking the data would have
found it. CITY_TIMEZONES has carried the warning since the beginning -- "getting
this wrong puts every Savannah job an hour out, which is invisible in the data
and obvious to a cleaner standing outside a locked door."
"""
from datetime import datetime
from zoneinfo import ZoneInfo

from connecteam_map import (CITY_SCHEDULERS, CITY_TIMEZONES, DEFAULT_TIMEZONE,
                            card_timezone, card_when, timezone_for,
                            timezone_for_board)

# 11:00 local on a Savannah Wednesday -- the 6 Dunmore stray of 2026-10-11.
EAST = ZoneInfo("America/New_York")
EPOCH = int(datetime(2026, 11, 18, 11, 0, tzinfo=EAST).timestamp())
CARD = {"startTime": EPOCH, "endTime": EPOCH + 900}


def test_the_same_instant_reads_differently_per_market():
    """The regression itself, stated as the two answers that must differ."""
    assert card_when(CARD, city="Savannah").endswith("11:00"), \
        card_when(CARD, city="Savannah")
    assert card_when(CARD, city="Thunderbolt").endswith("11:00"), \
        card_when(CARD, city="Thunderbolt")
    assert card_when(CARD, city="Austin").endswith("10:00"), \
        card_when(CARD, city="Austin")
    print("OK: one instant reads 11:00 in Savannah and 10:00 in Austin")


def test_a_savannah_card_is_not_reported_in_central():
    """What the bug actually was: the Eastern markets rendered an hour early."""
    for city in ("Savannah", "Thunderbolt"):
        assert timezone_for(city) == "America/New_York", city
        assert card_when(CARD, city=city) != card_when(CARD, city="Austin"), (
            f"{city} and Austin cannot render one instant identically; they are "
            f"an hour apart")
    print("OK: the Eastern markets no longer render in Central")


def test_the_board_answers_when_no_city_is_given():
    assert timezone_for_board("1000002") == "America/New_York"
    assert timezone_for_board("1000003") == "America/Chicago"
    assert timezone_for_board("1000001") == "America/Chicago"
    assert card_when(CARD, board="1000002").endswith("11:00")
    print("OK: a board resolves to the one zone its markets share")


def test_every_shared_board_is_single_zone():
    """What makes a board-level answer meaningful at all.

    Savannah shares a board with Thunderbolt, New Quintrell with Net St. Louis.
    If a future market joined an existing board from another zone, a per-board
    answer would silently pick one. timezone_for_board raises instead, and this
    is the check that the assumption still holds today.
    """
    by_board = {}
    for city, board in CITY_SCHEDULERS.items():
        by_board.setdefault(str(board), set()).add(
            CITY_TIMEZONES.get(city, DEFAULT_TIMEZONE))
    for board, zones in sorted(by_board.items()):
        assert len(zones) == 1, (
            f"board {board} now straddles {sorted(zones)}; every report that "
            f"resolves a zone per board must take the city instead")
        timezone_for_board(board)
    print(f"OK: all {len(by_board)} board(s) are single-zone")


def test_a_straddling_board_raises_rather_than_guessing():
    """The guard itself, proved on a fake board rather than waiting for one."""
    import connecteam_map as cm
    saved_s, saved_t = dict(cm.CITY_SCHEDULERS), dict(cm.CITY_TIMEZONES)
    try:
        cm.CITY_SCHEDULERS["boston"] = "1000002"
        cm.CITY_TIMEZONES["boston"] = "America/Denver"
        try:
            cm.timezone_for_board("1000002")
        except ValueError as e:
            assert "cannot be reported per board" in str(e), e
        else:
            raise AssertionError(
                "a board serving two zones returned one answer instead of "
                "refusing; a Savannah report would print Denver times")
    finally:
        cm.CITY_SCHEDULERS.clear(), cm.CITY_SCHEDULERS.update(saved_s)
        cm.CITY_TIMEZONES.clear(), cm.CITY_TIMEZONES.update(saved_t)
    print("OK: a two-zone board refuses a per-board answer")


def test_the_cards_own_zone_wins():
    """A card carries the zone it was created with, and that beats any guess --
    including for a card sitting on a board it does not belong to, which is
    exactly what the test board holds."""
    travelled = dict(CARD, timezone="America/New_York")
    assert card_when(travelled, city="Austin").endswith("11:00"), \
        "the card's own zone must beat the city passed in"
    assert card_timezone(travelled, city="Austin") == "America/New_York"
    assert card_timezone({}, city="Austin") == "America/Chicago"
    assert card_timezone({"timezone": ""}, city="Savannah") == "America/New_York"
    print("OK: a card's own timezone wins over the city or board")


def test_a_broken_timestamp_does_not_break_a_report():
    assert card_when({"startTime": None}, city="Austin") == "?"
    assert card_when({"startTime": "nonsense"}, city="Austin") == "?"
    print("OK: an unreadable timestamp degrades to '?' rather than crashing")


def test_no_report_hardcodes_a_zone_for_a_market_card():
    """The hardcoded constants are gone from the tools that print market cards.

    Deliberately NOT a check on every file: sync.py, daily_gate.py and
    watchdog.py are about the company's business day and are Central on purpose.
    """
    import io
    for tool in ("connecteam_push.py", "card_lookup.py", "shift_delete.py",
                 "board_peek.py"):
        src = io.open(tool, encoding="utf-8").read()
        for line in src.splitlines():
            if "America/Chicago" not in line or line.strip().startswith("#"):
                continue
            assert ("DEFAULT_TZ" in line or "DEFAULT_TIMEZONE" in line), (
                f"{tool} still hardcodes Central where a card is rendered: "
                f"{line.strip()}")
    print("OK: no market-card report hardcodes Central")


if __name__ == "__main__":
    test_the_same_instant_reads_differently_per_market()
    test_a_savannah_card_is_not_reported_in_central()
    test_the_board_answers_when_no_city_is_given()
    test_every_shared_board_is_single_zone()
    test_a_straddling_board_raises_rather_than_guessing()
    test_the_cards_own_zone_wins()
    test_a_broken_timestamp_does_not_break_a_report()
    test_no_report_hardcodes_a_zone_for_a_market_card()
    print("")
    print("ALL CARD-TIME-ZONE TESTS PASSED")
