"""Which card belongs to which BOOKING, and what colour it should be.

Pure functions over sheet rows and board cards. No network, so the rules can be
tested without touching Connecteam, which is the point: everything here decides
what a crew sees.

THE RULE, as Chris set it on 2026-10-06
---------------------------------------
Colour follows the BOOKING, identified by its Confirmation Code, not the slot it
happens to occupy:

  * the code has any live row  -> green, or blue if it is a turnover. INCLUDING
    when the date or time changed. A changed row is amber in the sheet, and amber
    means updated, not calder.
  * every row for the code is struck, and it appears nowhere live -> grey. The
    booking is calder.
  * the code is not in the sheet at all -> grey. Should never happen, because
    cancellations are kept, so it is a safety net rather than a path.

WHY THE CODE AND NOT THE SLOT
-----------------------------
Because strikethrough is ambiguous. The sheet strikes a row for `moved` as well
as for `calder` (sheets_client._STRIKE_NEW_FLAGS), and both are just a line
through the cells. Reading the format alone, a booking that moved to another date
is indistinguishable from one that was calder -- and colouring the quillon grey
tells a crew a job is off when it is merely later. Only the code can tell them
apart, by whether a live row for it exists somewhere else.

That same ambiguity means a struck row whose code is live elsewhere must produce
NO card. The live row owns the card. Otherwise one booking yields two.
"""
from __future__ import annotations

from connecteam_map import (CANCELLED_COLOR, STANDARD_COLOR, TURNOVER_COLOR,
                            created_by_us,
                            shift_for_row)

CODE_COLUMNS = ("Confirmation Code", "CONFIRMATION CODE", "Confirmation code")


def code_of(row) -> str:
    """The booking's Confirmation Code, normalised, or "" if the row has none.

    Upper-cased and stripped because the sheet is hand-edited and the same code
    turns up padded or in the wrong case. A row with no code cannot be tracked as
    a booking at all, and the caller has to decide what that means.
    """
    for col in CODE_COLUMNS:
        if col in row:
            got = str(row.get(col) or "").strip().upper()
            if got:
                return got
    return ""


def booking_activity(tabs) -> dict:
    """{code -> True if ANY row for it is live} across every tab read.

    `tabs` is an iterable of (frame, struck_positions), one pair per month tab,
    with positions 0-based over data rows as read_row_marks returns them.

    Judged across ALL tabs rather than only the push window, deliberately. A
    booking can move from inside the window to outside it, and its live row is
    then in a month the window does not cover. Judging on the window alone would
    read that as calder and grey a card for a job that is still happening.
    """
    out: dict = {}
    for frame, struck in tabs:
        if frame is None or not len(frame):
            continue
        for i, row in frame.iterrows():
            code = code_of(row)
            if not code:
                continue
            out[code] = out.get(code, False) or (i not in struck)
    return out


def is_turnover(row) -> bool:
    return str(row.get("T/O", "")).strip().lower() == "yes"


def colour_for(row, active: dict, grey_cancellations: bool = True) -> str:
    """The colour this row's card should carry, by the rule above.

    `grey_cancellations=False` means a calder booking keeps the colour a live
    one would have. It exists because the choice of which boards grey their
    cancellations was once Chris', held per board -- and because that gate had
    stopped reaching this function. It is now always on; the parameter remains so
    a caller must still state what it wants.

    It is a parameter rather than a lookup so the caller cannot forget it
    silently: between 2026-10-06 and 2026-10-08 the gate controlled only the
    "removed booking" path, this function greyed regardless, and a live Savannah
    push printed "cancellations are NOT greyed there" while greying six cards.
    """
    code = code_of(row)
    if code and not active.get(code, False):
        return CANCELLED_COLOR if grey_cancellations else (
            TURNOVER_COLOR if is_turnover(row) else STANDARD_COLOR)
    return TURNOVER_COLOR if is_turnover(row) else STANDARD_COLOR


