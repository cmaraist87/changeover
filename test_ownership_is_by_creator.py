"""A card is ours because WE created it, not because it looks like ours.

Run: python test_ownership_is_by_creator.py

Until 2026-10-13 ownership was decided by three tests: our title, our colour,
and our exact fifteen-minute length. Measured against all three live boards that
day, that rule was wrong in both directions at once:

  board          createdBy   pass 3 tests   fail 3 tests   openSpots
  Austin         4000001              69              0    always 1
  Savannah       4000001             309              1    always 1
  Chris Test     4000001             100              0    always 1
  (team admins)  9000010/9            10            584    0, 1, None

TEN of the team's own cards passed all three tests, so the system treated them
as ours and would move, recolour or grey a crew's real shift. And ONE card of
ours failed, because somebody had repainted it -- after which it could never be
corrected again, and the next run created a duplicate beside it. That is the
loop the client reported: duplicated shifts appearing every time a new week was
added, and cleaners receiving work nobody had given them.

createdBy is the only field on a shift that records who made it, and nothing the
team does in the UI touches it -- tinting, retitling, resizing, assigning and
publishing all leave it alone. So it is authoritative where present, and the old
pair of tests remains the fallback for a read that does not carry it.
"""
from connecteam_cards import is_assigned, orphans, ours
from connecteam_map import (CANCELLED_COLOR, CARD_MINUTES, OUR_CREATOR_ID,
                            OUR_TITLES, STANDARD_COLOR, STANDARD_TITLE,
                            TURNOVER_COLOR, created_by_us)

OUR_COLOURS = (STANDARD_COLOR, TURNOVER_COLOR, CANCELLED_COLOR)
TEAM_ADMIN = 9000011
SPAN = CARD_MINUTES * 60


def card(creator=OUR_CREATOR_ID, title=STANDARD_TITLE, colour=STANDARD_COLOR,
         span=SPAN, sid="c1", **extra):
    c = {"id": sid, "jobId": "J1", "title": title, "color": colour,
         "startTime": 1792162800, "endTime": 1792162800 + span}
    if creator is not None:
        c["createdBy"] = creator
    c.update(extra)
    return c


def test_created_by_us_reads_the_field():
    assert created_by_us(card()) is True
    assert created_by_us(card(creator=TEAM_ADMIN)) is False
    assert created_by_us(card(creator=None)) is None, "absent must be None"
    assert created_by_us({"createdBy": ""}) is None
    assert created_by_us({"createdBy": str(OUR_CREATOR_ID)}) is True
    assert created_by_us(None) is None
    print("OK: createdBy is read, and absent is None rather than False")


def test_a_team_card_that_looks_exactly_like_ours_is_not_ours():
    """The ten false positives. Our title, our colour, our exact length -- and
    the team made it. Before this, the system would move or grey a crew's own
    shift."""
    lookalike = card(creator=TEAM_ADMIN)
    assert not ours(lookalike, OUR_TITLES, OUR_COLOURS), (
        "a team card matching title, colour AND length was claimed as ours")
    print("OK: a perfect lookalike the team made is not ours")


def test_a_card_of_ours_that_somebody_repainted_is_still_ours():
    """The one false negative, and the purple cards. #A43778 is a colour we
    never send; the client painted it to mark cards for attention. Under the old
    rule that card stopped being ours, could never be corrected, and the next
    run duplicated it."""
    purple = card(colour="#A43778")
    assert ours(purple, OUR_TITLES, OUR_COLOURS), (
        "a card we created was disowned because somebody recoloured it")
    retitled = card(title="whatever the team typed")
    assert ours(retitled, OUR_TITLES, OUR_COLOURS)
    resized = card(span=3 * 3600)
    assert ours(resized, OUR_TITLES, OUR_COLOURS)
    print("OK: recolouring, retitling or resizing does not disown our card")


def test_the_old_tests_still_apply_when_createdby_is_absent():
    """A read that does not carry createdBy must behave exactly as before, or
    every existing proof about planning is void."""
    assert ours(card(creator=None), OUR_TITLES, OUR_COLOURS)
    assert not ours(card(creator=None, colour="#D9B443"),
                    OUR_TITLES, OUR_COLOURS)
    assert not ours(card(creator=None, title="sale entra gente"),
                    OUR_TITLES, OUR_COLOURS)
    print("OK: with no createdBy, title and colour still decide")


def test_ownership_does_not_override_assignment():
    """The two rules are independent and both must hold. A card we made that
    somebody is now on is STILL read-only -- ownership says we may touch it,
    assignment says we may not, and assignment wins."""
    mine_but_theirs = card(assignedUserIds=[9000001], isOpenShift=False)
    assert ours(mine_but_theirs, OUR_TITLES, OUR_COLOURS), "it is still our card"
    assert is_assigned(mine_but_theirs), "and it is still somebody's shift"
    print("OK: ours and assigned are independent; assigned still wins")


def test_the_stray_alarm_uses_the_creator_and_drops_the_length_rule():
    """orphans() applied a length test to tell us from the team. createdBy does
    that better, and insisting on length as well would hide a card of ours that
    somebody resized -- exactly the card most likely to be in trouble."""
    resized_ours = card(sid="mine", span=3 * 3600)
    team_long = card(sid="theirs", creator=TEAM_ADMIN, span=3 * 3600)
    found = orphans([resized_ours, team_long], {}, OUR_TITLES, OUR_COLOURS,
                    card_seconds=SPAN)
    ids = [str(s.get("id")) for s in found]
    assert ids == ["mine"], (
        "the alarm should report our resized card and ignore the team's: %r"
        % (ids,))
    print("OK: the alarm finds our resized card and leaves the team's alone")


def test_the_creator_id_is_a_single_known_value():
    """If this ever changes -- a new API key, a new integration user -- every
    card already on every board still carries the OLD id, and ownership would
    silently invert: ours disowned, theirs claimed. It is worth a loud test."""
    assert OUR_CREATOR_ID == 4000001, (
        "OUR_CREATOR_ID changed. Every card already on a board carries the old "
        "value, so this is not a config tweak: the boards must be re-measured "
        "with board_peek quillon, and both ids may need to be accepted.")
    print(f"OK: our creator id is {OUR_CREATOR_ID}, as measured on all 3 boards")


if __name__ == "__main__":
    test_created_by_us_reads_the_field()
    test_a_team_card_that_looks_exactly_like_ours_is_not_ours()
    test_a_card_of_ours_that_somebody_repainted_is_still_ours()
    test_the_old_tests_still_apply_when_createdby_is_absent()
    test_ownership_does_not_override_assignment()
    test_the_stray_alarm_uses_the_creator_and_drops_the_length_rule()
    test_the_creator_id_is_a_single_known_value()
    print("")
    print("ALL CREATOR-OWNERSHIP TESTS PASSED")
