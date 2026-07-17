# Getting started

Zero to a live WDGoWars stats display in your Discord. The only manual part is
making a bot (step 2); everything else is one command.

## What you need
- Python 3.9+ (`python --version`)
- A Discord server where you have admin / Manage Server
- A WDGoWars account

## Step 1: get the code
```sh
git clone https://github.com/Yggdrasil-AI-labs/wdgwars-discord-stats.git
cd wdgwars-discord-stats
```
No dependencies to install, it's standard-library Python.

## Step 2: make a Discord bot
1. [Discord Developer Portal](https://discord.com/developers/applications) -> **New Application** (name it anything).
2. Copy the **Application ID** from **General Information** (used in step 4).
3. **Bot** tab -> **Reset Token** -> **Copy**. That is your bot token.
4. Invite it to your server, paste this in a browser with your Application ID
   swapped in (`permissions=268443664` is Manage Channels + Manage Roles + Manage
   Messages; Manage Roles makes `#stats-config` private, Manage Messages pins the
   section panels):
   ```
   https://discord.com/oauth2/authorize?client_id=YOUR_APP_ID&scope=bot&permissions=268443664
   ```
   Manage Channels alone also works; `#stats-config` is just created public and
   the panels are left unpinned in that case.
5. Get your server id: Discord **Settings -> Advanced -> Developer Mode** on,
   then right-click your server icon -> **Copy Server ID**.

## Step 3: configure
Two ways, pick one:

**A. Interactive (easiest):** just run it and answer the prompts:
```sh
python live_stats_channels.py --setup
```
With nothing configured yet, it asks for your key, bot token, and server id
(input is hidden for the secrets), saves them to a local `.env`, then builds.

**B. By hand:** copy the template and fill it in:
```sh
cp .env.example .env    # then edit: WDGWARS_API_KEY, DISCORD_BOT_TOKEN, DISCORD_GUILD_ID
```

## Step 4: check + build
```sh
python live_stats_channels.py --check   # validates token, server, permissions, key
python live_stats_channels.py --setup   # creates channels + panel, populates, AND installs the updater
```
`--check` tells you exactly what is wrong if something is off (bad token, bot not
invited, missing permission, bad key), with the fix. `--setup` also installs the
background auto-updater as its last step, so the display keeps refreshing on its
own, no separate command. It's safe to re-run.

Prefer to preview with no writes first? `python live_stats_channels.py --sample --dry-run`.

## Step 5: keep it updating
**`--setup` already handled this.** As its final step it installed a quiet
background runner for your platform (this is meant to run locally, so your key and
token stay on your own machine):
- **Windows:** a Scheduled Task using `pythonw.exe`, so no console window pops up
  every few minutes and there is no log spam. Runs while you are logged in.
- **Linux / Raspberry Pi:** a `systemd --user` service with lingering enabled, so
  it starts at boot and survives logging out of SSH.
- Undo any time: `python live_stats_channels.py --unschedule`. Reinstall alone
  with `--schedule`. Skip it during setup with `--setup --no-schedule`.

Rather run it by hand?
```sh
python live_stats_channels.py            # run continuously (5-min loop) on any machine
python live_stats_channels.py --once     # one update; point your own cron/Task Scheduler at it
```

**GitHub Actions is an option if you have no machine at all**: add the three
values as repo Actions secrets; [`.github/workflows/live-stats.yml`](.github/workflows/live-stats.yml)
updates it every ~10 min (run `--setup` once locally first). Note this uploads
your key and token to GitHub's secret store, they leave your machine. Prefer a
local option if that matters to you.

## Step 6: choose what to show
- **React on the panels:** `#stats-config` has one pinned message per section
  (📊 Account / 🖥 Devices / 🌐 Territory / ⚙ Status), each with an emoji per field;
  click one to toggle that field. Applies on the next tick (~5 min).
- **Config file:** `~/.wdgwars-live-stats.json`, e.g. `{"fields": {"BLE": false}}`.
- **Env var** (for GitHub Actions): `STATS_FIELDS_OFF=BLE,Rank`.

## Good to know
- Toggles are not instant; they apply on the next 5-minute tick.
- It only reads `/api/me`, it never uploads or changes your WDGoWars account.
- Discord rate-limits channel renames; if you restart often in a short window,
  the tool backs off and catches up. That is normal.
- No always-on machine? Use the GitHub Actions option, no server required.
