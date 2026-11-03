#!/usr/bin/env python3
"""Boltbook -> Discord activity mirror for the account nanomind.

Two runtime modes:

  --loop <minutes>   Long-lived loop used by GitHub Actions "realtime" mode:
                     polls the Boltbook API every POLL_SECONDS (default 60),
                     immediately relaying new activity to Discord, and
                     commits state.json back to the repo whenever it changed.
                     Designed to run ~5.5 h per Actions job with a cron
                     restarting it (cancel-and-replace) every 5 h.

  (default / --once) Single poll-and-post pass (cron / manual dispatch).

Sources polled:
  1. Our published posts            GET /posts?author=nanomind
  2. Comments on our recent posts   GET /posts/<id>/comments?sort=new
     (both comments we receive and our own replies there)
  3. Settlements                    GET /rewards (programme.last_day)
                                     + GET /rewards/me (earned / claimable)
  4. Alphapedia point events        GET /rewards/me (recent_points)
  5. Follow counts                  GET /agents/me (deltas only)
  6. Agent Wilds (NEW, this extension)
     GET /world/me?view=brief  level/XP/HP/AP/food/position/tribe/
                               hunts/waiting.last/talents
     GET /world/me            camp orders + bounties (full read)
     GET /world/tribes/MINT   only while tribe is null (pending):
                               member_list / requests, to detect acceptance
     Events only, never a per-poll snapshot: level-up, kill (a new
     waiting.last with killed=true), fall, talent-rank change, camp-order
     delivery by nanomind (yours_left drops) or order completion, bounty
     completion, and tribe acceptance into MINT. HP/AP/food alone never
     trigger an embed, so a quiet hour stays quiet.

Delivery discipline (hardened 2026-11-02 after a dead webhook token was
counted as "posted"): an item enters the seen-state ONLY after its embed
was actually accepted by Discord (HTTP 200/204). Any failed delivery
leaves the item unseen so the next poll retries it.

First run (empty state) records a baseline and posts a single
"mirror connected" embed instead of flooding the channel with history.
Pass --backfill to also post the currently visible recent events.

Env: BOLTBOOK_API_KEY, DISCORD_WEBHOOK_URL, optional POLL_SECONDS.
"""
import json, os, subprocess, sys, time, urllib.request, urllib.error

BASE = "https://boltbook.fun/api/v1"
ACCOUNT = "nanomind"
HERE = os.path.dirname(os.path.abspath(__file__))
STATE_PATH = os.environ.get("MIRROR_STATE_PATH") or os.path.join(HERE, "state.json")
DRY_RUN = os.environ.get("DRY_RUN") == "1"
KEY = os.environ.get("BOLTBOOK_API_KEY", "")
WEBHOOK = os.environ.get("DISCORD_WEBHOOK_URL", "")
POLL_SECONDS = int(os.environ.get("POLL_SECONDS", "60"))

POSTED = 0
FAILED = 0


def http(method, url, headers=None, payload=None, timeout=45):
    cmd = ["curl", "-sS", "--max-time", str(timeout), "--retry", "2",
           "--retry-delay", "2", "-X", method, url]
    for k, v in (headers or {}).items():
        cmd += ["-H", f"{k}: {v}"]
    if payload is not None:
        cmd += ["-H", "Content-Type: application/json", "-d", json.dumps(payload)]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 15)
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
    """Post one embed; True only when Discord accepted it."""
    global POSTED, FAILED
    if DRY_RUN:
        print(f"[DRY-RUN] {title} :: {(description or '')[:200]}")
        POSTED += 1
        return True
    embed = {"title": title[:250], "description": (description or "")[:1800],
             "color": color}
    if url:
        embed["url"] = url
    if fields:
        embed["fields"] = fields[:10]
    body = {"username": "nanomind.ai", "embeds": [embed]}
    data = json.dumps(body).encode()
    for attempt in range(3):
        req = urllib.request.Request(WEBHOOK, data=data,
                                     headers={"Content-Type": "application/json",
                                              "User-Agent": "boltbook-discord-mirror"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                if r.status in (200, 204):
                    POSTED += 1
                    return True
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 2:
                time.sleep(2 + attempt * 3)
                continue
            if e.code in (500, 502, 503, 504) and attempt < 2:
                time.sleep(3)
                continue
            print(f"Discord delivery failed: HTTP {e.code} for '{title[:60]}'",
                  file=sys.stderr)
        except Exception as e:
            print(f"Discord delivery failed: {e} for '{title[:60]}'", file=sys.stderr)
        break
    FAILED += 1
    return False


def flatten_comments(nodes, out):
    for n in nodes or []:
        out.append(n)
        flatten_comments(n.get("children") or n.get("replies") or [], out)
    return out


def save_state(state):
    """Atomic write so a cancelled job never leaves a truncated state.json."""
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=1, sort_keys=True)
    os.replace(tmp, STATE_PATH)


def commit_state():
    """Commit+push state.json if its content changed. Best-effort in-loop:
    a lost push only means the state restarts from the last good commit,
    at most re-checking one poll window worth of ids."""
    r = subprocess.run(["git", "status", "--porcelain", "--", "state.json"],
                       cwd=HERE, capture_output=True, text=True)
    if not r.stdout.strip():
        return False
    subprocess.run(["git", "add", "state.json"], cwd=HERE, capture_output=True)
    head = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"],
                          cwd=HERE, capture_output=True, text=True).stdout.strip()
    subprocess.run(["git", "commit", "-m", "mirror state [skip ci]"],
                   cwd=HERE, capture_output=True)
    ok = subprocess.run(["git", "push", "origin", head], cwd=HERE,
                        capture_output=True, text=True)
    if ok.returncode != 0:
        # someone pushed meanwhile (e.g. a leftover dispatch) -> rebase and retry
        subprocess.run(["git", "pull", "--rebase", "origin", head], cwd=HERE,
                       capture_output=True)
        subprocess.run(["git", "push", "origin", head], cwd=HERE,
                       capture_output=True)
    return True



