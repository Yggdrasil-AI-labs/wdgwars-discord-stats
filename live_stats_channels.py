#!/usr/bin/env python3
"""Live WDGoWars stats as Discord voice-channel labels.

Renames a set of voice channels in a "live stats" category so their names
show your current WDGoWars numbers, updated on a schedule. This is the
at-a-glance display (User / Team / Total / WiFi / BLE / ADS-B / Mesh / Rank /
API), the kind you can leave pinned in a server sidebar.

Unlike discord_stats_webhook.py (which only needs a webhook URL), this needs a
Discord **bot** because renaming channels is a bot action. Standard library
only.

Setup
-----
1. Get your WDGoWars API key from https://wdgwars.pl/profile
2. Create a Discord bot (https://discord.com/developers/applications), copy its
   token, and invite it to your server with the "Manage Channels" permission.
3. Make a category in your server for the display (default name
   "📊 │ live stats"). The script creates/removes the voice channels inside it.
4. Export the config (never hard-code the token or key):

       export WDGWARS_API_KEY="your-64-char-key"
       export DISCORD_BOT_TOKEN="your-bot-token"
       export DISCORD_GUILD_ID="your-server-id"

5. Try it without touching Discord first:

       python live_stats_channels.py --sample --dry-run   # prints, no key, no writes
       python live_stats_channels.py --once               # one real update
       python live_stats_channels.py                       # loop every 5 min

Choosing what to show
---------------------
Not everyone wants to expose every number. Control which fields appear by:

- Reacting on the pinned panel in the mod-config channel: a field's emoji
  toggles it on/off (easiest, once `--setup` has run).
- Editing the config file (default ~/.wdgwars-live-stats.json):
  {"fields": {"BLE": false, "Rank": false}}  (missing = shown)
- Or, where the config file does not persist (e.g. GitHub Actions), the env
  var STATS_FIELDS_OFF=BLE,Rank.

Key safety
----------
The bot token and API key are read from the environment, never printed, and
redacted from any error output. Never commit them.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

try:
    from zoneinfo import ZoneInfo
    TZ = ZoneInfo(os.environ.get("STATS_TZ", "America/New_York"))
except Exception:
    TZ = timezone.utc


def _load_dotenv() -> None:
    """Populate the environment from a .env file so users can fill in a text
    file instead of running `export` commands. Does not override variables that
    are already set (real env wins). Looks at $STATS_ENV_FILE, else a .env next
    to this script."""
    path = os.environ.get("STATS_ENV_FILE") or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), ".env")
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, val = line.split("=", 1)
                os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))
    except FileNotFoundError:
        pass


_load_dotenv()

TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
WDGO_KEY = os.environ.get("WDGWARS_API_KEY", "").strip()
GUILD_ID = os.environ.get("DISCORD_GUILD_ID", "").strip()
OWNER_USERNAME = os.environ.get("STATS_OWNER_USERNAME", "").strip()
CATEGORY_NAME = os.environ.get("STATS_CATEGORY_NAME", "📊 │ live stats")
CONFIG_PATH = os.path.expanduser(
    os.environ.get("STATS_CONFIG_PATH", "~/.wdgwars-live-stats.json"))
# The mod-config channel is an admin surface, so setup hides it from regular
# members by default. Set STATS_CONFIG_PRIVATE=off to make it visible to all.
CONFIG_PRIVATE = os.environ.get("STATS_CONFIG_PRIVATE", "on").lower() not in (
    "0", "off", "false", "no")
STATE_PATH = os.path.expanduser(
    os.environ.get("STATS_STATE_PATH", "~/.wdgwars-live-stats-state.json"))
BASE = os.environ.get("WDGWARS_BASE_URL", "https://wdgwars.pl").rstrip("/")
INTERVAL = int(os.environ.get("STATS_INTERVAL", "300"))
USER_AGENT = "wdgwars-discord-stats/1.0 (+https://github.com/Yggdrasil-AI-labs/wdgwars-discord-stats)"

# Order the fields appear in the category. Also the set of valid field names.
FIELD_ORDER = ["User", "Team", "Gang Size", "Gang APs", "Updated", "Total",
               "WiFi", "BLE", "ADS-B", "Mesh", "Reinforced", "Today", "Week",
               "Credits", "Quota", "Rank", "API"]
# Fields to hide via environment: STATS_FIELDS_OFF is a comma-separated list of
# field labels, matched case-insensitively (e.g. "ble,rank,ads-b"). Useful where
# the config file does not persist, e.g. GitHub Actions.
_FIELD_BY_CASEFOLD = {lbl.casefold(): lbl for lbl in FIELD_ORDER}
_ENV_FIELDS_OFF = {
    _FIELD_BY_CASEFOLD.get(tok.strip().casefold(), tok.strip())
    for tok in os.environ.get("STATS_FIELDS_OFF", "").split(",") if tok.strip()
}

# Canned stats for --sample (no key, no network).
SAMPLE_ME = {
    "ok": True, "username": "SampleDriver", "gang": "Sample Gang",
    "total": 104063, "wifi": 84210, "ble": 15320, "aircraft": 4471, "mesh": 62,
    "reinforce_total": 32573, "recent_today": 340, "recent_7d": 2110,
    "credits": {"balance": 1662}, "new_ap_limit": {"used": 340, "cap": 500000},
    "your_rank": {"all_time": 42, "today": None, "week": 17, "top_n": 100},
}

log = logging.getLogger("live-stats")

# One reaction "button" emoji per field (single-codepoint so it encodes cleanly
# for the Discord reactions API). Reacting on the config panel toggles the field.
REACTION_EMOJI = {
    "User": "👤", "Team": "🏴", "Gang Size": "👥", "Gang APs": "🏰",
    "Updated": "🕒", "Total": "📊", "WiFi": "📶", "BLE": "🔵", "ADS-B": "🛫",
    "Mesh": "📡", "Reinforced": "🧱", "Today": "📅", "Week": "📆",
    "Credits": "🪙", "Quota": "⛽", "Rank": "🎯", "API": "🔌",
}
_BOT_ID = None


def bot_id() -> str:
    """The bot's own user id (cached) so its panel reactions can be told apart
    from a user's toggle press."""
    global _BOT_ID
    if _BOT_ID is None:
        me = discord_api("GET", "/users/@me")
        _BOT_ID = me.get("id", "") if isinstance(me, dict) else ""
    return _BOT_ID


