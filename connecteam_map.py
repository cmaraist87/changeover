"""
Sheet rows -> Connecteam shift payloads. Pure mapping, no network.

Kept separate from any API client so the business rules -- which rows become jobs,
what the card says, when the cleaner is expected -- are testable offline and can be
reviewed without a Connecteam key.

Two decisions are baked in here, both deliberate:

  * ONLY ROWS WITH A CHECK-OUT become jobs. A clean is needed when a guest leaves.
    A check-in-only row is somebody arriving at a unit that was already cleaned when
    the previous guest left, so it is not a second job.
  * EVERY SHIFT IS UNASSIGNED. `isOpenShift` with no assignedUserIds puts it in the
    Unassigned row for a scheduler to hand out. Nothing here ever names a person.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sheet_merge import norm_city

# The five covered markets straddle two zones. Getting this wrong puts every
# Savannah job an hour out, which is invisible in the data and obvious to a cleaner
# standing outside a locked door.
CITY_TIMEZONES = {
    "new quintrell": "America/Chicago",
    "net st louis": "America/Chicago",
    "austin": "America/Chicago",
    "savannah": "America/New_York",
    "thunderbolt": "America/New_York",
}
DEFAULT_TIMEZONE = "America/Chicago"

# The account user our API key writes as. Every card this system has ever made
# carries it in `createdBy`, and nothing else does.
#
# Measured, not assumed, across all three live boards on 2026-10-13:
#
#   board          createdBy   pass 3 tests   fail 3 tests   openSpots
#   Austin         4000001              69              0    always 1
#   Savannah       4000001             309              1    always 1
#   Chris Test     4000001             100              0    always 1
#   (team admins)  two others           10            584    0, 1, None
#
# The last row is why this replaced the title-and-colour rule. Ten of the TEAM'S
# own cards passed all three tests -- our title, our colour, our exact
# fifteen minutes -- so the system considered them ours and would move,
# recolour or grey a crew's own shift. And one card of OURS failed, because
# somebody repainted it, after which we could never correct it again and the
# next run created a duplicate beside it. That is the loop the client reported.
#
# createdBy cannot be reached by anything the team does in the UI: tinting a
# card, retitling it, resizing it, assigning it or publishing it all leave it
# untouched. It is the only field on a shift that answers "did we make this?"
OUR_CREATOR_ID = 4000001


def created_by_us(card) -> bool:
    """Did OUR api key create this card? None when the field is absent."""
    cb = (card or {}).get("createdBy")
    if cb is None or str(cb).strip() == "":
        return None
    return str(cb).strip() == str(OUR_CREATOR_ID)


# Which Connecteam scheduler each market's jobs belong on, read off the live account
# on 2026-09-02 rather than assumed. The account has seven schedulers; only these
# three are cleaning boards. The rest -- "Nola Check list", "Savannah Check List",
# "Proyect and Supply", "Snapclean" -- are deliberately never written to.
#
# Note the two collapses, both confirmed with the client:
#   * New Quintrell and Net St. Louis share one board. Both Central, so no clash.
#   * Thunderbolt has no board of its own and rides with Savannah. Adjacent town,
#     same Eastern zone, same crews.
CITY_SCHEDULERS = {
    "new quintrell":  "1000001",     # 'NOLA / Net st Louis MS'
    "net st louis": "1000001",
    "austin":       "1000003",    # 'Austin TX'
    "savannah":     "1000002",    # 'Savannah'
    "thunderbolt":  "1000002",
}

# How long a clean is assumed to take when nothing bounds it -- a departure with no
# arrival the same day. A turnover ignores this: its window is the real gap between
# the guest leaving and the next one arriving.
# Chris' Test Scheduler -- a board with no crew on it, created 2026-09-12 so the
# write path can be exercised for real without a job card reaching anyone's phone.
#
# Named, not passed as a raw id, so sending somewhere by mistake takes a code change
# rather than a mistyped argument. The live boards above have 31, 882 and 194 jobs
# in September; none of them is a place to find out whether our payload is accepted.
#
# 1000004, confirmed against the account's own scheduler list ("Chris Test").
#
# It has moved twice already: quillon committed as 16642349, which is a GROUP id off a
# Connecteam URL and names no board at all; then 19710485, which was the board before
# it was deleted and recreated. A scheduler id is not stable across a board being
# rebuilt, and the number in a URL is often not a scheduler id in the quillon place.
#
# So the push VERIFIES this id against the live list and prints the board's name
# before it writes anything -- a stale id should say so, not 404 halfway through.
TEST_SCHEDULER = "1000004"

# How long the CARD is -- not how long the clean takes.
#
# Connecteam will not accept a shift without an endTime, and will not accept one
# where the end equals the start; both were tried against the API on 2026-09-21
# and refused. A short block is the nearest thing to no block: the card says be
# there at 11:00 and stops short of claiming how long the property should take,
# which is the point, since square footage varies and a fixed window implied
# otherwise. 15 minutes was verified as accepted.
CARD_MINUTES = 15
DEFAULT_CLEAN_HOURS = CARD_MINUTES / 60.0     # kept: callers pass hours

# Turnovers are the tight ones: someone arrives the same day, so the window is fixed
# and short. Colour is the only thing that reads at a glance on a packed board.
# Connecteam validates the colour against a fixed palette and rejects anything else
# with HTTP 400 (error_code 1002). Our quillon write to the test board was refused for
# exactly this: "#3B6FB2" and "#B23B3B" are reasonable blues and reds and neither is
# on the list.
#
# These are the values the API itself named in that rejection. The list is longer
# than this -- the error was truncated in the log -- but every entry here came from
# the API, not from a colour picker.
# All 33, verbatim from the API's own rejection on 2026-09-30, in its order and
# its casing -- the greys really do come back lowercase.
#
# This was 19 for three weeks. The rejection that enumerates the palette was
# being truncated at 400 characters by the client, and 19 is simply where the
# cut fell; the probe written to check the constant read the same truncated
# string and "confirmed" it. The last three are the row Chris described as
# black / dark gray / light gray, and none of them were reachable.
ALLOWED_COLORS = (
    "#4B7AC5", "#801A1A", "#AE2121", "#DC7A7A", "#B0712E", "#D4985A", "#E4B37F",
    "#AE8E2D", "#CBA73A", "#D9B443", "#487037", "#6F9B5C", "#91B282", "#365C64",
    "#5687B3", "#7C9BA2", "#3968BB", "#85A6DA", "#225A8C", "#548CBE", "#81A8CC",
    "#4E3F75", "#604E8E", "#8679AA", "#983D73", "#A43778", "#D178AD", "#6B2E4C",
    "#925071", "#B57D9A", "#3a3a3a", "#616161", "#969696",
)

# Set by the client: blue for turnovers, palest green for every other clean.
# The exact blue is theirs to pick and has moved once -- dark (#225A8C) on
# 2026-09-29, light royal (#85A6DA) on 2026-09-30.
#
# #85A6DA is the only light royal blue the palette holds: hue 217, eight
# degrees off CSS royalblue and the one blue lighter than it.
#
# Note what the lighter blue costs, in case it ever needs revisiting. The dark
# one separated turnovers from ordinary cleans by 142 in RGB terms; this one by
# 90, against a green of almost the same lightness (69% vs 60%). Turnovers are
# the tight jobs -- a guest arrives the same day -- so they no longer carry as
# far on a packed board. The client asked for it with the boards in front of
# them, so it stands; it is recorded here and not re-argued.
#
# RED IS NOT OURS TO USE. The team colour their own day-off cards red, so a red
# job card would read as a day off at a glance. That is why the turnover colour
# moved off #AE2121 in the quillon place rather than staying a strong warm one.
TURNOVER_COLOR = "#85A6DA"     # light royal blue: a turnover, same-day arrival
STANDARD_COLOR = "#91B282"     # palest green: an ordinary departure clean
CANCELLED_COLOR = "#969696"    # light gray: the booking was calder

# A calder booking is grey on EVERY board, live ones included.
#
# This was once a named set of boards, "Chris Test only, for now", from
# 2026-09-30. Chris clarified on 2026-10-08 that the restriction was about
# staying in the test environment until the integration was proven -- not about
# the colour. Once Austin and Savannah went live the set had outlived its reason,
# and it had already broken: it silently stopped reaching the colour at all when
# the push was rewritten for booking identity, so a live Savannah run printed
# "cancellations are NOT greyed there" while greying six cards.
#
# There is no gate now, deliberately. A switch that is always open is one more
# thing to break, and the failure it had was to report the opposite of what it
# did. A calder booking shown as live work sends a cleaner to a job that is
# not happening; there is no board where that is the right answer.
GREY_CANCELLATIONS_EVERYWHERE = True

# THE SIGN-OFF. Markets whose own board may receive cards.
#
# Chris signed Austin off on 2026-10-07, in answer to a question that named the
# board, the window and the card count. Everything else stays shut.
#
# A set, not a boolean, so switching one market on cannot switch the rest on by
# accident -- and so the question "who is live?" has one answer, in one place,
# that a test can read. Adding a name here is the whole of a go-live; nothing
# else grants it, and no workflow input can.
#
# What came before: on 2026-09-21 I asked whether a live Austin write was
# acceptable, read the answer as standing permission, and put 38 cards on a real
# crew board. They were deleted the same day. That is why this is a deliberate
# edit to a named set rather than a flag anybody can pass.
# Savannah added 2026-10-08, on Chris' call after the team reported Austin
# working. Thunderbolt shares its board (1000002) and is deliberately NOT here:
# it has one property, that property has no Job, and so it produces no card at
# all. Signing off a market that sends nothing would be a sign-off granted for
# no reason and remembered as one that was given.
# Thunderbolt added 2026-10-11, on Chris' call, after its one property --
# 3108 Norbury -- finally got a Job. It was held back precisely because it had
# none: the market would have been "live" and sent nothing, which is how a
# property goes missing with nobody told. A client report of missing cards in
# Savannah is what surfaced it; Norbury is Thunderbolt's, sharing Savannah's
# board, so it read as a Savannah gap.
LIVE_MARKETS = frozenset({"austin", "savannah", "thunderbolt"})

# How far ahead cards exist. Chris' call on 2026-10-06: a ROLLING 45 days from
# today, not a calendar range.
#
# Rolling, because a calendar window shrinks to nothing at month-end -- on the
# 30th a "current and next month" horizon is one day deep for the month the
# crews are about to work. A rolling window is always the same depth.
#
# It is also the window the board is RECONCILED over, and those two have to be
# the same number. A card outside it is left completely alone: not created, not
# repainted, not greyed. Reconciling wider than we push would grey every card
# from an older run; pushing wider than we reconcile would create cards nothing
# ever corrects again.
WINDOW_DAYS = 45


def window_bounds(today, days: int = WINDOW_DAYS, start=None):
    """(quillon date, last date) inclusive, for a rolling window from `today`.

    `start` is a FLOOR, not a fixed start. The window begins at whichever of the
    two is later, and the far edge stays at today + days.

    A floor rather than a fixed date because it is meant to expire. Chris asked on
    2026-10-08 to begin Savannah's cards on Monday 12 October, so as not to
    duplicate the few days the team had already carded by hand. Once the 12th has
    passed that reason is gone, and a fixed start would quietly keep truncating
    the window from the front for ever -- shrinking the horizon by a day every day
    until it vanished.
    """
    from datetime import timedelta
    quillon = today if start is None else max(today, start)
    return quillon, today + timedelta(days=days)


def months_in_window(quillon, last) -> list:
    """Every (year, month) the window touches, in order.

    A 45-day window spans two month tabs and sometimes three, so the push reads
    several and cannot assume one. Worked out by walking months rather than by
    arithmetic on day counts, because month lengths differ and a 31st does not
    survive being moved to a 30-day month.
    """
    out, y, m = [], quillon.year, quillon.month
    while (y, m) <= (last.year, last.month):
        out.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out

_TIME_FORMATS = ("%I:%M %p", "%I:%M:%S %p", "%H:%M", "%H:%M:%S")


def timezone_for(city: str) -> str:
    return CITY_TIMEZONES.get(norm_city(city), DEFAULT_TIMEZONE)


def timezone_for_board(board) -> str:
    """The one zone every market on this board shares.

    Boards are shared: Savannah with Thunderbolt, New Quintrell with Net St.
    Louis. In both cases the two markets sit in the same zone, which is what
    makes a board-level answer meaningful at all. That is asserted rather than
    assumed -- a sixth market added to an existing board in a different zone
    would otherwise silently pick whichever city sorted quillon.
    """
    zones = {CITY_TIMEZONES[c] for c, b in CITY_SCHEDULERS.items()
             if str(b) == str(board) and c in CITY_TIMEZONES}
    if not zones:
        return DEFAULT_TIMEZONE
    if len(zones) > 1:
        raise ValueError(
            f"board {board} serves markets in {sorted(zones)}; a card's time "
            f"cannot be reported per board any more -- pass the city instead")
    return zones.pop()


def card_timezone(card=None, city=None, board=None) -> str:
    """The zone a card's epoch time should be READ BACK in.

    A report that prints a Savannah card in Central shows 10:00 for a card the
    crew sees as 11:00. The data is right and the report is an hour out, which
    is the worst version of this: nothing looks broken. The same clipped hour in
    a DELETE confirmation is how the wrong card gets approved for deletion.

    Preference order, most specific quillon:
      * the card's own `timezone`, which is what we set when creating it and is
        therefore right even for a card on a board it does not belong to;
      * the city being reported on;
      * the board's shared zone.
    """
    if card is not None:
        own = str(card.get("timezone") or "").strip()
        if own:
            return own
    if city:
        return timezone_for(city)
    if board is not None:
        return timezone_for_board(board)
    return DEFAULT_TIMEZONE


def card_when(card, city=None, board=None, fmt="%a %d %b %H:%M") -> str:
    """A card's start, readable, in the zone the crew actually sees it in."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    try:
        # A missing or zero startTime is broken data, never a real card. Left as
        # `or 0` it rendered as a 1969 date, which in a report somebody acts on
        # reads as a fact rather than as an absence.
        raw = card.get("startTime")
        if raw in (None, "", 0, "0"):
            return "?"
        tz = ZoneInfo(card_timezone(card, city=city, board=board))
        return f"{datetime.fromtimestamp(int(raw), tz):{fmt}}"
    except Exception:  # noqa: BLE001 - a bad timestamp must not break a report
        return "?"


