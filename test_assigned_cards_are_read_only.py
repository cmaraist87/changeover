"""A card with a person on it is read-only, and nothing is created beside one.

Run: python test_assigned_cards_are_read_only.py

Chris, 2026-10-12, after the client's report: "we need to prevent duplicates, by
Job Name, if the card exists in any assigned slot our system does not create the
unassigned 15 minute block ticket. Never, never do we update live, assigned
tickets. This is read only for our purposes of preventing duplication."

WHAT HAPPENED
-------------
The client assigned the week's shifts out of the draft to named cleaners. Around
midday more shifts appeared, duplicated, and some were published: one cleaner
rang up having been given several shifts they had not agreed to, and the same
happened to a second that evening. The client pulled the extras back into the
draft and marked them purple
so they could be told apart -- which is the #A43778 found on seven Savannah cards
the same night.

The duplicate came from the old create-skip key, (jobId, title, startTime). The
team's own card for 1208 Eastleigh on 12 October ran 15:00-18:00; our block sat at
11:00-11:15. Same property, same day, same clean -- and no collision at all by
that key, so a second card was created and the crew got the job twice.

THE TWO RULES
-------------
  * an assigned card is never updated, moved, recoloured, greyed or deleted;
  * no block is created for a Job on a day an assigned card already covers.

They need each other. Rule 1 alone would leave a booking's assigned card
untouchable, the planner would see no card it could use, and it would create the
duplicate itself -- which is the bug, re-introduced by the fix for it.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

from connecteam_cards import assigned_cover, day_of, is_assigned, plan
from connecteam_map import (CANCELLED_COLOR, STANDARD_COLOR, STANDARD_TITLE,
                            TURNOVER_COLOR)

OURS = (STANDARD_TITLE, "Turnover", "Clean")
OUR_COLOURS = (STANDARD_COLOR, TURNOVER_COLOR, CANCELLED_COLOR)
EAST = "America/New_York"


def at(y, m, d, hh, mm=0, tz=EAST) -> int:
    return int(datetime(y, m, d, hh, mm, tzinfo=ZoneInfo(tz)).timestamp())


# 1208 Eastleigh, 12 October 2026 -- the real collision, with the real times.
OUR_BLOCK = at(2026, 10, 12, 11)          # 11:00-11:15, ours
THEIR_SHIFT = at(2026, 10, 12, 15)        # 15:00-18:00, theirs, assigned


def our_want(job="J1208", start=OUR_BLOCK, colour=STANDARD_COLOR):
    return {"jobId": job, "title": STANDARD_TITLE, "startTime": start,
            "endTime": start + 900, "color": colour, "timezone": EAST,
            "isOpenShift": True}


def their_card(sid="team1", job="J1208", start=THEIR_SHIFT, users=(9000001,)):
    return {"id": sid, "jobId": job, "title": STANDARD_TITLE,
            "startTime": start, "endTime": start + 3 * 3600,
            "color": STANDARD_COLOR, "timezone": EAST,
            "assignedUserIds": list(users), "isOpenShift": False}


def our_card(sid="ours1", job="J1208", start=OUR_BLOCK, colour=STANDARD_COLOR):
    return {"id": sid, "jobId": job, "title": STANDARD_TITLE,
            "startTime": start, "endTime": start + 900, "color": colour,
            "timezone": EAST, "isOpenShift": True}


def test_is_assigned_reads_both_signals():
    """Either signal is enough, and neither is trusted to be absent."""
    assert is_assigned({"assignedUserIds": [1]})
    assert is_assigned({"assignedUsers": ["eddy"]})
    assert is_assigned({"userIds": [7]})
    assert is_assigned({"isOpenShift": False})
    assert not is_assigned({"assignedUserIds": [], "isOpenShift": True})
    assert not is_assigned({})
    assert not is_assigned(None)
    print("OK: a person on the card, or a shift that is not open, is assigned")


def test_the_day_comes_from_the_cards_own_zone():
    """A Savannah card at 00:30 Eastern is the 13th there and the 12th in
    Central. Taking the company zone would put our block and the team's shift on
    different days, and the duplicate check would never fire."""
    late = {"startTime": at(2026, 10, 13, 0, 30), "timezone": EAST}
    assert day_of(late) == "2026-10-13", day_of(late)
    assert day_of(dict(late, timezone="America/Chicago")) == "2026-10-12"
    assert day_of({"startTime": 0}) == ""
    assert day_of({"startTime": None}) == ""
    # an unknown zone must degrade, not raise
    assert day_of({"startTime": at(2026, 10, 12, 11), "timezone": "Nowhere/Bad"})
    print("OK: the day is the card's own local day")


def test_an_assigned_card_blocks_our_block_at_the_same_job_that_day():
    """THE BUG. Their shift at 15:00, ours wanted at 11:00, same Job, same day.

    The old key -- (jobId, title, startTime) -- saw no collision, so a second
    card was created and the crew got 1208 Eastleigh twice on 12 October.
    """
    u, c, g, m, held = plan([("HM1208", our_want())], [their_card()], {},
                            our_titles=OURS, our_colors=OUR_COLOURS,
                            adopt=False, stale_color=CANCELLED_COLOR)
    assert c == [], (
        "a block would be created beside an assigned shift at the same Job on "
        "the same day -- the duplicate the cleaners received: %r" % (c,))
    assert u == [] and g == [], (u, g)
    assert held and "already covers this Job" in held[0][0], held
    print("OK: no block is created beside an assigned shift for that Job that day")


def test_their_assigned_card_is_never_touched():
    """Not moved, not recoloured, not greyed, not claimed into the map."""
    board = [their_card()]
    u, c, g, m, _h = plan([], board, {}, our_titles=OURS,
                          our_colors=OUR_COLOURS, adopt=False,
                          stale_color=CANCELLED_COLOR)
    assert u == [] and g == [], (u, g)
    assert m == {}, m
    # and with adoption ON, which is the test board's setting
    u2, _c2, g2, m2, _h2 = plan([], board, {}, our_titles=OURS,
                                our_colors=OUR_COLOURS, adopt=True,
                                stale_color=CANCELLED_COLOR)
    assert u2 == [] and g2 == [] and m2 == {}, (u2, g2, m2)
    print("OK: an assigned card is never updated, greyed or adopted")


def test_a_booking_whose_own_card_was_assigned_is_left_entirely_alone():
    """The half that stops the fix from causing the bug.

    Our own card, in our own map, which the team then published to somebody. We
    must not touch it -- and must not create a replacement either, or we hand
    the crew the duplicate ourselves.
    """
    mine = dict(our_card(), assignedUserIds=[9000001], isOpenShift=False)
    u, c, g, m, held = plan([("HMX", our_want())], [mine], {"HMX": ["ours1"]},
                            our_titles=OURS, our_colors=OUR_COLOURS,
                            adopt=False, stale_color=CANCELLED_COLOR)
    assert u == [], "somebody's shift would have been modified: %r" % (u,)
    assert c == [], "a duplicate would have been created beside it: %r" % (c,)
    assert g == [], g
    assert m == {"HMX": ["ours1"]}, (
        "the assigned card must stay recorded, or it becomes a stray and the "
        "next run duplicates it: %r" % (m,))
    assert "this booking's card is assigned" in [h[0] for h in held], held
    print("OK: a booking whose card was assigned is untouched and not duplicated")


def test_an_assigned_card_is_not_updated_even_when_the_sheet_disagrees():
    """A calder booking whose card somebody is on. Grey would normally win.
    It does not: never update a live, assigned ticket."""
    mine = dict(our_card(colour=STANDARD_COLOR),
                assignedUserIds=[9000002], isOpenShift=False)
    u, c, _g, _m, held = plan([("HMC", our_want(colour=CANCELLED_COLOR))],
                              [mine], {"HMC": ["ours1"]}, our_titles=OURS,
                              our_colors=OUR_COLOURS, adopt=False,
                              stale_color=CANCELLED_COLOR)
    assert u == [], "an assigned card would have been recoloured: %r" % (u,)
    assert c == [], c
    assert held, held
    print("OK: even a cancellation does not repaint somebody's shift")


def test_the_block_is_still_created_on_a_different_day_or_job():
    """The suppression must not over-reach. An assigned card on the 12th says
    nothing about the 13th, and one at another property says nothing at all."""
    board = [their_card()]
    _u, c, _g, _m, _h = plan([("HMD", our_want(start=at(2026, 10, 13, 11)))],
                             board, {}, our_titles=OURS,
                             our_colors=OUR_COLOURS, adopt=False,
                             stale_color=CANCELLED_COLOR)
    assert len(c) == 1, "a clean on another day was suppressed: %r" % (c,)

    _u2, c2, _g2, _m2, _h2 = plan([("HME", our_want(job="J22LAKE"))], board, {},
                                  our_titles=OURS, our_colors=OUR_COLOURS,
                                  adopt=False, stale_color=CANCELLED_COLOR)
    assert len(c2) == 1, "another property was suppressed: %r" % (c2,)
    print("OK: only the same Job on the same day is suppressed")


def test_an_unassigned_team_card_does_not_block():
    """The check is about ASSIGNMENT, not about whose card it is. An open card
    with nobody on it blocks nothing; the ordinary rules still apply."""
    open_card = dict(their_card(), assignedUserIds=[], isOpenShift=True)
    assert not is_assigned(open_card)
    assert assigned_cover([open_card]) == {}
    _u, c, _g, _m, _h = plan([("HMF", our_want())], [open_card], {},
                             our_titles=OURS, our_colors=OUR_COLOURS,
                             adopt=False, stale_color=CANCELLED_COLOR)
    assert len(c) == 1, c
    print("OK: an open card with nobody on it does not suppress anything")


def test_assigned_cover_is_keyed_on_job_and_day():
    cover = assigned_cover([their_card(), their_card(sid="t2", job="J22LAKE"),
                            our_card()])
    assert set(cover) == {("J1208", "2026-10-12"),
                          ("J22LAKE", "2026-10-12")}, cover
    assert len(cover[("J1208", "2026-10-12")]) == 1
    print("OK: the cover is (Job, day) and holds only assigned cards")


def test_a_board_with_no_assigned_cards_behaves_exactly_as_before():
    """The regression guard. Everything already proven about planning must keep
    holding, so the new rules have to be inert when nobody is assigned."""
    board = [our_card()]
    u, c, g, m, held = plan([("HMG", our_want())], board, {"HMG": ["ours1"]},
                            our_titles=OURS, our_colors=OUR_COLOURS,
                            adopt=False, stale_color=CANCELLED_COLOR)
    assert not u and not c and not g and not held, (u, c, g, held)
    assert m == {"HMG": ["ours1"]}, m
    # a genuine move is still a move
    moved = our_want(start=OUR_BLOCK + 2 * 3600)
    u2, _c2, _g2, _m2, h2 = plan([("HMG", moved)], board, {"HMG": ["ours1"]},
                                 our_titles=OURS, our_colors=OUR_COLOURS,
                                 adopt=False, stale_color=CANCELLED_COLOR)
    assert len(u2) == 1 and "startTime" in u2[0][2] and not h2, (u2, h2)
    print("OK: with nobody assigned, planning is unchanged")


def test_every_suppression_is_reported():
    """A run that silently declines to create a card is as confusing as one that
    silently duplicates, so each held item carries a reason."""
    mine = dict(our_card(sid="m1"), assignedUserIds=[1], isOpenShift=False)
    board = [mine, their_card(sid="t1", job="J22LAKE")]
    desired = [("HMA", our_want()), ("HMB", our_want(job="J22LAKE"))]
    _u, c, _g, _m, held = plan(desired, board, {"HMA": ["m1"]},
                               our_titles=OURS, our_colors=OUR_COLOURS,
                               adopt=False, stale_color=CANCELLED_COLOR)
    assert c == [], c
    reasons = [h[0] for h in held]
    assert any("this booking's card is assigned" in r for r in reasons), reasons
    assert any("already covers this Job" in r for r in reasons), reasons
    for why, _code, what in held:
        assert why and isinstance(what, dict)
    print("OK: %d suppression(s), each with a reason" % len(held))


def test_the_network_layer_refuses_an_assigned_card_anyway():
    """Defence in depth. plan() now filters assigned cards out, so this should
    be unreachable -- which is exactly why it is worth asserting. If a later
    change lets one through, the call still does not leave the machine.
    """
    from connecteam_map import assert_unassigned

    for card in (dict(our_card(), assignedUserIds=[9000001]),
                 dict(our_card(), isOpenShift=False),
                 their_card()):
        try:
            assert_unassigned([card])
        except ValueError:
            continue
        raise AssertionError(
            "assert_unassigned accepted a card somebody is on: %r" % (card,))
    # and an open card of ours still passes, or nothing could ever be written
    assert_unassigned([our_card()])
    print("OK: the network layer refuses an assigned card as a last resort")


if __name__ == "__main__":
    test_is_assigned_reads_both_signals()
    test_the_day_comes_from_the_cards_own_zone()
    test_an_assigned_card_blocks_our_block_at_the_same_job_that_day()
    test_their_assigned_card_is_never_touched()
    test_a_booking_whose_own_card_was_assigned_is_left_entirely_alone()
    test_an_assigned_card_is_not_updated_even_when_the_sheet_disagrees()
    test_the_block_is_still_created_on_a_different_day_or_job()
    test_an_unassigned_team_card_does_not_block()
    test_assigned_cover_is_keyed_on_job_and_day()
    test_a_board_with_no_assigned_cards_behaves_exactly_as_before()
    test_every_suppression_is_reported()
    test_the_network_layer_refuses_an_assigned_card_anyway()
    print("")
    print("ALL ASSIGNED-CARD READ-ONLY TESTS PASSED")
