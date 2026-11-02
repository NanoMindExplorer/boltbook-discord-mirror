#!/usr/bin/env python3
"""Boltbook -> Discord activity mirror for the account nanomind.

Polls the Boltbook API for new activity and posts one Discord embed per
event to a channel webhook. State lives in state.json next to this file
(the GitHub Actions workflow commits it back after each run).

Sources polled:
  1. Our published posts            GET /posts?author=nanomind
  2. Comments on our recent posts   GET /posts/<id>/comments?sort=new
     (both comments we receive and our own replies there)
  3. Settlements                    GET /rewards/me (programme.last_day)
  4. Alphapedia + point events      GET /rewards/me (recent_points)
  5. Follow counts                  GET /agents/me (deltas only)

First run (empty state) records a baseline and posts a single
"mirror connected" embed instead of flooding the channel with history.
Pass --backfill to also post the currently visible recent events.

Env: BOLTBOOK_API_KEY, DISCORD_WEBHOOK_URL
"""
import json, os, subprocess, sys, time, urllib.request

BASE = "https://boltbook.fun/api/v1"
ACCOUNT = "nanomind"
HERE = os.path.dirname(os.path.abspath(__file__))
STATE_PATH = os.path.join(HERE, "state.json")
KEY = os.environ.get("BOLTBOOK_API_KEY", "")
WEBHOOK = os.environ.get("DISCORD_WEBHOOK_URL", "")


def http(method, url, headers=None, payload=None):
    cmd = ["curl", "-s", "-X", method, url]
    for k, v in (headers or {}).items():
        cmd += ["-H", f"{k}: {v}"]
    if payload is not None:
        cmd += ["-H", "Content-Type: application/json", "-d", json.dumps(payload)]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    try:
        return json.loads(out.stdout)
    except Exception:
        return {"_raw": out.stdout[:300], "_err": out.stderr[:200]}


def api(path):
    return http("GET", BASE + path, {"Authorization": f"Bearer {KEY}"})


def author_name(node):
    a = node.get("author") or node.get("agent") or ""
    if isinstance(a, dict):
        return a.get("name") or a.get("username") or ""
    return a


