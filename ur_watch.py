#!/usr/bin/env python3
"""
UR riverside vacancy monitor (Osaka / Kansai).

Polls the UR賃貸住宅 vacancy API for a configured set of danchi (housing
complexes), filters the returned rooms against your requirements, and fires a
macOS notification (+ sound) whenever a *new* matching room appears.

No third-party packages — uses only the Python standard library, so the system
python3 on macOS runs it as-is.

Usage:
    python3 ur_watch.py            # check once (ideal for launchd / cron)
    python3 ur_watch.py --loop     # keep running, re-check on an interval
    python3 ur_watch.py --test     # ignore saved state: show ALL current matches
    python3 ur_watch.py --all      # print every open room, matching or not

Files (kept next to this script):
    config.json    your properties + filters
    state.json     room IDs already alerted on (so you're not re-notified)
    matches.log    append-only log of every match found
"""

import argparse
import html
import json
import os
import re
import ssl
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

# Verify TLS normally; fall back to an unverified context only if this Mac's
# Python has no usable CA bundle (common with the framework build of python3).
# We only ever talk to the fixed UR host below, so the fallback is low-risk.
_VERIFIED_CTX = ssl.create_default_context()
_UNVERIFIED_CTX = ssl._create_unverified_context()

HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "config.json"
STATE_PATH = HERE / "state.json"
LOG_PATH = HERE / "matches.log"

API_URL = "https://chintai.r6.ur-net.go.jp/chintai/api/bukken/detail/detail_bukken_room/"
SITE = "https://www.ur-net.go.jp"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
)


# --------------------------------------------------------------------------- #
# Parsing helpers
# --------------------------------------------------------------------------- #
def yen_to_int(value):
    """'146,600円' -> 146600 ; '' / None -> 0."""
    if not value:
        return 0
    digits = re.sub(r"[^0-9]", "", str(value))
    return int(digits) if digits else 0


def to_int(value):
    """'13階' / '55&#13217;' -> 13 / 55 ; returns 0 if no number found."""
    if not value:
        return 0
    m = re.search(r"\d+", html.unescape(str(value)))
    return int(m.group()) if m else 0


# --------------------------------------------------------------------------- #
# API
# --------------------------------------------------------------------------- #
def fetch_rooms(prop):
    """Return a list of room dicts for one property (empty if no vacancy)."""
    rooms = []
    page = 0
    while page < 20:  # safety cap
        payload = {
            "shisya": prop["shisya"],
            "danchi": prop["danchi"],
            "shikibetu": prop["shikibetu"],
            "mode": "",
            "rent_low": "",
            "rent_high": "",
            "orderByField": "0",
            "orderBySort": "0",
            "pageIndex": str(page),
            "sp": "",
        }
        data = urllib.parse.urlencode(payload).encode("utf-8")
        req = urllib.request.Request(
            API_URL,
            data=data,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": USER_AGENT,
                "Referer": SITE + "/",
                "X-Requested-With": "XMLHttpRequest",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30, context=_VERIFIED_CTX) as resp:
                body = resp.read().decode("utf-8")
        except urllib.error.URLError as exc:
            if not isinstance(getattr(exc, "reason", None), ssl.SSLError):
                raise
            with urllib.request.urlopen(req, timeout=30, context=_UNVERIFIED_CTX) as resp:
                body = resp.read().decode("utf-8")
        parsed = json.loads(body)
        if not parsed:  # null or []  -> no (more) rooms
            break
        rooms.extend(parsed)
        all_count = to_int(parsed[0].get("allCount"))
        if len(rooms) >= all_count or len(parsed) == 0:
            break
        page += 1
    return rooms


def building_of(room_name):
    """'30号棟1316号室' -> '30' ; '14-5号棟1402号室' -> '14-5' ; '' if none."""
    if not room_name:
        return ""
    m = re.match(r"\s*([0-9\-]+)\s*号棟", room_name)
    return m.group(1) if m else ""


