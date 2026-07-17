#!/usr/bin/env python3
"""Live WDGoWars stats as Discord voice-channel labels.

Renames a set of voice channels in a "live stats" category so their names
show your current WDGoWars numbers, updated on a schedule. This is the
at-a-glance display (User / Team / Total / WiFi / BLE / ADS-B / Mesh / Footprint
/ Rank / API), the kind you can leave pinned in a server sidebar.

Unlike discord_stats_webhook.py (which only needs a webhook URL), this needs a
Discord **bot** because renaming channels is a bot action. Standard library
only.

Setup
-----
1. Get your WDGoWars API key from https://wdgwars.pl/profile
2. Create a Discord bot (https://discord.com/developers/applications), copy its
   token, and invite it with "Manage Channels" + "Manage Roles" + "Manage
   Messages". Only Manage Channels is strictly required; Manage Roles lets setup
   make #stats-config private, and Manage Messages lets it pin the section panels.
   Without the latter two, setup still works (public config channel, unpinned
   panels). `--check` prints the exact invite URL.
3. The script creates one "live stats" category and manages the voice channels
   inside it. The mod-config panel is split into per-section messages (📊 Account,
   🖥 Devices, 🌐 Territory, ⚙ Status), each with its own toggle reactions.
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
import shutil
import subprocess
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
# All stat voice channels live in one category. The config panel (in the private
# mod channel) is what's split by section, not the voice channels.
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
USER_AGENT = "wdgwars-discord-stats/1.4 (+https://github.com/Yggdrasil-AI-labs/wdgwars-discord-stats)"

# The stat fields, in panel order. Also the set of valid field names.
FIELD_ORDER = ["User", "Team", "Gang Size", "Gang APs", "Updated", "Total",
               "WiFi", "BLE", "ADS-B", "Mesh", "Reinforced", "Footprint",
               "Today", "Week", "Credits", "Quota", "Rank", "API"]

# Sections group the config panel (one message per section in the private mod
# channel) and set the display order of the voice channels within the single
# category. The Devices section is special: it has no fixed fields; it renders one
# voice channel per rig from the /api/me `devices` array and is toggled as a whole
# via the "Devices" pseudo-field.
DEVICES_TOGGLE = "Devices"
SECTIONS = [
    ("Account",   "📊", ["User", "Team", "Total", "WiFi", "BLE", "ADS-B",
                          "Mesh", "Reinforced", "Rank"]),
    ("Devices",   "🖥", None),
    ("Territory", "🌐", ["Footprint", "Gang Size", "Gang APs"]),
    ("Status",    "⚙", ["Updated", "Today", "Week", "Credits", "Quota", "API"]),
]
SECTION_EMOJI = {name: emoji for name, emoji, _ in SECTIONS}


def section_toggles(section_name: str) -> list:
    """Panel toggle labels for a section: its fields, or just the whole-section
    Devices toggle for the Devices section."""
    for name, _emoji, fields in SECTIONS:
        if name == section_name:
            return list(fields) if fields else [DEVICES_TOGGLE]
    return []


def panel_labels() -> list:
    """All toggle labels across every section, in order (for env-hide lookups)."""
    out = []
    for name, _emoji, _fields in SECTIONS:
        out.extend(section_toggles(name))
    return out


# Labels to hide via environment: STATS_FIELDS_OFF is a comma-separated list of
# labels (any field, or "Devices"), matched case-insensitively (e.g. "ble,rank").
# Useful where the config file does not persist, e.g. GitHub Actions.
_FIELD_BY_CASEFOLD = {lbl.casefold(): lbl
                      for lbl in FIELD_ORDER + [DEVICES_TOGGLE]}
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
    "devices": [
        {"device_name": "Cardputer", "networks": 61234, "aircraft": 0,
         "mesh": 0, "uploads": 210, "total": 61234},
        {"device_name": "Sleipnir", "networks": 30112, "aircraft": 4471,
         "mesh": 62, "uploads": 88, "total": 34645},
        {"device_name": "Pixel 8", "networks": 12717, "aircraft": 0,
         "mesh": 0, "uploads": 141, "total": 12717},
    ],
}
# Canned /api/me/cells for --sample: three tiles, 156 APs total.
SAMPLE_CELLS = {
    "ok": True, "grid_lat": 0.02, "grid_lng": 0.02, "count": 3,
    "cells": [{"lat": 41.5, "lng": -80.94, "aps": 40},
              {"lat": 41.56, "lng": -82.84, "aps": 100},
              {"lat": 41.74, "lng": -81.04, "aps": 16}],
}

log = logging.getLogger("live-stats")

# One reaction "button" emoji per field (single-codepoint so it encodes cleanly
# for the Discord reactions API). Reacting on the config panel toggles the field.
REACTION_EMOJI = {
    "User": "👤", "Team": "🏴", "Gang Size": "👥", "Gang APs": "🏰",
    "Updated": "🕒", "Total": "📊", "WiFi": "📶", "BLE": "🔵", "ADS-B": "🛫",
    "Mesh": "📡", "Reinforced": "🧱", "Footprint": "🗺", "Today": "📅",
    "Week": "📆", "Credits": "🪙", "Quota": "⛽", "Rank": "🎯", "API": "🔌",
    "Devices": "🖥",
}
_BOT_ID = None


def bot_id() -> str:
    """The bot's own user id (cached) so its panel reactions can be told apart
    from a user's toggle press."""
    global _BOT_ID
    if _BOT_ID is None:
        me = discord_api("GET", "/users/@me")
        if isinstance(me, dict) and me.get("id"):
            _BOT_ID = me["id"]
        else:
            return ""  # transient failure: don't cache it, retry next call
    return _BOT_ID