def config_overwrites():
    """Permission overwrites that hide the mod channel from @everyone while
    letting the bot view/post/manage it. None if privacy is disabled. (Server
    admins still see it, Discord's Administrator permission bypasses overwrites.)"""
    if not CONFIG_PRIVATE:
        return None
    view = 1 << 10  # VIEW_CHANNEL
    # view + send + add_reactions + manage_messages + read_history
    bot_allow = (1 << 10) | (1 << 11) | (1 << 6) | (1 << 13) | (1 << 16)
    return [
        {"id": GUILD_ID, "type": 0, "deny": str(view), "allow": "0"},
        {"id": bot_id(), "type": 1, "allow": str(bot_allow), "deny": "0"},
    ]


# ── small helpers ──────────────────────────────────────────────────────────
def scrub(text: str) -> str:
    """Redact secrets from a string before logging."""
    for secret in (TOKEN, WDGO_KEY):
        if secret and secret in text:
            text = text.replace(secret, "<redacted>")
    return text


def fmt_int(n) -> str:
    try:
        return f"{int(n):,}".replace(",", " ")
    except (TypeError, ValueError):
        return str(n)


# ── config + state ─────────────────────────────────────────────────────────
def load_json(path: str, default: dict) -> dict:
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return dict(default)


def save_json(path: str, data: dict) -> None:
    try:
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
    except Exception as e:
        log.warning("write %s failed: %s", path, scrub(str(e)))