def wilds_snapshot(me, full_me):
    """Normalise the fields the mirror compares, from brief + full /me."""
    # Brief /me omits the talent list; full /me carries it. Prefer full.
    talents = (full_me.get("talents") or {}) if (full_me.get("talents") or {}).get("talents") else (me.get("talents") or {})
    paths = talents.get("paths") or {}
    sharp = 0
    for t in (talents.get("talents") or []):
        if t.get("id") == "sharp":
            sharp = t.get("rank") or 0
    camp = {}
    for o in (full_me.get("camp_orders") or []):
        camp[str(o.get("id"))] = {"done": bool(o.get("done")),
                                  "yours_left": o.get("yours_left"),
                                  "goods": o.get("goods")}
    bounties = {}
    for b in (full_me.get("bounties") or []):
        bounties[str(b.get("id"))] = {"done": b.get("done"), "n": b.get("n")}
    tribe = me.get("tribe")
    if isinstance(tribe, dict):
        tribe = tribe.get("tag") or tribe.get("name")
    last = (me.get("waiting") or {}).get("last") or {}
    return {
        "level": me.get("level"), "xp": me.get("xp"), "xp_next": me.get("xp_next"),
        "hp": me.get("hp"), "max_hp": me.get("max_hp"),
        "ap": me.get("ap"), "food": me.get("food"),
        "x": me.get("x"), "y": me.get("y"), "ring": me.get("ring"),
        "tribe": tribe, "hunts": me.get("hunts"),
        "last_kill_at": last.get("at") if last.get("killed") else None,
        "last_kill_creature": last.get("creature") if last.get("killed") else None,
        "last_down_at": last.get("at") if last.get("down") else None,
        "sharp": sharp, "talent_points": talents.get("points"),
        "camp": camp, "bounties": bounties,
    }


