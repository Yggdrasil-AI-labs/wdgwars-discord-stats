# wdgwars-discord-stats

Build your own [WDGoWars](https://wdgwars.pl) stats display in Discord.

Two ready-to-run displays plus the docs to build your own:

1. **Live voice-channel display** (`live_stats_channels.py`): a category of
   channels whose names show your current numbers, updated on a schedule, the
   sidebar dashboard look. Needs a Discord bot. You pick which fields to show.
2. **Webhook post** (`discord_stats_webhook.py`): posts a stats embed to a
   channel via an incoming webhook. No bot, just a webhook URL. Simplest.
3. A [consolidated WDGoWars API reference](docs/api-reference.md) so you can
   build something bigger.

Everything is standard-library only. Fork it, change your config, run it.

## Option A: live voice-channel display

The sidebar dashboard. A bot renames voice channels in a "live stats" category
so their names read `📊 Total: 273 239`, `📶 WiFi: 67 201`, and so on. You choose
which fields appear, and can toggle them from a mod channel. `setup` builds the
category, the mod channel, and the control panel for you, no manual channel
creation.

**1. Make a bot.** In the [Discord Developer Portal](https://discord.com/developers/applications):
create an application, add a Bot, copy its token, and invite it to your server
with the **Manage Channels** permission.

**2. Configure** (never hard-code the token or key):

```sh
export WDGWARS_API_KEY="your-64-char-key"     # wdgwars.pl/profile
export DISCORD_BOT_TOKEN="your-bot-token"
export DISCORD_GUILD_ID="your-server-id"       # right-click your server -> Copy Server ID
```

**3. Bootstrap the Discord side** (creates the category, a `#stats-config` mod
channel, and a pinned control panel; safe to re-run):

```sh
python live_stats_channels.py --setup
```

**4. Run it:**

```sh
python live_stats_channels.py --sample --dry-run   # preview fields, no key, no writes
python live_stats_channels.py --once               # one real update (creates the stat channels)
python live_stats_channels.py                        # loop (default every 5 min)
```

To run it continuously, install the service (see [`systemd/`](systemd/)):

```sh
mkdir -p ~/.config/wdgwars-discord-stats
cp systemd/env.example ~/.config/wdgwars-discord-stats/env   # fill in + chmod 600
cp systemd/wdgwars-live-stats.service ~/.config/systemd/user/
systemctl --user enable --now wdgwars-live-stats.service
```

**Choosing what to show.** Not everyone wants every number public. After `setup`,
go to the `#stats-config` channel and type `hide ble`, `show rank`, `hide all`,
`show all`, or `list`. The poller applies the change, reacts with a check, edits
the pinned panel, and adds/removes the matching voice channel. Or edit the config
file directly (`~/.wdgwars-live-stats.json`, e.g. `{"fields": {"BLE": false}}`;
a field missing from the map is shown).

## Option B: webhook post

1. Get your API key from your profile at `wdgwars.pl/profile`.
2. In your Discord server: Server Settings, then Integrations, then Webhooks.
   Create a webhook pointed at the channel you want, then copy its URL.
3. Export both (do not hard-code them):

   ```sh
   export WDGWARS_API_KEY="your-64-char-key"
   export DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..."
   ```

4. Run it:

   ```sh
   python discord_stats_webhook.py --sample    # posts canned stats, no API key needed
   python discord_stats_webhook.py --dry-run   # prints the payload, posts nothing
   python discord_stats_webhook.py             # posts your real stats to Discord
   ```

   Run `--sample` first to confirm your webhook works and the embed looks right,
   then drop it to post your real numbers.

That is the whole thing. Put it on a cron if you want a daily stats post.

## Requirements

Python 3.8 or newer. Standard library only, nothing to install.

## Key safety

Your API key is a bearer credential for your account. The script reads it from
the environment, never prints it, and redacts it from any error output. Never
commit it, never paste it into a channel. The webhook URL is also a secret:
anyone who has it can post to that channel.

## Where the numbers come from

The script reads [`GET /api/me`](docs/api-reference.md#3-read-endpoints), which
returns your username, per-type capture counts (Wi-Fi, BLE, aircraft, MeshCore),
and lifetime total. See the [API reference](docs/api-reference.md) for the full
field list, what the API does not expose, and the other read endpoints you can
build on.

## Family

Part of the WDGoWars feeder and tooling family:

- [gungnir](https://github.com/Yggdrasil-AI-labs/gungnir) - shared upload transport client
- [Muninn](https://github.com/Yggdrasil-AI-labs/adsb-to-wdgwars) - ADS-B feeder
- [Heimdall](https://github.com/Yggdrasil-AI-labs/meshcore-to-wdgwars) - MeshCore LoRa feeder
- [wigle-to-wdgwars](https://github.com/Yggdrasil-AI-labs/wigle-to-wdgwars) - WiGLE Wi-Fi/BLE feeder

## A note on scope

This is a community tool, not official LOCOSP software. The API reference is
compiled from operating the public feeders and from LOCOSP's answers in the
WDGoWars Discord. If the live API disagrees with the docs, open an issue.

Maintained by Hiro AlleyCat ([github.com/HiroAlleyCat](https://github.com/HiroAlleyCat)).

## License

MIT. See [LICENSE](LICENSE).