def normalize(prop, room):
    rent = yen_to_int(room.get("rent"))
    common = yen_to_int(room.get("commonfee"))
    link = room.get("roomDetailLink") or prop.get("url", "")
    if link.startswith("/"):
        link = SITE + link
    name = room.get("name", "?")
    bldg = building_of(name)
    priority_list = [str(b) for b in (prop.get("priority_buildings") or [])]
    return {
        "property": prop["name"],
        "room": name,
        "building": bldg,
        # true when this 号棟 is a best river-edge building (see config)
        "priority": bool(bldg) and bldg in priority_list,
        # per-property allowlist of river-facing 号棟 (empty = all buildings ok)
        "view_buildings": [str(b) for b in (prop.get("view_buildings") or [])],
        # per-property blocklist of known no-view 号棟 (empty = block none)
        "exclude_buildings": [str(b) for b in (prop.get("exclude_buildings") or [])],
        "type": room.get("type", "?"),
        "floor": to_int(room.get("floor")),
        "floor_all": to_int(room.get("floorAll")),
        "floorspace": to_int(room.get("floorspace")),
        "rent": rent,
        "commonfee": common,
        "total": rent + common,
        "link": link,
        "id": room.get("id") or f'{prop["danchi"]}-{name}',
    }


# --------------------------------------------------------------------------- #
# Filtering
# --------------------------------------------------------------------------- #
def matches(room, f):
    if room["total"] > f.get("max_total_rent", 10**9):
        return False
    if room["floor"] < f.get("min_floor", 0):
        return False
    if room["floorspace"] < f.get("min_floorspace", 0):
        return False
    allowed = f.get("allowed_types") or []
    if allowed and room["type"] not in allowed:
        return False
    # per-property good-view building allowlist (empty = accept every building)
    view_buildings = room.get("view_buildings") or []
    if view_buildings and room["building"] not in view_buildings:
        return False
    # per-property blocklist of known no-view buildings
    exclude_buildings = room.get("exclude_buildings") or []
    if exclude_buildings and room["building"] in exclude_buildings:
        return False
    return True


# --------------------------------------------------------------------------- #
# macOS notification
# --------------------------------------------------------------------------- #
def notify(title, message, sound="Glass"):
    def esc(s):
        return str(s).replace("\\", "\\\\").replace('"', '\\"')

    script = f'display notification "{esc(message)}" with title "{esc(title)}"'
    if sound:
        script += f' sound name "{esc(sound)}"'
    try:
        subprocess.run(["osascript", "-e", script], check=False)
    except FileNotFoundError:
        pass  # not on macOS; console output still shows the match


def _http_push(url, data=None, headers=None, method="POST"):
    """Fire-and-forget HTTP request for a push provider; never raises."""
    try:
        req = urllib.request.Request(
            url,
            data=data.encode("utf-8") if isinstance(data, str) else data,
            headers=headers or {},
            method=method,
        )
        ctx = _VERIFIED_CTX
        try:
            urllib.request.urlopen(req, timeout=15, context=ctx).read()
        except urllib.error.URLError as exc:
            if isinstance(getattr(exc, "reason", None), ssl.SSLError):
                urllib.request.urlopen(req, timeout=15, context=_UNVERIFIED_CTX).read()
            else:
                raise
    except Exception as exc:  # a push failure must not kill the run
        print(f"  ! push failed: {exc}", file=sys.stderr)


