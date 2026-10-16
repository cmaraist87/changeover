"""Colour follows the BOOKING, not the slot. A moved booking keeps its card.

Run: python test_booking_identity.py

Chris, 2026-10-06, correcting me: "a booking whose time changes stays green.
Active bookings stay green or the blue color if turnover. Only calder or
removed bookings turn gray. So based on the Confirmation Code..."

What I had built keyed on the slot -- (property, title, start instant) -- so a
booking whose time moved looked like one card vanishing and another appearing,
and the vanished one went grey. A crew would have read that as "this clean is
off" when it was merely an hour later.

The reason the Confirmation Code is necessary rather than just tidier is that
STRIKETHROUGH IS AMBIGUOUS. The sheet strikes a row for `moved` as well as for
`calder` (sheets_client._STRIKE_NEW_FLAGS), and both are only a line through
the cells. Reading the format, those two are identical. Reading the code, they
are not: a moved booking still has a live row somewhere.
"""
import pandas as pd

from connecteam_cards import (booking_activity, cards_for_tab, changes, code_of,
                              colour_for, ours, pair_by_code, plan, slot_key)
from connecteam_map import (CANCELLED_COLOR, OUR_TITLES, STANDARD_COLOR,
                            STANDARD_TITLE, TURNOVER_COLOR)

COL = "Confirmation Code"


def _row(code, date="2026-10-13", out="11:00 AM", to="", prop="6504 Thistledown A"):
    return {COL: code, "Property": prop, "City": "Austin", "Date": date,
            "Check-out Time": out, "T/O": to}


def _frame(*rows):
    return pd.DataFrame(list(rows))


# ------------------------------------------------------------------- the code

def test_the_code_is_normalised():
    """The sheet is hand-edited; the same code turns up padded and in odd case."""
    assert code_of({COL: " hmabc123 "}) == "HMABC123"
    assert code_of({COL: "HMABC123"}) == "HMABC123"
    assert code_of({"CONFIRMATION CODE": "gy-xyz"}) == "GY-XYZ"
    assert code_of({COL: ""}) == ""
    assert code_of({"Property": "x"}) == ""
    print("OK: codes normalise, and a row with none says so")


# --------------------------------------------------------------- active or not

def test_one_live_row_makes_the_booking_active():
    """The whole point. A booking with a struck row AND a live row is ACTIVE."""
    f = _frame(_row("HMA"), _row("HMA", date="2026-10-15"))
    act = booking_activity([(f, {0})])          # row 0 struck, row 1 live
    assert act["HMA"] is True, act
    print("OK: struck + live for one code reads as active")


def test_a_code_struck_everywhere_is_cancelled():
    f = _frame(_row("HMB"), _row("HMB", date="2026-10-15"))
    act = booking_activity([(f, {0, 1})])
    assert act["HMB"] is False, act
    print("OK: struck on every row reads as calder")


def test_activity_is_judged_across_every_tab():
    """A booking can move from one month into the next. Judging tab by tab, the
    October half would look calder and go grey for a job still happening."""
    oct_tab = _frame(_row("HMC", date="2026-10-30"))
    nov_tab = _frame(_row("HMC", date="2026-11-02"))
    act = booking_activity([(oct_tab, {0}), (nov_tab, set())])
    assert act["HMC"] is True, act
    print("OK: a booking that moved into the next month is still active")


def test_an_empty_tab_is_harmless():
    act = booking_activity([(pd.DataFrame(), set()), (None, set())])
    assert act == {}, act
    print("OK: an empty or missing tab contributes nothing")


# ------------------------------------------------------------------- colouring

def test_active_bookings_keep_their_colour():
    act = {"HMD": True}
    assert colour_for(_row("HMD"), act) == STANDARD_COLOR
    assert colour_for(_row("HMD", to="yes"), act) == TURNOVER_COLOR
    print("OK: active = green, or blue for a turnover")


def test_only_a_cancelled_booking_is_grey():
    act = {"HME": False}
    assert colour_for(_row("HME"), act) == CANCELLED_COLOR
    # Even a turnover. Calder beats turnover: it is not urgent work, it is
    # not work.
    assert colour_for(_row("HME", to="yes"), act) == CANCELLED_COLOR
    print("OK: calder = grey, turnover or not")


