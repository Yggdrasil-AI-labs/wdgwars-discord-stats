# Examples

Sample output so you can see what each mode produces before running it against
your own account. Both were generated with the built-in `--sample` data (no API
key, no network), so you can reproduce them exactly:

```sh
python discord_stats_webhook.py --sample --dry-run > webhook-embed.sample.json
python live_stats_channels.py   --sample --dry-run > live-stats.sample.txt
```

| File | What it shows |
|---|---|
| [`webhook-embed.sample.json`](webhook-embed.sample.json) | The exact Discord embed payload the webhook poster sends. |
| [`live-stats.sample.txt`](live-stats.sample.txt) | The channel names the voice-channel dashboard would set, and which fields are shown or hidden. |

Run either command without `--dry-run` (and with your real key configured) to act
for real instead of printing.
