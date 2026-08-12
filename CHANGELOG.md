# Changelog

All notable changes to wdgwars-discord-stats are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/) and the project uses
[Semantic Versioning](https://semver.org/).

## [1.5.1] - 2026-08-12 - Setup wizard: hardened .env write

Found in an audit against a key-handling standard being published for community
tools in this family (see gungnir/gungnir/keys.py's `save_key()` for the same
pattern).

### Fixed

- **`live_stats_channels.py`'s setup wizard no longer briefly world-readable
  writes `.env`.** It previously wrote the secrets first and `chmod 600`'d the
  file afterward, leaving a window at default permissions, silently ignored a
  failing `chmod`, and followed a pre-existing symlink at the `.env` path
  instead of refusing it. It now refuses to write through a symlink (aborting
  with a clear message), creates the file at mode 0600 atomically before any
  secret byte is written, and surfaces a chmod failure instead of swallowing
  it. On Windows, where `chmod` bits do not govern NTFS ACLs, the wizard says
  so rather than implying a guarantee the platform doesn't give.
- **`discord_stats_webhook.py` did not load `.env`**, while its two sibling
  scripts do, so following `.env.example`'s "copy to `.env`" instructions and
  running the webhook script produced a bare "set `WDGWARS_API_KEY`" error
  with no hint why. It now loads `.env` the same way the other two scripts do.

### Docs

- SECURITY.md's `.env` permission claim now states the POSIX guarantee and the
  Windows caveat precisely, instead of implying `chmod 600` everywhere.

## [1.5.0] - 2026-07-19 - War feed: event alerts, not just snapshots

A third tool, `war_feed.py`. Where the other two show your current numbers, this
posts a message **when something happens**, by remembering state between runs and
diffing it.

### Added

- **`war_feed.py`**: a diff-and-post alerter with three detectors:
  - **⚔ Captures**: new entries in `recent_captures` on `/api/me`, announced
    once each (deduped by a per-capture fingerprint, since the feed has no id).
  - **🛡 Territory losses**: cells that lost APs or vanished, derived by diffing
    `/api/me/cells` between runs. WDGoWars exposes no defender-side loss feed, so
    this is the only way to see you are being pushed back; drops are aggregated
    into one message.
  - **📴 Rig down / ✅ recovered**: a device whose `last_upload` crossed the
    staleness threshold (`WARFEED_RIG_STALE_HOURS`, default 12), and its recovery
    when it uploads again. Alerts once per state change, not every tick.
- Posts through either a **webhook** (`DISCORD_WEBHOOK_URL`) or an existing
  **bot** (`DISCORD_BOT_TOKEN` + `WARFEED_CHANNEL_ID`), batching embeds in tens.
- First real run **seeds** state and stays silent (no backlog flood / false
  rig-down); `--seed` does this on demand. `--sample`, `--dry-run`, `--once`,
  `--schedule`/`--unschedule` mirror the other tools.
- Alert selection via `WARFEED_ALERTS` (comma list of `captures,losses,rigs`).
- `parse_ts` handles the raw Postgres `timestamptz` shape (space separator,
  microseconds, 2-digit offset) that `datetime.fromisoformat` rejects before
  Python 3.11.
- 31 tests covering timestamp parsing, each detector, dedup, seeding, the
  alert filter, batching, and the bot-vs-webhook post paths.

## [1.4.1] - 2026-07-17 - Manage Messages for pinning; LXC notes

From a detailed field report by **Vito**, who deployed on a Proxmox unprivileged
LXC and wrote up everything he hit. Thanks!

### Fixed

- **Pinning the section panels needs Manage Messages**, which the invite didn't
  request, so a bot invited exactly per the docs 403'd on the `pins` call (a
  second, distinct permission from the Manage Roles one for private channels).
  The invite now requests Manage Channels + Manage Roles + **Manage Messages**
  (`permissions=268443664`), pinning degrades gracefully (the panel still works
  unpinned, with a log hint), and `--check` adds a non-fatal Manage Messages
  heads-up.

### Docs

- New FAQ entry disambiguating the two setup 403s (Manage Roles on channel
  create vs Manage Messages on pin).
- New FAQ entry for **Proxmox / LXC**: `--schedule` and `systemctl --user` need
  container nesting, `dbus-user-session`, and an explicit `XDG_RUNTIME_DIR` under
  `pct enter`; logs land in the system journal (use `journalctl` without `--user`).

