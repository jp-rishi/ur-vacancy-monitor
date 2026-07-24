#!/usr/bin/env python3
"""
Local web dashboard for the UR riverside vacancy monitor.

    python3 serve.py            # then open http://localhost:8765

Shows every currently-open room across the watched properties, highlights the
ones that match your filters, and lets you tweak the filters live (per-view,
without touching config.json). Pure Python standard library.
"""

import html as html_mod
import json
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import ur_watch as core

PORT = 8765
CACHE_TTL = 60  # seconds; avoids hammering UR on every browser refresh

_cache = {"ts": 0.0, "rooms": []}
_lock = threading.Lock()


def collect_rooms():
    """Fetch all watched properties (cached), return a flat list of rooms."""
    with _lock:
        if time.time() - _cache["ts"] < CACHE_TTL and _cache["rooms"]:
            return _cache["rooms"], _cache["ts"]

    config = json.loads(core.CONFIG_PATH.read_text(encoding="utf-8"))
    props = config["properties"]

    def one(prop):
        try:
            return [core.normalize(prop, r) for r in core.fetch_rooms(prop)]
        except Exception:
            return []

    rooms = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        for result in pool.map(one, props):
            rooms.extend(result)

    with _lock:
        _cache["rooms"] = rooms
        _cache["ts"] = time.time()
    return rooms, _cache["ts"]


def filters_from_query(qs):
    """Build a filter dict from query params, falling back to config.json."""
    config = json.loads(core.CONFIG_PATH.read_text(encoding="utf-8"))
    f = dict(config["filters"])

    def num(key, default):
        vals = qs.get(key)
        if vals and vals[0].strip():
            try:
                return int(vals[0])
            except ValueError:
                return default
        return default

    f["max_total_rent"] = num("maxrent", f.get("max_total_rent", 10**9))
    f["min_floor"] = num("minfloor", f.get("min_floor", 0))
    f["min_floorspace"] = num("minspace", f.get("min_floorspace", 0))
    types = qs.get("types", [""])[0].strip()
    if "types" in qs:  # present (even if empty) => user submitted the form
        f["allowed_types"] = [t.strip() for t in types.split(",") if t.strip()]
    return f


def esc(s):
    return html_mod.escape(str(s))


