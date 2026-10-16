# Changeover

Turns short-term-rental bookings into reconciled cleaning shifts across five
markets. A nightly job pulls reservations from a property-management API, merges
them into the spreadsheet the crews actually work from, and keeps a workforce
scheduler in step — without ever overwriting work a human has already touched.

All data in this repository is synthetic. Property addresses, guest names and
booking references are generated; the structure they exercise is real.

---

## The problem

A cleaning company serving ~185 short-term rental units needs to know, every
day, which properties turn over tomorrow. The booking data lives in a
property-management system. The crews work from a Google Sheet they edit by
hand. The schedulers assign shifts in a separate workforce app.

Three systems, two of them edited by people, and nobody wants to retype
anything. The hard part is not moving the data — it is moving it *repeatedly*
without destroying what the humans did in between.

## What it does

```
property-management API  ──►  reservations
                                  │
                      normalise, expand combined listings,
                      resolve ~110 street abbreviations
                                  │
                                  ▼
                     Google Sheet  ◄── edited by hand daily
                     (merge, never overwrite)
                                  │
                                  ▼
                   workforce scheduler shift cards
                     (create · move · recolour · retire)
```

## The interesting parts

**Idempotent reconciliation, not append.** Running twice changes nothing.
A booking that moves keeps its own card and the card moves with it; a
cancellation recolours in place rather than being deleted and recreated. The
planner is a pure function — `plan()` in `connecteam_cards.py` returns what
*would* change, and every write path has a preview that must be read before
anything is sent.

**Identity is the whole problem.** A card belongs to a *booking*, not to a slot
in a calendar. Keying on (property, time) looked fine until a booking changed
time — then the system could not recognise its own card, greyed it as
cancelled, and created a duplicate beside it. Identity now hangs off the
booking's confirmation code, and `connecteam_cards.py` documents why each
alternative fails.

**Ownership is provenance, not resemblance.** Deciding "is this card ours?" by
title, colour and duration claimed ten of the crews' own shifts — cards they had
made that happened to match on all three. Meanwhile a card of ours that someone
recoloured stopped being recognised, so the next run duplicated it. Ownership
now reads the creating user id, which nothing in the UI can change. See
`OUR_CREATOR_ID` in `connecteam_map.py` for the measurements that forced the
change.

**Assigned work is read-only.** Once a named person is on a shift, the system
will not move, recolour, retire or delete it — and will not create a second
shift beside it either. Both halves are necessary: the first alone left a
booking's card untouchable, so the planner created the duplicate itself.
`test_assigned_cards_are_read_only.py` pins both.

**Nothing is published by default.** New shifts land in the scheduler's draft
layer. A published open shift is an *offer* the app advertises to staff, who can
claim it — so publishing straight to a board handed people work nobody had
assigned them. Draft is now the default and publishing takes an explicit flag no
automated route passes.

**Drift detection.** `board_watch.py` asks the question nothing else asked: does
the board still match the sheet? It fails only on conditions that are *silently*
wrong — a property with cleans and no job record, or a card the system can no
longer recognise — because an alarm that fires on normal conditions gets muted.

## Layout

| Area | Files |
|---|---|
| Ingest & normalise | `processing.py`, `guesty_client.py`, `guesty_adapter.py`, `listing_map.py` |
| Spreadsheet merge | `sheet_merge.py`, `sheets_client.py`, `sync.py` |
| Scheduler sync | `connecteam_cards.py` (pure planner), `connecteam_push.py` (orchestration), `connecteam_client.py` (API), `connecteam_map.py` (constants, windows, rules) |
| Safety & diagnostics | `board_watch.py`, `map_audit.py`, `card_lookup.py`, `card_restore.py`, `card_unpublish.py`, `watchdog.py` |
| Scheduled jobs | `.github/workflows/` |

## Tests

41 suites, run without a network or credentials:

```bash
for t in test_*.py; do python "$t" || echo "FAILED $t"; done
```

They are written as regression records. Most docstrings open with the incident
that produced the test — what broke, what it cost, and why the obvious fix was
wrong. `test_booking_identity.py`, `test_ownership_is_by_creator.py` and
`test_assigned_cards_are_read_only.py` are the ones worth reading first.

## Running it

Needs a property-management API credential, a Google service-account key and a
workforce-scheduler API key, all supplied as environment variables. `SETUP.md`
has the detail. Every write path defaults to a preview; `--live` is always
explicit, and market boards are gated behind a named allow-list in code rather
than a runtime flag.

The scheduled workflows are kept here as part of the record; GitHub Actions is
disabled on this repository, since it has no credentials and nothing to sync.
