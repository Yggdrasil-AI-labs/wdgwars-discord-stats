<!--
Reviewer's verification checklist. Fill out each box before merging. The point
is to slow down at merge time so a fix doesn't surface a new issue the moment a
user touches it.
-->

## Summary

<!-- 1-3 sentences: what changed and why. -->

## What changed (user-facing)

<!-- What an end user notices, if anything: new flag, new default, new prompt.
"Internal only" is a valid answer for refactors. -->

## Verification

- [ ] Both scripts byte-compile: `python -m compileall live_stats_channels.py discord_stats_webhook.py`.
- [ ] Offline dry runs pass: `python live_stats_channels.py --sample --dry-run` and `python discord_stats_webhook.py --sample --dry-run`.
- [ ] If the change touches Discord writes (`--setup`, channel rename, the panel): live-tested against a throwaway server, or `--dry-run` reasoned through.
- [ ] README / GETTING-STARTED / FAQ updated where the change is user-visible.
- [ ] CHANGELOG.md has an entry.
- [ ] No `Co-Authored-By: Claude` trailer in any commit (public-repo convention).
- [ ] No hostnames, real names, API keys, tokens, guild/channel IDs, or other lab-internal references in code, commits, or docs.

## Notes for reviewer

<!-- Context, related PRs, the user report that motivated this, etc. -->