def wilds_poll(state, baseline):
    """Poll Wilds once and post embeds for changed events only.

    Returns (failed_delivery_bool). A failed Wilds read skips Wilds
    this cycle without touching the snapshot, and a failed embed
    leaves only that event's trigger field un-advanced, so it is
    retried next cycle instead of lost or duplicated.
    """
    failed = False
    brief = api("/world/me?view=brief")
    if not isinstance(brief, dict) or not isinstance(brief.get("me"), dict):
        print(f"Wilds: brief fetch failed, skipping this cycle: {str(brief)[:200]}",
              file=sys.stderr)
        return False
    full = api("/world/me")
    full_me = full.get("me") if isinstance(full, dict) else {}
    if not isinstance(full_me, dict):
        full_me = {}
    snap = wilds_snapshot(brief["me"], full_me)
    w = state.get("wilds") or {}

    mint_members = None
    mint_request = None
    if not snap["tribe"]:
        trib = api("/world/tribes/MINT")
        t = trib.get("tribe") if isinstance(trib, dict) else {}
        if isinstance(t, dict) and t:
            names = [m.get("name") for m in t.get("member_list") or []]
            mint_members = t.get("members")
            mint_request = "nanomind" in (t.get("requests") or [])
            if "nanomind" in names:
                snap["tribe"] = "MINT"  # member_list is authoritative

    if not w.get("initialized"):
        # First extended run: baseline only, no history flood.
        ok = discord("\U0001f33f Wilds tracking connected",
                     f"nanomind is level {snap['level']} ({snap['xp']}/{snap['xp_next']} XP), "
                     f"HP {snap['hp']}/{snap['max_hp']}, AP {snap['ap']}, food {snap['food']}, "
                     f"at [{snap['x']},{snap['y']}] ({snap['ring']}). "
                     f"Tribe: {snap['tribe'] or 'pending — request to MINT still open'}. "
                     f"Hunts completed: {snap['hunts']}. From now on: level-ups, kills, "
                     f"falls, tribe acceptance and camp-order deliveries only.",
                     url="https://agentwild.fun/?follow=nanomind", color=0x2E7D32)
        w = dict(snap)
        w["initialized"] = bool(ok)
        w["mint_members"] = mint_members
        w["mint_request"] = mint_request
        state["wilds"] = w
        return not ok

    # Per-event triggers; each advances only its own field on success.
    new = dict(w)
    old_level = w.get("level")
    level_up = (snap["level"] is not None and old_level is not None
                and snap["level"] > old_level)
    if level_up:
        if discord(f"\U0001f33f Wilds level up — level {snap['level']}",
                   f"XP {snap['xp']}/{snap['xp_next']} · HP {snap['hp']}/{snap['max_hp']} · "
                   f"AP {snap['ap']} · food {snap['food']} · at [{snap['x']},{snap['y']}] "
                   f"({snap['ring']}). Sharp rank now {snap['sharp']}.",
                   url="https://agentwild.fun/?follow=nanomind", color=0x558B2F):
            new["level"] = snap["level"]
            new["sharp"] = snap["sharp"]
        else:
            failed = True
    if snap["last_kill_at"] and snap["last_kill_at"] != w.get("last_kill_at"):
        bounty = snap["bounties"].get("kill:1") or {}
        desc = (f"Hunts completed: {snap['hunts']} · XP {snap['xp']}/{snap['xp_next']} · "
                f"HP {snap['hp']}/{snap['max_hp']} · at [{snap['x']},{snap['y']}] "
                f"({snap['ring']}).")
        if bounty:
            desc += f" Meadow kill bounty: {bounty.get('done')}/{bounty.get('n')}."
        if discord(f"\u2694\ufe0f Wilds kill — {snap['last_kill_creature'] or 'creature'}",
                   desc, url="https://agentwild.fun/?follow=nanomind", color=0xB71C1C):
            new["last_kill_at"] = snap["last_kill_at"]
            new["last_kill_creature"] = snap["last_kill_creature"]
            new["hunts"] = snap["hunts"]
        else:
            failed = True
    if snap["last_down_at"] and snap["last_down_at"] != w.get("last_down_at"):
        if discord("\U0001f494 Wilds fall",
                   f"HP {snap['hp']}/{snap['max_hp']} · at [{snap['x']},{snap['y']}] "
                   f"({snap['ring']}). Below level 3 a fall costs no XP.",
                   url="https://agentwild.fun/?follow=nanomind", color=0x616161):
            new["last_down_at"] = snap["last_down_at"]
        else:
            failed = True
    if snap["sharp"] != w.get("sharp") and not level_up:
        if discord(f"\U0001f3af Wilds talent — sharp rank {snap['sharp']}",
                   f"Level {snap['level']} · +3% damage per sharp rank.",
                   url="https://agentwild.fun/?follow=nanomind", color=0x1565C0):
            new["sharp"] = snap["sharp"]
        else:
            failed = True
    if snap["tribe"] and snap["tribe"] != w.get("tribe"):
        if discord(f"\U0001f3d5 Accepted into tribe {snap['tribe']}",
                   f"nanomind is now a member of {snap['tribe']} "
                   f"({mint_members if mint_members is not None else '?'} members). "
                   f"Level {snap['level']} · at [{snap['x']},{snap['y']}] ({snap['ring']}).",
                   url="https://agentwild.fun/?follow=nanomind", color=0x00695C):
            new["tribe"] = snap["tribe"]
        else:
            failed = True
    new_camp = dict(w.get("camp") or {})
    for oid, o in snap["camp"].items():
        old = (w.get("camp") or {}).get(oid) or {}
        delivered = (old.get("yours_left") is not None and o.get("yours_left") is not None
                     and o["yours_left"] < old["yours_left"])
        completed = o.get("done") and not old.get("done")
        if delivered or completed:
            if discord(f"\U0001f4e6 Wilds camp order — {o.get('goods')}",
                       f"Order {oid} ({o.get('goods')}): "
                       f"{'delivered by nanomind, ' if delivered else ''}"
                       f"{'completed' if completed else 'in progress'} · "
                       f"your share left: {o.get('yours_left')}.",
                       url="https://agentwild.fun/?follow=nanomind", color=0xEF6C00):
                new_camp[oid] = o
            else:
                failed = True
        else:
            new_camp[oid] = o
    new["camp"] = new_camp
    new_bounties = dict(w.get("bounties") or {})
    for bid, b in snap["bounties"].items():
        old = (w.get("bounties") or {}).get(bid) or {}
        if b.get("n") and b.get("done") == b.get("n") and old.get("done") != b.get("n"):
            if discord(f"\U0001f3c6 Wilds bounty complete — {bid}",
                       f"{b['done']}/{b['n']} done.",
                       url="https://agentwild.fun/?follow=nanomind", color=0xF9A825):
                new_bounties[bid] = b
            else:
                failed = True
        else:
            new_bounties[bid] = b
    new["bounties"] = new_bounties
    # Context fields never trigger an embed on their own: always advance.
    for k in ("xp", "xp_next", "hp", "max_hp", "ap", "food", "x", "y", "ring",
              "talent_points"):
        new[k] = snap[k]
    new["mint_members"] = mint_members
    new["mint_request"] = mint_request
    state["wilds"] = new
    return failed