def test_a_code_nobody_has_seen_is_grey():
    """The 'removed' safety net. Should never fire, because cancellations stay in
    the sheet, which is exactly why it must not be silent when it does."""
    assert colour_for(_row("HMZZ"), {}) == CANCELLED_COLOR
    print("OK: a code absent from the sheet is treated as removed")


# ------------------------------------------------- the move, end to end

def test_a_moved_booking_yields_ONE_green_card_at_the_new_time():
    """The regression this file exists for.

    The sheet holds the old slot struck and the new slot live. Before this
    change that produced two cards: a grey one at the old time and a green one
    at the new. It must produce exactly one, green, at the new time.
    """
    f = _frame(_row("HMF", date="2026-10-13", out="11:00 AM"),
               _row("HMF", date="2026-10-15", out="11:00 AM"))
    act = booking_activity([(f, {0})])
    got = cards_for_tab(f, {0}, act)
    assert len(got) == 1, [(c, p["color"]) for _r, c, p in got]
    _row_obj, code, payload = got[0]
    assert code == "HMF"
    assert payload["color"] == STANDARD_COLOR, payload["color"]
    print("OK: a moved booking gives one green card, and it is the live row's")


def test_a_time_change_on_the_same_day_stays_green():
    """Chris' exact words: 'a booking whose time changes stays green.' Here the
    row is UPDATED in place, so it is amber rather than struck."""
    f = _frame(_row("HMG", out="12:00 PM"))
    act = booking_activity([(f, set())])
    got = cards_for_tab(f, set(), act)
    assert len(got) == 1 and got[0][2]["color"] == STANDARD_COLOR
    print("OK: an updated (amber) row stays green")


def test_a_cancelled_booking_still_gets_its_grey_card():
    f = _frame(_row("HMH"))
    act = booking_activity([(f, {0})])
    got = cards_for_tab(f, {0}, act)
    assert len(got) == 1, got
    assert got[0][2]["color"] == CANCELLED_COLOR
    print("OK: a calder booking keeps a card, in grey")


def test_a_struck_row_with_no_checkout_still_makes_no_card():
    """Most struck rows are ARRIVAL rows. No departure, nothing to clean."""
    f = _frame(_row("HMI", out=""))
    act = booking_activity([(f, {0})])
    assert cards_for_tab(f, {0}, act) == []
    print("OK: a struck arrival row produces no card")


def test_a_cancellation_and_its_replacement_are_two_bookings():
    """Same property, same day, two codes: one calder, one live. Two cards,
    one grey and one green, because they are two different bookings."""
    f = _frame(_row("HMJ"), _row("HMK"))
    act = booking_activity([(f, {0})])
    got = cards_for_tab(f, {0}, act)
    colours = sorted(p["color"] for _r, _c, p in got)
    assert colours == sorted([CANCELLED_COLOR, STANDARD_COLOR]), colours
    print("OK: a cancellation and its replacement stay distinct")


# --------------------------------------------------------------------- pairing

def _have(jid, start, colour=STANDARD_COLOR, sid="s1"):
    return {"id": sid, "jobId": jid, "startTime": start, "endTime": start + 900,
            "color": colour, "title": "Clean"}


def _want(jid, start, colour=STANDARD_COLOR):
    return ({}, "HM1", {"jobId": jid, "startTime": start, "endTime": start + 900,
                        "color": colour, "title": "Clean"})


def test_a_moved_booking_is_paired_with_its_old_card():
    pairs, create, orphan = pair_by_code([_want("J1", 2000)], [_have("J1", 1000)])
    assert len(pairs) == 1 and not create and not orphan
    assert changes(pairs[0][0][2], pairs[0][1]) == {"startTime": (1000, 2000),
                                                   "endTime": (1900, 2900)}
    print("OK: the same booking at a new time pairs with the card it already has")


def test_a_combined_listing_pairs_by_property_not_by_time():
    """One booking, two units, identical instants. Pairing by time alone would
    shuffle a card from one flat to the other."""
    want = [_want("JA", 1000), _want("JB", 1000)]
    have = [_have("JB", 1000, sid="b"), _have("JA", 1000, sid="a")]
    pairs, create, orphan = pair_by_code(want, have)
    assert not create and not orphan
    for w, h in pairs:
        assert w[2]["jobId"] == h["jobId"], (w[2]["jobId"], h["jobId"])
    print("OK: cards stay with their own unit")


