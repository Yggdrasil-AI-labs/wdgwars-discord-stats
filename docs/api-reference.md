# WDGoWars API reference

A consolidated map of the [WDGoWars](https://wdgwars.pl) HTTP API, focused on the
read endpoints you need to build things on top of your own account: a stats
display, a Discord bot, a dashboard, a "how am I doing" widget.

This is a community reference maintained alongside the feeder family, not
official LOCOSP documentation. Where a mechanic is confirmed by LOCOSP it is
marked as such; where it is an observation from operating public feeders it is
marked as an assumption. If LOCOSP publishes something authoritative that
contradicts this, LOCOSP wins.

For the signed-upload side of the API (the HMAC envelope), the source of truth
is the [gungnir](https://github.com/Yggdrasil-AI-labs/gungnir) library's
[`envelope.py`](https://github.com/Yggdrasil-AI-labs/gungnir/blob/main/gungnir/envelope.py)
and
[`transport.py`](https://github.com/Yggdrasil-AI-labs/gungnir/blob/main/gungnir/transport.py).
This document covers the surface a read-only consumer touches.

---

## 1. Authentication

Every programmatic call authenticates with an API key sent in a header:

```
X-API-Key: <64-char hex>
```

You get your key from your profile page at `wdgwars.pl/profile`. All keys issued
to your account are functionally equivalent (per LOCOSP, 2026-06-02): there is no
per-key scoping, per-device rate limit, or analytics. The `device_name` attached
to a key is a cosmetic label (defaults to `"Mobile App"`, max 64 chars) so you
can tell your keys apart on the dashboard.

### Key safety (read this before you write any code)

- Load the key from an environment variable or a file outside your repo. Never
  hard-code it and never commit it.
- Never log it. When you print request/response bodies for debugging, redact it
  first. gungnir does this with `gungnir.keys.scrub()`.
- A key is a bearer credential for your account. Treat it like a password.

---

## 2. Base URLs and rate limits

| | |
|---|---|
| Production host | `https://wdgwars.pl` |
| Upload rate ceiling | 120 requests/min (raised from 30/min, per LOCOSP 2026-05-31) |
| Read endpoints | No published per-endpoint ceiling. Be polite: cache and poll slowly. |

### The `/endpoint/*` alias

`/endpoint/<name>` is a permanent server-side alias of `/api/<name>` (per LOCOSP,
2026-06-02). If the path starts with `/endpoint/`, the router rewrites it to
`/api/` and dispatches the same handler. It exists because Cloudflare's L7 DDoS
protection intermittently 429s bursts against `/api/*`. Bulk uploaders should
default to `/endpoint/upload/`; single read calls like `/api/me` are unaffected
by burst limits and stay on `/api/*`.

This is a supported contract, not a workaround that will be removed.

---

## 3. Read endpoints

### `GET /api/me`: your identity and totals

The workhorse for any stats display. Returns your username, per-type capture
counts, and lifetime total for the calling key.

Fields observed on live responses (core counts also verified in the gungnir
library's transport code):

| Field | Type | Meaning |
|---|---|---|
| `ok` | bool | `true` on success. On failure, `ok` is falsey and `error` holds a string. |
| `username` | string | Your display name. |
| `gang` | string | Your gang name (absent / `—` if you are not in one). |
| `wifi` | int | Wi-Fi networks credited to you. |
| `ble` | int | Bluetooth LE devices credited to you. |
| `aircraft` | int | ADS-B aircraft credited to you. |
| `mesh` | int | MeshCore nodes credited to you. |
| `total` | int | Flat sum of `wifi + ble + aircraft + mesh` (each type counts equally). |
| `your_rank` | object | `{all_time, today, week, top_n}`. Your rank per board, or `null` for a board where you fall outside the cached window (`top_n`, server default 100). Shipped 2026-06-03. |
| `recent_captures` | array | Up to ~20 of your most recent territory captures, newest first, attacker-side only. Each: `{when, defender_gang, ap_count}` (`when` is a naive UTC `YYYY-MM-DD HH:MM:SS` string). Shipped 2026-06-03. |

Also observed: `gang_id`, `gang_role`, and an earned `badges` list. Exact keys can
drift, so confirm against your own call before hard-coding.

> Note on paths: these fields are identical on `/api/me` and `/endpoint/me`. The
> live pollers in this repo read `/endpoint/me` because `/endpoint/*` bypasses
> Cloudflare's burst limiter (see section 2). Either works for a single read.

> Confirm the real shape yourself. The quickest way to see exactly what your
> account returns:
> ```
> curl -s https://wdgwars.pl/api/me -H "X-API-Key: $WDGWARS_API_KEY" | python -m json.tool
> ```
> Do this once and build against what you actually get back rather than trusting
> any table, including this one.

### `GET /api/leaderboard`: top boards

Returns the ranked boards (`all_time`, `today`, `week`) as arrays, plus a
`gangs` array for gang standings. The snapshot is cached and refreshes every 15
minutes (per LOCOSP, 2026-06-09), so polling faster than that gains you nothing.

For your *own* rank, prefer `your_rank` on `/api/me` (see above): it reports your
position per board directly, up to `top_n` (server default 100), and `null`
beyond that. Use the leaderboard arrays for the standings themselves and for gang
rank (match your gang name in the `gangs` array). Show `>{top_n}` for anyone
outside the cached window.

### `GET /api/me/aps?since=<ISO8601>`: your upload volume in a window

Returns `{count}`, the number of APs credited to you since the given timestamp.
Handy for a "you uploaded N in the last 24h" line.

### `GET /api/badges`: badges you have earned

Returns only the badges the calling account has unlocked. There is no public
"catalog" endpoint that enumerates every possible badge and its unlock criteria,
so you cannot render "you have 12 of 40" from the public API today.

### `GET /api/member-territories`: cells your gang dominates

Returns the cells where your gang has the most APs and meets the privacy
threshold (owned cells only, per LOCOSP 2026-06-02). It does not include
contested cells, allied cells, or cells where a rival leads. A large row count
(tens of thousands) for a multi-region gang is expected: it is the count of
distinct ~2 km tiles you dominate, not a bug.

### `GET /api/contested-cells`: recent-activity surface

Flags cells with two or more capture events in the last 7 days. This is a
recent-activity surface, not an ownership state: each cell still renders one
visual owner the whole time.

### `GET /api/bounty-target-aps?gang_id=N`: gated

Per-cell AP counts for a single gang, gated to that gang's owner/officer role.
Not a general query endpoint.

### `GET /api/team/me` and `GET /api/team/{id}`: gang roster (see caveat)

Both routes hit the same backend; `/me` resolves your gang and dispatches as if
you passed its id. The intended response shape (per LOCOSP, 2026-06-02):

```json
{
  "ok": true,
  "gang": { "id": 0, "name": "", "color": "", "founded": "", "member_count": 0, "ap_count": 0, "rank": 0 },
  "members": [
    { "user_id": 0, "username": "", "role": "", "wifi": 0, "ble": 0, "aircraft": 0, "mesh": 0, "joined_at": "" }
  ],
  "bounties": { "active": 0, "completed": 0, "lifetime_earned": 0 },
  "credits": { "balance": 0, "locked": 0 }
}
```

> Caveat: this endpoint has been unstable. It returned a `400` usage error in
> early June, flapped between `200 OK` and errors through mid-June, and has been
> returning `404` since around 2026-06-16. So the roster shape above is **not
> reliably served today**. Verify against a live call before building on it; if
> you get a `404`/`400`, treat gang roster as unavailable and use the gang name
> from `/api/me` plus the `gangs` array on `/api/leaderboard` for gang rank.
> Related `team-messages` endpoints currently return `403`.

---

## 4. Write endpoints (summary)

You do not need these for a stats display, but for completeness:

| Endpoint | Purpose |
|---|---|
| `POST /endpoint/upload/` | Signed-JSON bulk record upload (recommended path). |
| `POST /api/upload/` | Same handler, legacy path (can trip Cloudflare L7 on cold-IP bursts). |
| `POST /api/upload-csv` | WiGLE-CSV multipart bulk upload. |
| `POST /api/v2/upload-csv` | Async CSV upload; poll `GET /api/v2/upload-job/<id>` for status. |

The upload body is an HMAC-signed envelope. Do not hand-roll it: use the
[gungnir](https://github.com/Yggdrasil-AI-labs/gungnir) library
(`gungnir.Client.send`) or match
[`envelope.py`](https://github.com/Yggdrasil-AI-labs/gungnir/blob/main/gungnir/envelope.py)
byte-for-byte. Job metadata for async uploads persists indefinitely; the raw
uploaded bytes are cleaned up after 7 days.

---

## 5. What the public API does not expose

Do not waste requests probing for these. They are portal-only or intentionally
withheld:

| Metric | Status |
|---|---|
| Losses (lost-to-enemies) | Not exposed to key clients. `recent_captures` on `/api/me` covers your *attacker-side* captures only, not what you lost. The full `/api/captures` page is session-only (redirects to `/login`, ignores `X-API-Key`). |
| Rank beyond `top_n` | `your_rank` covers ranks up to `top_n` (default 100); past that it is `null`. There is no endpoint for an exact rank in the long tail. |
| Per-cell, per-team AP breakdown | Deliberately withheld as an anti-cheat measure. Do not re-request it. |
| Full badge catalog | No public enumeration endpoint (only your earned `badges`). |
| Per-AP effective hardening | Not known to be exposed on any read endpoint. Do not assume a field for it. |
| Gang roster / per-member stats | Intended for `/api/team/me`, but that endpoint is not reliably served (see section 3). |

Note: your own rank and your recent captures *are* exposed now (`your_rank` and
`recent_captures` on `/api/me`, both shipped 2026-06-03). Earlier drafts of this
reference said they were not; that was wrong.

---

## 6. Confirmed game mechanics (Territory v2)

Enough to answer the common "why did my score/cell change" questions correctly.
Current model is Territory v2, live since 2026-06-09 (per LOCOSP), which
supersedes the earlier flat-count model.

- Cell ownership is per-AP. Each AP carries a **decay-aware effective hardening**
  value. Rescanning your own AP reinforces it; time without a rescan lowers it.
- A cell flips only when a **different** driver attacks a weakly-reinforced AP
  (effective hardening at or near 1). A plain rescan by anyone else does not flip
  a well-reinforced AP; a rescan by the owner only reinforces.
- Practical consequence: reinforcement frequency matters, not just raw count. 500
  APs scanned once eight months ago are 500 vulnerable APs. A 50-AP route driven
  weekly is a smaller but better-defended footprint. The advice is "drive your
  perimeter more often," not "scan more new APs."
- Deduplication is on the `(MAC, SSID)` pair, not MAC alone. The same physical AP
  reported with two different SSIDs lands as two rows.
- Multi-tier caching means the live count, the materialized view, and the
  leaderboard snapshot can briefly disagree. This is normal and self-resolves.
  The escalation threshold is one hour.

LOCOSP has not published the hardening formula (reinforcement gain, decay rate,
floor/ceiling). Do not assert specific numbers.

---

## 7. Build your own Discord stats display

The lowest-friction integration: a script that reads your `/api/me` stats and
posts them to a Discord channel through an incoming webhook. No bot token, no
hosting, no OAuth. You can run it by hand or on a cron.

A complete, dependency-free (standard-library-only) example lives at
[`discord_stats_webhook.py`](../discord_stats_webhook.py) in this repo.
The shape of it:

1. Read your API key from `WDGWARS_API_KEY` (never hard-coded).
2. `GET /api/me` with the `X-API-Key` header.
3. Format the fields into a Discord embed.
4. POST the embed to a Discord webhook URL you create in your own server
   (Server Settings, Integrations, Webhooks).

To create the webhook: in your Discord server, open Server Settings, then
Integrations, then Webhooks, and create one pointed at the channel you want the
stats in. That URL is all the example needs. Keep it private: anyone with the
URL can post to that channel.

For the sidebar-dashboard look (a category of voice channels whose names show
your live numbers, with per-field show/hide control), see
[`live_stats_channels.py`](../live_stats_channels.py). That one needs a Discord
bot with Manage Channels, but reads the same endpoints documented here.

---

## 8. Endpoint quick reference

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/api/me` | X-API-Key | Identity, per-type counts, total, `your_rank`, `recent_captures`. |
| GET | `/api/me/aps?since=<ISO>` | X-API-Key | `{count}` of APs credited to you since a timestamp. |
| GET | `/api/leaderboard` | X-API-Key | Ranked boards (all_time/today/week) + `gangs` (15-min cache). |
| GET | `/api/badges` | X-API-Key | Badges you have earned. |
| GET | `/api/member-territories` | X-API-Key | Cells your gang dominates (owned only). |
| GET | `/api/contested-cells` | X-API-Key (likely) | Cells with recent capture activity. |
| GET | `/api/bounty-target-aps?gang_id=N` | gang owner/officer | Per-cell AP counts for one gang. |
| GET | `/api/team/me`, `/api/team/{id}` | X-API-Key | Gang roster (see section 3 caveat). |
| POST | `/endpoint/upload/` | X-API-Key + HMAC | Signed bulk record upload. |
| POST | `/api/upload-csv` | X-API-Key | WiGLE-CSV multipart upload. |

---

## Sources and attribution

Compiled from operating the public feeder family against `wdgwars.pl` and from
LOCOSP's answers in the WDGoWars Discord (2026-06-02 and 2026-06-09). Maintained
by Hiro AlleyCat ([github.com/HiroAlleyCat](https://github.com/HiroAlleyCat)).

Corrections welcome: open an issue if the live API disagrees with anything here.