# Discord permission bits and what each is for here:
#   Manage Channels  — create/rename/delete the stat voice channels (required).
#   Manage Roles     — set the overwrite that makes #stats-config private.
#   Manage Messages  — pin the section panels in #stats-config.
# The invite requests all three so setup works cleanly out of the box; a missing
# one degrades gracefully (public config channel, and/or unpinned panels).
PERM_ADMIN = 0x8
PERM_MANAGE_CHANNELS = 0x10
PERM_MANAGE_MESSAGES = 0x2000
PERM_MANAGE_ROLES = 0x10000000
INVITE_PERMS = (PERM_MANAGE_CHANNELS | PERM_MANAGE_MESSAGES
                | PERM_MANAGE_ROLES)  # 268443664


def invite_url(client_id: str) -> str:
    """Bot invite URL requesting Manage Channels + Manage Messages + Manage Roles."""
    return (f"https://discord.com/oauth2/authorize?client_id={client_id}"
            f"&scope=bot&permissions={INVITE_PERMS}")


def config_overwrites():
    """Permission overwrites that hide the mod channel from @everyone while
    letting the bot view/post/manage it. None if privacy is disabled. (Server
    admins still see it, Discord's Administrator permission bypasses overwrites.)
    Applying these requires the bot to hold Manage Roles."""
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


# ── config panel (one message per section) ────────────────────────────────────
def render_section_panel(section_name: str, cfg: dict) -> str:
    """The control-panel message for one section: a header plus its field toggles.
    Posted as its own message in the private mod channel, so the sections are
    split out there rather than crammed into a single panel."""
    emoji = SECTION_EMOJI.get(section_name, "•")
    lines = [f"**{emoji} {section_name}** — react to show/hide:", ""]
    for lbl in section_toggles(section_name):
        state = "✅ shown " if field_enabled(cfg, lbl) else "⬜ hidden"
        lines.append(f"{REACTION_EMOJI.get(lbl, '•')}  {state}  {lbl}")
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


def footprint_aps(cells):
    """Total APs you own, summed from the /api/me/cells server-aggregated per-cell
    counts. This is the ownership-engine AP total, the number /api/me/aps can't
    give once it truncates its point list. Returns None when the cells payload is
    missing or invalid, so the caller can simply omit the Footprint field rather
    than show a wrong zero."""
    if not isinstance(cells, dict) or not cells.get("ok"):
        return None
    rows = cells.get("cells")
    if not isinstance(rows, list):
        return None
    return sum(c["aps"] for c in rows
               if isinstance(c, dict) and isinstance(c.get("aps"), int))