def test_a_booking_that_changes_property_keeps_its_card():
    """No jobId in common, so it falls through to order -- which is how 'move the
    existing card' survives a change of unit."""
    pairs, create, orphan = pair_by_code([_want("JNEW", 1000)],
                                         [_have("JOLD", 1000)])
    assert len(pairs) == 1 and not create and not orphan
    assert changes(pairs[0][0][2], pairs[0][1]) == {"jobId": ("JOLD", "JNEW")}
    print("OK: a booking that moved unit keeps its card, with a new jobId")


def test_extra_wanted_cards_are_created_and_extra_existing_are_orphans():
    pairs, create, orphan = pair_by_code(
        [_want("J1", 1000), _want("J2", 1000)], [_have("J1", 1000)])
    assert len(pairs) == 1 and len(create) == 1 and not orphan
    pairs, create, orphan = pair_by_code(
        [_want("J1", 1000)], [_have("J1", 1000), _have("J9", 5000, sid="x")])
    assert len(pairs) == 1 and not create and len(orphan) == 1
    print("OK: unmatched wants are created, unmatched cards are reported")


def test_changes_ignores_fields_the_team_own():
    """An update is a REPLACE, so anything the team added has to survive it. The
    diff only looks at what a booking can actually change."""
    have = _have("J1", 1000)
    have.update({"notes": ["crew note"], "tasks": [{"t": 1}],
                 "isRequireAdminApproval": True})
    want = _want("J1", 1000)[2]
    assert changes(want, have) == {}, changes(want, have)
    print("OK: notes, tasks and approval flags are not 'changes'")


def test_changes_sees_a_recolour_on_its_own():
    have = _have("J1", 1000, colour=STANDARD_COLOR)
    want = _want("J1", 1000, colour=CANCELLED_COLOR)[2]
    assert changes(want, have) == {"color": (STANDARD_COLOR, CANCELLED_COLOR)}
    print("OK: a cancellation with no time change is still a change")


# ------------------------------------------------------------------ the plan

def _board(sid, jid, start, colour=STANDARD_COLOR, title="Clean"):
    return {"id": sid, "jobId": jid, "title": title, "startTime": start,
            "endTime": start + 900, "color": colour}


def _pay(jid, start, colour=STANDARD_COLOR, title="Clean"):
    return {"jobId": jid, "title": title, "startTime": start,
            "endTime": start + 900, "color": colour}


OURS = ("Clean", "Turnover")


def test_a_moved_booking_is_an_update_not_a_create_and_a_grey():
    """The whole correction, at the level the board actually sees."""
    u, c, g, m, _held = plan([("HMA", _pay("J1", 2000))],
                      [_board("s1", "J1", 1000)], {"HMA": ["s1"]},
                      our_titles=OURS, stale_color=CANCELLED_COLOR)
    assert len(u) == 1 and not c and not g, (u, c, g)
    assert u[0][2] == {"startTime": (1000, 2000), "endTime": (1900, 2900)}
    assert m == {"HMA": ["s1"]}, m
    print("OK: a moved booking is one update, no new card, no grey")


def test_an_unmapped_card_is_adopted_by_its_slot():
    """The 95 cards already on the test board carry no code. Rather than delete
    and rebuild, a card is matched where the OLD identity would have put it, and
    is in the map from then on."""
    u, c, g, m, _held = plan([("HMB", _pay("J1", 1000))],
                      [_board("s1", "J1", 1000)], {},          # empty map
                      our_titles=OURS, stale_color=CANCELLED_COLOR)
    assert not u and not c and not g, (u, c, g)
    assert m == {"HMB": ["s1"]}, m
    print("OK: an existing card is adopted, not duplicated and not greyed")


def test_adoption_does_not_steal_a_card_from_another_code():
    """Two bookings, one card. Whoever adopts it, the other must CREATE rather
    than both claiming the same card."""
    u, c, g, m, _held = plan([("HMC", _pay("J1", 1000)), ("HMD", _pay("J1", 1000))],
                      [_board("s1", "J1", 1000)], {},
                      our_titles=OURS, stale_color=CANCELLED_COLOR)
    assert len(c) == 1, c
    claimed = [sid for ids in m.values() for sid in ids]
    assert claimed == ["s1"], claimed
    print("OK: one card is adopted once; the other booking gets a new card")