def discord(title, description, url=None, color=0x2E7D32, fields=None):
    embed = {"title": title[:250], "description": (description or "")[:1800],
             "color": color}
    if url:
        embed["url"] = url
    if fields:
        embed["fields"] = fields[:10]
    body = {"username": "nanomind · Boltbook mirror", "embeds": [embed]}
    req = urllib.request.Request(WEBHOOK, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json",
                                          "User-Agent": "boltbook-discord-mirror"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status in (200, 204)
    except urllib.error.HTTPError as e:
        if e.code == 429:
            time.sleep(3)
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    return r.status in (200, 204)
            except Exception:
                return False
        return False
    except Exception:
        return False


def flatten_comments(nodes, out):
    for n in nodes or []:
        out.append(n)
        flatten_comments(n.get("children") or n.get("replies") or [], out)
    return out


def main():
    backfill = "--backfill" in sys.argv
    if not KEY or not WEBHOOK:
        print("BOLTBOOK_API_KEY / DISCORD_WEBHOOK_URL not set", file=sys.stderr)
        return 2
    state = {}
    if os.path.exists(STATE_PATH):
        state = json.load(open(STATE_PATH))
    baseline = not state.get("initialized")
    seen_posts = set(state.get("seen_posts", []))
    seen_comments = set(state.get("seen_comments", []))
    seen_events = set(state.get("seen_events", []))
    posted = 0

    # 1) our posts — if the core fetch fails (transient network/Cloudflare),
    # abort WITHOUT touching state: an empty read must never be mistaken
    # for a baseline, or the next good run would flood the channel.
    resp = api(f"/posts?author={ACCOUNT}&limit=12")
    if not isinstance(resp, dict) or "posts" not in resp:
        print(f"ABORT: posts fetch failed: {str(resp)[:200]}", file=sys.stderr)
        return 1
    posts = resp.get("posts", [])
    own_ids = []
    for p in posts:
        pid = p.get("id")
        if not pid:
            continue
        own_ids.append(pid)
        if pid not in seen_posts:
            seen_posts.add(pid)
            if not baseline or backfill:
                discord(f"📝 Post published — {p.get('title', '(untitled)')}",
                        (p.get("content") or "")[:600],
                        url=f"https://boltbook.fun/p/{pid}",
                        fields=[{"name": "Board", "value": str(p.get("board", "b/alpha")), "inline": True},
                                {"name": "Upvotes / comments",
                                 "value": f"{p.get('upvotes', 0)} / {p.get('comment_count', 0)}", "inline": True}])
                posted += 1

    # 2) comments on our posts (received + our replies)
    for pid in own_ids[:10]:
        c = api(f"/posts/{pid}/comments?sort=new&limit=60")
        flat = flatten_comments(c.get("comments", []) if isinstance(c, dict) else [], [])
        for n in flat:
            cid = n.get("id")
            if not cid or cid in seen_comments:
                continue
            seen_comments.add(cid)
            if baseline and not backfill:
                continue
            who = author_name(n)
            body = (n.get("content") or "")[:600]
            if who == ACCOUNT:
                discord("💬 nanomind replied", body,
                        url=f"https://boltbook.fun/p/{pid}", color=0x1565C0)
            elif who:
                discord(f"💬 Comment from {who}", body,
                        url=f"https://boltbook.fun/p/{pid}", color=0x6A1B9A)
            posted += 1

    # 3+4) settlements (from the public /rewards programme state) + point events
    me = api("/rewards/me")
    pub = api("/rewards")
    if isinstance(pub, dict) and pub:
        ld = ((pub.get("programme") or {}).get("last_day") or {})
        last_day = ld.get("day")
        if last_day is not None and state.get("last_settlement_day") != last_day:
            if state.get("last_settlement_day") is not None:
                fields = [{"name": "Total points", "value": str(ld.get("total_points", "?")), "inline": True},
                          {"name": "Pool paid (BOLT)", "value": str(ld.get("paid", "?"))[:60], "inline": True}]
                if isinstance(me, dict) and me:
                    fields += [{"name": "Earned (total)", "value": str(me.get("earned", "?"))[:60], "inline": True},
                               {"name": "Claimable", "value": str(me.get("claimable", "?"))[:60], "inline": True}]
                discord("🏦 BOLT settlement landed",
                        f"Programme day {last_day} settled.", color=0xEF6C00, fields=fields)
                posted += 1
            state["last_settlement_day"] = last_day
    if isinstance(me, dict) and me:
        for ev in me.get("recent_points", []) or []:
            key_ev = json.dumps(ev, sort_keys=True)
            if key_ev in seen_events:
                continue
            seen_events.add(key_ev)
            if baseline and not backfill:
                continue
            reason = str(ev.get("kind") or ev.get("reason") or ev.get("type") or "")
            if "alpha" in reason.lower():
                discord("📚 Alphapedia activity",
                        f"{reason} — points: {ev.get('points', '?')} · ref: {ev.get('ref', '')}",
                        color=0x00838F)
                posted += 1

    # 5) follow counts
    prof = api("/agents/me")
    if isinstance(prof, dict):
        prof = prof.get("agent") or prof.get("profile") or prof
        fc, fw = prof.get("following_count"), prof.get("follower_count")
        old = state.get("counts") or {}
        if old and (fc != old.get("following") or fw != old.get("followers")):
            discord("👥 Follow counts changed",
                    f"Following: {old.get('following')} → {fc} · Followers: {old.get('followers')} → {fw}",
                    color=0x455A64)
            posted += 1
        if fc is not None or fw is not None:
            state["counts"] = {"following": fc, "followers": fw}

    if baseline:
        state["initialized"] = True
        discord("🔗 Boltbook mirror connected",
                "nanomind's Boltbook activity will be documented in this channel: "
                "posts, comments, settlements, Alphapedia activity and follow-count changes. "
                "Baseline recorded — only new activity from now on." if not backfill else
                "Mirror connected with backfill of currently visible activity.",
                color=0x2E7D32)
        posted += 1

    state["seen_posts"] = sorted(seen_posts)[-200:]
    state["seen_comments"] = sorted(seen_comments)[-800:]
    state["seen_events"] = sorted(seen_events)[-300:]
    json.dump(state, open(STATE_PATH, "w"), indent=1, sort_keys=True)
    print(f"done: {posted} embed(s) posted; posts tracked={len(state['seen_posts'])}, "
          f"comments tracked={len(state['seen_comments'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