def device_channels(me) -> dict:
    """Ordered {label: channel_name} for the Devices section, one entry per rig
    from the /api/me `devices` array. `label` is the sanitized device_name (the
    key the row is grouped on); the channel name shows its networks count. Empty
    dict when there is no devices array (older server). Colliding sanitized names
    get a numeric suffix so two rigs never fight over one channel."""
    rows = me.get("devices")
    if not isinstance(rows, list):
        return {}
    out = {}
    for d in rows:
        if not isinstance(d, dict):
            continue
        raw = d.get("device_name")
        label = " ".join((str(raw) if raw else "unnamed")
                         .replace(":", " ").replace("│", " ").split())[:80] or "unnamed"
        key, i = label, 2
        while key in out:
            key, i = f"{label} ({i})", i + 1
        nets = d.get("networks")
        out[key] = f"🖥 {key}: {fmt_int(nets) if isinstance(nets, int) else '?'} nets"
    return out


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


def gather_stats(sample: bool = False):
    """Build the label->value map for every field (before visibility filter).
    Returns (stats, devices, api_ok): stats is the fixed-field map, devices is
    the per-rig {label: channel_name} for the Devices section, and api_ok is
    False when WDGoWars was unreachable so the caller can avoid overwriting good
    numbers with the zero fallbacks."""
    now_local = datetime.now(TZ)
    tz_label = now_local.tzname() or "UTC"

    if sample:
        me, latency, status = SAMPLE_ME, 120, 200
        lb = {"gangs": [{"name": "Sample Gang", "member_count": 42,
                         "ap_count": 1234567}]}
        cells = SAMPLE_CELLS
    else:
        me, latency, status = wdgo_api("/endpoint/me")
        lb = (wdgo_api("/endpoint/leaderboard")[0] or {}) if me else {}
        cells = (wdgo_api("/endpoint/me/cells")[0] or {}) if me else {}
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
    # Footprint (total APs owned) from /api/me/cells, only when that endpoint
    # answered. Omitted (no channel) on a server without it or a failed fetch,
    # rather than shown as a misleading 0.
    aps = footprint_aps(cells)
    if aps is not None:
        stats["Footprint"] = f"🗺 Footprint: {fmt_int(aps)} APs"

    # Gang stats from the leaderboard, only when the caller is in a gang that
    # appears on the board. Solo drivers simply don't get these channels.
    if ge:
        stats["Gang Size"] = f"👥 Gang Size: {fmt_int(ge.get('member_count', 0))}"
        stats["Gang APs"] = f"🏰 Gang APs: {fmt_int(ge.get('ap_count', 0))}"
    return stats, device_channels(me), api_ok


# ── channel plumbing ─────────────────────────────────────────────────────────
def label_of(name: str) -> str:
    """Recover a field label from a channel name like '📊 Total: 1 234 ·'."""
    if ":" not in name:
        return name
    head = name.split(":")[0].strip()
    parts = head.split(" ", 1)
    return parts[1] if len(parts) > 1 else head


def reconcile_channels(active):
    """Make the single stats category hold exactly the voice channels in `active`
    ({label: channel_name}, in display order). Creates missing channels and
    deletes stale ones. A channel is 'ours' to manage if its label is a known
    field or it is a device channel (🖥-prefixed name); a manually-added channel
    is left alone. Returns {label: channel}."""
    chs = discord_api("GET", f"/guilds/{GUILD_ID}/channels") or []
    cat = next((c for c in chs if c["type"] == 4 and c["name"] == CATEGORY_NAME), None)
    if not cat:
        log.error("category %r not found in guild (run --setup)", CATEGORY_NAME)
        return {}
    present = {label_of(c["name"]): c for c in chs
               if c["type"] == 2 and c.get("parent_id") == cat["id"]}
    for lbl, ch in list(present.items()):
        ours = lbl in FIELD_ORDER or ch["name"].startswith("🖥 ")
        if ours and lbl not in active:
            if discord_api("DELETE", f"/channels/{ch['id']}") is not None:
                log.info("removed %r channel", lbl)
            present.pop(lbl, None)
    for pos, lbl in enumerate(active):
        if lbl not in present:
            # Create with the full target name up front (not a bare label). A bare
            # intermediate that failed to rename would collide with the real
            # channel under label_of and couldn't be reconciled away.
            created = discord_api("POST", f"/guilds/{GUILD_ID}/channels", {
                "name": active[lbl], "type": 2, "parent_id": cat["id"], "position": pos,
            })
            if created:
                present[lbl] = created
                log.info("added %r channel", lbl)
    return present