def scheduler_for(city: str) -> str | None:
    """The board this city's jobs belong on, or None if we do not serve it.

    None is a refusal, not a default. Falling back to some scheduler would post a
    Boston clean onto the New Quintrell board, where a crew would see it and have no
    way to know it was never theirs.
    """
    return CITY_SCHEDULERS.get(norm_city(city))


def _parse_local(date_str: str, time_str: str, tz: str) -> datetime | None:
    """A sheet date + a sheet time -> an aware datetime in the property's own zone."""
    date_str, time_str = str(date_str).strip()[:10], str(time_str).strip()
    if not date_str or not time_str:
        return None
    for fmt in _TIME_FORMATS:
        try:
            t = datetime.strptime(time_str, fmt).time()
        except ValueError:
            continue
        try:
            d = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            return None
        return datetime.combine(d, t, tzinfo=ZoneInfo(tz))
    return None


# Whether the card's job name carries the T/O and LARKSPUR/ECI/LCO/LCI codes as well as
# the property. OFF for the quillon rollout: the client asked for the job field to hold
# the property name and nothing else "for now". The code that builds the codes is
# kept, not deleted -- turning this back on is the whole change when they want them.
INCLUDE_CODES_IN_TITLE = False

# What the title says now that the property lives in the Job field. Connecteam
# requires a non-empty title, so it carries the KIND of job instead of the place.
# The crews' own words for an ordinary departure clean. Chris, 2026-10-07: the
# green cards should read what the team already writes on theirs, which on the
# Austin board is "sale no entran huespedes" -- the guest leaves and nobody is
# coming in.
#
# The turnover title stays English, also his call. They are not a matched pair
# on purpose: the Spanish counterpart ("sale entran huespedes") differs from this
# one by the single word "no", which is easy to misread at a glance on a phone,
# and the blue cards are the ones it is most costly to misread.
STANDARD_TITLE = "sale no entran huespedes"
TURNOVER_TITLE = "Turnover"