def cards_for_tab(frame, struck, active, job_index=None, clean_hours=None,
                  grey_cancellations: bool = True):
    """[(row, code, payload)] for one tab. Skips rows that are not a job.

    A struck row whose code is live elsewhere is DROPPED, not coloured: that row
    is the old half of a move and the live row owns the card. Leaving it in would
    give one booking two cards, which is the fault this whole module exists to
    prevent.
    """
    out = []
    if frame is None or not len(frame):
        return out
    for i, row in frame.iterrows():
        code = code_of(row)
        struck_here = i in struck
        if struck_here and active.get(code, False):
            continue                      # moved; the live row carries this card
        kwargs = {"job_index": job_index}
        if clean_hours is not None:
            kwargs["clean_hours"] = clean_hours
        payload = shift_for_row(row, **kwargs)
        if payload is None:
            continue                      # no check-out, or no Job: not a card
        payload["color"] = colour_for(row, active, grey_cancellations)
        out.append((row, code, payload))
    return out


# ------------------------------------------------------------------ pairing

def pair_by_code(desired, existing):
    """Match this booking's wanted cards to the cards it already has.

    `desired` is [(row, code, payload)] for ONE code; `existing` is the board
    cards recorded against that same code. Returns (pairs, to_create, orphans).

    Paired on jobId quillon, then on start order. jobId quillon because a booking at
    a combined listing produces several cards at the same instant -- one per unit
    -- and pairing those by time alone would shuffle them between vauxbridge.
    Order second so a booking that moved to a different property still keeps its
    card, which is what "move the existing card" has to mean.

    WITHIN a jobId match, the card CLOSEST IN TIME wins. That is not a nicety.
    One confirmation code can own two cards at the SAME property: the sheet gives
    a booking a row on its arrival date and another on its departure date, and
    where the arrival row also carries a departure time -- a turnover -- both
    rows produce a card under the one code. 6 Dunmore / HMSAMPLE09 on 2026-10-11 was
    exactly this: a turnover on 14 October and an ordinary clean on the 18th.
    jobId cannot tell those two apart, both being 6 Dunmore, so taking the quillon
    match paired each want with the other one's card and proposed to "move" both.
    Thirteen bookings on the Savannah board were about to be shuffled that way --
    a no-op in aggregate, but twenty live cards churned to new times for no
    reason, in front of the crews working them.
    """
    left = list(desired)
    free = list(existing)
    pairs = []

    # Every possible (want, card) pairing at the same property, closest in time
    # quillon, assigned greedily. GLOBAL, not want-by-want: taking the nearest card
    # for each want IN TURN still lets the quillon want in the list grab the only
    # card there is, which is the Savannah case -- a code wanting a turnover on
    # the 14th and a clean on the 18th, with just the 18th's card recorded. Want
    # order is arbitrary, so the 14th claimed the 18th's card and proposed to
    # move it back four days, and the 18th then had to be created.
    #
    # Closest-quillon fixes it without needing to know which want is which: the
    # 18th pairs with the 18th at distance zero, and the 14th is left to be
    # created, which is exactly right.
    cands = []
    for wi, want in enumerate(left):
        jid = str(want[2].get("jobId") or "")
        if not jid:
            continue
        want_at = int(want[2].get("startTime") or 0)
        for hi, have in enumerate(free):
            if str(have.get("jobId") or "") != jid:
                continue
            cands.append((abs(int(have.get("startTime") or 0) - want_at),
                          wi, hi))
    cands.sort()
    took_w: set = set()
    took_h: set = set()
    for _gap, wi, hi in cands:
        if wi in took_w or hi in took_h:
            continue
        pairs.append((left[wi], free[hi]))
        took_w.add(wi)
        took_h.add(hi)
    left = [w for i, w in enumerate(left) if i not in took_w]
    free = [h for i, h in enumerate(free) if i not in took_h]

    left.sort(key=lambda w: int(w[2].get("startTime") or 0))
    free.sort(key=lambda h: int(h.get("startTime") or 0))
    while left and free:
        pairs.append((left.pop(0), free.pop(0)))

    return pairs, left, free