def push_notify(config, title, message, link, urgent=False):
    """Send a phone push via whichever provider(s) are configured.

    urgent=True (river-edge priority) escalates the push so it stands out:
    ntfy 'urgent' priority (bypasses silent mode) + a rotating-light tag.
    """
    push = config.get("push") or {}
    if not push.get("enabled"):
        return

    ntfy = push.get("ntfy") or {}
    # env vars (used by CI / GitHub Actions secret) override config.json
    topic = os.environ.get("NTFY_TOPIC") or ntfy.get("topic")
    if topic:
        server = (os.environ.get("NTFY_SERVER")
                  or ntfy.get("server") or "https://ntfy.sh").rstrip("/")
        _http_push(
            f"{server}/{topic}",
            data=message,
            headers={
                "Title": title.encode("utf-8").decode("latin-1", "ignore"),
                "Priority": "urgent" if urgent else "high",
                "Tags": "rotating_light" if urgent else "house",
                "Click": link,
            },
        )

    po = push.get("pushover") or {}
    if po.get("token") and po.get("user"):
        _http_push(
            "https://api.pushover.net/1/messages.json",
            data=urllib.parse.urlencode({
                "token": po["token"], "user": po["user"],
                "title": title, "message": message, "url": link,
                "priority": "1" if urgent else "0",
            }),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

    tg = push.get("telegram") or {}
    if tg.get("bot_token") and tg.get("chat_id"):
        _http_push(
            f"https://api.telegram.org/bot{tg['bot_token']}/sendMessage",
            data=urllib.parse.urlencode({
                "chat_id": tg["chat_id"],
                "text": f"{title}\n{message}\n{link}",
            }),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

    wh = push.get("webhook") or {}
    if wh.get("url"):
        _http_push(
            wh["url"],
            data=json.dumps({"title": title, "message": message, "link": link}),
            headers={"Content-Type": "application/json"},
        )


def describe(r):
    space = f"{r['floorspace']}㎡" if r["floorspace"] else "?㎡"
    return (
        f"{r['property']} — {r['room']}\n"
        f"{r['type']}, {space}, {r['floor']}/{r['floor_all']}F\n"
        f"¥{r['total']:,}/mo (rent ¥{r['rent']:,} + fee ¥{r['commonfee']:,})"
    )


# --------------------------------------------------------------------------- #
# State
# --------------------------------------------------------------------------- #
def load_state():
    if STATE_PATH.exists():
        try:
            return set(json.loads(STATE_PATH.read_text()))
        except (ValueError, OSError):
            return set()
    return set()


def save_state(seen):
    STATE_PATH.write_text(json.dumps(sorted(seen), ensure_ascii=False, indent=0))


def log_match(r):
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    with LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write(
            f"{stamp}\t{r['property']}\t{r['room']}\t{r['type']}\t"
            f"{r['floor']}F\t{r['floorspace']}㎡\t¥{r['total']:,}\t{r['link']}\n"
        )


# --------------------------------------------------------------------------- #
# One pass
# --------------------------------------------------------------------------- #
def run_once(config, *, test=False, show_all=False):
    f = config["filters"]
    seen = set() if test else load_state()
    new_seen = set(seen)
    new_matches = []
    total_open = 0

    for prop in config["properties"]:
        try:
            rooms = fetch_rooms(prop)
        except Exception as exc:  # network hiccup — skip this property this pass
            print(f"  ! {prop['name']}: fetch failed ({exc})", file=sys.stderr)
            continue

        total_open += len(rooms)
        for raw in rooms:
            room = normalize(prop, raw)
            hit = matches(room, f)
            if show_all:
                tag = "MATCH" if hit else "     "
                print(f"  [{tag}] {room['property']} {room['room']} "
                      f"{room['type']} {room['floor']}F ¥{room['total']:,}")
            if not hit:
                continue
            if room["id"] in seen:
                continue
            new_matches.append(room)
            new_seen.add(room["id"])

    stamp = datetime.now().strftime("%H:%M:%S")
    if new_matches:
        for r in new_matches:
            prio = bool(r.get("priority"))
            star = "⭐ RIVER-EDGE " if prio else ""
            print(f"\n🎉  NEW {star}MATCH ({stamp})\n{describe(r)}\n{r['link']}")
            log_match(r)
            title = "⭐ UR RIVER-EDGE vacancy!" if prio else "UR riverside vacancy!"
            sound = (config.get("priority_alert_sound", "Sosumi") if prio
                     else config.get("alert_sound", "Glass"))
            notify(title, describe(r), sound)
            push_notify(config, f"🏞️ {title}", describe(r), r["link"], urgent=prio)
            if config.get("open_browser_on_match"):
                subprocess.run(["open", r["link"]], check=False)
    else:
        print(f"[{stamp}] no new matches "
              f"({total_open} room(s) currently open across watched properties)")

    if not test:
        save_state(new_seen)
    return new_matches


def main():
    ap = argparse.ArgumentParser(description="UR riverside vacancy monitor")
    ap.add_argument("--loop", action="store_true", help="keep running on an interval")
    ap.add_argument("--test", action="store_true",
                    help="ignore saved state; report all current matches")
    ap.add_argument("--all", action="store_true", dest="show_all",
                    help="print every open room, matching or not")
    args = ap.parse_args()

    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))

    if not args.loop:
        run_once(config, test=args.test, show_all=args.show_all)
        return

    interval = max(1, int(config.get("poll_interval_minutes", 15))) * 60
    print(f"Watching {len(config['properties'])} properties every "
          f"{interval // 60} min. Ctrl-C to stop.")
    while True:
        try:
            run_once(config, show_all=args.show_all)
        except Exception as exc:
            print(f"pass failed: {exc}", file=sys.stderr)
        time.sleep(interval)


if __name__ == "__main__":
    main()