def add_panel_reactions(channel_id: str, message_id: str, labels) -> None:
    """Put one clickable reaction on a section message for each of `labels`
    (idempotent)."""
    for lbl in labels:
        emoji = REACTION_EMOJI.get(lbl)
        if not emoji:
            continue
        enc = urllib.parse.quote(emoji)
        discord_api("PUT",
                    f"/channels/{channel_id}/messages/{message_id}/reactions/{enc}/@me")


def _pin_message(channel_id: str, message_id: str) -> None:
    """Pin a panel message. Pinning needs Manage Messages; if the bot lacks it the
    message still works (just unpinned), so log a hint rather than treating it as
    a failure."""
    if discord_api("PUT", f"/channels/{channel_id}/pins/{message_id}") is None:
        log.info("couldn't pin a panel message (bot needs Manage Messages to pin); "
                 "the panel still works unpinned")


def update_panel(cfg: dict) -> None:
    """Edit each section's pinned control-panel message to reflect the current
    field state. Reposts and re-pins any that were deleted. No-op if setup never
    ran. Panel layout: {channel_id, messages: {section: message_id}}."""
    panel = cfg.get("panel") or {}
    ch = panel.get("channel_id")
    if not ch:
        return
    msgs = panel.setdefault("messages", {})
    dirty = False
    for name, _emoji, _fields in SECTIONS:
        body = {"content": render_section_panel(name, cfg)}
        mid = msgs.get(name)
        if mid and discord_api("PATCH", f"/channels/{ch}/messages/{mid}", body) is not None:
            continue
        new = discord_api("POST", f"/channels/{ch}/messages", body)
        if new:
            _pin_message(ch, new["id"])
            msgs[name] = new["id"]
            add_panel_reactions(ch, new["id"], section_toggles(name))
            dirty = True
    if dirty:
        save_json(CONFIG_PATH, cfg)


def poll_reactions(cfg: dict) -> bool:
    """Toggle any field whose reaction a user pressed on a section panel, then
    clear that reaction so it acts like a button. Returns True if config changed."""
    panel = cfg.get("panel") or {}
    ch = panel.get("channel_id")
    msgs = panel.get("messages") or {}
    if not (ch and msgs):
        return False
    me_id = bot_id()
    changed = False
    for name, _emoji, _fields in SECTIONS:
        mid = msgs.get(name)
        if not mid:
            continue
        # One read per section message: the bot's own button counts as 1; a user
        # press makes a reaction's count >= 2. Only then do we fetch who pressed.
        message = discord_api("GET", f"/channels/{ch}/messages/{mid}")
        if not isinstance(message, dict):
            continue
        counts = {r.get("emoji", {}).get("name"): r.get("count", 0)
                  for r in message.get("reactions", [])}
        for label in section_toggles(name):
            emoji = REACTION_EMOJI.get(label)
            if not emoji or counts.get(emoji, 0) < 2:
                continue
            enc = urllib.parse.quote(emoji)
            users = discord_api(
                "GET", f"/channels/{ch}/messages/{mid}/reactions/{enc}?limit=20")
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
                    f"/channels/{ch}/messages/{mid}/reactions/{enc}/{u['id']}")
    if changed:
        save_json(CONFIG_PATH, cfg)
        update_panel(cfg)
    return changed