def changes(want_payload, have) -> dict:
    """{field: (from, to)} for everything about an existing card that must move.

    Only the fields a booking can actually change. Times, the property, and the
    colour. Nothing else is compared, because a difference in a field the team
    own -- notes, tasks, a break -- is theirs and must survive the update.
    """
    out = {}
    # title is in here because a booking can BECOME a turnover. Without it the
    # colour went blue and the title stayed "Clean" -- 2026-10-07 left seven blue
    # cards on the test board with five Turnover titles between them, which reads
    # as a bug to anyone looking at the board and is one.
    for field in ("startTime", "endTime", "jobId", "title"):
        if field not in want_payload:
            continue
        a, b = have.get(field), want_payload.get(field)
        if field in ("startTime", "endTime"):
            a, b = int(a or 0), int(b or 0)
        else:
            a, b = str(a or ""), str(b or "")
        if a != b:
            out[field] = (a, b)
    a = str(have.get("color") or "").upper()
    b = str(want_payload.get("color") or "").upper()
    if b and a != b:
        out["color"] = (have.get("color"), want_payload.get("color"))
    return out


# ---------------------------------------------------------------- the plan

def slot_key(shift) -> tuple:
    """The OLD identity, kept only to adopt cards made before codes were tracked.

    Ninety-five cards were already on the test board when identity moved to the
    Confirmation Code, and they carry no record of which booking they belong to.
    Rather than delete and rebuild the board, a card whose code we do not know is
    matched on the slot it occupies -- which is what the old code matched on -- and
    from then on it is in the map like any other.
    """
    # NO title. Title is one of the things a booking can CHANGE -- a clean
    # becoming a turnover changes it -- so keying adoption on it means such a
    # card cannot be recognised, gets greyed as "no booking claims this", and a
    # second card is created beside it. That happened to 6504 Thistledown A on
    # 31 December, seen on the test board on 2026-10-07.
    #
    # (jobId, startTime) is the stable pair: the property and the instant. The
    # create-skip key in connecteam_client keeps the title, deliberately -- there,
    # a Clean and a Turnover at one slot really are two different cards.
    return (str(shift.get("jobId") or ""), int(shift.get("startTime") or 0))


def ours(shift, our_titles=(), our_colors=()) -> bool:
    """Could this card have been made by us? Title AND colour must both match.

    THE SAFETY RAIL, and the reason a live market board can be promised that its
    existing cards are untouched. Austin's own cards are titled in Spanish --
    "sale no entran huespedes", "Vacia no entra gente" -- and carry no colour at
    all. Ours are titled exactly "Clean" or "Turnover" and always carry one of
    three colours. A card has to clear both tests, so a team card fails twice.

    Both, not either, deliberately. A title test alone would claim a team card
    that happened to be called Clean; a colour test alone would claim one they
    had tinted green themselves.
    """
    # createdBy WINS when the board gives it. It is the only field that records
    # who made the card, and nothing the team can do in the UI changes it -- so
    # it is right in both directions where title-and-colour was wrong in both:
    # it stops us claiming the ten team cards that happen to match our title,
    # colour and length, and it keeps a card of ours that somebody repainted.
    made_by_us = created_by_us(shift)
    if made_by_us is not None:
        return made_by_us

    # No createdBy on this read. Fall back to the old pair of tests, which is
    # all there was before 2026-10-13 and is still better than nothing.
    if our_titles and str(shift.get("title") or "") not in our_titles:
        return False
    if our_colors:
        have = str(shift.get("color") or "").upper()
        if have not in {str(c).upper() for c in our_colors}:
            return False
    return True


