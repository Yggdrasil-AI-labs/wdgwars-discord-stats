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
(Or use a `.env` file so you don't set variables each time.) You don't need a
separate step to keep it running: `--setup` installs the background auto-updater
for you as its last step (a windowless Windows Scheduled Task, or a `systemd`
user service on Linux/Pi). Remove it with `--unschedule`, or reinstall it alone
with `--schedule`. Pass `--setup --no-schedule` if you'd rather drive updates
yourself.

### My channels populated once and then went stale.
On the current version this shouldn't happen from a fresh `--setup`, because
setup installs the auto-updater automatically (older versions left that as a
separate `--schedule` step that was easy to forget). To diagnose a stale display:
look at the **⏱ Updated** channel. If it's frozen, nothing is running the updates,
reinstall the runner with `python live_stats_channels.py --schedule` (on Windows,
note the task only runs while you're logged in; on Linux check
`systemctl --user status wdgwars-live-stats`). If **Updated** is advancing but the
numbers aren't, check the **API** channel: DOWN means the tool is holding your
last-good values on purpose and it's a key/API issue, run `--check` to see which.

### It keeps popping up a console window / how do I run it quietly?
Use `python live_stats_channels.py --schedule`. On Windows that registers the
task with `pythonw.exe` (the windowless Python), so no console window appears on
each run, and it passes `--quiet` so only warnings and errors are logged. On
Linux/Pi the same command installs a background `systemd` service that logs to
the journal, not your terminal. If you are running it by hand in a terminal
instead, add `--quiet` to silence the per-tick log lines.

### Does the auto-run start on its own after a reboot?
On Linux/Pi, yes: `--schedule` enables lingering so the service comes up at boot.
On Windows, the Scheduled Task runs while you are logged in (running when logged
out needs stored credentials, which this tool does not handle, so set that up in
Task Scheduler yourself if you need it).

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
Yes, as of the 2026-07-17 server update. `/api/me` now returns a `devices` array,
one row per rig with `networks` (Wi-Fi + BLE combined), `aircraft`, `mesh`,
`uploads`, `total`, and `last_upload`. The webhook poster
(`discord_stats_webhook.py`) renders one embed field per rig. It groups uploads by
the API key that sent them and reports each key's `device_name`, so the trick is
**one clearly named key per rig** (`"Cardputer"`, `"Sleipnir"`, `"Pixel 8"`), used
only on that device. Reusing a key across rigs merges them; two spellings of one
name (`"Monster RF"` vs `"MOnster RF"`) split one rig in two. Renaming a key keeps
its history. These are per-rig *contribution* counts (what each key brought in,
history back to ~mid-June), not a reslice of your live account total, so they
won't sum to it.

### Can it show my whole footprint (how many APs I own, and where)?
The live display has a **Footprint** channel showing your total APs owned, summed
from `/api/me/cells`. That endpoint is server-aggregated and uncapped, so it's the
real ownership number, unlike raw `/api/me/aps`, whose point list truncates on a
wide window. The channel is omitted automatically if your server doesn't serve
`/api/me/cells`. Toggle it off like any other field.

### I already have a "live stats" style category.
The tool creates its own four section categories (📊 Account, 🖥 Devices,
🌐 Territory, ⚙ Status), so it won't touch a differently-named one. If you run
more than one display on the same server, set `STATS_SECTION_PREFIX` (e.g.
`wdgo`) to namespace them: `📊 │ wdgo Account`, and so on.

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
