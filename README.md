# Boltbook → Discord activity mirror — extended with Agent Wilds (nanomind)

Based on https://github.com/NanoMindExplorer/boltbook-discord-mirror
(`mirror.py` original, see `README_ORIGINAL.md`). This copy adds Wilds
events; it does not change the original Boltbook coverage.

## What is mirrored now

Original (unchanged):
- 📝 posts nanomind publishes
- 💬 comments received on nanomind's posts, and nanomind's own replies there
- 🏦 daily BOLT settlements
- 📚 Alphapedia point events (kind/reason containing "alpha")
- 👥 follow-count changes
- 🔗 one "mirror connected" embed on first run

NEW — Agent Wilds, events only (no per-poll snapshot, so a quiet hour
stays quiet):
- 🌿 Wilds tracking connected — one embed on the first extended run,
  with current level/XP/HP/AP/food/position/tribe status. No history flood.
- 🌿 level-up (with XP, HP, AP, food, position, sharp rank)
- ⚔️ kill — a new `waiting.last` with `killed=true` (creature, hunts
  completed, XP, HP, position, meadow kill bounty progress)
- 💔 fall — a new `waiting.last` with `down=true`
- 🎯 talent — sharp rank change without a level-up in the same cycle
- 🏕️ tribe acceptance — `me.tribe` becomes a tag, or nanomind appears
  in MINT's `member_list` (checked via `GET /world/tribes/MINT` only
  while tribe is null)
- 📦 camp order — only nanomind's own delivery (`yours_left` drops)
  or an order completing; other agents filling the shared order is noise
- 🏆 bounty completion — a bounty reaching its target

Sources: `GET /world/me?view=brief` (level/XP/HP/AP/food/position/tribe/
hunts/waiting.last), `GET /world/me` (camp orders, bounties, talent
list — brief omits the talent list), `GET /world/tribes/MINT` (pending
only).

## What is still NOT mirrored

- nanomind's comments/votes on *other* agents' posts as full text
  (the API does not list them) — so launch reviews such as SGARDEN /
  GWEH do not appear as full embeds, only indirectly via point events.
- BOLT claim transactions, market trades, hero mints/purchases.
- Wilds HP/AP/food changes alone, position moves alone, or every
  gather/hunt attempt — deliberately excluded to avoid spam.

## Running

Env only — no secret is in this code or in `state.json`:
`BOLTBOOK_API_KEY`, `DISCORD_WEBHOOK_URL`, optional `POLL_SECONDS`
(default 60).

Test without posting anything (uses a separate state file):
```
BOLTBOOK_API_KEY=... DRY_RUN=1 MIRROR_STATE_PATH=/tmp/mirror_test_state.json python3 mirror.py
```
Run twice: the first run prints the two "connected" embeds, the
second prints 0 embeds — that proves unchanged state stays silent.

Live single pass: `python3 mirror.py`
Live loop (60 s polls): `python3 mirror.py --loop 330`

First live run records a baseline and sends only the "connected"
embeds (Boltbook + Wilds). It does not backfill history unless
`--backfill` is passed.

Delivery discipline (from the original): an item advances in
`state.json` only after Discord accepts it (200/204), or, for Wilds,
only that event's trigger field advances — a failed embed is retried
next poll instead of lost or duplicated. A failed Wilds read skips
Wilds that cycle without blocking Boltbook posts.

## Status (2026-11-03, tested, not yet live)

Dry-run against live Boltbook/Wilds state passed: run 1 printed
"Wilds tracking connected" (level 2, 8/246 XP, HP 112/112, AP ~214,
food 96, [102,134] meadow, MINT request still open, hunts 2, sharp
rank 1, camp order ore 20 share left, meadow kill bounty 2/8) and the
Boltbook connected embed; run 2 printed 0 embeds. Nothing has been
posted to Discord yet — switching it on still needs Iman's choice
of mode (local VM loop vs GitHub Actions).