## [1.4.0] - 2026-07-17 - Split the panel, not the voice channels

The v1.2 "split into sections" put the split in the wrong place (four voice
categories). The split belongs in the private config channel.

### Changed

- **Voice channels are back in a single `📊 │ live stats` category** (configurable
  via `STATS_CATEGORY_NAME`), including the per-rig Devices channels and Footprint.
  The four-category layout is gone; `STATS_SECTION_PREFIX` is removed.
- **The control panel is now split by section:** `#stats-config` gets one pinned
  message per section (📊 Account, 🖥 Devices, 🌐 Territory, ⚙ Status), each
  carrying only that section's toggle reactions, instead of a single all-in-one
  panel. `poll_reactions`/`update_panel` handle the four messages; the panel
  config is now `{channel_id, messages: {section: message_id}}` (a pre-split
  single-panel `message_id` is migrated on the next `--setup`).

### Fixed

- Voice channels are created with their full name up front instead of a bare
  label that gets renamed a moment later. A rename that failed (e.g. a rate-limit
  during a big reconcile) used to leave a colonless duplicate that `label_of`
  couldn't reconcile away.

### Notes

- `--setup` is idempotent and migrates an existing display: it reuses the single
  category and re-posts the per-section panels. If you're coming from v1.2/1.3's
  four categories, delete the now-empty extra categories by hand.

## [1.3.1] - 2026-07-17 - Graceful private-channel fallback

### Fixed

- **Setup no longer hard-fails when the bot lacks Manage Roles.** Creating the
  `#stats-config` channel *private* writes permission overwrites, which Discord
  only permits with the Manage Roles permission (Manage Channels alone is not
  enough). Previously that 403'd and setup aborted; you had to set
  `STATS_CONFIG_PRIVATE=off` to get past it. Setup now falls back to creating the
  channel public, prints exactly why, and tells you how to lock it down (grant
  Manage Roles and re-run, or set it private by hand and give the bot access).
  The reuse path (PATCH overwrites on an existing channel) degrades the same way
  instead of silently claiming success.

### Changed

- The invite URL `--check` prints now requests **Manage Channels + Manage Roles**
  (`permissions=268435472`) so the private config channel works out of the box.
  Manage Channels alone still works; the config channel is just created public.
- `--check` adds a non-fatal heads-up when `STATS_CONFIG_PRIVATE` is on but the
  bot lacks Manage Roles, so you learn about it before setup rather than after.

## [1.3.0] - 2026-07-17 - Setup installs the updater

### Changed

- **`--setup` now installs the auto-updater as its last step** (the same runner
  `--schedule` sets up: a windowless Scheduled Task on Windows, a lingering
  `systemd --user` service on Linux/Pi, or a printed cron line). Setup alone only
  populated the channels once, so a display that was set up but never scheduled
  froze at its first values, the most common "my channels went stale" report.
  This closes that gap by default.

### Added

- **`--no-schedule`**: pair with `--setup` to skip installing the runner, for
  when something else drives updates (GitHub Actions, or a hand-managed unit).
  The standalone `--schedule` / `--unschedule` commands are unchanged.

## [1.2.0] - 2026-07-17 - Sectioned display

Splits both displays into sections and surfaces the per-rig breakdown as its own
section.

### Added

- **Sectioned voice-channel display** (`live_stats_channels.py`): the flat
  category is replaced by four category "sections", 📊 Account, 🖥 Devices,
  🌐 Territory, ⚙ Status. The **Devices** section renders one voice channel per
  rig from the `/api/me` `devices` array (e.g. `🖥 Cardputer: 61 234 nets`) and is
  toggled as a whole from the config panel (or `STATS_FIELDS_OFF=Devices`).
  `--setup` now creates all four categories.
- **`STATS_SECTION_PREFIX`**: optional namespace for the section categories
  (`📊 │ wdgo Account`) so more than one display can share a server.
- **Sectioned webhook embed** (`discord_stats_webhook.py`): the embed is grouped
  under 📊 Account / 🌐 Territory / 🖥 Devices header dividers. The poster now also
  makes a best-effort `/api/me/cells` call to show a Footprint line (skipped
  silently if the endpoint is unavailable, never fails the post).

### Changed

- `gather_stats()` now returns `(stats, devices, api_ok)`; the Devices section is
  built from the per-rig map.