def load_config() -> dict:
    cfg = load_json(CONFIG_PATH, {"fields": {}})
    cfg.setdefault("fields", {})
    return cfg


def field_enabled(cfg: dict, label: str) -> bool:
    if label in _ENV_FIELDS_OFF:
        return False
    return bool(cfg.get("fields", {}).get(label, True))


# ── config panel ─────────────────────────────────────────────────────────────
def render_panel_text(cfg: dict) -> str:
    lines = ["**live-stats fields** (react with a field's emoji below to "
             "show/hide it):", ""]
    for lbl in FIELD_ORDER:
        state = "✅ shown " if field_enabled(cfg, lbl) else "⬜ hidden"
        lines.append(f"{REACTION_EMOJI.get(lbl, '•')}  {state}  {lbl}")
    lines.append("")
    lines.append("_(one press toggles a field; your reaction clears once applied)_")
    return "\n".join(lines)


# ── Discord REST ─────────────────────────────────────────────────────────────
def discord_api(method: str, path: str, body=None):
    """Call the Discord REST API with the bot token. Returns parsed JSON,
    {} for empty bodies (e.g. 204), or None on unrecoverable error."""
    url = f"https://discord.com/api/v10{path}"
    data = None if body is None else json.dumps(body).encode()
    headers = {
        "Authorization": f"Bot {TOKEN}",
        "User-Agent": USER_AGENT,
        "Content-Type": "application/json",
    }
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    for _ in range(3):
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                txt = resp.read().decode()
                return json.loads(txt) if txt else {}
        except urllib.error.HTTPError as e:
            body_txt = e.read().decode("utf-8", "replace")
            if e.code == 429:
                wait = 2.0
                try:
                    wait = float(json.loads(body_txt).get("retry_after", 2)) + 0.5
                except Exception:
                    pass
                log.warning("rate limited on %s, sleeping %.1fs", path, wait)
                time.sleep(wait)
                continue
            log.error("discord HTTP %d on %s: %s", e.code, path,
                      scrub(body_txt[:200]))
            return None
        except urllib.error.URLError as e:
            log.error("discord network error on %s: %s", path, scrub(str(e.reason)))
            return None
    return None


# ── WDGoWars API ─────────────────────────────────────────────────────────────
def wdgo_api(path: str, timeout: float = 8.0):
    """GET a WDGoWars endpoint. Returns (data|None, latency_ms, http_status)."""
    if not WDGO_KEY:
        return None, 0, 0
    req = urllib.request.Request(f"{BASE}{path}", headers={
        "X-API-Key": WDGO_KEY, "Accept": "application/json",
        "User-Agent": USER_AGENT,
    })
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            latency = int((time.monotonic() - t0) * 1000)
            return json.loads(r.read()), latency, r.status
    except urllib.error.HTTPError as e:
        return None, int((time.monotonic() - t0) * 1000), e.code
    except Exception as e:
        log.warning("wdgo %s failed: %s", path, scrub(str(e)))
        return None, int((time.monotonic() - t0) * 1000), 0


def rank_str(me: dict) -> str:
    """Format your_rank (all_time preferred), or >top_n if outside the window."""
    rank = me.get("your_rank") or {}
    top_n = rank.get("top_n") or 100
    for cat in ("all_time", "today", "week"):
        n = rank.get(cat)
        if isinstance(n, int):
            return f"#{n} {cat.replace('_', ' ')}"
    return f">{top_n}"


def find_gang(lb: dict, gang_name: str):
    """Return (rank, entry) for gang_name in the leaderboard `gangs` array, or
    (None, None). Rank is the 1-based position; entry carries member_count and
    ap_count. The leaderboard `gangs` board is the lightweight source of gang
    stats that works for any caller, including solo accounts; /api/team/me gives
    the full per-member roster but 404s when you are not in a team."""
    if not isinstance(lb, dict) or not gang_name or gang_name == "-":
        return None, None
    for i, e in enumerate(lb.get("gangs", []), 1):
        if isinstance(e, dict) and e.get("name") == gang_name:
            return i, e
    return None, None