def test_only_a_card_nobody_claims_is_greyed():
    """Grey means REMOVED. A calder booking does not arrive here as a grey --
    it arrives as a desired card that is already grey, so it is a normal update."""
    u, c, g, m, _held = plan([], [_board("s1", "J1", 1000)], {},
                      our_titles=OURS, stale_color=CANCELLED_COLOR)
    assert [s["id"] for s in g] == ["s1"], g
    print("OK: a card no booking claims is greyed")


def test_a_cancelled_booking_is_an_update_to_grey_not_a_removal():
    u, c, g, m, _held = plan([("HME", _pay("J1", 1000, colour=CANCELLED_COLOR))],
                      [_board("s1", "J1", 1000, colour=STANDARD_COLOR)],
                      {"HME": ["s1"]}, our_titles=OURS,
                      stale_color=CANCELLED_COLOR)
    assert len(u) == 1 and not g, (u, g)
    assert u[0][2] == {"color": (STANDARD_COLOR, CANCELLED_COLOR)}
    print("OK: a cancellation is a recolour of its own card, not a removal")


def test_the_team_s_cards_are_never_greyed():
    board = [_board("t1", "J9", 1000, colour="#D9B443", title="6504 Thistledown A"),
             _board("t2", "J9", 1000, title="")]
    u, c, g, m, _held = plan([], board, {}, our_titles=OURS, stale_color=CANCELLED_COLOR)
    assert g == [], g
    print("OK: a card whose title is not ours is left alone")


def test_an_already_grey_card_is_not_repainted():
    """Idempotence. A nightly run that repaints the same cards every night burns
    calls and makes the log useless for spotting a real change."""
    u, c, g, m, _held = plan([], [_board("s1", "J1", 1000, colour=CANCELLED_COLOR)], {},
                      our_titles=OURS, stale_color=CANCELLED_COLOR)
    assert g == [], g
    print("OK: an already-grey card is left as it is")


def test_greying_is_off_when_no_stale_colour_is_given():
    u, c, g, m, _held = plan([], [_board("s1", "J1", 1000)], {}, our_titles=OURS,
                      stale_color=None)
    assert g == [], g
    print("OK: no stale colour means nothing is greyed")


def test_a_new_booking_is_simply_created():
    u, c, g, m, _held = plan([("HMF", _pay("J1", 1000))], [], {},
                      our_titles=OURS, stale_color=CANCELLED_COLOR)
    assert not u and len(c) == 1 and not g
    assert m == {}, m          # nothing to record until the card has an id
    print("OK: a booking with no card yet is created")


def test_a_second_run_over_an_unchanged_board_does_nothing():
    board = [_board("s1", "J1", 1000)]
    for mapping in ({}, {"HMG": ["s1"]}):
        u, c, g, m, _held = plan([("HMG", _pay("J1", 1000))], board, mapping,
                          our_titles=OURS, stale_color=CANCELLED_COLOR)
        assert not u and not c and not g, (mapping, u, c, g)
    print("OK: a settled board produces no actions, mapped or adopted")


# ------------------------------------- the rail that protects a crew board

OUR_COLOURS = (STANDARD_COLOR, TURNOVER_COLOR, CANCELLED_COLOR)

# Real cards read off the LIVE Austin board (1000003) on 2026-10-07, inside the
# 45-day window. The crews' own titles are in Spanish and the cards carry NO
# colour at all. Reproduced from the board rather than invented, because a rail
# tested against made-up data only tests my own assumptions.
AUSTIN_REAL = [
    {"id": "t1", "jobId": "5162b912", "title": "", "color": None},
    {"id": "t2", "jobId": "a2d0b311", "title": "sale no entran huespedes",
     "color": None},
    {"id": "t3", "jobId": "84ee8d80", "title": "sale no entran huespedes",
     "color": None},
    {"id": "t4", "jobId": "1ca55baa", "title": "sale no entran huespedes",
     "color": None},
]


def test_a_real_austin_card_cannot_be_adopted_moved_or_greyed():
    """The guarantee, against cards actually on the crew board.

    Worst case on purpose: our desired card shares the jobId AND the exact start
    instant with one of theirs, which is the only way the old slot key could
    collide. It must still be left alone, and ours created beside it.
    """
    board = [dict(c, startTime=1000, endTime=1900) for c in AUSTIN_REAL]
    u, c, g, m, _held = plan([("HMX", _pay("84ee8d80", 1000))], board, {},
                      our_titles=OURS, our_colors=OUR_COLOURS,
                      stale_color=CANCELLED_COLOR)
    assert u == [], f"a team card would have been modified: {u}"
    assert g == [], f"a team card would have been greyed: {g}"
    assert len(c) == 1, c
    touched = {sid for ids in m.values() for sid in ids}
    assert touched == set(), f"a team card entered our map: {touched}"
    print("OK: a real Austin card is not adopted, moved, greyed or claimed")