def render(rooms, fetched_ts, f, show_all_rows):
    for r in rooms:
        r["_match"] = core.matches(r, f)
    rooms.sort(key=lambda r: (not r["_match"], not r.get("priority"), r["total"]))
    match_count = sum(1 for r in rooms if r["_match"])
    total_open = len(rooms)
    hidden = total_open - match_count
    updated = datetime.fromtimestamp(fetched_ts).strftime("%Y-%m-%d %H:%M:%S")

    display = rooms if show_all_rows else [r for r in rooms if r["_match"]]
    show_all_checked = "checked" if show_all_rows else ""
    types_val = ",".join(f.get("allowed_types") or [])
    rows = []
    for r in display:
        if r["_match"] and r.get("priority"):
            cls, badge = "match prio", "⭐ river-edge"
        elif r["_match"]:
            cls, badge = "match", "✓ match"
        else:
            cls, badge = "miss", "—"
        space = f"{r['floorspace']}㎡" if r["floorspace"] else "—"
        rows.append(f"""
        <tr class="{cls}">
          <td class="badge">{badge}</td>
          <td class="prop">{esc(r['property'])}</td>
          <td class="bldg">{esc(r['building'] or '—')}</td>
          <td>{esc(r['room'])}</td>
          <td>{esc(r['type'])}</td>
          <td class="num">{r['floor']}/{r['floor_all']}F</td>
          <td class="num">{space}</td>
          <td class="num rent">¥{r['total']:,}</td>
          <td class="fee">¥{r['rent']:,} + ¥{r['commonfee']:,}</td>
          <td><a href="{esc(r['link'])}" target="_blank" rel="noopener">open ↗</a></td>
        </tr>""")

    if rows:
        body_rows = "".join(rows)
    elif total_open == 0:
        body_rows = ('<tr><td colspan="10" class="empty">No rooms currently open '
                     'across the watched properties.</td></tr>')
    else:
        body_rows = (f'<tr><td colspan="10" class="empty">No rooms match your '
                     f'filters right now. {hidden} other room(s) are open — tick '
                     f'"Show non-matching too" to see them.</td></tr>')

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="120">
<title>UR Riverside Vacancies</title>
<style>
  :root {{ color-scheme: light dark; }}
  * {{ box-sizing: border-box; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         margin: 0; padding: 24px; background: Canvas; color: CanvasText; }}
  h1 {{ font-size: 20px; margin: 0 0 4px; }}
  .sub {{ opacity: .65; font-size: 13px; margin-bottom: 20px; }}
  .summary {{ display: flex; gap: 24px; margin-bottom: 18px; flex-wrap: wrap; }}
  .stat {{ background: color-mix(in srgb, CanvasText 6%, Canvas);
          border-radius: 10px; padding: 12px 18px; }}
  .stat b {{ font-size: 24px; display: block; }}
  .stat span {{ font-size: 12px; opacity: .7; }}
  form {{ display: flex; gap: 12px; align-items: flex-end; flex-wrap: wrap;
         background: color-mix(in srgb, CanvasText 5%, Canvas);
         padding: 14px 16px; border-radius: 10px; margin-bottom: 20px; }}
  label {{ display: flex; flex-direction: column; font-size: 12px; opacity: .8; gap: 4px; }}
  label.chk {{ flex-direction: row; align-items: center; gap: 6px; }}
  label.chk input {{ width: auto; }}
  input {{ padding: 6px 8px; border-radius: 6px; border: 1px solid
          color-mix(in srgb, CanvasText 25%, Canvas); background: Canvas;
          color: CanvasText; font-size: 14px; width: 130px; }}
  button {{ padding: 7px 16px; border-radius: 6px; border: none;
           background: #2563eb; color: white; font-size: 14px; cursor: pointer; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
  th, td {{ text-align: left; padding: 9px 10px;
           border-bottom: 1px solid color-mix(in srgb, CanvasText 12%, Canvas); }}
  th {{ font-size: 12px; text-transform: uppercase; letter-spacing: .04em; opacity: .6; }}
  .num {{ text-align: right; white-space: nowrap; }}
  .rent {{ font-weight: 600; }}
  .fee {{ opacity: .6; font-size: 12px; white-space: nowrap; }}
  .prop {{ font-weight: 600; }}
  tr.match {{ background: color-mix(in srgb, #22c55e 16%, Canvas); }}
  tr.prio {{ background: color-mix(in srgb, #f59e0b 24%, Canvas); }}
  tr.prio .badge {{ color: #b45309; font-weight: 700; }}
  tr.miss {{ opacity: .55; }}
  .badge {{ font-size: 12px; white-space: nowrap; }}
  tr.match .badge {{ color: #16a34a; font-weight: 700; }}
  .empty {{ text-align: center; padding: 30px; opacity: .6; }}
  a {{ color: #2563eb; text-decoration: none; }}
</style>
</head>
<body>
  <h1>🏞️ UR Riverside Vacancies — Osaka</h1>
  <div class="sub">Last checked {updated} · auto-refreshes every 2 min ·
      data cached up to {CACHE_TTL}s</div>

  <div class="summary">
    <div class="stat"><b>{match_count}</b><span>matching your filters</span></div>
    <div class="stat"><b>{total_open}</b><span>open rooms total</span></div>
    <div class="stat"><b>{hidden}</b><span>hidden (below filters)</span></div>
  </div>

  <form method="get">
    <label>Max total rent (¥)
      <input type="number" name="maxrent" value="{f['max_total_rent']}"></label>
    <label>Min floor
      <input type="number" name="minfloor" value="{f['min_floor']}"></label>
    <label>Min ㎡
      <input type="number" name="minspace" value="{f['min_floorspace']}"></label>
    <label>Layouts (comma, blank = any)
      <input type="text" name="types" value="{esc(types_val)}"
             placeholder="e.g. 2LDK,3LDK"></label>
    <label class="chk"><input type="checkbox" name="showall" value="1" {show_all_checked}>
      Show non-matching too</label>
    <button type="submit">Apply</button>
  </form>

  <table>
    <thead><tr>
      <th>Match</th><th>Property</th><th>Bldg</th><th>Room</th><th>Layout</th>
      <th class="num">Floor</th><th class="num">Size</th>
      <th class="num">Total/mo</th><th>Breakdown</th><th>Link</th>
    </tr></thead>
    <tbody>{body_rows}</tbody>
  </table>
</body>
</html>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path not in ("/", "/index.html"):
            self.send_error(404)
            return
        qs = urllib.parse.parse_qs(parsed.query)
        f = filters_from_query(qs)
        show_all_rows = "showall" in qs
        rooms, ts = collect_rooms()
        page = render(rooms, ts, f, show_all_rows).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(page)))
        self.end_headers()
        self.wfile.write(page)

    def log_message(self, *args):
        pass  # quiet


def main():
    print(f"UR vacancy dashboard → http://localhost:{PORT}  (Ctrl-C to stop)")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