def gather_stats(sample: bool = False) -> dict:
    """Build the label->value map for every field (before visibility filter)."""
    now_local = datetime.now(TZ)
    tz_label = now_local.tzname() or "UTC"

    if sample:
        me, latency, status = SAMPLE_ME, 120, 200
        lb = {"gangs": [{"name": "Sample Gang", "member_count": 42,
                         "ap_count": 1234567}]}
    else:
        me, latency, status = wdgo_api("/endpoint/me")
        lb = (wdgo_api("/endpoint/leaderboard")[0] or {}) if me else {}
    api_ok = bool(me) and me.get("ok") is True
    me = me or {}

    if api_ok:
        api_line = (f"🟡 API: SLOW ({latency}ms)" if latency > 3000
                    else f"🟢 API: UP ({latency}ms)")
    else:
        api_line = f"🔴 API: DOWN (HTTP {status})"

    gang = me.get("gang") or "-"
    gr, ge = find_gang(lb, gang)
    team = (f"#{gr} {gang}" if gr else (gang if gang != "-" else "-"))[:30]
    username = me.get("username") or OWNER_USERNAME or "unknown"

    stats = {
        "User":    f"👤 User: {username}",
        "Team":    f"🏴 Team: {team}",
        "Updated": f"⏱ Updated: {now_local.strftime('%H:%M')} {tz_label}",
        "Total":   f"📊 Total: {fmt_int(me.get('total', 0))}",
        "WiFi":    f"📶 WiFi: {fmt_int(me.get('wifi', 0))}",
        "BLE":     f"🔵 BLE: {fmt_int(me.get('ble', 0))}",
        "ADS-B":   f"✈ ADS-B: {fmt_int(me.get('aircraft', 0))}",
        "Mesh":    f"📡 Mesh: {fmt_int(me.get('mesh', 0))}",
        "Reinforced": f"🧱 Reinforced: {fmt_int(me.get('reinforce_total', 0))}",
        "Today":   f"📅 Today: {fmt_int(me.get('recent_today', 0))}",
        "Week":    f"📆 Week: {fmt_int(me.get('recent_7d', 0))}",
        "Credits": f"🪙 Credits: {fmt_int((me.get('credits') or {}).get('balance', 0))}",
        "Quota":   f"⛽ Quota: {fmt_int((me.get('new_ap_limit') or {}).get('used', 0))}"
                   f"/{fmt_int((me.get('new_ap_limit') or {}).get('cap', 0))}",
        "Rank":    f"🎯 Rank: {rank_str(me)}",
        "API":     api_line,
    }
    # Gang stats from the leaderboard, only when the caller is in a gang that
    # appears on the board. Solo drivers simply don't get these channels.
    if ge:
        stats["Gang Size"] = f"👥 Gang Size: {fmt_int(ge.get('member_count', 0))}"
        stats["Gang APs"] = f"🏰 Gang APs: {fmt_int(ge.get('ap_count', 0))}"
    return stats


# ── channel plumbing ─────────────────────────────────────────────────────────
def label_of(name: str) -> str:
    """Recover a field label from a channel name like '📊 Total: 1 234 ·'."""
    if ":" not in name:
        return name
    head = name.split(":")[0].strip()
    parts = head.split(" ", 1)
    return parts[1] if len(parts) > 1 else head


