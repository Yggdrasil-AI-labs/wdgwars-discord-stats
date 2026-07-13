#!/usr/bin/env python3
"""Post your WDGoWars stats to a Discord channel via an incoming webhook.

This is a complete, dependency-free example of building your own thing on the
WDGoWars API. It reads your ``/api/me`` stats and posts them as a Discord embed.
No bot token, no hosting, no OAuth: a Discord webhook URL is all you need.

Standard library only. Copy it, change it, run it on a cron. See
``docs/api-reference.md`` for the endpoints it uses.

Setup
-----
1. Get your API key from https://wdgwars.pl/profile
2. In your Discord server: Server Settings, Integrations, Webhooks, New Webhook.
   Point it at the channel you want, then Copy Webhook URL.
3. Export both as environment variables (do NOT hard-code them):

       export WDGWARS_API_KEY="your-64-char-key"
       export DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..."

4. Run it:

       python discord_stats_webhook.py             # posts your stats to Discord
       python discord_stats_webhook.py --dry-run   # prints the payload, posts nothing
       python discord_stats_webhook.py --sample     # posts canned stats (no API key needed)

   Tip: run ``--sample`` first to confirm your webhook works and the embed looks
   right, then drop ``--sample`` to post your real numbers.

Key safety
----------
Your API key is a bearer credential for your account. This script reads it from
the environment, never prints it, and redacts it if it shows up in an error
body. Never commit it and never paste it into a channel. The webhook URL is also
a secret: anyone who has it can post to your channel.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

ME_URL = "https://wdgwars.pl/api/me"
USER_AGENT = "wdgwars-discord-stats/1.0 (+https://github.com/Yggdrasil-AI-labs/wdgwars-discord-stats)"
TIMEOUT = 30.0

# Canned data for --sample. Lets you verify your webhook and check the embed
# formatting without an API key and without calling wdgwars.pl.
SAMPLE_ME = {
    "ok": True,
    "username": "SampleDriver",
    "wifi": 12345,
    "ble": 678,
    "aircraft": 90,
    "mesh": 12,
    "total": 13125,
    "gang": "Sample Gang",
}


def scrub(text: str, key: str) -> str:
    """Redact the API key from a string before it is printed or logged."""
    if key and key in text:
        return text.replace(key, "<redacted-key>")
    return text


def fetch_me(key: str, url: str = ME_URL) -> dict:
    """GET /api/me and return the parsed JSON, or raise with a clean message.

    ``url`` defaults to production; override with $WDGWARS_ME_URL to point at a
    staging or mock endpoint for testing. Never echoes the key, even on the
    error paths.
    """
    req = urllib.request.Request(
        url,
        headers={
            "X-API-Key": key,
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:200]
        raise SystemExit(f"wdgwars.pl returned HTTP {e.code}: {scrub(body, key)}")
    except urllib.error.URLError as e:
        raise SystemExit(f"could not reach wdgwars.pl: {scrub(str(e.reason), key)}")

    if not data.get("ok", True):
        raise SystemExit(f"key rejected: {scrub(str(data.get('error', 'unknown')), key)}")
    return data


def build_embed(me: dict) -> dict:
    """Turn an /api/me response into a Discord embed payload.

    Only fields that are reliably present are shown. Unknown or missing fields
    are simply skipped rather than rendered as zero, so this keeps working if
    the response shape changes.
    """
    username = me.get("username", "unknown")

    # (label, key) pairs. Missing keys are dropped, not shown as 0.
    metrics = [
        ("Wi-Fi", "wifi"),
        ("BLE", "ble"),
        ("Aircraft", "aircraft"),
        ("MeshCore", "mesh"),
    ]
    fields = [
        {"name": label, "value": f"{me[key]:,}", "inline": True}
        for label, key in metrics
        if isinstance(me.get(key), int)
    ]
    if isinstance(me.get("total"), int):
        fields.append({"name": "Total", "value": f"{me['total']:,}", "inline": True})

    gang = me.get("gang")
    description = f"Gang: {gang}" if gang else None

    return {
        "username": "WDGoWars Stats",
        "embeds": [
            {
                "title": f"{username} on WDGoWars",
                "description": description,
                "url": "https://wdgwars.pl/profile",
                "color": 0xB08850,  # matches the gungnir badge accent
                "fields": fields,
                "footer": {"text": "via /api/me"},
            }
        ],
    }


def post_to_discord(webhook_url: str, payload: dict) -> None:
    """POST the embed payload to a Discord webhook URL."""
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        webhook_url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            # Discord returns 204 No Content on success.
            if resp.status not in (200, 204):
                raise SystemExit(f"Discord returned HTTP {resp.status}")
    except urllib.error.HTTPError as e:
        body_txt = e.read().decode("utf-8", "replace")[:200]
        raise SystemExit(f"Discord webhook rejected the post (HTTP {e.code}): {body_txt}")
    except urllib.error.URLError as e:
        raise SystemExit(f"could not reach Discord: {e.reason}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Post WDGoWars stats to a Discord webhook.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the embed payload instead of posting it to Discord",
    )
    parser.add_argument(
        "--webhook",
        default=os.environ.get("DISCORD_WEBHOOK_URL"),
        help="Discord webhook URL (defaults to $DISCORD_WEBHOOK_URL)",
    )
    parser.add_argument(
        "--sample",
        action="store_true",
        help="use canned stats instead of calling /api/me (no API key needed); "
             "handy for verifying your webhook and the embed layout",
    )
    args = parser.parse_args()

    if args.sample:
        me = SAMPLE_ME
    else:
        key = os.environ.get("WDGWARS_API_KEY")
        if not key:
            raise SystemExit(
                "set WDGWARS_API_KEY (get your key from https://wdgwars.pl/profile), "
                "or pass --sample to test without one"
            )
        me = fetch_me(key, os.environ.get("WDGWARS_ME_URL", ME_URL))

    payload = build_embed(me)

    if args.dry_run:
        print(json.dumps(payload, indent=2))
        return 0

    if not args.webhook:
        raise SystemExit("set DISCORD_WEBHOOK_URL or pass --webhook (or use --dry-run)")

    post_to_discord(args.webhook, payload)
    print("posted to Discord")
    return 0


if __name__ == "__main__":
    sys.exit(main())
