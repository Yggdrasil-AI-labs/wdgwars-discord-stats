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

### Step 1: make a Discord bot

Only a human can do this part; everything after is one command.

1. Open the [Discord Developer Portal](https://discord.com/developers/applications) and click **New Application**. Name it anything.
2. Left sidebar → **Bot** → **Reset Token** → **Copy**. That is your `DISCORD_BOT_TOKEN`.
3. Invite the bot to your server. Paste this URL in your browser, replacing
   `YOUR_CLIENT_ID` with the **Application ID** from the **General Information**
   page (`permissions=16` is "Manage Channels", the only permission it needs):

   ```
   https://discord.com/oauth2/authorize?client_id=YOUR_CLIENT_ID&scope=bot&permissions=16
   ```
   Pick your server and authorize.
4. Get your server id: Discord → Settings → Advanced → turn on **Developer Mode**,
   then right-click your server icon → **Copy Server ID**.

### Step 2: configure

Copy `.env.example` to `.env` and fill in the three values (this is the easy way,
no shell knowledge needed):

```sh
cp .env.example .env
# then edit .env: WDGWARS_API_KEY, DISCORD_BOT_TOKEN, DISCORD_GUILD_ID
```

`.env` is gitignored, so your secrets stay local. (If you prefer, you can
`export` the three variables instead of using a file.)

### Step 3: build it

```sh
python live_stats_channels.py --setup
```

This creates the `📊 │ live stats` category, a `#stats-config` mod channel with a
pinned control panel, and (if your key is set) populates the stat channels
immediately. Safe to re-run.

### Step 4: keep it updating

The tool is a plain Python script with no GitHub dependency, run it however you
like. Three options, pick one:

```sh
python live_stats_channels.py            # simplest: run it on any machine you control (5-min loop)
```

- **Your own cron / Task Scheduler:** schedule `python live_stats_channels.py --once`
  at whatever interval you want. No service, no loop process.

- **systemd** (a server/Pi you own): copy `systemd/env.example` to
  `~/.config/wdgwars-discord-stats/env` (fill in, `chmod 600`), copy
  `systemd/wdgwars-live-stats.service` to `~/.config/systemd/user/`, then
  `systemctl --user enable --now wdgwars-live-stats.service`.
- **GitHub Actions** (no machine at all): fork/clone this repo, add
  `WDGWARS_API_KEY`, `DISCORD_BOT_TOKEN`, `DISCORD_GUILD_ID` under
  **Settings → Secrets and variables → Actions**, and the included
  [`.github/workflows/live-stats.yml`](.github/workflows/live-stats.yml) updates
  the display every ~10 minutes on GitHub's runners. Run `--setup` once locally
  first to create the channels.

### Choosing what to show

Not everyone wants every number public. Three ways:

- **From Discord:** in `#stats-config`, type `hide ble`, `show rank`, `hide all`,
  `show all`, or `list`. The poller applies it, reacts with a check, edits the
  pinned panel, and adds/removes the matching channel. (Takes up to one tick,
  ~5 min, to reflect.)
- **Config file:** edit `~/.wdgwars-live-stats.json`, e.g. `{"fields": {"BLE": false}}`
  (a field missing from the map is shown).
- **Environment** (works even where the config file does not persist, e.g.
  GitHub Actions): set `STATS_FIELDS_OFF=BLE,Rank`.

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