def is_assigned(shift) -> bool:
    """Is a named person on this card? Then it is READ-ONLY to us, for ever.

    Chris, 2026-10-12, after the client reported cleaners being sent to shifts
    nobody meant to give them: "Never, never do we update live, assigned
    tickets. This is read only for our purposes of preventing duplication."

    Two independent signals, either one enough. A card the team has published to
    somebody carries the user ids; a card that is no longer an open shift is
    somebody's even if the id list comes back empty from a partial read. Treating
    the weaker signal as "unassigned" is the mistake that would send a cleaner a
    shift they did not agree to, so absence of evidence is not taken as evidence
    of absence.
    """
    for key, value in (shift or {}).items():
        k = key.lower()
        if ("user" in k or "assign" in k) and value:
            return True
    if "isOpenShift" in (shift or {}) and not shift.get("isOpenShift"):
        return True
    return False


def day_of(card, default_tz="America/Chicago") -> str:
    """The card's own local calendar day, 'YYYY-MM-DD'.

    In the CARD'S zone, not the company's. A Savannah card at 11:00 Eastern is
    10:00 Central, and on a late-evening clean the two zones disagree about which
    DAY it is -- which would put the block and the team's own shift on different
    days and defeat the duplicate check entirely.
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo
    try:
        tz = ZoneInfo(str((card or {}).get("timezone") or "").strip() or default_tz)
    except Exception:  # noqa: BLE001 - an unknown zone must not break the check
        tz = ZoneInfo(default_tz)
    try:
        ts = int((card or {}).get("startTime") or 0)
    except (TypeError, ValueError):
        return ""
    if not ts:
        return ""
    return f"{datetime.fromtimestamp(ts, tz):%Y-%m-%d}"


def assigned_cover(board_cards) -> dict:
    """{(jobId, 'YYYY-MM-DD'): [assigned card, ...]} -- where NOT to create.

    Keyed on the JOB and the DAY, deliberately, not on the exact instant. The
    team's own card for 1208 Eastleigh on 12 October ran 15:00-18:00 while ours sat
    at 11:00-11:15: a slot key of (job, startTime) saw no collision at all, and
    the crew got the clean twice. The property and the date are what a human
    means by "this clean is already on the board".
    """
    cover: dict = {}
    for s in board_cards or ():
        if not is_assigned(s):
            continue
        jid = str(s.get("jobId") or "")
        day = day_of(s)
        if not jid or not day:
            continue
        cover.setdefault((jid, day), []).append(s)
    return cover


def plan(desired, board, card_map, our_titles=(), stale_color=None,
         our_colors=(), adopt=True):
    """What to do to the board. Pure, so every branch is testable offline.

    desired  : [(code, payload)] -- every card the sheet wants in the window
    board    : [shift] -- every card actually on the board in the window
    card_map : {code: [shift id, ...]} -- what an earlier run recorded

    Returns (updates, creates, greys, new_map, held):
      updates : [(shift, payload, changes)] -- move or recolour in place
      creates : [(code, payload)]           -- raise a new card
      greys   : [shift]                     -- ours, wanted by nobody: removed
      new_map : {code: [shift id, ...]}     -- to store for the next run
      held    : [(why, code, card_or_payload)] -- left alone because a person is
                on the card, or because an assigned card already covers that
                Job that day. Reported, never acted on.

    ASSIGNED CARDS ARE READ-ONLY. A card with a person on it is never updated,
    moved, recoloured, greyed or counted as free, and no block is created beside
    one. It stays in the map so the booking is still known to own it, which is
    what stops the next run creating the duplicate that rule 1 would otherwise
    cause by itself.

    A card is only GREYED when no booking claims it at all. That is the "removed"
    case, which should never happen because cancellations stay in the sheet. A
    calder booking does not come through here as a grey: it comes through as a
    desired card that is already grey, and so is a normal update.
    """
    by_id = {str(s.get("id")): s for s in board}
    claimed: set = set()
    held: list = []
    # Where an assigned card already covers a Job for a day. Built from the WHOLE
    # board, the team's cards included, because the point is to notice a clean
    # that is already somebody's whoever put it there.
    cover = assigned_cover(board)

    want_by_code: dict = {}
    for code, payload in desired:
        want_by_code.setdefault(code, []).append(({}, code, payload))

    # Cards an earlier run recorded against each code, still on the board.
    have_by_code: dict = {}
    frozen_codes: set = set()
    frozen_ids: dict = {}
    for code in want_by_code:
        for sid in card_map.get(code, ()):
            s = by_id.get(str(sid))
            if s is None or str(sid) in claimed:
                continue
            if is_assigned(s):
                # Somebody is on this card. It is not ours to change, and it is
                # not a free slot either: the booking HAS its card. Claimed so
                # nothing else adopts it, recorded so it stays in the map, and
                # the code is frozen so no replacement is created beside it.
                claimed.add(str(sid))
                frozen_codes.add(code)
                frozen_ids.setdefault(code, []).append(str(sid))
                held.append(("a person is on this card", code, s))
                continue
            have_by_code.setdefault(code, []).append(s)
            claimed.add(str(sid))

    # Adoption. A code with fewer recorded cards than it wants looks for its
    # cards where the old code would have put them.
    #
    # ONLY cards that could be ours are adoptable, by `ours` above. This is what
    # makes "a live market board's existing cards are not touched" a property of
    # the code and not an observation about today's data: every card that can be
    # MOVED, RECOLOURED or GREYED below comes from this dict, and this dict holds
    # only cards the map already recorded or cards that pass both tests.
    #
    # `adopt=False` turns this off entirely, and a MARKET board passes that. The
    # title test stopped separating us from the team on 2026-10-07, when Chris
    # asked for the green cards to read "sale no entran huespedes" -- which is
    # exactly what four of the team's own Austin cards say. That left colour as
    # the only thing telling us apart, and a crew tinting a card green would have
    # been enough to lose it.
    #
    # With adoption off, the ONLY cards a market board can modify are ones this
    # system created and recorded in the map itself. That is a harder guarantee
    # than the two tests ever gave, and it costs nothing: a card we did not
    # create is not ours to move, and create_shifts still skips its slot so
    # nothing is duplicated.
    by_slot: dict = {}
    for s in (board if adopt else []):
        if str(s.get("id")) in claimed:
            continue
        if not ours(s, our_titles, our_colors):
            continue
        by_slot.setdefault(slot_key(s), []).append(s)
    for code, wants in want_by_code.items():
        if len(have_by_code.get(code, ())) >= len(wants):
            continue
        for _r, _c, payload in wants:
            pool = by_slot.get(slot_key(payload)) or []
            while pool:
                s = pool.pop(0)
                if str(s.get("id")) in claimed:
                    continue
                have_by_code.setdefault(code, []).append(s)
                claimed.add(str(s.get("id")))
                break

    updates, creates, new_map = [], [], {}
    for code, wants in want_by_code.items():
        pairs, to_create, orphans = pair_by_code(wants, have_by_code.get(code, []))
        kept = []
        for (_r, _c, payload), have in pairs:
            diff = changes(payload, have)
            if diff:
                updates.append((have, payload, diff))
            kept.append(str(have.get("id")))
        for _r, _c, payload in to_create:
            # RULE: no block beside an assigned card. Either this booking's own
            # card is somebody's already, or somebody's card covers that Job on
            # that day -- a clean already on the board, whoever put it there.
            if code in frozen_codes:
                held.append(("this booking's card is assigned", code, payload))
                continue
            key = (str(payload.get("jobId") or ""), day_of(payload))
            if key[0] and key[1] and key in cover:
                held.append(
                    (f"an assigned card already covers this Job on {key[1]}",
                     code, payload))
                continue
            creates.append((code, payload))
        # An orphan here means the booking wants FEWER cards than it has, which
        # only happens if a combined listing stopped being combined. Reported by
        # being left out of the map rather than acted on; deleting a card for a
        # LIVE booking is not something to do on a guess.
        for have in orphans:
            kept.append(str(have.get("id")))
        # A frozen card stays recorded. Dropping it would make it a stray, and
        # the next run would create the duplicate all over again.
        for sid in frozen_ids.get(code, ()):
            if sid not in kept:
                kept.append(sid)
        if kept:
            new_map[code] = kept

    greys = []
    if stale_color:
        recorded = {str(v) for vs in card_map.values() for v in vs}
        for s in board:
            if str(s.get("id")) in claimed:
                continue
            if is_assigned(s):
                # Never repaint somebody's shift, whatever it looks like.
                held.append(("a person is on this card", "", s))
                continue
            if not ours(s, our_titles, our_colors):
                continue
            if not adopt and str(s.get("id")) not in recorded:
                # Adoption is off, so this card is not known to be ours. If we
                # will not MOVE an unrecognised card we must not REPAINT one
                # either -- the two have to answer to the same evidence.
                continue                  # the team's own card; not ours to touch
            if str(s.get("color") or "").upper() == stale_color.upper():
                continue                  # already grey
            greys.append(s)
    return updates, creates, greys, new_map, held


# ------------------------------------------------------- where the map lives

def orphans(board_cards, card_map, our_titles=(), our_colors=(),
            card_seconds=None) -> list:
    """Cards that look like OURS but are in no map entry. A lost-map alarm.

    On a market board adoption is off, so a card we cannot find in the map can
    never be moved, recoloured or greyed again -- and create_shifts will skip its
    slot as occupied, so nothing is created either. The card simply stays as it
    is for ever, and a calder booking keeps a green card.

    That is a silent failure, which is the only kind worth building an alarm for.
    It became possible on 2026-10-08, when turning adoption off for market boards
    made the system depend on one JSON object in a bucket. This is the check that
    turns losing it into a visible problem rather than a frozen board.
    """
    recorded = {str(v) for vs in (card_map or {}).values() for v in vs}
    out = []
    for s in board_cards:
        if not ours(s, our_titles, our_colors):
            continue
        if card_seconds is not None and created_by_us(s) is not True:
            # The length test, same as the map rebuild uses. Without it the alarm
            # counted the TEAM'S lookalikes -- they write our titles and some of
            # their cards carry our colours -- and reported 40 strays on a board
            # with 3. An alarm that is wrong most of the time is an alarm nobody
            # reads, which is worse than not having one.
            span = int(s.get("endTime") or 0) - int(s.get("startTime") or 0)
            if span != card_seconds:
                continue
        if str(s.get("id")) not in recorded:
            out.append(s)
    return out


def city_slug(city: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in str(city).lower()).strip("-")


def map_name(board: str, city: str) -> str:
    """The state-bucket object holding {code -> card ids} for one board AND CITY.

    PER CITY, not per board, and that is not tidiness -- it is the difference
    between a correct push and wiping a market.
    //
    Two markets share a board: Savannah with Thunderbolt, New Quintrell with Net
    St. Louis. A push knows only its own city's bookings, so every card the map
    holds for the OTHER city is unclaimed, and the stale pass reads unclaimed as
    "no booking wants this any more" and greys it. With one map per board, a
    Thunderbolt push -- nine cards -- planned to grey 300 Savannah cards on
    2026-10-11, which would have told the crews 300 real cleans were calder.
    Caught in a preview.
    """
    return f"connecteam/cards-{board}-{city_slug(city)}.json"


def read_map(store, board: str, city: str):
    """({code: [id, ...]}, generation). ({}, 0) when there is nothing yet.

    The map is a HINT, never an authority. Every id it returns is checked against
    the board before anything is done with it, because a card deleted in the
    Connecteam UI leaves the map stale and nothing tells us. An unreadable map is
    not fatal either: the plan falls back to adopting cards by their slot, which
    is how the quillon run after this change behaves anyway.
    """
    import json
    if store is None:
        return {}, 0
    try:
        raw, gen = store.read(map_name(board, city))
    except Exception as e:  # noqa: BLE001 - a missing map must not stop the push
        print(f"   (could not read the card map: {e}; falling back to slots)")
        return {}, 0
    if not raw:
        return {}, 0
    try:
        got = json.loads(raw)
    except ValueError as e:
        print(f"   (card map is not valid JSON: {e}; falling back to slots)")
        return {}, 0
    if not isinstance(got, dict):
        return {}, 0
    return {str(k): [str(v) for v in (vs or [])] for k, vs in got.items()}, gen


def write_map(store, board: str, mapping: dict, generation: int,
              city: str) -> bool:
    """Store the map, and a dated copy. False if the live one could not be
    written, which is not fatal.

    The dated copy is never overwritten. The live map is one object rewritten
    every run, so without an archive a single bad write would be the end of the
    only record linking bookings to cards -- and on a market board that record is
    the ONLY thing that lets a card be corrected afterwards.
    """
    import json
    from datetime import datetime, timezone
    if store is None:
        return False
    payload = json.dumps(mapping, indent=1, sort_keys=True).encode("utf-8")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    try:
        store.write(f"connecteam/cards-{board}-{city_slug(city)}-{stamp}.json",
                    payload, if_generation_match=0)
    except Exception as e:  # noqa: BLE001 - the live map still matters more
        print(f"   (could not archive the card map: {e})")
    try:
        store.write(map_name(board, city), payload,
                    if_generation_match=generation)
    except Exception as e:  # noqa: BLE001 - losing the map costs a slot-adoption
        print(f"   (could not write the card map: {e}. The next run adopts cards "
              f"by their slot instead, which is slower to reason about but "
              f"correct.)")
        return False
    return True


# ----------------------------------------------- a snapshot, so undo is cheap

SNAPSHOT_DIR = "connecteam/snapshots"


def snapshot_names(board: str, when) -> tuple:
    """(dated archive, latest pointer) for one board's before-snapshot.

    Two objects, not one. The dated copy is the record -- never overwritten, so a
    run from three days ago is still recoverable. The `latest` copy is what an
    undo reaches for by default, because the thing somebody wants to undo at 6am
    is almost always the last thing that happened.

    Two rather than a listing because the object store here does read and write
    and nothing else, and adding a list operation to it to support a convenience
    is the wrong trade.
    """
    stamp = when.strftime("%Y%m%d-%H%M%S")
    return (f"{SNAPSHOT_DIR}/{board}-{stamp}.json",
            f"{SNAPSHOT_DIR}/{board}-latest.json")


def write_snapshot(store, board: str, shifts, meta: dict, when=None) -> str:
    """Record the board EXACTLY as it is, before anything is written to it.

    Returns the dated object's name, or "" if it could not be stored.

    Called before the quillon write of a live run, never on a preview. The whole
    point is to be able to answer "what did it look like before?" without reading
    a log and squinting, so it holds the cards whole rather than a summary -- a
    summary cannot be restored from.
    """
    import json
    from datetime import datetime, timezone
    if store is None:
        return ""
    when = when or datetime.now(timezone.utc)
    dated, latest = snapshot_names(board, when)
    body = {"board": str(board), "taken": when.isoformat(),
            "count": len(shifts), **meta, "shifts": list(shifts)}
    payload = json.dumps(body, indent=1, sort_keys=True, default=str).encode("utf-8")
    try:
        store.write(dated, payload, if_generation_match=0)
    except Exception as e:  # noqa: BLE001 - see below; this must not block a write
        print(f"   (could not store the before-snapshot: {e})")
        return ""
    try:
        _old, gen = store.read(latest)
        store.write(latest, payload, if_generation_match=gen)
    except Exception as e:  # noqa: BLE001
        print(f"   (snapshot stored as {dated} but the 'latest' pointer did not "
              f"move: {e})")
    return dated


def read_snapshot(store, board: str, name: str = ""):
    """The snapshot to undo from. `name` defaults to this board's latest."""
    import json
    if store is None:
        return None
    want = name or snapshot_names(board, __import__("datetime").datetime.now())[1]
    raw, _gen = store.read(want)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None