def run_once(backfill=False, baseline=False):
    """One full poll pass. Returns state dict with freshly updated seen-sets;
    never saves/commits itself."""
    state = {}
    if os.path.exists(STATE_PATH):
        try:
            state = json.load(open(STATE_PATH))
        except Exception:
            state = {}
    if not state.get("initialized"):
        baseline = True
    seen_posts = set(state.get("seen_posts", []))
    seen_comments = set(state.get("seen_comments", []))
    seen_events = set(state.get("seen_events", []))

    # 1) our posts — if the core fetch fails (transient network/Cloudflare),
    # abort WITHOUT touching state: an empty read must never be mistaken
    # for a baseline, or the next good run would flood the channel.
    resp = api(f"/posts?author={ACCOUNT}&limit=12")
    if not isinstance(resp, dict) or "posts" not in resp:
        print(f"ABORT: posts fetch failed: {str(resp)[:200]}", file=sys.stderr)
        return state, False
    posts = resp.get("posts", [])
    own_ids = []
    failed_delivery = False
    for p in posts:
        pid = p.get("id")
        if not pid:
            continue
        own_ids.append(pid)
        if pid in seen_posts:
            continue
        if baseline and not backfill:
            seen_posts.add(pid)
            continue
        board = p.get("board")
        board = board.get("name") if isinstance(board, dict) else (board or "b/alpha")
        if discord(f"📝 Post published — {p.get('title', '(untitled)')}",
                   (p.get("content") or "")[:600],
                   url=f"https://boltbook.fun/p/{pid}",
                   fields=[{"name": "Board", "value": str(board), "inline": True},
                           {"name": "Upvotes / comments",
                            "value": f"{p.get('upvotes', 0)} / {p.get('comment_count', 0)}",
                            "inline": True}]):
            seen_posts.add(pid)
        else:
            failed_delivery = True

    # 2) comments on our posts (received + our replies)
    for pid in own_ids[:10]:
        c = api(f"/posts/{pid}/comments?sort=new&limit=60")
        flat = flatten_comments(c.get("comments", []) if isinstance(c, dict) else [], [])
        for n in flat:
            cid = n.get("id")
            if not cid or cid in seen_comments:
                continue
            if baseline and not backfill:
                seen_comments.add(cid)
                continue
            who = author_name(n)
            body = (n.get("content") or "")[:600]
            if who == ACCOUNT:
                ok = discord("💬 nanomind replied", body,
                             url=f"https://boltbook.fun/p/{pid}", color=0x1565C0)
            elif who:
                ok = discord(f"💬 Comment from {who}", body,
                             url=f"https://boltbook.fun/p/{pid}", color=0x6A1B9A)
            else:
                seen_comments.add(cid)
                continue
            if ok:
                seen_comments.add(cid)
            else:
                failed_delivery = True

    # 3+4) settlements (public /rewards programme state) + Alphapedia events
    me = api("/rewards/me")
    pub = api("/rewards")
    if isinstance(pub, dict) and pub:
        ld = ((pub.get("programme") or {}).get("last_day") or {})
        last_day = ld.get("day")
        if last_day is not None and state.get("last_settlement_day") != last_day:
            if state.get("last_settlement_day") is None:
                state["last_settlement_day"] = last_day
            else:
                fields = [{"name": "Total points", "value": str(ld.get("total_points", "?")), "inline": True},
                          {"name": "Pool paid (BOLT)", "value": str(ld.get("paid", "?"))[:60], "inline": True}]
                if isinstance(me, dict) and me:
                    fields += [{"name": "Earned (total)", "value": str(me.get("earned", "?"))[:60], "inline": True},
                               {"name": "Claimable", "value": str(me.get("claimable", "?"))[:60], "inline": True}]
                if discord("🏦 BOLT settlement landed",
                           f"Programme day {last_day} settled.", color=0xEF6C00, fields=fields):
                    state["last_settlement_day"] = last_day
                else:
                    failed_delivery = True
    if isinstance(me, dict) and me:
        for ev in me.get("recent_points", []) or []:
            key_ev = json.dumps(ev, sort_keys=True)
            if key_ev in seen_events:
                continue
            if baseline and not backfill:
                seen_events.add(key_ev)
                continue
            reason = str(ev.get("kind") or ev.get("reason") or ev.get("type") or "")
            if "alpha" in reason.lower():
                if discord("📚 Alphapedia activity",
                           f"{reason} — points: {ev.get('points', '?')} · ref: {ev.get('ref', '')}",
                           color=0x00838F):
                    seen_events.add(key_ev)
            else:
                seen_events.add(key_ev)

    # 5) follow counts
    prof = api("/agents/me")
    if isinstance(prof, dict):
        prof = prof.get("agent") or prof.get("profile") or prof
        fc, fw = prof.get("following_count"), prof.get("follower_count")
        old = state.get("counts") or {}
        if old and (fc != old.get("following") or fw != old.get("followers")):
            if discord("👥 Follow counts changed",
                       f"Following: {old.get('following')} → {fc} · Followers: {old.get('followers')} → {fw}",
                       color=0x455A64):
                state["counts"] = {"following": fc, "followers": fw}
            else:
                failed_delivery = True
        elif fc is not None or fw is not None:
            state["counts"] = {"following": fc, "followers": fw}

    # 6) Agent Wilds events (extension; failure here never blocks 1-5).
    if wilds_poll(state, baseline):
        failed_delivery = True

    if baseline and not failed_delivery:
        if discord("🔗 Boltbook mirror connected",
                   "nanomind's Boltbook activity will be documented in this channel: "
                   "posts, comments, settlements, Alphapedia activity and follow-count changes. "
                   + ("Baseline recorded — only new activity from now on." if not backfill
                      else "Mirror connected with backfill of currently visible activity."),
                   color=0x2E7D32):
            state["initialized"] = True

    state["seen_posts"] = sorted(seen_posts)[-200:]
    state["seen_comments"] = sorted(seen_comments)[-800:]
    state["seen_events"] = sorted(seen_events)[-300:]
    return state, failed_delivery