def setup_discord(install_runner: bool = True) -> int:
    """Create the section categories, a mod-config text channel, and a pinned
    control panel, then populate the channels once. Idempotent: reuses existing
    categories/channels by name. The mod-config channel id + panel location are
    written to the config file so the poller picks them up with no extra
    environment variables.

    Unless install_runner is False, setup finishes by installing the
    boot-persistent auto-updater (the same thing --schedule does), so the display
    keeps refreshing. Setup alone only populates the channels once; without a
    running updater they would freeze at the first values, which is the most
    common "my channels went stale" report. Pass install_runner=False (--setup
    --no-schedule) when something else drives updates, e.g. GitHub Actions or a
    hand-managed unit."""
    chs = discord_api("GET", f"/guilds/{GUILD_ID}/channels")
    if not isinstance(chs, list):
        raise SystemExit("could not list guild channels (check the bot token and guild id)")

    # One category holds every stat voice channel (the split is in the panel).
    existing_cats = {c["name"]: c for c in chs if c["type"] == 4}
    cat = existing_cats.get(CATEGORY_NAME)
    if cat:
        print(f"category exists: {CATEGORY_NAME} ({cat['id']})")
    else:
        cat = discord_api("POST", f"/guilds/{GUILD_ID}/channels",
                          {"name": CATEGORY_NAME, "type": 4})
        if not cat:
            raise SystemExit("failed to create the live-stats category")
        print(f"created category: {CATEGORY_NAME} ({cat['id']})")
    parent_id = cat["id"]

    cfg_name = os.environ.get("STATS_CONFIG_CHANNEL_NAME", "stats-config")
    ow = config_overwrites()
    priv_note = (
        "  To hide it: grant the bot 'Manage Roles' (re-invite with "
        f"{invite_url(bot_id())} ) and re-run --setup, or set the channel private "
        "by hand and give the bot access.")
    modch = next((c for c in chs if c["type"] == 0 and c["name"] == cfg_name), None)
    if modch:
        print(f"mod-config channel exists: #{cfg_name} ({modch['id']})")
        if ow is not None:
            if discord_api("PATCH", f"/channels/{modch['id']}",
                           {"permission_overwrites": ow}) is not None:
                print("  set private (hidden from regular members)")
            else:
                print("  could NOT set it private: the bot needs 'Manage Roles' to "
                      "change channel privacy. Left as-is.")
                print(priv_note)
    else:
        base = {
            "name": cfg_name, "type": 0, "parent_id": parent_id,
            "topic": "Control which live-stats fields show. React with a field's "
                     "emoji below to toggle it.",
        }
        if ow is None:
            modch = discord_api("POST", f"/guilds/{GUILD_ID}/channels", base)
            if modch:
                print(f"created mod-config channel: #{cfg_name} ({modch['id']}) [visible to all]")
        else:
            modch = discord_api("POST", f"/guilds/{GUILD_ID}/channels",
                                dict(base, permission_overwrites=ow))
            if modch:
                print(f"created mod-config channel: #{cfg_name} ({modch['id']}) [hidden from members]")
            else:
                # Creating a channel WITH overwrites needs Manage Roles; the bot
                # may only have Manage Channels. Fall back to a public channel so
                # setup still completes, and say how to lock it down.
                print("  couldn't create it private (the bot needs 'Manage Roles' to "
                      "set channel privacy). Creating it public instead.")
                modch = discord_api("POST", f"/guilds/{GUILD_ID}/channels", base)
                if modch:
                    print(f"created mod-config channel: #{cfg_name} ({modch['id']}) [PUBLIC]")
                    print(priv_note)
        if not modch:
            raise SystemExit("failed to create the mod-config channel")

    cfg = load_config()
    panel = cfg.setdefault("panel", {})
    if panel.get("channel_id") != modch["id"]:
        panel["messages"] = {}          # channel changed -> old message ids are stale
    panel["channel_id"] = modch["id"]
    panel.pop("message_id", None)       # drop the pre-split single-panel field
    msgs = panel.setdefault("messages", {})
    for name, _emoji, _fields in SECTIONS:
        body = {"content": render_section_panel(name, cfg)}
        mid = msgs.get(name)
        if mid and discord_api("PATCH", f"/channels/{modch['id']}/messages/{mid}", body) is not None:
            add_panel_reactions(modch["id"], mid, section_toggles(name))
        else:
            m = discord_api("POST", f"/channels/{modch['id']}/messages", body)
            if m:
                _pin_message(modch["id"], m["id"])
                msgs[name] = m["id"]
                add_panel_reactions(modch["id"], m["id"], section_toggles(name))
    save_json(CONFIG_PATH, cfg)
    print(f"posted {len(msgs)} section panels to #{cfg_name}")

    if WDGO_KEY:
        print("populating stat channels...")
        tick(load_json(STATE_PATH, {"tick": 0}), sample=False)
        print("done.")
    else:
        print("set WDGWARS_API_KEY and run `--once` to populate the stat channels.")

    print()
    print(f"Setup complete. Config written to {CONFIG_PATH}.")
    if install_runner:
        print()
        print("Installing the auto-updater so the display keeps refreshing...")
        install_schedule()  # so setup never leaves a display that populates once then freezes
    else:
        print("Auto-updater NOT installed (--no-schedule). The channels are populated")
        print("but will freeze until something updates them. Keep them fresh with:")
        print("  python live_stats_channels.py --schedule   # install it later")
        print("  python live_stats_channels.py              # or run the loop yourself")
        print("  or the GitHub Actions workflow (see the README)")
    print()
    print(f"Change which fields show by reacting on the panel in #{cfg_name}, "
          "or edit the config file.")
    return 0