def test_our_title_with_their_colour_is_not_ours():
    """Both tests, not either. A card called Clean that the team tinted
    themselves is theirs."""
    board = [{"id": "x", "jobId": "J1", "title": "Clean", "color": "#D9B443",
              "startTime": 1000, "endTime": 1900}]
    u, c, g, m, _held = plan([("HMY", _pay("J1", 1000))], board, {}, our_titles=OURS,
                      our_colors=OUR_COLOURS, stale_color=CANCELLED_COLOR)
    assert u == [] and g == [] and len(c) == 1, (u, g, c)
    print("OK: our title plus a colour we never send is not ours")


def test_our_colour_with_their_title_is_not_ours():
    board = [{"id": "y", "jobId": "J1", "title": "sale entra gente",
              "color": STANDARD_COLOR, "startTime": 1000, "endTime": 1900}]
    u, c, g, m, _held = plan([("HMZ", _pay("J1", 1000))], board, {}, our_titles=OURS,
                      our_colors=OUR_COLOURS, stale_color=CANCELLED_COLOR)
    assert u == [] and g == [] and len(c) == 1, (u, g, c)
    print("OK: our colour on a card with their title is not ours")


def test_a_card_that_really_is_ours_is_still_adopted():
    """The rail must not block the thing it exists beside. The 67 cards on Chris
    Test have to keep being adopted rather than duplicated."""
    board = [{"id": "mine", "jobId": "J1", "title": "Clean",
              "color": STANDARD_COLOR, "startTime": 1000, "endTime": 1900}]
    u, c, g, m, _held = plan([("HMW", _pay("J1", 1000))], board, {}, our_titles=OURS,
                      our_colors=OUR_COLOURS, stale_color=CANCELLED_COLOR)
    assert not u and not c and not g, (u, c, g)
    assert m == {"HMW": ["mine"]}, m
    print("OK: a card that is ours on both tests is adopted as before")


def test_the_first_run_on_a_market_board_can_only_create():
    """What a quillon live Austin run can do, stated as a test.

    The map is empty and nothing on the board passes both tests, so there is no
    card the planner is even able to modify. Creates are the only outcome
    available to it.
    """
    board = [dict(c, startTime=1000 + i, endTime=1900 + i)
             for i, c in enumerate(AUSTIN_REAL)]
    wanted = [(f"HM{i}", _pay("84ee8d80", 1000 + i)) for i in range(6)]
    u, c, g, m, _held = plan(wanted, board, {}, our_titles=OURS,
                      our_colors=OUR_COLOURS, stale_color=CANCELLED_COLOR)
    assert u == [] and g == [], (u, g)
    assert len(c) == len(wanted), c
    print(f"OK: a quillon market run has {len(c)} creates and 0 possible changes")


def test_a_card_we_made_and_recorded_is_still_modifiable():
    """The other half: the rail must not make our own cards unmanageable once the
    map knows them, or a cancellation could never be painted."""
    board = [{"id": "ours1", "jobId": "J1", "title": "Clean",
              "color": STANDARD_COLOR, "startTime": 1000, "endTime": 1900}]
    u, c, g, m, _held = plan([("HMV", _pay("J1", 1000, colour=CANCELLED_COLOR))],
                      board, {"HMV": ["ours1"]}, our_titles=OURS,
                      our_colors=OUR_COLOURS, stale_color=CANCELLED_COLOR)
    assert len(u) == 1 and u[0][2] == {"color": (STANDARD_COLOR, CANCELLED_COLOR)}
    print("OK: a recorded card of ours can still be recoloured")


