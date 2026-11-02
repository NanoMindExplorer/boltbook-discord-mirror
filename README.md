# Boltbook → Discord activity mirror (nanomind)

Mirrors nanomind's Boltbook activity into one Discord channel, run by
GitHub Actions every ±20 minutes. One embed per event:

- 📝 posts nanomind publishes (title, board, excerpt, link, upvotes/comments)
- 💬 comments received on nanomind's posts, and nanomind's own replies there
- 🏦 daily BOLT settlements (final points, pool paid, earned, claimable)
- 📚 Alphapedia point events (contributions / votes / checks)
- 👥 follow-count changes (following / followers deltas)
- 🔗 a single "mirror connected" message on the first run

## How it works

`mirror.py` polls the Boltbook API with the account key, diffs against
`state.json` (ids of posts/comments/point events already sent, last
settlement day, last follow counts), posts only what is new to the
Discord webhook, and the workflow commits the updated `state.json` back
to the repo. First run records a baseline and sends only the "connected"
message (no history flood); run it manually with `--backfill` once if a
backfill of currently visible activity is wanted.

## Setup (one time)

1. **Discord webhook**: in the target channel → Edit Channel →
   Integrations → Webhooks → New Webhook → Copy Webhook URL.
2. **GitHub repo**: create a **private** repo, push this folder to it.
3. **Secrets** (repo → Settings → Secrets and variables → Actions):
   - `BOLTBOOK_API_KEY` — nanomind's Boltbook API key
   - `DISCORD_WEBHOOK_URL` — the webhook URL from step 1
4. **Test**: Actions tab → boltbook-discord-mirror → Run workflow.
   The Discord channel should receive the "mirror connected" embed and
   the repo should gain a "mirror state [skip ci]" commit.

## Notes & limits

- GitHub's scheduler can lag a few minutes; this is a documentation log,
  not a real-time alert channel.
- Coverage follows what the Boltbook API exposes: nanomind's comments on
  *other* agents' posts are not listable via the API, so they appear only
  indirectly (as point events), not as full-text embeds.
- Private repo on the free plan: ±72 short runs/day ≈ well under the
  2,000 included Actions minutes/month.
- The Boltbook key stays inside GitHub Secrets (encrypted, masked in
  logs). It is the same key the VM crons use — if the account is ever
  re-signed-in, the old key retires and BOTH this secret and the VM copy
  must be updated.
- The webhook URL can post only to that one channel and can be revoked
  or regenerated at any time in the channel's integration settings.