def reconcile_channels(active):
    """Make the category's voice channels match `active` (the labels that are
    both enabled and produced this tick). Deletes channels for any known field
    not in `active` (hidden, or not applicable, e.g. gang stats for a solo
    driver); creates channels for active labels missing one. Returns
    {label: channel}."""
    active = set(active)
    chs = discord_api("GET", f"/guilds/{GUILD_ID}/channels") or []
    cat = next((c for c in chs if c["type"] == 4 and c["name"] == CATEGORY_NAME), None)
    if not cat:
        log.error("category %r not found in guild", CATEGORY_NAME)
        return {}
    present = {label_of(c["name"]): c for c in chs
               if c["type"] == 2 and c.get("parent_id") == cat["id"]}
    for lbl, ch in list(present.items()):
        if lbl in FIELD_ORDER and lbl not in active:
            if discord_api("DELETE", f"/channels/{ch['id']}") is not None:
                log.info("removed %r channel", lbl)
            present.pop(lbl, None)
    for pos, lbl in enumerate(FIELD_ORDER):
        if lbl in active and lbl not in present:
            created = discord_api("POST", f"/guilds/{GUILD_ID}/channels", {
                "name": lbl, "type": 2, "parent_id": cat["id"], "position": pos,
            })
            if created:
                present[lbl] = created
                log.info("added %r channel", lbl)
    return present


def update_panel(cfg: dict) -> None:
    """Edit the pinned control panel to reflect the current field state. Reposts
    and re-pins if the stored message was deleted. No-op if setup never ran."""
    panel = cfg.get("panel") or {}
    ch, msg = panel.get("channel_id"), panel.get("message_id")
    if not ch:
        return
    body = {"content": render_panel_text(cfg)}
    if msg and discord_api("PATCH", f"/channels/{ch}/messages/{msg}", body) is not None:
        return
    new = discord_api("POST", f"/channels/{ch}/messages", body)
    if new:
        discord_api("PUT", f"/channels/{ch}/pins/{new['id']}")
        cfg.setdefault("panel", {})["message_id"] = new["id"]
        save_json(CONFIG_PATH, cfg)
        add_panel_reactions(ch, new["id"])


def add_panel_reactions(channel_id: str, message_id: str) -> None:
    """Put one clickable reaction per field on the panel message (idempotent)."""
    for emoji in REACTION_EMOJI.values():
        enc = urllib.parse.quote(emoji)
        discord_api("PUT",
                    f"/channels/{channel_id}/messages/{message_id}/reactions/{enc}/@me")


def poll_reactions(cfg: dict) -> bool:
    """Toggle any field whose reaction a user pressed on the panel, then clear
    that reaction so it acts like a button. Returns True if the config changed."""
    panel = cfg.get("panel") or {}
    ch, msg = panel.get("channel_id"), panel.get("message_id")
    if not (ch and msg):
        return False
    # One read per tick: pull the message and look at reaction counts. The bot's
    # own button counts as 1; a user press makes it >= 2. Only then do we spend
    # calls fetching who reacted. Keeps steady-state API traffic to ~1 call/tick.
    message = discord_api("GET", f"/channels/{ch}/messages/{msg}")
    if not isinstance(message, dict):
        return False
    counts = {r.get("emoji", {}).get("name"): r.get("count", 0)
              for r in message.get("reactions", [])}
    me_id = bot_id()
    changed = False
    for label, emoji in REACTION_EMOJI.items():
        if counts.get(emoji, 0) < 2:
            continue
        enc = urllib.parse.quote(emoji)
        users = discord_api(
            "GET", f"/channels/{ch}/messages/{msg}/reactions/{enc}?limit=20")
        pressers = ([u for u in users if isinstance(u, dict) and u.get("id") != me_id]
                    if isinstance(users, list) else [])
        if not pressers:
            continue
        cfg.setdefault("fields", {})[label] = not field_enabled(cfg, label)
        changed = True
        log.info("toggled %r -> %s via reaction", label, cfg["fields"][label])
        for u in pressers:
            discord_api(
                "DELETE",
                f"/channels/{ch}/messages/{msg}/reactions/{enc}/{u['id']}")
    if changed:
        save_json(CONFIG_PATH, cfg)
        update_panel(cfg)
    return changed