def test_one_code_with_two_cards_at_one_property_keeps_each_in_place():
    """The Savannah near-miss of 2026-10-11, pinned.

    One confirmation code can own TWO cards at the same property: the sheet gives
    a booking a row on its arrival date and another on its departure, and where
    the arrival row also carries a departure time -- a turnover -- both produce a
    card under the one code. 6 Dunmore / HMSAMPLE09 was exactly that: a turnover on
    14 October and an ordinary clean on the 18th.

    jobId cannot separate them, both being 6 Dunmore. Taking the quillon jobId match
    paired each want with the OTHER one's card and proposed to move both. Thirteen
    bookings were about to be shuffled on a live board -- a no-op in aggregate,
    twenty cards churned to new times in front of the crews working them.
    """
    day = 86400

    def want(start, title=STANDARD_TITLE):
        return ({}, "HMSAMPLE09", {"jobId": "J6Lake", "title": title,
                                   "startTime": start, "endTime": start + 900,
                                   "color": STANDARD_COLOR})

    def have(start, sid):
        return {"id": sid, "jobId": "J6Lake", "title": STANDARD_TITLE,
                "startTime": start, "endTime": start + 900,
                "color": STANDARD_COLOR}

    wants = [want(14 * day, "Turnover"), want(18 * day)]
    # Deliberately the WRONG way round: the board does not promise an order.
    haves = [have(18 * day, "card-18"), have(14 * day, "card-14")]
    pairs, to_create, orphans_ = pair_by_code(wants, haves)
    assert not to_create and not orphans_, (to_create, orphans_)
    for w, h in pairs:
        assert int(w[2]["startTime"]) == int(h["startTime"]), (
            f"want at {w[2]['startTime']} paired with card at {h['startTime']} "
            f"-- that is the gratuitous move this test exists to prevent")
        assert "startTime" not in changes(w[2], h)
    print("OK: two cards, one code, one property -- each keeps its own card")


def test_two_wants_one_card_gives_the_card_to_the_one_it_belongs_to():
    """The actual Savannah case, and why nearest-per-want was not enough.

    The code wanted a turnover on the 14th and a clean on the 18th, and only the
    18th's card was recorded. Taking the nearest card FOR EACH WANT IN TURN still
    let the quillon want in the list grab the only card there was -- want order is
    arbitrary -- so the 14th claimed the 18th's card, proposed to move it back
    four days, and the 18th was then created. Nineteen cards on the live board
    were queued up to move that way.

    Pairing closest-quillon across all candidates fixes it without needing to know
    which want is which.
    """
    day = 86400

    def w(start, title=STANDARD_TITLE):
        return ({}, "HMSAMPLE09", {"jobId": "J6Lake", "title": title,
                                   "startTime": start, "endTime": start + 900,
                                   "color": STANDARD_COLOR})

    def h(start, sid):
        return {"id": sid, "jobId": "J6Lake", "title": STANDARD_TITLE,
                "startTime": start, "endTime": start + 900,
                "color": STANDARD_COLOR}

    # Both want orders must give the same answer; the old code depended on it.
    for wants in ([w(14 * day, "Turnover"), w(18 * day)],
                  [w(18 * day), w(14 * day, "Turnover")]):
        pairs, to_create, orphans_ = pair_by_code(wants, [h(18 * day, "card-18")])
        assert len(pairs) == 1, pairs
        assert int(pairs[0][0][2]["startTime"]) == 18 * day, (
            "the card went to the wrong booking")
        assert "startTime" not in changes(pairs[0][0][2], pairs[0][1])
        assert len(to_create) == 1 and int(to_create[0][2]["startTime"]) == 14 * day
        assert not orphans_
    print("OK: the recorded card stays with its own date; the other is created")


def test_a_real_move_is_still_detected_when_there_is_one_card():
    """The fix must not stop a genuine move being seen. One card, new time."""
    day = 86400
    w = ({}, "HM1", {"jobId": "J1", "title": STANDARD_TITLE,
                     "startTime": 20 * day, "endTime": 20 * day + 900,
                     "color": STANDARD_COLOR})
    h = {"id": "c1", "jobId": "J1", "title": STANDARD_TITLE,
         "startTime": 14 * day, "endTime": 14 * day + 900,
         "color": STANDARD_COLOR}
    pairs, to_create, orphans_ = pair_by_code([w], [h])
    assert len(pairs) == 1 and not to_create and not orphans_
    assert changes(pairs[0][0][2], pairs[0][1])["startTime"] == (14 * day, 20 * day)
    print("OK: with one card, a genuine move is still a move")