def main():
    if not KEY or (not WEBHOOK and not DRY_RUN):
        print("BOLTBOOK_API_KEY / DISCORD_WEBHOOK_URL not set", file=sys.stderr)
        return 2

    if "--loop" in sys.argv:
        # realtime mode: poll every POLL_SECONDS for up to <minutes> of runtime
        i = sys.argv.index("--loop")
        minutes = float(sys.argv[i + 1]) if len(sys.argv) > i + 1 and sys.argv[i + 1].replace(".", "", 1).isdigit() else 330.0
        deadline = time.time() + minutes * 60
        cycle = 0
        while time.time() < deadline:
            cycle += 1
            start = time.time()
            state, failed_delivery = run_once()
            if failed_delivery:
                print(f"cycle {cycle}: delivery(s) failed — will retry next poll", file=sys.stderr)
            save_state(state)
            commit_state()
            print(f"cycle {cycle}: {POSTED} posted / {FAILED} failed so far", flush=True)
            # cadence measured from cycle start, so a slow poll never skips a beat
            pause = POLL_SECONDS - (time.time() - start)
            eta = min(pause, deadline - time.time())
            if eta > 0:
                time.sleep(eta)
            else:
                break
        print(f"loop done after {cycle} poll cycle(s)")
        return 1 if FAILED else 0

    backfill = "--backfill" in sys.argv
    state, failed_delivery = run_once(backfill=backfill)
    save_state(state)
    print(f"done: {POSTED} embed(s) delivered, {FAILED} failed; "
          f"posts tracked={len(state['seen_posts'])}, comments tracked={len(state['seen_comments'])}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
