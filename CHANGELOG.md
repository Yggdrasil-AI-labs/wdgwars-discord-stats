# Changelog

All notable changes to wdgwars-discord-stats are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/) and the project uses
[Semantic Versioning](https://semver.org/).

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