def setup_discord() -> int:
    """Create the live-stats category, a mod-config text channel, and a pinned
    control panel. Idempotent: reuses an existing category/channel by name. The
    mod-config channel id + panel location are written to the config file so the
    poller picks them up with no extra environment variables."""
    chs = discord_api("GET", f"/guilds/{GUILD_ID}/channels")
    if not isinstance(chs, list):
        raise SystemExit("could not list guild channels (check the bot token and guild id)")

    cat = next((c for c in chs if c["type"] == 4 and c["name"] == CATEGORY_NAME), None)
    if cat:
        print(f"category exists: {CATEGORY_NAME} ({cat['id']})")
    else:
        cat = discord_api("POST", f"/guilds/{GUILD_ID}/channels",
                          {"name": CATEGORY_NAME, "type": 4})
        if not cat:
            raise SystemExit("failed to create the live-stats category")
        print(f"created category: {CATEGORY_NAME} ({cat['id']})")

    cfg_name = os.environ.get("STATS_CONFIG_CHANNEL_NAME", "stats-config")
    ow = config_overwrites()
    modch = next((c for c in chs if c["type"] == 0 and c["name"] == cfg_name), None)
    if modch:
        print(f"mod-config channel exists: #{cfg_name} ({modch['id']})")
        if ow is not None:
            discord_api("PATCH", f"/channels/{modch['id']}", {"permission_overwrites": ow})
            print("  set private (hidden from regular members)")
    else:
        payload = {
            "name": cfg_name, "type": 0, "parent_id": cat["id"],
            "topic": "Control which live-stats fields show. React with a field's "
                     "emoji below to toggle it.",
        }
        if ow is not None:
            payload["permission_overwrites"] = ow
        modch = discord_api("POST", f"/guilds/{GUILD_ID}/channels", payload)
        if not modch:
            raise SystemExit("failed to create the mod-config channel")
        vis = "hidden from members" if ow is not None else "visible to all"
        print(f"created mod-config channel: #{cfg_name} ({modch['id']}) [{vis}]")

    cfg = load_config()
    body = {"content": render_panel_text(cfg)}
    existing = cfg.get("panel") or {}
    pid = existing.get("message_id") if existing.get("channel_id") == modch["id"] else None
    if pid and discord_api("PATCH", f"/channels/{modch['id']}/messages/{pid}", body) is not None:
        add_panel_reactions(modch["id"], pid)  # reuse existing panel
    else:
        panel = discord_api("POST", f"/channels/{modch['id']}/messages", body)
        if panel:
            discord_api("PUT", f"/channels/{modch['id']}/pins/{panel['id']}")
            cfg["panel"] = {"channel_id": modch["id"], "message_id": panel["id"]}
            add_panel_reactions(modch["id"], panel["id"])
    save_json(CONFIG_PATH, cfg)

    if WDGO_KEY:
        print("populating stat channels...")
        tick(load_json(STATE_PATH, {"tick": 0}), sample=False)
        print("done.")
    else:
        print("set WDGWARS_API_KEY and run `--once` to populate the stat channels.")

    print()
    print(f"Setup complete. Config written to {CONFIG_PATH}.")
    print("Keep it updating with any of:")
    print("  python live_stats_channels.py            # run continuously (5-min loop)")
    print("  the systemd unit, or the GitHub Actions workflow (see the README)")
    print(f"Change which fields show by reacting on the panel in #{cfg_name}, "
          "or edit the config file.")
    return 0


# ── tick ─────────────────────────────────────────────────────────────────────
def tick(state: dict, sample: bool = False) -> None:
    cfg = load_config()
    poll_reactions(cfg)

    t = state.get("tick", 0)
    raw = {k: v for k, v in gather_stats(sample=sample).items()
           if field_enabled(cfg, k)}

    channels = reconcile_channels(raw.keys())
    if not channels:
        return
    changes = 0
    # Rename only when the value actually changed (the channel name already
    # carries the last value), which keeps well inside Discord's rename rate
    # limit. The Updated field changes with the clock, so it refreshes on its own.
    for lbl, new_name in raw.items():
        ch = channels.get(lbl)
        if not ch or ch["name"] == new_name:
            continue
        if discord_api("PATCH", f"/channels/{ch['id']}", {"name": new_name}) is not None:
            changes += 1
    state["tick"] = t + 1
    state["prev_raw"] = raw
    state["updated_iso"] = datetime.now(timezone.utc).isoformat()
    save_json(STATE_PATH, state)
    log.info("tick %d: %d/%d channels updated", t, changes, len(raw))


