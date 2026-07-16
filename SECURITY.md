# Security Notes

This tool touches two credentials (a WDGoWars API key and a Discord bot token or
webhook URL) and makes changes in your Discord server. Here is exactly what it
does with them and what it does not.

## What this tool does

- Reads your WDGoWars stats from `GET /endpoint/me` and `GET /endpoint/leaderboard`.
- Renames voice channels, posts and edits a control-panel message, and reads
  reactions on that message (voice-channel display), or posts one embed to a
  webhook (webhook mode).
- Writes a local config file (`~/.wdgwars-live-stats.json`) and a small state
  file so it knows what changed between ticks.

## What this tool **does not** do

- It never calls any WDGoWars write/upload endpoint. It cannot change your
  WDGoWars account, score, or territory.
- It never opens a Discord gateway/WebSocket connection, so it will not clash
  with a bot you already run on the same token.
- It never sends your key, token, or stats anywhere except WDGoWars and your own
  Discord server.
- It has no telemetry, no analytics, and no network calls other than the two
  hosts above.

## API key handling

- The key is read from the `WDGWARS_API_KEY` environment variable (or a
  `chmod 600` `.env` file you create). It is never hard-coded.
- It is never printed to the console or logs, and it is redacted from any error
  body before that error is shown (`scrub()` in both scripts).
- Treat it like a password: it is a bearer credential for your account. Do not
  commit it, and do not paste it into a Discord channel.

## What the API key can do

Per WDGoWars, all keys on your account are functionally equivalent: there is no
per-key scoping. A leaked key lets someone read your `/api/me` and upload records
as you. It cannot change your password or delete your account. If a key leaks,
rotate it at `wdgwars.pl/profile`.

## Discord token / webhook handling

- The bot token is read from `DISCORD_BOT_TOKEN`, handled the same way as the API
  key (never printed, redacted from errors).
- The bot needs only the **Manage Channels** permission (`permissions=16`). Do
  not grant Administrator.
- A webhook URL is itself a secret: anyone who has it can post to that channel.
  Keep it out of commits and chat.

## Config + state file handling

- The config and state files are written to your home directory with an atomic
  replace. They hold channel/message IDs and your field choices, not secrets.
- The setup wizard writes `.env` with `chmod 600` (owner read/write only) where
  the OS supports it. `.env` is gitignored.

## Dependencies

None. Both scripts are standard-library Python (3.9+). There is no third-party
package in the runtime path, so there is no dependency supply chain to audit.

## Hosting trade-off

Running locally keeps your key and token on your own machine, which is the
recommended setup. The optional GitHub Actions workflow puts them in GitHub's
Actions secret store instead; use it only if you have no machine to leave
running, and understand that the credentials leave your device in that case.

## Threat model - what this tool defends against

- **Credential disclosure in logs/errors:** secrets are redacted from all output.
- **Accidental account changes:** the tool is read-only against WDGoWars; there is
  no code path that writes to your account.
- **Over-permissioned bot:** documentation and the `--check` doctor steer you to
  the single Manage Channels permission, not Administrator.
- **Committing secrets:** `.env` is gitignored and the setup wizard sets 0600.

## Out of scope

- Protecting a key or token you paste somewhere public yourself.
- The security of GitHub's or Discord's own infrastructure.
- Anyone who already has admin on the machine running the tool (they can read the
  environment and the `.env` file, by design of the OS).

## Reporting

Found something? Open an issue, or for anything sensitive, contact the maintainer
via the GitHub profile linked in the README rather than filing a public issue.