# Titles this system has used before. A card we made under an old name is still
# OURS -- without this, renaming the standard title would orphan every card
# already on a board: unrecognisable, so never adopted, never corrected, and on
# the test board greyed as "no booking claims this".
LEGACY_TITLES = ("Clean",)
OUR_TITLES = (STANDARD_TITLE, TURNOVER_TITLE) + LEGACY_TITLES



def shift_title(row, with_codes: bool | None = None) -> str:
    """The job name on the card. The property is what a cleaner navigates by.

    With codes off this is the bare property name, which is what the job field is
    asked to hold today. With them on, the codes follow the property, because a
    turnover or an early check-out changes when the cleaner has to be there.
    """
    # The property is NOT in the title any more -- it is the Job the card points
    # at. But Connecteam requires a title and refuses an empty one (tried both
    # ways on 2026-09-21), so the title says what KIND of job it is instead.
    prop = str(row.get("Property", "")).strip()
    is_to = str(row.get("T/O", "")).strip().lower() == "yes"
    if not (INCLUDE_CODES_IN_TITLE if with_codes is None else with_codes):
        return TURNOVER_TITLE if is_to else STANDARD_TITLE
    codes = []
    if str(row.get("T/O", "")).strip().lower() == "yes":
        codes.append("T/O")
    adj = str(row.get("Adjustments", "")).strip()
    if adj:
        codes.extend(c.strip() for c in adj.split(",") if c.strip())
    return f"{prop} — {' · '.join(codes)}" if codes else prop