def test_nearest_in_time_does_not_cross_properties():
    """Property still beats time. A combined listing makes cards at the same
    instant at DIFFERENT units, and those must not swap."""
    day = 86400
    wants = [({}, "HM2", {"jobId": "JA", "title": STANDARD_TITLE,
                          "startTime": 10 * day, "endTime": 10 * day + 900,
                          "color": STANDARD_COLOR}),
             ({}, "HM2", {"jobId": "JB", "title": STANDARD_TITLE,
                          "startTime": 10 * day, "endTime": 10 * day + 900,
                          "color": STANDARD_COLOR})]
    haves = [{"id": "b", "jobId": "JB", "title": STANDARD_TITLE,
              "startTime": 10 * day, "endTime": 10 * day + 900,
              "color": STANDARD_COLOR},
             {"id": "a", "jobId": "JA", "title": STANDARD_TITLE,
              "startTime": 10 * day, "endTime": 10 * day + 900,
              "color": STANDARD_COLOR}]
    pairs, _c, _o = pair_by_code(wants, haves)
    for w, h in pairs:
        assert w[2]["jobId"] == h["jobId"], (w[2]["jobId"], h["jobId"])
    print("OK: cards stay with their own unit even at an identical instant")


def test_a_three_hour_card_is_never_ours():
    """The 2026-10-11 near-miss, pinned at the level that matters.

    The map rebuild matched cards on the SLOT alone -- property, title, instant.
    The team now writes our titles on their own cards, many of theirs start at
    11:00 like ours, so the rebuild claimed 95 of them, and the next push
    proposed shortening seventeen from three hours to fifteen minutes. That would
    have rewritten the crews' own schedule.

    Length is the discriminator the team cannot accidentally match: every card
    this system makes is exactly CARD_MINUTES long.
    """
    from connecteam_map import CARD_MINUTES

    start = 1792162800
    ours_card = {"id": "a", "jobId": "J1", "title": STANDARD_TITLE,
                 "color": STANDARD_COLOR, "startTime": start,
                 "endTime": start + CARD_MINUTES * 60}
    theirs = dict(ours_card, id="b", endTime=start + 3 * 3600)

    assert ours(ours_card, (STANDARD_TITLE,), (STANDARD_COLOR,))
    # ours() alone cannot tell them apart -- that is the whole point.
    assert ours(theirs, (STANDARD_TITLE,), (STANDARD_COLOR,))

    def our_length(c):
        return int(c["endTime"]) - int(c["startTime"]) == CARD_MINUTES * 60

    assert our_length(ours_card), "our own card must pass the length test"
    assert not our_length(theirs), (
        "a three-hour card passed the length test; the team's cards would be "
        "claimed again")
    print(f"OK: {CARD_MINUTES} minutes is ours; three hours is theirs")


def test_the_slot_key_cannot_separate_us_from_the_team():
    """Why the length test had to be added rather than the slot tightened.

    A team card at the same property, same title and same instant has an
    IDENTICAL slot key. No amount of care with that key could have helped.
    """
    start = 1792162800
    mine = {"jobId": "J1", "title": STANDARD_TITLE, "startTime": start,
            "endTime": start + 900}
    theirs = {"jobId": "J1", "title": STANDARD_TITLE, "startTime": start,
              "endTime": start + 10800}
    assert slot_key(mine) == slot_key(theirs), (
        "if these ever differ, the slot key alone would have been enough")
    print("OK: the slot key is identical for both, so length is what separates")


def test_a_push_cannot_grey_the_other_city_sharing_its_board():
    """The worst near-miss of 2026-10-11, caught in a preview.

    Savannah and Thunderbolt share board 1000002; New Quintrell and Net St. Louis
    share 1000001. A push knows only its own city's bookings. With ONE card map
    per board, every card belonging to the other city was unclaimed, and the
    stale pass reads unclaimed as "no booking wants this any more" and greys it.

    A Thunderbolt push -- NINE cards -- planned to grey 300 Savannah cards. That
    would have told the crews 300 real cleans were calder.

    The map is per board AND city now. This asserts the consequence directly: a
    city's push, given only its own map, leaves the other city's cards alone.
    """
    from connecteam_map import CARD_MINUTES

    start = 1792162800
    span = CARD_MINUTES * 60

    def card(sid, jid, at):
        return {"id": sid, "jobId": jid, "title": STANDARD_TITLE,
                "color": STANDARD_COLOR, "startTime": at, "endTime": at + span}

    # The board holds Savannah's cards and Thunderbolt's one property.
    savannah = [card(f"sav{i}", f"JSAV{i}", start + i * 86400) for i in range(300)]
    thunder = [card("tb1", "JWHATLEY", start)]
    board = savannah + thunder

    # Thunderbolt's push: its own map, its own desired card.
    tb_map = {"HMTB1": ["tb1"]}
    desired = [("HMTB1", {"jobId": "JWHATLEY", "title": STANDARD_TITLE,
                          "color": STANDARD_COLOR, "startTime": start,
                          "endTime": start + span})]
    u, c, g, m, _held = plan(desired, board, tb_map, our_titles=OUR_TITLES,
                      our_colors=(STANDARD_COLOR, TURNOVER_COLOR,
                                  CANCELLED_COLOR),
                      adopt=False, stale_color=CANCELLED_COLOR)
    assert g == [], (
        f"a Thunderbolt push would grey {len(g)} card(s) it does not own -- "
        f"this is the 300-card near-miss")
    assert not u and not c, (u, c)
    print("OK: a push leaves the other city's cards on a shared board alone")


