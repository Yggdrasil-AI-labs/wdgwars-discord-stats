#!/usr/bin/env python3
"""WDGWars "war feed": event alerts to Discord, not just a snapshot.

Where discord_stats_webhook.py posts a point-in-time card and
live_stats_channels.py keeps a sidebar of current numbers, this posts a message
*when something happens*. It remembers the last state between runs and diffs it,
so it can tell you about:

- **Captures** — you took APs from someone (from /api/me `recent_captures`).
- **Territory losses** — cells you owned lost APs or disappeared (diffing
  /api/me/cells between runs). WDGWars has no defender-side loss feed, so this
  is the only way to know you're being pushed back; the tool derives it locally.
- **Rig down / recovered** — a device stopped uploading for too long, or came
  back (from the per-rig `last_upload` in /api/me `devices`).

Standard library only, like the rest of this repo. Post either through a Discord
**webhook** (simplest, no bot) or through a **bot token + channel id** (reuse a
bot you already run for live_stats_channels.py).

Setup
-----
1. Get your API key from https://wdgwars.pl/profile
2. Pick how to post:
   - Webhook: Server Settings, Integrations, Webhooks, New Webhook, Copy URL.
         export DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..."
   - Or a bot you already run (needs Send Messages in the target channel):
         export DISCORD_BOT_TOKEN="your-bot-token"
         export WARFEED_CHANNEL_ID="the-channel-id"
3. Export your key and run:
       export WDGWARS_API_KEY="your-64-char-key"
       python war_feed.py --sample --dry-run   # see the alerts, no key, no post
       python war_feed.py --seed               # remember current state, post nothing
       python war_feed.py --once               # one diff-and-post pass
       python war_feed.py                        # loop every 5 min
       python war_feed.py --schedule            # install a boot-persistent runner

The first real run **seeds** state and stays quiet on purpose, so you don't get
a flood of 20 old captures or a false rig-down. Alerts start from the next tick.

Which alerts fire is set with WARFEED_ALERTS (comma list of captures,losses,rigs;
default all). Rig-down threshold is WARFEED_RIG_STALE_HOURS (default 12).

Key safety
----------
The API key, bot token, and webhook URL are read from the environment, never
printed, and redacted from any error output. Never commit them.
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
    TZ = ZoneInfo(os.environ.get("WARFEED_TZ", "America/New_York"))
except Exception:
    TZ = timezone.utc


def _load_dotenv() -> None:
    """Populate the environment from a .env file next to this script (or
    $STATS_ENV_FILE), so users can fill in a text file instead of exporting.
    Real environment variables always win."""
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

WDGO_KEY = os.environ.get("WDGWARS_API_KEY", "").strip()
WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
CHANNEL_ID = os.environ.get("WARFEED_CHANNEL_ID", "").strip()
BASE = os.environ.get("WARFEED_BASE_URL",
                      os.environ.get("WDGWARS_BASE_URL", "https://wdgwars.pl")).rstrip("/")
STATE_PATH = os.path.expanduser(
    os.environ.get("WARFEED_STATE_PATH", "~/.wdgwars-war-feed-state.json"))
INTERVAL = int(os.environ.get("WARFEED_INTERVAL", "300"))
RIG_STALE_HOURS = float(os.environ.get("WARFEED_RIG_STALE_HOURS", "12"))
# How many past captures to remember so we never re-announce one. recent_captures
# returns up to 20; keep a comfortable multiple.
SEEN_CAP = int(os.environ.get("WARFEED_SEEN_CAP", "300"))
USER_AGENT = ("wdgwars-discord-stats/1.5 war_feed "
              "(+https://github.com/Yggdrasil-AI-labs/wdgwars-discord-stats)")

ALL_ALERTS = ("captures", "losses", "rigs")
_alerts_env = os.environ.get("WARFEED_ALERTS", "").strip()
ALERTS = ({a.strip().lower() for a in _alerts_env.split(",") if a.strip()}
          if _alerts_env else set(ALL_ALERTS))

# Embed accent colors.
COLOR_CAPTURE = 0x2ECC71   # green
COLOR_LOSS = 0xE74C3C      # red
COLOR_RIG_DOWN = 0xE67E22  # orange
COLOR_RIG_UP = 0x3498DB    # blue

log = logging.getLogger("war-feed")

# Canned data for --sample: a capture, a shrinking territory, and a stale rig.
SAMPLE_ME = {
    "ok": True, "username": "SampleDriver", "gang": "Sample Gang",
    "recent_captures": [
        {"when": "2026-07-18 14:02:11+00", "ap_count": 14, "lat": 41.49,
         "lng": -81.69, "defender_gang_id": 7, "defender_gang": "Rival Crew"},
        {"when": "2026-07-18 13:40:00+00", "ap_count": 3, "lat": 41.50,
         "lng": -81.70, "defender_gang_id": None, "defender_gang": None},
    ],
    "devices": [
        {"device_name": "Cardputer", "networks": 11800, "aircraft": 0, "mesh": 0,
         "uploads": 57, "total": 11800, "last_upload": "2026-07-19 15:55:00+00"},
        {"device_name": "Sleipnir", "networks": 1223, "aircraft": 90, "mesh": 12,
         "uploads": 40, "total": 1325, "last_upload": "2026-07-17 02:10:00+00"},
    ],
}
SAMPLE_CELLS = {
    "ok": True, "grid_lat": 0.02, "grid_lng": 0.02, "count": 3,
    "cells": [{"lat": 41.50, "lng": -80.94, "aps": 38},   # was 40 -> lost 2
              {"lat": 41.56, "lng": -82.84, "aps": 100},  # unchanged
              {"lat": 41.74, "lng": -81.04, "aps": 16}],  # unchanged
}
# The "previous" state --sample diffs against, so the sample always shows alerts.
SAMPLE_PREV = {
    "seeded": True,
    "seen_captures": ["2026-07-18 13:40:00+00|41.5|-81.7|3|None"],
    "cells": {"41.50000,-80.94000": 40, "41.56000,-82.84000": 100,
              "41.74000,-81.04000": 16, "41.60000,-81.00000": 25},  # last one vanished
    "rigs": {"Cardputer": {"last_upload": "2026-07-19 15:55:00+00", "down": False},
             "Sleipnir": {"last_upload": "2026-07-17 02:10:00+00", "down": False}},
}


# ── secrets + formatting ─────────────────────────────────────────────────────
def scrub(text: str) -> str:
    """Redact secrets from a string before logging."""
    for secret in (WDGO_KEY, BOT_TOKEN, WEBHOOK_URL):
        if secret and secret in text:
            text = text.replace(secret, "<redacted>")
    return text


def fmt_int(n) -> str:
    try:
        return f"{int(n):,}"
    except (TypeError, ValueError):
        return str(n)


def _cell_key(lat, lng) -> str:
    """Stable dict key for a cell from its (grid-center) coordinates. Rounded so
    float noise never makes the same tile look like a new one."""
    try:
        return f"{float(lat):.5f},{float(lng):.5f}"
    except (TypeError, ValueError):
        return f"{lat},{lng}"


def _capture_key(c: dict) -> str:
    """A fingerprint for one capture. recent_captures has no id, so we build one
    from the fields that identify the event. Stable across ticks for the same
    capture, distinct between different ones."""
    return "|".join(str(c.get(k, "")) for k in
                    ("when", "lat", "lng", "ap_count", "defender_gang_id"))


def parse_ts(value):
    """Parse a WDGWars timestamp into an aware datetime, or None.

    last_upload / capture `when` arrive as raw Postgres timestamptz like
    '2026-07-17 02:32:02.854003+00' (space separator, microseconds, 2-digit
    offset). datetime.fromisoformat can't handle the space or the 2-digit offset
    before Python 3.11, so normalize first, then fall back to a couple of
    strptime shapes."""
    if not isinstance(value, str) or not value.strip():
        return None
    s = value.strip().replace("T", " ")
    # Normalize a trailing "+00" / "-05" to "+00:00" so fromisoformat accepts it.
    iso = s.replace(" ", "T", 1)
    if len(iso) >= 3 and iso[-3] in "+-" and iso[-2:].isdigit():
        iso = iso + ":00"
    for candidate in (iso, iso.replace("T", " ")):
        try:
            dt = datetime.fromisoformat(candidate)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    head = s.split(".")[0].split("+")[0].strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(head, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return None


def _short_loc(lat, lng) -> str:
    try:
        return f"{float(lat):.3f}, {float(lng):.3f}"
    except (TypeError, ValueError):
        return "?"


def _map_url(lat, lng):
    """A link to the WDGWars map centered on a location, or None if coords are
    unusable. Uses only lat/lng in the fragment (no personal data in the query)."""
    try:
        return f"{BASE}/map#15/{float(lat):.5f}/{float(lng):.5f}"
    except (TypeError, ValueError):
        return None


# ── state ────────────────────────────────────────────────────────────────────
def load_state() -> dict:
    try:
        with open(STATE_PATH) as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def save_state(state: dict) -> None:
    try:
        tmp = STATE_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(state, f, indent=2)
        os.replace(tmp, STATE_PATH)
    except Exception as e:
        log.warning("write state failed: %s", scrub(str(e)))


# ── WDGWars API ─────────────────────────────────────────────────────────────
def wdgo_api(path: str, timeout: float = 10.0):
    """GET a WDGWars endpoint. Returns parsed JSON or None. Uses /endpoint/*,
    the permanent contract alias Cloudflare rewrites /api/* to at the edge."""
    if not WDGO_KEY:
        return None
    req = urllib.request.Request(f"{BASE}{path}", headers={
        "X-API-Key": WDGO_KEY, "Accept": "application/json",
        "User-Agent": USER_AGENT,
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        log.warning("wdgo %s HTTP %d", path, e.code)
        return None
    except Exception as e:
        log.warning("wdgo %s failed: %s", path, scrub(str(e)))
        return None


# ── detectors ────────────────────────────────────────────────────────────────
def detect_captures(me: dict, state: dict) -> list:
    """Embeds for captures we haven't announced yet. Updates state['seen_captures']
    with every current fingerprint (capped) so each capture is announced once."""
    caps = me.get("recent_captures")
    if not isinstance(caps, list):
        return []
    seen = list(state.get("seen_captures", []))
    seen_set = set(seen)
    embeds = []
    fresh_keys = []
    # Oldest first, so a batch reads in chronological order.
    for c in reversed(caps):
        if not isinstance(c, dict):
            continue
        key = _capture_key(c)
        fresh_keys.append(key)
        if key in seen_set:
            continue
        seen_set.add(key)
        aps = c.get("ap_count")
        defender = c.get("defender_gang") or "unclaimed territory"
        loc = _short_loc(c.get("lat"), c.get("lng"))
        when = c.get("when") or ""
        embed = {
            "title": f"⚔ Captured {fmt_int(aps)} AP" + ("s" if aps != 1 else ""),
            "description": f"Took territory from **{defender}**",
            "color": COLOR_CAPTURE,
            "fields": [{"name": "Location", "value": loc, "inline": True}],
        }
        if when:
            embed["fields"].append({"name": "When", "value": str(when)[:40],
                                    "inline": True})
        url = _map_url(c.get("lat"), c.get("lng"))
        if url:
            embed["url"] = url
        embeds.append(embed)
    # Remember current + previously-seen keys, newest-capped. Keep previously
    # seen ones too so a capture that scrolls out of recent_captures isn't
    # re-announced if it briefly reappears.
    merged = fresh_keys + [k for k in seen if k not in set(fresh_keys)]
    state["seen_captures"] = merged[:SEEN_CAP]
    return embeds


def detect_losses(cells: dict, state: dict) -> list:
    """A single embed when cells you owned lost APs or vanished, from diffing
    /api/me/cells against last run. Aggregates so a big recount is one message,
    not fifty. Updates state['cells'] with the current snapshot."""
    if not isinstance(cells, dict) or not cells.get("ok"):
        return []
    rows = cells.get("cells")
    if not isinstance(rows, list):
        return []
    current = {}
    for c in rows:
        if isinstance(c, dict) and isinstance(c.get("aps"), int):
            current[_cell_key(c.get("lat"), c.get("lng"))] = {
                "aps": c["aps"], "lat": c.get("lat"), "lng": c.get("lng")}
    prev = state.get("cells") or {}
    state["cells"] = {k: v["aps"] for k, v in current.items()}

    if not prev:
        return []  # nothing to compare against yet

    lost_total, lost_cells, examples = 0, 0, []
    for key, prev_aps in prev.items():
        if not isinstance(prev_aps, int):
            continue
        cur = current.get(key)
        cur_aps = cur["aps"] if cur else 0
        if cur_aps < prev_aps:
            drop = prev_aps - cur_aps
            lost_total += drop
            lost_cells += 1
            if key in current:
                lat, lng = current[key]["lat"], current[key]["lng"]
            else:
                lat, lng = (key.split(",") + [None, None])[:2]
            gone = " (cell lost)" if cur_aps == 0 else ""
            examples.append((drop, _short_loc(lat, lng), gone))

    if lost_total <= 0:
        return []
    examples.sort(reverse=True)  # biggest drops first
    lines = [f"−{fmt_int(d)} APs at {loc}{gone}" for d, loc, gone in examples[:6]]
    if len(examples) > 6:
        lines.append(f"…and {len(examples) - 6} more cell(s)")
    return [{
        "title": f"🛡 Lost {fmt_int(lost_total)} AP"
                 + ("s" if lost_total != 1 else "")
                 + f" across {lost_cells} cell" + ("s" if lost_cells != 1 else ""),
        "description": "\n".join(lines),
        "color": COLOR_LOSS,
        "footer": {"text": "derived by diffing /api/me/cells — there is no "
                           "server loss feed"},
    }]


def detect_rigs(me: dict, state: dict, now: datetime) -> list:
    """Embeds for rigs that just crossed the staleness threshold (down) or
    uploaded again after being flagged (recovered). Per-rig state remembers the
    down flag so a rig alerts once, not every tick while it stays down."""
    devices = me.get("devices")
    if not isinstance(devices, list):
        return []
    rigs = state.setdefault("rigs", {})
    seen_names = set()
    embeds = []
    for d in devices:
        if not isinstance(d, dict):
            continue
        name = (str(d.get("device_name")) if d.get("device_name") else "unnamed")[:80]
        seen_names.add(name)
        last_raw = d.get("last_upload")
        last_dt = parse_ts(last_raw)
        rec = rigs.setdefault(name, {"last_upload": last_raw, "down": False})
        rec["last_upload"] = last_raw
        if last_dt is None:
            continue
        age_h = (now - last_dt).total_seconds() / 3600.0
        was_down = bool(rec.get("down"))
        is_down = age_h >= RIG_STALE_HOURS
        if is_down and not was_down:
            rec["down"] = True
            embeds.append({
                "title": f"📴 {name} stopped uploading",
                "description": f"No upload in **{age_h:.1f} h** "
                               f"(threshold {RIG_STALE_HOURS:g} h).",
                "color": COLOR_RIG_DOWN,
                "fields": [{"name": "Last upload",
                            "value": str(last_raw)[:40] or "unknown",
                            "inline": True}],
            })
        elif was_down and not is_down:
            rec["down"] = False
            embeds.append({
                "title": f"✅ {name} is uploading again",
                "description": f"Last upload {age_h:.1f} h ago.",
                "color": COLOR_RIG_UP,
            })
    return embeds


def gather(sample: bool):
    """Fetch (me, cells) from WDGWars, or the canned sample pair."""
    if sample:
        return SAMPLE_ME, SAMPLE_CELLS
    me = wdgo_api("/endpoint/me")
    cells = wdgo_api("/endpoint/me/cells") if me else None
    return me, cells


def build_events(me, cells, state: dict, now: datetime) -> list:
    """Run the enabled detectors and return the combined embed list, mutating
    state with the new snapshot in the process."""
    embeds = []
    if me and me.get("ok"):
        if "captures" in ALERTS:
            embeds += detect_captures(me, state)
        if "rigs" in ALERTS:
            embeds += detect_rigs(me, state, now)
    if cells and "losses" in ALERTS:
        embeds += detect_losses(cells, state)
    return embeds


# ── Discord posting ──────────────────────────────────────────────────────────
def _post(url: str, payload: dict, method: str = "POST", token: str = "") -> bool:
    body = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json", "User-Agent": USER_AGENT}
    if token:
        headers["Authorization"] = f"Bot {token}"
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    for _ in range(3):
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return resp.status in (200, 204)
        except urllib.error.HTTPError as e:
            txt = e.read().decode("utf-8", "replace")
            if e.code == 429:
                wait = 2.0
                try:
                    wait = float(json.loads(txt).get("retry_after", 2)) + 0.5
                except Exception:
                    pass
                log.warning("rate limited, sleeping %.1fs", wait)
                time.sleep(wait)
                continue
            log.error("discord HTTP %d: %s", e.code, scrub(txt[:200]))
            return False
        except urllib.error.URLError as e:
            log.error("discord network error: %s", scrub(str(e.reason)))
            return False
    return False


def post_embeds(embeds: list) -> bool:
    """Post embeds to whichever channel is configured (webhook or bot+channel),
    in batches of 10 (Discord's per-message embed cap). Returns True if all
    batches posted."""
    if not embeds:
        return True
    ok = True
    for i in range(0, len(embeds), 10):
        batch = embeds[i:i + 10]
        if WEBHOOK_URL:
            ok &= _post(WEBHOOK_URL,
                        {"username": "WDGWars War Feed", "embeds": batch})
        elif BOT_TOKEN and CHANNEL_ID:
            ok &= _post(f"https://discord.com/api/v10/channels/{CHANNEL_ID}/messages",
                        {"embeds": batch}, token=BOT_TOKEN)
        else:
            raise SystemExit(
                "no post target: set DISCORD_WEBHOOK_URL, or "
                "DISCORD_BOT_TOKEN + WARFEED_CHANNEL_ID")
    return ok


def has_target() -> bool:
    return bool(WEBHOOK_URL or (BOT_TOKEN and CHANNEL_ID))


# ── run ──────────────────────────────────────────────────────────────────────
def tick(sample: bool, dry_run: bool = False, seed: bool = False) -> int:
    """One diff-and-post pass. Returns the number of events posted (or that would
    post, for dry-run)."""
    state = SAMPLE_PREV.copy() if sample else load_state()
    now = datetime.now(timezone.utc)
    me, cells = gather(sample)

    if not sample and (not me or not me.get("ok")):
        log.warning("WDGoWars unreachable or key rejected; skipping this tick")
        return 0

    first_time = not state.get("seeded")
    embeds = build_events(me, cells, state, now)
    state["seeded"] = True
    state["updated_iso"] = now.isoformat()

    # First real run: record state, announce nothing (avoid a backlog flood).
    if first_time and not sample and not seed:
        save_state(state)
        log.info("seeded state (%d cell(s), %d rig(s)); alerts start next tick",
                 len(state.get("cells", {})), len(state.get("rigs", {})))
        return 0

    if seed:
        save_state(state)
        log.info("state seeded; %d event(s) suppressed", len(embeds))
        return 0

    if dry_run:
        print(json.dumps({"events": len(embeds), "embeds": embeds}, indent=2))
        return len(embeds)

    if embeds:
        if post_embeds(embeds):
            log.info("posted %d event(s)", len(embeds))
        else:
            log.error("some events failed to post; not saving state so they retry")
            return 0
    else:
        log.info("no new events")
    if not sample:
        save_state(state)
    return len(embeds)


# ── auto-run install (--schedule), mirrors live_stats_channels.py ─────────────
TASK_NAME = "wdgwars-war-feed"
SERVICE_NAME = "wdgwars-war-feed.service"


def _interval_minutes() -> int:
    return max(1, INTERVAL // 60)


def install_schedule() -> int:
    script = os.path.abspath(__file__)
    if os.name == "nt":
        return _install_windows(script)
    if shutil.which("systemctl"):
        return _install_systemd(script)
    return _install_cron(script)


def _pythonw() -> str:
    exe = sys.executable or "python"
    if os.name == "nt":
        cand = os.path.join(os.path.dirname(exe), "pythonw.exe")
        if os.path.exists(cand):
            return cand
    return exe


def _install_windows(script: str) -> int:
    mins = _interval_minutes()
    tr = f'"{_pythonw()}" "{script}" --once --quiet'
    cmd = ["schtasks", "/Create", "/TN", TASK_NAME, "/TR", tr,
           "/SC", "MINUTE", "/MO", str(mins), "/F"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print("Could not create the scheduled task:")
        print("  " + scrub((r.stderr or r.stdout).strip()))
        return 1
    print(f"Scheduled task '{TASK_NAME}' created: runs every {mins} min, "
          "windowless, quiet.")
    print("Remove it with:  python war_feed.py --unschedule")
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
        "Description=WDGWars war-feed Discord alerter\n"
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
    print(f"Follow logs:  journalctl --user -u {SERVICE_NAME} -f")
    print("Remove it with:  python war_feed.py --unschedule")
    return 0


def _install_cron(script: str) -> int:
    mins = _interval_minutes()
    line = (f"*/{mins} * * * * cd {os.path.dirname(script)} && "
            f"{sys.executable} {script} --once --quiet")
    print("No systemd here. Add this line with `crontab -e` (review it first):\n")
    print("  " + line)
    return 0