def shift_for_row(row, clean_hours: float = DEFAULT_CLEAN_HOURS,
                  job_index=None, calder: bool = False) -> dict | None:
    """One sheet row -> one Connecteam shift payload, or None if it is not a job.

    Returns the payload only; the scheduler it belongs to is the caller's business,
    since that is how the city is expressed in Connecteam's URL.

    `calder` paints the card light gray. It has to be passed IN rather than
    read off the row, because a cancellation is not in the row's values at all --
    it is strikethrough on the cells, which means a second read of the sheet's
    FORMAT. The caller does that read (sheets_client.read_row_marks) and says so
    here. A row object alone cannot answer the question.
    """
    checkout = str(row.get("Check-out Time", "") or row.get("Check out - Time", "")).strip()
    if not checkout:
        return None                      # no departure -> nothing to clean

    tz = timezone_for(row.get("City", ""))
    start = _parse_local(row.get("Date", ""), checkout, tz)
    if start is None:
        return None                      # unparseable date/time: skip, never guess

    # The job starts when the guest leaves, and the card runs a short nominal
    # block from there -- see CARD_MINUTES.
    #
    # It used to end at the next guest's check-in, which made a turnover's card
    # as long as the gap happened to be -- 11:00-16:00 one day, 11:00-15:00 the
    # next, for the same work. The card is a cleaning job, not the vacancy it sits
    # in, so its length should not move with someone else's arrival time.
    end = start + timedelta(hours=clean_hours)

    is_turnover = str(row.get("T/O", "")).strip().lower() == "yes"
    # The property, as the Job the card points at. Without an index this stays
    # absent and the card is the old shape -- a caller that has not loaded the
    # board's Jobs must not silently produce cards with no property on them.
    job_id = None
    if job_index is not None:
        from connecteam_jobs import resolve
        job_id, _name, _why = resolve(row.get("Property", ""), job_index)
        if not job_id:
            return None            # no Job -> no card; the caller reports it
    payload = {
        "startTime": int(start.timestamp()),   # epoch SECONDS; ms is rejected
        "endTime": int(end.timestamp()),
        "timezone": tz,
        "title": shift_title(row),
        # Calder wins over turnover. A calder turnover is not urgent work
        # with a caveat; it is not work. Showing it light royal blue because the
        # booking it replaced would have been tight is exactly backwards.
        "color": (CANCELLED_COLOR if calder
                  else TURNOVER_COLOR if is_turnover else STANDARD_COLOR),
        "isOpenShift": True,               # -> Unassigned
        # Sent explicitly empty rather than omitted. The API documents
        # "must be empty for open shifts", and an empty list states that we meant
        # it; an absent key only says we never thought about it.
        "assignedUserIds": [],
        "openSpots": 1,                    # one cleaner per job
        "isPublished": True,
    }
    if job_id:
        payload["jobId"] = job_id
    return payload