def dry_run(sample: bool) -> int:
    cfg = load_config()
    stats = gather_stats(sample=sample)
    print(f"category: {CATEGORY_NAME!r}")
    print("fields (✅ shown, ⬜ hidden by config):")
    for lbl in FIELD_ORDER:
        mark = "✅" if field_enabled(cfg, lbl) else "⬜"
        print(f"  {mark} {stats.get(lbl, '(missing)')}")
    return 0


def preflight():
    """Validate config against Discord + WDGoWars before doing any work.
    Returns (checks, can_write, key_ok): checks is a list of (ok, message);
    can_write is True only if the bot token + guild + Manage Channels all pass;
    key_ok is True/False/None (None = no key set to test)."""
    checks = []
    me = discord_api("GET", "/users/@me")
    if not (isinstance(me, dict) and me.get("id")):
        checks.append((False, "Discord rejected the bot token. Re-copy it from the "
                              "Developer Portal (Bot -> Reset Token)."))
        return checks, False, None
    checks.append((True, f"bot token OK (logged in as {me.get('username', 'bot')})"))
    invite = (f"https://discord.com/oauth2/authorize?client_id={me['id']}"
              "&scope=bot&permissions=16")

    guild = discord_api("GET", f"/guilds/{GUILD_ID}")
    if not (isinstance(guild, dict) and guild.get("id")):
        checks.append((False, f"Bot is not in server {GUILD_ID} (or the id is wrong). "
                              f"Invite it: {invite}"))
        return checks, False, None
    checks.append((True, f"bot is in server {guild.get('name', GUILD_ID)!r}"))

    perms = 0
    member = discord_api("GET", f"/guilds/{GUILD_ID}/members/{me['id']}")
    roles = discord_api("GET", f"/guilds/{GUILD_ID}/roles")
    if isinstance(roles, list) and isinstance(member, dict):
        held = set(member.get("roles", [])) | {GUILD_ID}  # @everyone role id == guild id
        for r in roles:
            if r.get("id") in held:
                try:
                    perms |= int(r.get("permissions", 0))
                except (TypeError, ValueError):
                    pass
    # 0x8 = Administrator, 0x10 = Manage Channels
    can_write = bool(guild.get("owner_id") == me["id"] or perms & 0x8 or perms & 0x10)
    checks.append((can_write, "bot has Manage Channels" if can_write else
                   f"Bot lacks Manage Channels in this server. Re-invite: {invite}"))

    key_ok = None
    if WDGO_KEY:
        m2, _, status = wdgo_api("/endpoint/me")
        key_ok = bool(m2 and m2.get("ok"))
        checks.append((key_ok, f"WDGoWars key OK (user {m2.get('username')})" if key_ok
                       else f"wdgwars.pl rejected your API key (HTTP {status}). "
                            "Re-copy it from wdgwars.pl/profile."))
    else:
        checks.append((False, "WDGWARS_API_KEY is not set."))
    return checks, can_write, key_ok


def print_checks(checks) -> None:
    for ok, msg in checks:
        print(f"  {'✓' if ok else '✗'} {msg}")