# ── tick ─────────────────────────────────────────────────────────────────────
def active_sections(cfg: dict, stats: dict, devices: dict) -> dict:
    """{section: {label: channel_name}} for every section, honoring visibility.
    A fixed field appears when it was produced this tick and is enabled; the
    Devices section carries the per-rig map when its whole-section toggle is on."""
    by = {}
    for name, _emoji, fields in SECTIONS:
        if fields is None:
            by[name] = dict(devices) if field_enabled(cfg, DEVICES_TOGGLE) else {}
        else:
            by[name] = {lbl: stats[lbl] for lbl in fields
                        if lbl in stats and field_enabled(cfg, lbl)}
    return by


def active_channels(cfg: dict, stats: dict, devices: dict) -> dict:
    """Flat {label: channel_name} for the single category, honoring visibility,
    in section order (Account fields, then per-rig device channels, then Territory,
    then Status)."""
    by = active_sections(cfg, stats, devices)
    active = {}
    for name, _emoji, _fields in SECTIONS:
        active.update(by[name])
    return active


def _refresh_api_channel(api_name: str) -> None:
    """Rename only the API-status channel. Used when WDGoWars is down so the
    status flips to DOWN without touching the data channels. No-op if the field
    is hidden or the category/channel is absent."""
    if not api_name:
        return
    chs = discord_api("GET", f"/guilds/{GUILD_ID}/channels") or []
    cat = next((c for c in chs if c["type"] == 4 and c["name"] == CATEGORY_NAME), None)
    if not cat:
        return
    for c in chs:
        if (c["type"] == 2 and c.get("parent_id") == cat["id"]
                and label_of(c["name"]) == "API"):
            if c["name"] != api_name:
                discord_api("PATCH", f"/channels/{c['id']}", {"name": api_name})
            return


def tick(state: dict, sample: bool = False) -> None:
    cfg = load_config()
    poll_reactions(cfg)

    t = state.get("tick", 0)
    stats, devices, api_ok = gather_stats(sample=sample)
    active = active_channels(cfg, stats, devices)

    # When WDGoWars is unreachable its counts fall back to zero. Don't repaint
    # the whole dashboard with 0s over good numbers: flip only the API channel
    # to DOWN and leave the data channels showing their last good values.
    if not sample and not api_ok:
        _refresh_api_channel(active.get("API"))
        state["tick"] = t + 1
        state["updated_iso"] = datetime.now(timezone.utc).isoformat()
        save_json(STATE_PATH, state)
        log.warning("tick %d: WDGoWars API down, left data channels unchanged", t)
        return

    channels = reconcile_channels(active)
    if not channels:
        return
    changes = 0
    # Rename only when the value actually changed (the channel name already
    # carries the last value), which keeps well inside Discord's rename rate
    # limit. The Updated field changes with the clock, so it refreshes on its own.
    for lbl, new_name in active.items():
        ch = channels.get(lbl)
        if not ch or ch["name"] == new_name:
            continue
        if discord_api("PATCH", f"/channels/{ch['id']}", {"name": new_name}) is not None:
            changes += 1
    state["tick"] = t + 1
    state["prev_raw"] = active
    state["updated_iso"] = datetime.now(timezone.utc).isoformat()
    save_json(STATE_PATH, state)
    log.info("tick %d: %d/%d channels updated", t, changes, len(active))