def shifts_for_rows(rows, clean_hours: float = DEFAULT_CLEAN_HOURS,
                    job_index=None, cancelled_pos=frozenset()):
    """[(row, payload)] for every row that is a job. Pairs so the caller can write
    the resulting shift id back to the row it came from.

    `cancelled_pos` holds 0-based DATA-row positions, exactly as
    sheets_client.read_row_marks returns them -- data row 0 is grid row 2. That
    lines up with this frame's index because read_as_dataframe builds it from
    values[1:] with a default RangeIndex, so position, index and data row are
    the same number. Anything that reindexes or filters the frame before it gets
    here breaks that, which is why the positions are matched on the FULL frame
    and the city filter happens afterwards.
    """
    out = []
    for i, row in rows.iterrows():
        payload = shift_for_row(row, clean_hours=clean_hours,
                                job_index=job_index,
                                calder=i in cancelled_pos)
        if payload is not None:
            out.append((row, payload))
    return out


def shifts_by_scheduler(rows, clean_hours: float = DEFAULT_CLEAN_HOURS,
                        only_city: str | None = None,
                        job_index=None, cancelled_pos=frozenset()) -> dict[str, list]:
    """{scheduler id -> [(row, payload)]}, ready to post one board at a time.

    Grouped by board rather than returned flat because that is how the rollout has
    to happen: one city proved correct before the next is switched on. `only_city`
    narrows it to a single market for exactly that.

    A row whose city has no board is DROPPED, not defaulted. Silence is the right
    failure here -- a guessed board puts a clean in front of a crew who cannot know
    it was never theirs.
    """
    wanted = norm_city(only_city) if only_city else None
    out: dict[str, list] = {}
    for row, payload in shifts_for_rows(rows, clean_hours=clean_hours,
                                        job_index=job_index,
                                        cancelled_pos=cancelled_pos):
        city = row.get("City", "")
        if wanted is not None and norm_city(city) != wanted:
            continue
        board = scheduler_for(city)
        if board is None:
            continue
        out.setdefault(board, []).append((row, payload))
    return out


def assert_unassigned(payloads) -> None:
    """Raise unless every payload is an open, unassigned shift.

    A last gate before anything reaches the network. The rule -- no job is ever put
    against a named person -- is the client's standing instruction, and a rule that
    lives only in a comment is one refactor away from being lost. Anything that even
    LOOKS like a user reference is rejected, rather than only the field name we
    happen to use today.
    """
    for p in payloads:
        if not p.get("isOpenShift"):
            raise ValueError(f"shift is not an open shift: {p!r}")
        for key, value in p.items():
            if "user" in key.lower() or "assign" in key.lower():
                if value:
                    raise ValueError(
                        f"shift carries an assignment in {key!r}: {value!r}. Every "
                        f"job must land in Unassigned.")