def run_wizard() -> bool:
    """Interactively collect the three secrets, write .env, and update the live
    config. Only runs on a real terminal; returns False otherwise so cron/CI
    fall back to env/.env. Secrets are entered with hidden input."""
    if not sys.stdin.isatty():
        return False
    import getpass
    print("No configuration found. Let's set it up (saved to a local .env file).\n"
          "Get your API key from wdgwars.pl/profile, and your bot token + server id\n"
          "from the Discord Developer Portal (see the README if you have not made a bot).\n")
    key = getpass.getpass("WDGoWars API key (hidden): ").strip()
    token = getpass.getpass("Discord bot token (hidden): ").strip()
    guild = input("Discord server (guild) id: ").strip()
    if not (key and token and guild):
        print("Setup cancelled (all three values are required).")
        return False
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(env_path):
        if input(f"{env_path} already exists. Overwrite? [y/N] ").strip().lower() != "y":
            print("Kept existing .env; using its values.")
            return False
    with open(env_path, "w") as f:
        f.write(f"WDGWARS_API_KEY={key}\nDISCORD_BOT_TOKEN={token}\n"
                f"DISCORD_GUILD_ID={guild}\n")
    try:
        os.chmod(env_path, 0o600)
    except OSError:
        pass
    global TOKEN, WDGO_KEY, GUILD_ID
    TOKEN, WDGO_KEY, GUILD_ID = token, key, guild
    os.environ.update({"DISCORD_BOT_TOKEN": token, "WDGWARS_API_KEY": key,
                       "DISCORD_GUILD_ID": guild})
    print(f"Saved to {env_path} (readable only by you).\n")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Show WDGoWars stats as Discord voice-channel labels.")
    parser.add_argument("--once", action="store_true",
                        help="run a single update and exit")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the channel names that would be set; no Discord writes")
    parser.add_argument("--sample", action="store_true",
                        help="use canned stats (no API key, no network)")
    parser.add_argument("--setup", action="store_true",
                        help="create the category + mod-config channel + panel, then exit")
    parser.add_argument("--check", action="store_true",
                        help="validate token/server/permissions/key and exit (no changes)")
    args = parser.parse_args()

    # Field labels contain emoji; make sure stdout can render them even on a
    # non-UTF-8 console (e.g. Windows cp1252).
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass

    logging.basicConfig(
        level=os.environ.get("STATS_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if args.sample and not (args.once or args.dry_run):
        raise SystemExit("--sample only makes sense with --once or --dry-run")

    if args.dry_run:
        return dry_run(args.sample)

    # First run with nothing configured: offer an interactive setup. TTY only,
    # so cron/CI just fall through to the env/.env values.
    if not args.sample and not (TOKEN and GUILD_ID):
        run_wizard()

    if args.check:
        print("Config check:")
        checks, can_write, key_ok = preflight()
        print_checks(checks)
        return 0 if (can_write and key_ok) else 1

    if not TOKEN:
        raise SystemExit("set DISCORD_BOT_TOKEN (or run it in a terminal for the setup wizard)")
    if not GUILD_ID:
        raise SystemExit("set DISCORD_GUILD_ID (or run it in a terminal for the setup wizard)")

    if args.setup:
        checks, can_write, _ = preflight()
        print_checks(checks)
        if not can_write:
            raise SystemExit("cannot create channels until the issues above are fixed")
        return setup_discord()

    if not args.sample and not WDGO_KEY:
        raise SystemExit("set WDGWARS_API_KEY (or pass --sample)")

    state = load_json(STATE_PATH, {"tick": 0})
    if args.once:
        tick(state, sample=args.sample)
        return 0

    # Continuous mode: validate once up front so a misconfig fails loudly at
    # startup instead of looping silently.
    if not args.sample:
        checks, can_write, key_ok = preflight()
        print_checks(checks)
        if not can_write or key_ok is False:
            raise SystemExit("fix the config issues above and restart")
    log.info("live-stats channels starting (interval=%ss)", INTERVAL)
    while True:
        try:
            tick(state, sample=args.sample)
        except Exception as e:
            log.exception("tick failed: %s", scrub(str(e)))
        time.sleep(INTERVAL)


if __name__ == "__main__":
    sys.exit(main())
