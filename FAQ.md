# FAQ / common situations

### Do I have to make a new bot, or can I use one I already have?
Either. If you already run **your own** bot (an application whose token you
control), just put its token in `DISCORD_BOT_TOKEN`, it only needs the **Manage
Channels** permission in the server. No new bot required.

### Can I use MEE6 / Carl-bot / Dyno / another hosted bot?
No. Those are hosted by their vendors and you never get their token. You can only
reuse a bot you created yourself. If you don't have one, making a bot takes a
minute (see GETTING-STARTED).

### Will this clash with my existing bot?
No. This tool only makes REST calls (rename channels, post the panel, poll
reactions), it never opens a gateway/WebSocket connection. Discord only limits
you to one gateway connection per token; REST is unlimited. So running this
alongside your bot's normal operation on the same token is fine.

### I'm on Windows, how do I run it?
It's plain Python, it runs anywhere. From PowerShell or Command Prompt:
```
set WDGWARS_API_KEY=your-key
set DISCORD_BOT_TOKEN=your-token
set DISCORD_GUILD_ID=your-server-id
python live_stats_channels.py --setup
python live_stats_channels.py
```
(Or use a `.env` file so you don't set variables each time.) To keep it running,
either leave that window open, or make a **Task Scheduler** task that runs
`python C:\path\to\live_stats_channels.py --once` every few minutes.

### I don't have a machine to leave running.
The tool is designed to run locally (your key stays on your machine), but if you
truly have nowhere to run it, the **GitHub Actions** path works: GitHub runs it
on a schedule for free, no server of your own. Run `--setup` once locally first.
Trade-off: your key and token go into GitHub's secret store, so they leave your
machine. If that matters, use a local option instead (even a Raspberry Pi).

### Voice-channel dashboard vs. webhook post, which do I want?
- **`live_stats_channels.py`** (voice channels): the always-visible sidebar
  dashboard. Needs a bot. Use this if you want a live at-a-glance display.
- **`discord_stats_webhook.py`** (embed post): drops a stats message in a
  channel via a webhook. No bot needed. Use this if you just want a periodic
  post and don't want to make a bot.

### Whose stats does it show?
The owner of the API key you configure, one key = one account. It is not a
whole-server or multi-account aggregate.

### Can it show my gang's stats?
Yes, if you're in a gang: it currently shows your **gang rank, gang size, and
gang total APs**, pulled from the leaderboard. Solo drivers don't get gang
channels at all. A richer **per-member breakdown is possible** too, `/api/team/me`
returns the full roster with each member's counts (it only 404s if you're not in
a team). The tool doesn't render per-member channels yet, but the data's there.

### Can it show per-device / per-hardware uploads (how much each rig contributed)?
Not yet. WDGoWars aggregates stats per *account*, `/api/me` returns your combined
totals with no per-device split, so the tool has nothing to break out. The data
likely exists server-side (every API key carries a `device_name`), so it needs
LOCOSP to expose a per-device breakdown (e.g. a `devices` array on `/api/me`).
It's been requested. Once an endpoint exists, adding per-device channels is a
small change. The only workaround today is one API key per device, which is
per-key rather than per-hardware and doesn't scale to a gang.

### I already have a "live stats" style category.
Point the tool at a different name with `STATS_CATEGORY_NAME="📊 │ my stats"` so
it doesn't touch your existing one.

### I don't want the config channel.
It's created **private** by default (only admins see it). If you want it gone
entirely, delete it, the display keeps working and you change fields via the
config file or `STATS_FIELDS_OFF`. Or never create it: `STATS_CONFIG_PRIVATE`
controls visibility; deleting the channel after setup is fine.

### How do I hide/show fields?
React to a field's emoji on the pinned panel in `#stats-config`, edit
`~/.wdgwars-live-stats.json` (`{"fields": {"BLE": false}}`), or set
`STATS_FIELDS_OFF=BLE,Rank`. Changes apply on the next tick (~5 min).

### How often does it update? Can I change that?
Every 5 minutes by default. Set `STATS_INTERVAL` (seconds) to change it. Don't go
too fast, Discord rate-limits channel renames.

### Is it safe for my account?
Yes. It only reads `/api/me` and the leaderboard. It never uploads, never
changes your WDGoWars account, and your key is read from the environment, never
logged, and redacted from any error output.

### Something's not working.
Run `python live_stats_channels.py --check`, it validates your token, server
membership, Manage Channels permission, and API key, and tells you exactly
what's wrong and how to fix it.