- Removed the `STATS_CATEGORY_NAME` env var (the single-category model it
  configured is gone). A leftover old category can be deleted by hand; it is no
  longer written to.

## [1.1.0] - 2026-07-17 - Per-rig breakdown and footprint

Tracks the 2026-07-17 WDGoWars server update, which added a per-device `devices`
array to `/api/me` and a server-aggregated `/api/me/cells` footprint endpoint.
Both were confirmed against a live account before shipping this release.

### Added

- **Per-rig breakdown in the webhook poster** (`discord_stats_webhook.py`): reads
  the new `devices` array on `/api/me` and renders one embed field per rig
  (`networks`, `aircraft`/`mesh` when non-zero, `uploads`, last-upload date),
  grouped by each API key's `device_name`. Name a key per device for clean rows.
  Degrades silently on older servers with no `devices` array.
- **`Footprint` channel in the live display** (`live_stats_channels.py`): total
  APs owned, summed from `/api/me/cells` (server-aggregated and uncapped, the real
  ownership number that raw `/api/me/aps` truncates away). Toggles like any field;
  omitted automatically when the endpoint isn't served.
- **`docs/api-reference.md`**: documents the `devices` array and `/api/me/cells`,
  corrects the auth section (a key's `device_name` is now the per-rig grouping
  key, not cosmetic), flags the `/api/me/aps` truncation, and notes the
  raw-Postgres `last_upload` timestamp format (needs care before Python 3.11).

### Notes

- Per-rig `devices` counts are *contributions* (what each key brought in, history
  back to about mid-June 2026), not a reslice of your live account total, so they
  do not sum to it. Documented in the reference and FAQ.

## [1.0.0] - 2026-07-16 - Initial release

First public release. Two ready-to-run Discord displays for your WDGoWars stats,
plus a consolidated API reference. Standard-library Python, no dependencies.

### Added

- **Live voice-channel dashboard** (`live_stats_channels.py`): a Discord bot
  renames voice channels in a "live stats" category so their names show your
  current numbers (User, Team, Total, WiFi, BLE, ADS-B, Mesh, Reinforced, Today,
  Week, Credits, Quota, Rank, gang size/APs, and an API health line). Reads
  `/endpoint/me` and `/endpoint/leaderboard` only, never uploads.
- **Webhook poster** (`discord_stats_webhook.py`): posts a stats embed to a
  channel via an incoming webhook. No bot, no hosting, no OAuth.
- **`--setup`**: one command creates the category, a `#stats-config` mod channel,
  and a pinned control panel, then populates the channels. Idempotent.
- **Interactive setup wizard**: first run with nothing configured prompts for the
  key, bot token, and server id (hidden input), writes a `chmod 600` `.env`, and
  continues. TTY-only, so cron/CI/systemd fall through to env/`.env`.
- **`--check` doctor mode**: validates the bot token, server membership, Manage
  Channels permission, and API key, printing the exact fix for each failure.
- **`--schedule` / `--unschedule`**: one command installs a quiet,
  boot-persistent auto-runner for the current platform: a windowless
  (`pythonw.exe`) Scheduled Task on Windows so no console window pops up, a
  lingering `systemd --user` service on Linux/Pi (starts at boot, survives
  logout), or a printed `cron` line as a fallback.
- **`--quiet`**: log warnings and errors only. Used automatically by the
  scheduled runner so background operation stays silent.
- **Per-field visibility**: toggle fields by reacting on the pinned panel, editing
  the config file, or setting `STATS_FIELDS_OFF` (comma-separated, case-insensitive).
- **Hosting options**: continuous loop, `--once` for your own cron, an included
  systemd user unit, and a GitHub Actions workflow for people with no always-on
  machine.
- **`docs/api-reference.md`**: consolidated map of the WDGoWars read/write API
  surface, including `your_rank` and `recent_captures` on `/api/me` and the
  `/api/team/me` team dossier.

### Reliability

- If WDGoWars is briefly unreachable, the dashboard keeps the last good numbers
  and flips only the API channel to DOWN, instead of blanking every stat to 0.
- The bot's own user id is not cached after a failed lookup, so a transient
  error no longer disables the reaction panel until the next restart.

### Security

- API key and bot token are read from the environment, never printed, and
  redacted from any error output. See [SECURITY.md](SECURITY.md).