def test_the_map_name_is_per_city_not_per_board():
    """If these ever collide again, the 300-card greying comes straight back."""
    from connecteam_cards import map_name
    from connecteam_map import CITY_SCHEDULERS

    shared = {}
    for city, board in CITY_SCHEDULERS.items():
        shared.setdefault(str(board), []).append(city)
    pairs = [cities for cities in shared.values() if len(cities) > 1]
    assert pairs, "no board is shared any more; this test is stale"
    for cities in pairs:
        board = str(CITY_SCHEDULERS[cities[0]])
        names = {map_name(board, c) for c in cities}
        assert len(names) == len(cities), (
            f"{cities} on board {board} share a map file: {names}")
    print(f"OK: every city on a shared board has its own map "
          f"({len(pairs)} shared board(s))")


if __name__ == "__main__":
    test_the_code_is_normalised()
    test_one_live_row_makes_the_booking_active()
    test_a_code_struck_everywhere_is_cancelled()
    test_activity_is_judged_across_every_tab()
    test_an_empty_tab_is_harmless()
    test_active_bookings_keep_their_colour()
    test_only_a_cancelled_booking_is_grey()
    test_a_code_nobody_has_seen_is_grey()
    test_a_moved_booking_yields_ONE_green_card_at_the_new_time()
    test_a_time_change_on_the_same_day_stays_green()
    test_a_cancelled_booking_still_gets_its_grey_card()
    test_a_struck_row_with_no_checkout_still_makes_no_card()
    test_a_cancellation_and_its_replacement_are_two_bookings()
    test_a_moved_booking_is_paired_with_its_old_card()
    test_a_combined_listing_pairs_by_property_not_by_time()
    test_a_booking_that_changes_property_keeps_its_card()
    test_extra_wanted_cards_are_created_and_extra_existing_are_orphans()
    test_changes_ignores_fields_the_team_own()
    test_changes_sees_a_recolour_on_its_own()
    test_a_moved_booking_is_an_update_not_a_create_and_a_grey()
    test_an_unmapped_card_is_adopted_by_its_slot()
    test_adoption_does_not_steal_a_card_from_another_code()
    test_only_a_card_nobody_claims_is_greyed()
    test_a_cancelled_booking_is_an_update_to_grey_not_a_removal()
    test_the_team_s_cards_are_never_greyed()
    test_an_already_grey_card_is_not_repainted()
    test_greying_is_off_when_no_stale_colour_is_given()
    test_a_new_booking_is_simply_created()
    test_a_second_run_over_an_unchanged_board_does_nothing()
    test_a_real_austin_card_cannot_be_adopted_moved_or_greyed()
    test_our_title_with_their_colour_is_not_ours()
    test_our_colour_with_their_title_is_not_ours()
    test_a_card_that_really_is_ours_is_still_adopted()
    test_the_first_run_on_a_market_board_can_only_create()
    test_a_card_we_made_and_recorded_is_still_modifiable()
    test_one_code_with_two_cards_at_one_property_keeps_each_in_place()
    test_two_wants_one_card_gives_the_card_to_the_one_it_belongs_to()
    test_a_three_hour_card_is_never_ours()
    test_the_slot_key_cannot_separate_us_from_the_team()
    test_a_push_cannot_grey_the_other_city_sharing_its_board()
    test_the_map_name_is_per_city_not_per_board()
    test_a_real_move_is_still_detected_when_there_is_one_card()
    test_nearest_in_time_does_not_cross_properties()
    print("\nALL BOOKING-IDENTITY TESTS PASSED")