def dry_run(sample: bool) -> int:
    cfg = load_config()
    stats, devices, _ = gather_stats(sample=sample)
    print(f"category: {CATEGORY_NAME!r}  (all channels; panel is split by section)")
    print("channels (✅ shown, ⬜ hidden by config), grouped by panel section:")
    for name, emoji, fields in SECTIONS:
        print(f"\n  {emoji} {name}")
        if fields is None:
            mark = "✅" if field_enabled(cfg, DEVICES_TOGGLE) else "⬜"
            if not devices:
                print(f"    {mark} 🖥 (no rigs reported)")
            for val in devices.values():
                print(f"    {mark} {val}")
        else:
            for lbl in fields:
                mark = "✅" if field_enabled(cfg, lbl) else "⬜"
                print(f"    {mark} {stats.get(lbl, '(missing)')}")
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
    invite = invite_url(me["id"])

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
    is_admin = bool(guild.get("owner_id") == me["id"] or perms & PERM_ADMIN)
    can_write = bool(is_admin or perms & PERM_MANAGE_CHANNELS)
    checks.append((can_write, "bot has Manage Channels" if can_write else
                   f"Bot lacks Manage Channels in this server. Re-invite: {invite}"))

    # Manage Roles is only needed to auto-set the config channel private. Missing
    # it is not fatal (setup falls back to a public channel), so this is a
    # heads-up, not a blocker.
    if CONFIG_PRIVATE:
        has_roles = bool(is_admin or perms & PERM_MANAGE_ROLES)
        checks.append((has_roles,
                       "bot has Manage Roles (can make #stats-config private)"
                       if has_roles else
                       "bot lacks Manage Roles, so #stats-config can't be made "
                       "private automatically; setup will create it public. Grant "
                       f"Manage Roles (re-invite) to fix, or set STATS_CONFIG_PRIVATE=off. {invite}"))

    # Manage Messages only affects whether the section panels get pinned. Not
    # fatal (they still work unpinned), so this is a heads-up too.
    has_msgs = bool(is_admin or perms & PERM_MANAGE_MESSAGES)
    checks.append((has_msgs,
                   "bot has Manage Messages (can pin the section panels)"
                   if has_msgs else
                   "bot lacks Manage Messages, so the section panels post but won't "
                   f"be pinned. Grant Manage Messages (re-invite) to fix. {invite}"))

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


# ── auto-run install (--schedule) ─────────────────────────────────────────────
TASK_NAME = "wdgwars-discord-stats"
SERVICE_NAME = "wdgwars-live-stats.service"


def _pythonw() -> str:
    """The windowless Python for this interpreter (pythonw.exe on Windows, so a
    scheduled run does not flash a console window every tick). Falls back to the
    normal interpreter if pythonw is missing."""
    exe = sys.executable or "python"
    if os.name == "nt":
        cand = os.path.join(os.path.dirname(exe), "pythonw.exe")
        if os.path.exists(cand):
            return cand
    return exe


