# UR Riverside Vacancy Monitor 🏞️

Watches the official **UR賃貸住宅** vacancy API for a set of riverside properties in
Osaka and pops a **macOS notification + sound** the moment a room matching your
requirements opens up. Pure Python standard library — nothing to install.

## Currently watching

All the riverside properties along the Yodogawa / Ōkawa:

- さざなみプラザ (main, 第2, 第3, 第6, 第7, 第8) — on the Ōkawa
- リバーサイドながら
- リバーサイドしろきた
- 都島リバーシティ
- 桜宮リバーシティ中央
- リバーサイドともぶち第2

**Filters (edit in `config.json`):**
- Total rent (rent + common fee) ≤ **¥130,000**
- Floor ≥ **10**
- Any layout

## Try it now

```bash
cd ~/Develop/personal/ur-vacancy-monitor
python3 ur_watch.py --all      # list every currently-open room + which match
python3 ur_watch.py --test     # show all current MATCHES (ignores saved state)
python3 ur_watch.py            # one real check (alerts only on brand-new matches)
python3 ur_watch.py --loop     # stay running, re-check every 15 min
```

## Web dashboard

A local, dependency-free dashboard: a live table of every open room across the
watched properties, with matches highlighted and filter controls you can tweak
on the fly (per-view — doesn't change `config.json`).

```bash
cd ~/Develop/personal/ur-vacancy-monitor
python3 serve.py
```

Then open **http://localhost:8765**. It auto-refreshes every 2 minutes and
caches UR responses for up to 60s so refreshes are instant. Ctrl-C to stop.

By default the table shows **only rooms that match your filters**; tick **"Show
non-matching too"** in the filter bar to also see near-misses (dimmed). The stat
tiles always show matching / total open / hidden counts.

To restart the server after editing, kill it by matching the script name (the
process runs as `Python serve.py`, so match `serve.py`, not `python3`):

```bash
pkill -f serve.py; python3 serve.py
```

The `ur_watch.py` monitor (below) and the dashboard are independent — run
either or both. Only the monitor sends notifications.

## Building-level view filtering (号棟)

Within one complex, only some buildings (号棟) face the river. Each property in
`config.json` supports two optional keys that filter on the building number
parsed from the room name (e.g. `30号棟1316号室` → building `30`):

- `"view_buildings": ["28","29","30"]` — **allowlist**: only these 号棟 can match.
- `"exclude_buildings": ["32","33"]` — **blocklist**: these 号棟 never match.
- `"priority_buildings": ["22"]` — **best river-edge 号棟**: still filtered normally,
  but flagged ⭐ — floated to the top of the dashboard (amber row) and given a
  distinct "⭐ RIVER-EDGE" push/notification so you know to chase it first.

Leave both out (or empty) = every building matches. Current setup is **loose**:
we only block buildings confirmed to have no river view, so nothing good is
missed. To go stricter, add `view_buildings` to a property using the table below.

**Current rules** — [O] = owner's first-hand map / Google Earth read (authoritative);
[R] = desk research. Buildings not listed in an allowlist are excluded; undetermined
properties are left fully open.

⭐ = best river-edge building (priority tier).

| Property | Config rule | Basis |
|---|---|---|
| リバーサイドながら | allow 1·2·3·4; ⭐**3** at edge | [O] |
| リバーサイドほんじょう | allow 1 (higher floors) | [O] |
| さざなみプラザ (main) | allow 21·22; ⭐**22** at edge | [O] |
| さざなみプラザ第3 | allow 21·22; ⭐**21** at edge | [O] |
| さざなみプラザ第6 | allow 32; ⭐**32** on the river edge (best of 5/6 pair) | [O] |
| さざなみプラザ第5 | allow 31 (faces 大川 but set back; weaker than 32) | [O] |
| さざなみプラザ第8 | allow 36 (higher floors, unconfirmed) | [O] |
| さざなみプラザ第7 | loose (owner: inland; research: 28号棟 east balcony over 大川) | [O]/[R] |
| リバーサイドしろきた | block 32·33·36 (courtyard / face Umeda) | [R] |
| さざなみプラザ第2 | loose (undetermined) | — |
| 都島リバーシティ | loose (12·13 front the riverpark, [R] HIGH) | [R] |
| 桜宮リバーシティ中央 | loose (all units 南向き; undetermined) | [R] |
| リバーサイドともぶち第2 | loose (buildings face E/SE/S, away from 大川) | [R] |

Not monitorable (not UR): 桜宮リバーシティ**ウエスト** (分譲マンション, 23F/199戸);
"Sakura 第2 コーポ" (unidentified — provide exact name/address to check).

The dashboard's **Bldg** column shows each room's 号棟 so you can sanity-check.

## Phone push alerts (on the go)

The monitor can also push to your phone via **ntfy** (already configured), so you
get alerted away from the Mac. Note: **your Mac must be awake and online** to do
the polling — the phone only receives the push.

Your private topic is set in `config.json` → `push.ntfy.topic`.

**Set up your phone (once):**
1. Install the **ntfy** app ([iOS](https://apps.apple.com/app/ntfy/id1625396347) /
   [Android](https://play.google.com/store/apps/details?id=io.heckel.ntfy)).
2. Tap **+ / Subscribe to topic** and enter your exact topic name from
   `config.json` (e.g. `ur-riverside-xxxxxxxx`).
3. That's it — matching rooms now push to your phone, and tapping the alert opens
   the UR room page.

**Priority levels:** a normal match sends an ntfy `high`-priority push; a ⭐
river-edge match (see `priority_buildings`) sends `urgent` priority — on your
phone that bypasses silent/Do-Not-Disturb and buzzes insistently, with a
🚨 tag, so you can't miss the best listings. (Pushover users get priority 1 vs 0.)

Send yourself a test any time:

```bash
curl -d "test alert" "ntfy.sh/ur-riverside-606108c9"
```

Other channels (fill the matching block in `config.json` → `push`, leave the rest
blank): **Pushover** (`token`+`user`), **Telegram** (`bot_token`+`chat_id`),
or a generic **webhook** (`url`, receives JSON `{title,message,link}`) for
Slack/Discord. Set `push.enabled` to `false` to disable phone alerts entirely.

## Run it automatically in the background (recommended)

Uses macOS `launchd` to check every 15 minutes, including after reboot/login:

```bash
cp com.urwatch.riverside.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.urwatch.riverside.plist
```

Check status / stop it:

```bash
launchctl list | grep urwatch                                   # is it loaded?
launchctl unload ~/Library/LaunchAgents/com.urwatch.riverside.plist   # stop
```

Console output from each scheduled run is appended to `run.log`.

> First time only: macOS may ask to allow notifications for the script runner
> (osascript / Script Editor). Allow it, or notifications won't show.

## Run in the cloud, Mac-free (GitHub Actions)

Runs the check on GitHub's servers on a schedule and pushes to your phone via
ntfy — nothing needs to be on your Mac or phone. Included: `.github/workflows/watch.yml`.

Set to poll every **10 minutes** on a **public** repo (unlimited Actions minutes).
Your ntfy topic is **not** in the committed files — it's injected from the
`NTFY_TOPIC` secret, so a public repo doesn't expose it.

**Setup (one time):**

```bash
cd ~/Develop/personal/ur-vacancy-monitor
git init && git add -A && git commit -m "UR vacancy monitor"
gh repo create ur-vacancy-monitor --public --source=. --push
gh secret set NTFY_TOPIC --body "ur-riverside-606108c9"
```

Then open the repo's **Actions** tab once and confirm workflows are enabled (you
can also trigger a run manually with **Run workflow**).

**Important caveats:**
- **Public repo.** The code, properties, and filters are world-readable. Only the
  ntfy topic is sensitive, and it lives in the secret — never commit it back into
  `config.json`.
- **Delays.** GitHub's scheduled runs are best-effort and can be late under load,
  so effective polling may be slower than 10 min. Fine for apartment hunting.
- **Auto-disable.** GitHub disables scheduled workflows after **60 days** with no
  repo activity. Push any commit (or click Run workflow) to keep it alive.
- **State.** The "already alerted" list is carried between runs via Actions cache,
  so you're not re-notified about the same room.

Run this *or* the local launchd job — not both, or you'll get duplicate alerts.
For a **local** run to still push to your phone (config topic is now blank),
export it first: `export NTFY_TOPIC=ur-riverside-606108c9` (macOS banners work
regardless).

## Tuning

Edit **`config.json`**:

| Key | Meaning |
|---|---|
| `filters.max_total_rent` | Rent + common fee ceiling (¥) |
| `filters.min_floor` | Lowest acceptable floor |
| `filters.min_floorspace` | Minimum ㎡ (0 = any) |
| `filters.allowed_types` | e.g. `["2LDK","3LDK"]`; empty `[]` = any layout |
| `poll_interval_minutes` | Interval used by `--loop` mode |
| `open_browser_on_match` | `true` = also open the room page in your browser |
| `alert_sound` | macOS sound for a normal match (e.g. `Glass`, `Ping`) |
| `priority_alert_sound` | macOS sound for a ⭐ river-edge match (e.g. `Sosumi`) |
| `properties` | The complexes being watched |

## Files

| File | Purpose |
|---|---|
| `ur_watch.py` | The monitor |
| `config.json` | Your properties + filters |
| `state.json` | Room IDs already alerted on (auto-created; delete to reset) |
| `matches.log` | Append-only log of every match found |
| `run.log` | Output of scheduled launchd runs |

## How it works

Each property maps to `shisya` / `danchi` / `shikibetu` codes. The script POSTs
them to UR's room API:

```
https://chintai.r6.ur-net.go.jp/chintai/api/bukken/detail/detail_bukken_room/
```

`null` = no vacancy; otherwise a JSON array of open rooms (rent, floor, layout,
㎡, direct link). Matching rooms not seen before trigger the alert; their IDs are
saved so you're notified once per room, not on every check.

## Notes & etiquette

- 15-minute polling is gentle; please don't crank it down to seconds.
- UR is first-come — when you get an alert, act fast (no key money / deposit /
  guarantor makes application quick).
- If UR ever changes the API, `python3 ur_watch.py --all` will show fetch errors
  first; that's the place to start debugging.