def remove_schedule() -> int:
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
        description="Post WDGWars capture / loss / rig-down alerts to Discord.")
    parser.add_argument("--once", action="store_true",
                        help="run a single diff-and-post pass and exit")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the events that would post; send nothing")
    parser.add_argument("--sample", action="store_true",
                        help="use canned data with a built-in previous state, so "
                             "every alert type fires (no key, no network)")
    parser.add_argument("--seed", action="store_true",
                        help="record the current state and exit without posting "
                             "(silences the existing backlog)")
    parser.add_argument("--schedule", action="store_true",
                        help="install a quiet, boot-persistent runner for this platform")
    parser.add_argument("--unschedule", action="store_true",
                        help="remove the runner installed by --schedule")
    parser.add_argument("--quiet", action="store_true",
                        help="log warnings and errors only")
    args = parser.parse_args()

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass

    level = "WARNING" if args.quiet else os.environ.get("WARFEED_LOG_LEVEL", "INFO")
    logging.basicConfig(
        level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if args.unschedule:
        return remove_schedule()

    if args.sample and not (args.once or args.dry_run):
        raise SystemExit("--sample only makes sense with --once or --dry-run")

    if not args.sample and not WDGO_KEY:
        raise SystemExit("set WDGWARS_API_KEY (get it from https://wdgwars.pl/profile), "
                         "or pass --sample")

    if not ALERTS:
        raise SystemExit("WARFEED_ALERTS disabled every alert; nothing to do")

    if args.dry_run:
        tick(sample=args.sample, dry_run=True)
        return 0

    if not args.sample and not has_target():
        raise SystemExit("set DISCORD_WEBHOOK_URL, or DISCORD_BOT_TOKEN + "
                         "WARFEED_CHANNEL_ID (or use --dry-run)")

    if args.seed:
        tick(sample=False, seed=True)
        return 0

    if args.schedule:
        return install_schedule()

    if args.once:
        tick(sample=args.sample)
        return 0

    log.info("war-feed starting (interval=%ss, alerts=%s)",
             INTERVAL, ",".join(sorted(ALERTS)))
    while True:
        try:
            tick(sample=False)
        except Exception as e:
            log.exception("tick failed: %s", scrub(str(e)))
        time.sleep(INTERVAL)


if __name__ == "__main__":
    sys.exit(main())