def _interval_minutes() -> int:
    return max(1, INTERVAL // 60)


def install_schedule() -> int:
    """Install a boot-persistent, quiet auto-runner for the current platform:
    a windowless Scheduled Task on Windows, a lingering systemd user service on
    Linux/Pi, or a printed cron line as a fallback."""
    script = os.path.abspath(__file__)
    if os.name == "nt":
        return _install_windows(script)
    if shutil.which("systemctl"):
        return _install_systemd(script)
    return _install_cron(script)


def _install_windows(script: str) -> int:
    mins = _interval_minutes()
    # pythonw.exe + --quiet: no console window pops up on each run, no log spam.
    tr = f'"{_pythonw()}" "{script}" --once --quiet'
    cmd = ["schtasks", "/Create", "/TN", TASK_NAME, "/TR", tr,
           "/SC", "MINUTE", "/MO", str(mins), "/F"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print("Could not create the scheduled task:")
        print("  " + scrub((r.stderr or r.stdout).strip()))
        return 1
    print(f"Scheduled task '{TASK_NAME}' created: runs every {mins} min, "
          "windowless (no popup), quiet.")
    print("It runs while you are logged in. Remove it with:")
    print("  python live_stats_channels.py --unschedule")
    return 0


def _install_systemd(script: str) -> int:
    unit_dir = os.path.expanduser("~/.config/systemd/user")
    os.makedirs(unit_dir, exist_ok=True)
    env_file = os.path.expanduser("~/.config/wdgwars-discord-stats/env")
    env_line = (f"EnvironmentFile={env_file}" if os.path.exists(env_file)
                else "# EnvironmentFile=%h/.config/wdgwars-discord-stats/env  "
                     "# (create it, or rely on the .env beside the script)")
    unit = (
        "[Unit]\n"
        "Description=WDGoWars live-stats Discord display\n"
        "After=network-online.target\n"
        "Wants=network-online.target\n\n"
        "[Service]\n"
        "Type=simple\n"
        f"{env_line}\n"
        f"ExecStart={sys.executable} {script} --quiet\n"
        "Restart=on-failure\n"
        "RestartSec=30\n\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )
    with open(os.path.join(unit_dir, SERVICE_NAME), "w") as f:
        f.write(unit)
    # enable-linger so the service starts at boot and survives logout on a
    # headless box (the usual "it stopped when I closed my SSH session" gotcha).
    user = os.environ.get("USER") or ""
    if user:
        subprocess.run(["loginctl", "enable-linger", user],
                       capture_output=True, text=True)
    subprocess.run(["systemctl", "--user", "daemon-reload"],
                   capture_output=True, text=True)
    r = subprocess.run(["systemctl", "--user", "enable", "--now", SERVICE_NAME],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print("Wrote the unit but could not enable it:")
        print("  " + scrub(r.stderr.strip()))
        print(f"Finish manually:  systemctl --user enable --now {SERVICE_NAME}")
        return 1
    print(f"Installed and started systemd user service '{SERVICE_NAME}'.")
    print("Runs continuously, restarts on failure, starts at boot (lingering on).")
    print(f"Follow logs:  journalctl --user -u {SERVICE_NAME} -f")
    print("Remove it with:  python live_stats_channels.py --unschedule")
    return 0


def _install_cron(script: str) -> int:
    mins = _interval_minutes()
    line = (f"*/{mins} * * * * cd {os.path.dirname(script)} && "
            f"{sys.executable} {script} --once --quiet")
    print("No systemd here. Add this line with `crontab -e` (review it first):\n")
    print("  " + line)
    print(f"\nThat runs one quiet update every {mins} min.")
    return 0


def remove_schedule() -> int:
    """Undo install_schedule() for the current platform."""
    if os.name == "nt":
        r = subprocess.run(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"],
                           capture_output=True, text=True)
        print(f"Removed scheduled task '{TASK_NAME}'." if r.returncode == 0
              else f"No task '{TASK_NAME}' to remove (or delete failed).")
        return 0
    if shutil.which("systemctl"):
        subprocess.run(["systemctl", "--user", "disable", "--now", SERVICE_NAME],
                       capture_output=True, text=True)
        try:
            os.remove(os.path.expanduser(f"~/.config/systemd/user/{SERVICE_NAME}"))
        except FileNotFoundError:
            pass
        subprocess.run(["systemctl", "--user", "daemon-reload"],
                       capture_output=True, text=True)
        print(f"Disabled and removed systemd user service '{SERVICE_NAME}'.")
        return 0
    print("Nothing auto-installed to remove (cron entries are manual: crontab -e).")
    return 0


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
                        help="create the section categories + mod-config channel + panel, "
                             "populate them, and install the auto-updater, then exit")
    parser.add_argument("--no-schedule", action="store_true",
                        help="with --setup: skip installing the auto-updater (use when "
                             "GitHub Actions or a hand-managed unit drives updates)")
    parser.add_argument("--check", action="store_true",
                        help="validate token/server/permissions/key and exit (no changes)")
    parser.add_argument("--schedule", action="store_true",
                        help="install a quiet, boot-persistent auto-runner for this "
                             "platform (windowless task on Windows, systemd on Linux/Pi)")
    parser.add_argument("--unschedule", action="store_true",
                        help="remove the auto-runner installed by --schedule")
    parser.add_argument("--quiet", action="store_true",
                        help="log warnings and errors only (used by the scheduled runner)")
    args = parser.parse_args()

    # Field labels contain emoji; make sure stdout can render them even on a
    # non-UTF-8 console (e.g. Windows cp1252).
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass

    level = "WARNING" if args.quiet else os.environ.get("STATS_LOG_LEVEL", "INFO")
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    # Removing the auto-runner needs no config, so handle it before any checks.
    if args.unschedule:
        return remove_schedule()

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
        return setup_discord(install_runner=not args.no_schedule)

    if not args.sample and not WDGO_KEY:
        raise SystemExit("set WDGWARS_API_KEY (or pass --sample)")

    if args.schedule:
        # The scheduled runs need a working config and the channels to exist.
        checks, can_write, key_ok = preflight()
        print_checks(checks)
        if not can_write or key_ok is False:
            raise SystemExit("fix the config issues above, then --schedule again")
        print("Tip: run --setup once first so the channels exist.\n")
        return install_schedule()

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
