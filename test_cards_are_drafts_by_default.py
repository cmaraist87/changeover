"""A card we create lands in the DRAFT layer unless somebody asks otherwise.

Run: python test_cards_are_drafts_by_default.py

Chris, 2026-10-13: "go with both" -- make draft the default for every write.

WHY THIS IS A SAFETY RULE AND NOT A PREFERENCE
----------------------------------------------
Our create payload carries isOpenShift true, openSpots 1 and (until now)
isPublished true. In Connecteam that combination is not a neutral placeholder:
a published open shift with a spot is an OFFER, advertised to eligible staff,
and a cleaner can take it. The statuses record on an assigned card shows
creatingUserId equal to assignedUserId -- the person created their own
assignment, which is a claim, not an admin handing work out.

That is how two cleaners received shifts nobody meant to give them. We never
wrote a person's name -- assert_unassigned makes that impossible -- but we
published a claimable invitation, and the duplicates meant extra invitations.

A draft card is invisible, so it cannot be claimed. The client publishes
deliberately once they have assigned the week, which is how they already work.
"""
import inspect
import io
import re
from pathlib import Path

import connecteam_push
from connecteam_client import ConnecteamClient

HERE = Path(__file__).parent
WORKFLOWS = HERE / ".github" / "workflows"


class Stub(ConnecteamClient):
    """A client that records what would be POSTed instead of sending it."""

    def __init__(self):            # noqa: D107 - deliberately no network
        self.posted = []
        self.on_board = []

    def existing_shifts(self, scheduler_id, start, end):
        return list(self.on_board)

    def _request(self, method, path, body=None, tries=4):
        self.posted.append((method, path, body))
        # echo the request back the way the API does, including isPublished
        return [dict(b) for b in (body or [])]


def _payload(start=1792162800):
    return {"jobId": "J1", "title": "Turnover", "startTime": start,
            "endTime": start + 900, "color": "#85A6DA",
            "timezone": "America/New_York", "isOpenShift": True,
            "assignedUserIds": [], "openSpots": 1, "isPublished": True}


def test_draft_posts_ispublished_false():
    c = Stub()
    c.create_shifts("B1", [_payload()], live=True, draft=True)
    assert c.posted, "nothing was posted"
    _m, _p, body = c.posted[-1]
    assert body[0]["isPublished"] is False, (
        "a draft create must send isPublished false, got %r"
        % (body[0].get("isPublished"),))
    print("OK: draft=True posts isPublished false")


def test_publishing_is_possible_but_must_be_asked_for():
    c = Stub()
    c.create_shifts("B1", [_payload()], live=True, draft=False)
    _m, _p, body = c.posted[-1]
    assert body[0]["isPublished"] is True, body[0].get("isPublished")
    print("OK: draft=False still publishes, for whoever explicitly wants it")


def test_the_draft_create_does_not_put_anybody_on_the_card():
    """Draft-ness must not have quietly loosened the standing rule."""
    c = Stub()
    c.create_shifts("B1", [_payload()], live=True, draft=True)
    _m, _p, body = c.posted[-1]
    assert body[0]["assignedUserIds"] == [], body[0]
    assert body[0]["isOpenShift"] is True, body[0]
    print("OK: a draft card is still an unassigned open shift")


def test_a_dishonoured_ispublished_is_reported_not_assumed():
    """An ignored field would publish every card while the log claimed
    otherwise. The create path reads isPublished off the RESPONSE."""

    class Liar(Stub):
        def _request(self, method, path, body=None, tries=4):
            self.posted.append((method, path, body))
            # the API ignores isPublished and publishes anyway
            return [dict(b, isPublished=True) for b in (body or [])]

    import contextlib
    out = io.StringIO()
    c = Liar()
    with contextlib.redirect_stdout(out):
        c.create_shifts("B1", [_payload()], live=True, draft=True)
    said = out.getvalue()
    assert "isPublished TRUE" in said or "did not honour" in said, (
        "a dishonoured isPublished was not reported: %r" % (said,))
    print("OK: an API that ignores isPublished is reported loudly")


def test_a_run_with_no_flags_creates_drafts():
    """The default itself. --publish is opt-in; everything else is draft."""
    src = inspect.getsource(connecteam_push)
    assert "draft=not args.publish" in src, (
        "the push no longer derives draft from --publish; the default may have "
        "silently flipped back to publishing")
    assert re.search(r'add_argument\(\s*"--publish"', src), src[:200]
    print("OK: the push creates drafts unless --publish is given")


def test_no_workflow_route_publishes():
    """A published card is claimable by a cleaner. Reaching that must take a
    code change, not a dropdown."""
    offenders = []
    for wf in sorted(WORKFLOWS.glob("*.yml")):
        for line in io.open(wf, encoding="utf-8").read().splitlines():
            bare = line.strip()
            if bare.startswith("#") or "connecteam_push.py" not in bare:
                continue
            if "--publish" in bare:
                offenders.append(f"{wf.name}: {bare}")
    assert not offenders, (
        "a workflow route publishes cards straight to a board: "
        + "; ".join(offenders))
    print("OK: no workflow route passes --publish")


if __name__ == "__main__":
    test_draft_posts_ispublished_false()
    test_publishing_is_possible_but_must_be_asked_for()
    test_the_draft_create_does_not_put_anybody_on_the_card()
    test_a_dishonoured_ispublished_is_reported_not_assumed()
    test_a_run_with_no_flags_creates_drafts()
    test_no_workflow_route_publishes()
    print("")
    print("ALL DRAFT-BY-DEFAULT TESTS PASSED")
