#!/usr/bin/env python3
"""Twin Chinese-dragon contribution graph generator.

Fetches a GitHub user's contribution calendar and emits an animated SVG in
which two Chinese-style serpentine dragons -- a black dragon leading and a
white dragon trailing half a loop behind on the same route -- fly a
serpentine route across the contribution grid; every cell the LEAD dragon
passes over "burns" (flashes ember orange) and fades to empty, in sync
with the flight. Pure SMIL animation, so it works when embedded via
<img> in a GitHub README.

Data sources (first that succeeds wins):
  1. GitHub GraphQL API (needs GITHUB_TOKEN in the environment).
  2. Public fallback: github-contributions-api.jogruber.de (no token).

Usage:
    python generate_dragon.py --user LlvallVeiz --outdir dist

Outputs (in --outdir):
    github-contribution-grid-dragon.svg        (light palette)
    github-contribution-grid-dragon-dark.svg   (dark palette)

Standard library only.
"""

import argparse
import json
import math
import os
import sys
import urllib.request
from datetime import date, timedelta

# ---------------------------------------------------------------- geometry
CELL = 10
GAP = 3
PITCH = CELL + GAP            # 13
ROWS = 7
WEEKS = 53
GRID_W = WEEKS * PITCH - GAP  # 686
GRID_X0 = 70
GRID_X1 = GRID_X0 + GRID_W    # 756
GRID_Y = 58
PASS_XS = 36                  # flight start x (left overhang)
PASS_XE = 790                 # flight end x (right overhang)
TOP_LANE_Y = 27               # return-flight lane centre
MONTH_LABEL_Y = 50
SVG_W = 850
SVG_H = 176

SPEED = 460.0                 # target px per second along the flight path
HEAD = 33                     # snout offset from motion origin, px
BOB_AMP = 3.2                 # vertical bob amplitude on passes, px
BOB_WL = 170.0                # bob wavelength, px

FLAME_BRIGHT = "#ffd166"
FLAME_EMBER = "#f97316"
FLAME_DARK = "#7c2d12"

PALETTES = {
    "light": {
        "empty": "#ebedf0",
        "levels": ["#9be9a8", "#40c463", "#30a14e", "#216e39"],
        "label": "#57606a",
        # Lead dragon: black body, silver-white mane.
        "lead": {
            "body": "#1D2A32", "outline": "none",
            "mane": "#f4f6f8", "mane_stroke": "#9aa5b1",
            "horn": "#a8842c", "belly": "rgba(255,255,255,0.18)",
            "fin": "#5b6b78",
        },
        # Trailing dragon: white body, gold mane/horns/fins.
        "follow": {
            "body": "#f7f9fa", "outline": "#a7b0b8",
            "mane": "#c9a227", "mane_stroke": "#8a6d1f",
            "horn": "#c9a227", "belly": "rgba(29,42,50,0.07)",
            "fin": "#c9a227",
        },
    },
    "dark": {
        "empty": "#161b22",
        "levels": ["#0e4429", "#006d32", "#26a641", "#39d353"],
        "label": "#7d8590",
        "lead": {
            "body": "#232b33", "outline": "#46525e",
            "mane": "#e9eef3", "mane_stroke": "none",
            "horn": "#dfe5ea", "belly": "rgba(255,255,255,0.10)",
            "fin": "#8b95a1",
        },
        "follow": {
            "body": "#eef1f4", "outline": "#7d8590",
            "mane": "#d4af37", "mane_stroke": "#8a6d1f",
            "horn": "#d4af37", "belly": "rgba(0,0,0,0.08)",
            "fin": "#d4af37",
        },
    },
}

# GitHub's own day colours -> level (used to decode GraphQL `color`).
_COLOR_LEVEL = {
    "#9be9a8": 1, "#40c463": 2, "#30a14e": 3, "#216e39": 4,
    "#0e4429": 1, "#006d32": 2, "#26a641": 3, "#39d353": 4,
}

GRAPHQL_QUERY = """
query($login: String!) {
  user(login: $login) {
    contributionsCollection {
      contributionCalendar {
        weeks {
          contributionDays { date contributionCount color weekday }
        }
      }
    }
  }
}
"""


def row_cy(r):
    return GRID_Y + r * PITCH + CELL / 2


# ---------------------------------------------------------------- fetching
def _http_json(url, data=None, headers=None):
    req = urllib.request.Request(url, data=data, headers=headers or {})
    req.add_header("User-Agent", "dragon-contribution-graph/1.0")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _level_from_count(count):
    if count <= 0:
        return 0
    if count <= 2:
        return 1
    if count <= 5:
        return 2
    if count <= 9:
        return 3
    return 4


def fetch_graphql(user, token):
    """Return list of weeks; each week is a list of 7 (date, count, level)."""
    payload = json.dumps(
        {"query": GRAPHQL_QUERY, "variables": {"login": user}}).encode()
    data = _http_json(
        "https://api.github.com/graphql",
        data=payload,
        headers={
            "Authorization": "bearer " + token,
            "Content-Type": "application/json",
        },
    )
    calendar = (data["data"]["user"]["contributionsCollection"]
                ["contributionCalendar"])
    weeks = []
    for week in calendar["weeks"]:
        days = [(None, 0, 0)] * ROWS
        days = list(days)
        for idx, day in enumerate(week["contributionDays"]):
            row = int(day.get("weekday", idx))
            row = max(0, min(ROWS - 1, row))
            count = int(day["contributionCount"])
            if count == 0:
                level = 0
            else:
                level = _COLOR_LEVEL.get(str(day.get("color", "")).lower())
                if level is None:
                    level = _level_from_count(count)
            days[row] = (day["date"], count, level)
        weeks.append(days)
    return weeks[-WEEKS:]


def fetch_fallback(user):
    """Public API (jogruber): flat day list -> Sunday-start week columns."""
    url = "https://github-contributions-api.jogruber.de/v4/" + user + "?y=last"
    data = _http_json(url)
    by_date = {}
    for item in data.get("contributions", []):
        d = date.fromisoformat(item["date"])
        by_date[d] = (int(item.get("count", 0)), int(item.get("level", 0)))
    if not by_date:
        raise ValueError("fallback API returned no contributions")
    today = date.today()
    last = min(max(by_date), today)
    first = min(by_date)
    # Sunday of the week containing `first` (Python: Mon=0 .. Sun=6).
    start = first - timedelta(days=(first.weekday() + 1) % 7)
    ncols = (last - start).days // 7 + 1
    weeks = []
    for c in range(ncols):
        col = []
        for r in range(ROWS):
            d = start + timedelta(days=c * 7 + r)
            count, level = by_date.get(d, (0, 0))
            if count == 0:
                level = 0
            col.append((d.isoformat(), count, level))
        weeks.append(col)
    return weeks


def normalize_weeks(weeks):
    """Force exactly WEEKS columns of ROWS cells, padding on the left."""
    weeks = [list(w) for w in weeks][-WEEKS:]
    blank = [(None, 0, 0)] * ROWS
    while len(weeks) < WEEKS:
        weeks.insert(0, list(blank))
    return weeks


def fetch_calendar(user):
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        try:
            return normalize_weeks(fetch_graphql(user, token)), "graphql"
        except Exception as exc:  # fall through to the public API
            print("[dragon] GraphQL fetch failed (" + str(exc) + "); "
                  "falling back to public API", file=sys.stderr)
    return normalize_weeks(fetch_fallback(user)), "public-api"


# ------------------------------------------------------------- flight path
def build_route():
    """Waypoints (x, y, facing) for the closed flight loop.

    Serpentine passes across the 7 day-rows (alternating direction),
    U-turns at the sides, then a return flight across the top lane back
    to the start point. Returns (loop_points, pass_wp_ranges) where
    pass_wp_ranges[row] = (first_wp_index, last_wp_index) inclusive.
    """
    pts = []
    pass_ranges = {}

    def add(x, y, f):
        pts.append((float(x), float(y), f))

    for r in range(ROWS):
        cy = row_cy(r)
        d = 1 if r % 2 == 0 else -1
        xa, xb = (PASS_XS, PASS_XE) if d == 1 else (PASS_XE, PASS_XS)
        start_idx = len(pts) - (1 if r > 0 else 0)
        n = 26
        for i in range(n + 1):
            if r > 0 and i == 0:
                continue  # already added as the U-turn's last point
            x = xa + (xb - xa) * i / n
            y = cy + BOB_AMP * math.sin(2 * math.pi * (x - PASS_XS) / BOB_WL)
            add(x, y, d)
        pass_ranges[r] = (start_idx, len(pts) - 1)
        if r < ROWS - 1:
            cy2 = row_cy(r + 1)
            midy = (cy + cy2) / 2
            ry = (cy2 - cy) / 2
            rx = 20
            steps = 12
            for k in range(1, steps + 1):
                phi = -math.pi / 2 + math.pi * k / steps
                x = xb + d * rx * math.cos(phi)
                y = midy + ry * math.sin(phi)
                add(x, y, d if phi < 0 else -d)
    # Return leg: up the right side, across the top lane, down the left.
    cy6, cy0 = row_cy(ROWS - 1), row_cy(0)
    add(PASS_XE + 4, cy6 - 26, 1)
    add(PASS_XE - 6, TOP_LANE_Y + 14, 1)
    n = 24
    for i in range(1, n + 1):
        x = PASS_XE - (PASS_XE - PASS_XS) * i / n
        y = TOP_LANE_Y + 1.6 * math.sin(2 * math.pi * (x - PASS_XS) / 260.0)
        add(x, y, -1)
    add(PASS_XS - 4, cy0 - 20, -1)
    add(PASS_XS, cy0, -1)  # coincides with pts[0]; dropped as loop duplicate
    return pts[:-1], pass_ranges


def catmull_segments(pts):
    """Closed-loop Catmull-Rom -> cubic Bezier segments.

    Each segment is (p0, c1, c2, p1, facing); segment i starts at pts[i].
    """
    n = len(pts)
    segs = []
    for i in range(n):
        p0 = pts[i]
        p1 = pts[(i + 1) % n]
        pm = pts[(i - 1) % n]
        p2 = pts[(i + 2) % n]
        c1 = (p0[0] + (p1[0] - pm[0]) / 6.0, p0[1] + (p1[1] - pm[1]) / 6.0)
        c2 = (p1[0] - (p2[0] - p0[0]) / 6.0, p1[1] - (p2[1] - p0[1]) / 6.0)
        segs.append((p0, c1, c2, p1, p0[2]))
    return segs


def _cubic(p0, c1, c2, p1, t):
    mt = 1 - t
    x = (mt**3 * p0[0] + 3 * mt * mt * t * c1[0]
         + 3 * mt * t * t * c2[0] + t**3 * p1[0])
    y = (mt**3 * p0[1] + 3 * mt * mt * t * c1[1]
         + 3 * mt * t * t * c2[1] + t**3 * p1[1])
    return x, y


def sample_route(segs, per_seg=24):
    """Sample every segment; returns (samples, total_length).

    samples: list of (x, y, cumlen, seg_index), starting at cumlen 0.
    """
    samples = []
    total = 0.0
    prev = None
    for si, (p0, c1, c2, p1, _f) in enumerate(segs):
        for k in range(per_seg + 1):
            if si > 0 and k == 0:
                continue
            x, y = _cubic(p0, c1, c2, p1, k / per_seg)
            if prev is not None:
                total += math.hypot(x - prev[0], y - prev[1])
            samples.append((x, y, total, si))
            prev = (x, y)
    x0, y0 = samples[0][0], samples[0][1]
    total += math.hypot(x0 - prev[0], y0 - prev[1])
    return samples, total


def flip_timeline(segs, samples, total):
    """(values, keyTimes) for the discrete horizontal-flip animation."""
    seg_start_len = {}
    for (_x, _y, cum, si) in samples:
        seg_start_len.setdefault(si, cum)
    values = []
    key_times = []
    current = None
    for si, seg in enumerate(segs):
        facing = seg[4]
        if facing != current:
            values.append("1 1" if facing == 1 else "-1 1")
            key_times.append(seg_start_len.get(si, 0.0) / total)
            current = facing
    values.append(values[0])
    key_times.append(1.0)
    return values, key_times


def burn_times(segs, samples, pass_ranges, duration, total_len):
    """Map (col, row) -> seconds when the dragon's head reaches the cell."""
    tf = duration / total_len  # seconds per px (paced motion)
    burns = {}
    for r in range(ROWS):
        wp0, wp1 = pass_ranges[r]
        leg = [s for s in samples if wp0 <= s[3] < wp1]
        if not leg:
            continue
        d = 1 if r % 2 == 0 else -1
        for c in range(WEEKS):
            cx = GRID_X0 + c * PITCH + CELL / 2
            prev = None
            hit = None
            for s in leg:
                hx = s[0] + HEAD * segs[s[3]][4]
                if prev is not None:
                    phx = prev[0] + HEAD * segs[prev[3]][4]
                    if (d == 1 and phx < cx <= hx) or \
                       (d == -1 and phx > cx >= hx):
                        span = (hx - phx) or 1e-9
                        frac = (cx - phx) / span
                        hit = (prev[2] + frac * (s[2] - prev[2])) * tf
                        break
                prev = s
            if hit is None:  # fallback: proportional across the leg window
                t_start = leg[0][2] * tf
                t_end = leg[-1][2] * tf
                frac = (cx - GRID_X0) / GRID_W
                if d == -1:
                    frac = 1 - frac
                hit = t_start + frac * (t_end - t_start)
            burns[(c, r)] = min(max(hit, 0.0), duration)
    return burns


# ------------------------------------------------------------------ dragon
def head_inner(pal, scheme):
    """Head-station artwork (facing +x), centred on the motion origin.

    Only the head lives on this station: skull, antler horns, whiskers,
    flowing mane, eye, breath plume (lead only), the front clawed leg
    and the small dorsal fin behind the skull. The body is NOT drawn
    here any more -- it is a chain of round stations (see BODY_STATIONS)
    that bend along the flight path.
    """
    d = pal[scheme]
    body = d["body"]
    mane = d["mane"]
    mane_stroke = d["mane_stroke"]
    horn = d["horn"]
    fin = d["fin"]
    mane_stroke_attr = ""
    if mane_stroke != "none":
        mane_stroke_attr = f' stroke="{mane_stroke}" stroke-width="0.6"'
    edge_attr = ""
    if d["outline"] != "none":
        edge_attr = f' stroke="{d["outline"]}" stroke-width="0.8"'
    und = "2.4s" if scheme == "lead" else "2.9s"
    shim = "2.8s" if scheme == "lead" else "3.4s"
    breath = ""
    if scheme == "lead":
        breath = """
        <!-- gold-white breath plume (lead dragon only) -->
        <g transform="translate(13.2,-0.4)">
          <animate attributeName="opacity" values="0.95;0.4;1;0.55;0.95" dur="0.5s" repeatCount="indefinite"/>
          <g>
            <animateTransform attributeName="transform" type="scale" values="1 1;0.82 0.88;1.06 1.04;0.9 0.94;1 1" dur="0.5s" repeatCount="indefinite"/>
            <path d="M 0 -1.1 C 4.5 -2.8, 9 -2.8, 13.5 -1.2 C 10.8 -0.2, 10.8 0.4, 13.5 1.4 C 9 2.9, 4.5 2.6, 0 1.1 Z" fill="url(#plumeGrad)"/>
            <path d="M 0 -0.55 C 3 -1.4, 6 -1.4, 8.5 -0.6 C 6.5 0, 6.5 0.2, 8.5 0.7 C 6 1.5, 3 1.3, 0 0.55 Z" fill="#fffbe6" opacity="0.95"/>
          </g>
        </g>"""
    return f"""
    <g>
      <animateTransform attributeName="transform" type="rotate" values="-1.3 0 0; 1.3 0 0; -1.3 0 0" dur="{und}" repeatCount="indefinite"/>
      <!-- dorsal fin behind the skull -->
      <path d="M 7 -3.4 L 11 -9.5 L 15 -4.4 Z" fill="{fin}"/>
      <!-- front clawed leg -->
      <path d="M 11 2.8 C 13.6 5.2, 14.4 8.2, 13.6 11.4 L 10.8 10.8 C 11.6 8, 11.2 5.6, 9.8 3.8 Z" fill="{body}"{edge_attr}/>
      <path d="M 11 11.2 L 9.6 13 M 12.4 11.5 L 11.6 13.6 M 13.8 11.3 L 13.6 13.6" fill="none" stroke="{horn}" stroke-width="0.9" stroke-linecap="round"/>
      <!-- head -->
      <g transform="translate(22,-3)">
        <!-- flowing mane -->
        <g fill="{mane}"{mane_stroke_attr}>
          <animate attributeName="opacity" values="0.72;1;0.72" dur="{shim}" repeatCount="indefinite"/>
          <path d="M -2 -6.5 C -8 -10, -14 -11, -20 -10 C -14 -7.5, -8.5 -5.8, -3 -4.6 Z"/>
          <path d="M -3.5 -2.5 C -9.5 -4.5, -15.5 -4, -20.5 -1 C -15 -0.6, -9.5 -0.2, -4 0.6 Z"/>
          <path d="M -2.5 2 C -7.5 4, -11.5 6.8, -13.5 10.5 C -9.8 8.6, -6 6.2, -3.2 3.8 Z"/>
        </g>
        <!-- antler horns -->
        <g fill="none" stroke="{horn}" stroke-width="1.7" stroke-linecap="round">
          <path d="M -1 -5.5 C -3.5 -8.5, -6.5 -11, -10.5 -12.5 M -5 -9.2 C -6.8 -10.8, -9 -11.6, -11.2 -11.8 M -3.4 -7.6 C -4.2 -9.6, -5.6 -11, -7.4 -11.9"/>
          <g transform="translate(-3.5,1.4) scale(0.78)" opacity="0.6">
            <path d="M -1 -5.5 C -3.5 -8.5, -6.5 -11, -10.5 -12.5 M -5 -9.2 C -6.8 -10.8, -9 -11.6, -11.2 -11.8 M -3.4 -7.6 C -4.2 -9.6, -5.6 -11, -7.4 -11.9"/>
          </g>
        </g>
        <!-- skull -->
        <path d="M -5 -4.5 C -1 -7.2, 4.5 -7.4, 8 -5.6 C 10.8 -4.2, 12.6 -2.4, 13 -0.6 C 10.6 0.4, 9.2 1.2, 7.8 2.8 C 5.2 4.6, 0.6 5, -2.6 3.8 C -5 2.6, -6.2 0.2, -5.8 -2 C -5.6 -3, -5.4 -3.8, -5 -4.5 Z" fill="{body}"{edge_attr}/>
        <path d="M 2.5 3 C 5.5 3.6, 8.6 3, 11.6 1.2" fill="none" stroke="{fin}" stroke-width="0.7" opacity="0.8"/>
        <circle cx="11" cy="-2.6" r="0.85" fill="{fin}"/>
        <!-- eye -->
        <circle cx="6.2" cy="-3.2" r="1.6" fill="#fbbf24">
          <animate attributeName="opacity" values="1;0.45;1" dur="1.7s" repeatCount="indefinite"/>
        </circle>
        <!-- whiskers -->
        <path d="M 12.2 -0.8 C 16 1.2, 17.4 4.6, 15.8 8.2 M 12.6 1 C 15.4 3.8, 15.8 6.8, 13.8 9.8" fill="none" stroke="{mane}" stroke-width="0.9" stroke-linecap="round"/>{breath}
      </g>
    </g>"""


# ------------------------------------------------------- segmented body
# v3.1 smoothness rework: the dragon is a CHAIN of stations riding the
# same flight path, each with a negative `begin` offset equal to its
# arc-length distance behind the head -- the same trick that parks the
# white dragon half a loop behind the black one. The body therefore
# bends through the U-turns segment by segment (like the old snake)
# instead of sliding around them as one rigid drawing. Round stations
# need no orientation; the finned stations, rear legs and tail tuft
# carry the discrete facing flip with the SAME delayed begin, so each
# part flips when IT reaches the turn.
BODY_STATIONS = [  # (px behind head origin, radius)
    (2.0, 4.1), (8.0, 4.4), (14.0, 4.55), (20.0, 4.5), (26.0, 4.3),
    (32.0, 4.0), (38.0, 3.6), (43.5, 3.1), (48.5, 2.55), (53.5, 2.0),
]
FIN_STATIONS = {14.0, 32.0, 48.5}
LEG_STATION = 32.0
TAIL_DIST = 58.0


def _station_wrap(duration, total_len, path_d, flip_values, flip_key_times,
                  base_begin, dist, inner):
    """One station riding the path `dist` px behind the head station."""
    off = dist * duration / total_len
    begin = f"{base_begin - off:.3f}s"
    kt = ";".join(f"{v:.4f}" for v in flip_key_times)
    vals = ";".join(flip_values)
    return f"""
  <g transform="translate({PASS_XS},{row_cy(0):.0f})">
    <animateMotion begin="{begin}" dur="{duration:.2f}s" repeatCount="indefinite" path="{path_d}"/>
    <g>
      <animateTransform attributeName="transform" type="scale" calcMode="discrete" values="{vals}" keyTimes="{kt}" begin="{begin}" dur="{duration:.2f}s" repeatCount="indefinite"/>
{inner}
    </g>
  </g>"""


def _body_station_inner(pal, scheme, dist, r):
    d = pal[scheme]
    stroke = ""
    if d["outline"] != "none":
        stroke = f' stroke="{d["outline"]}" stroke-width="0.7"'
    parts = [f'      <circle cx="0" cy="0" r="{r}" fill="{d["body"]}"{stroke}/>']
    parts.append(f'      <circle cx="0" cy="1.6" r="{r * 0.5:.2f}" fill="{d["belly"]}"/>')
    if dist in FIN_STATIONS:
        parts.append(
            f'      <path d="M {-0.42 * r - 1:.1f} {-r + 0.8:.1f} '
            f'L 0 {-r - 4.6:.1f} L {0.42 * r + 1:.1f} {-r + 0.8:.1f} Z" '
            f'fill="{d["fin"]}"/>')
    if dist == LEG_STATION:
        edge_attr = ""
        if d["outline"] != "none":
            edge_attr = f' stroke="{d["outline"]}" stroke-width="0.8"'
        parts.append(
            '      <g transform="translate(33,0)">'
            f'<path d="M -34 -2.5 C -32 -0.2, -31.6 2.6, -32.4 5.6 '
            f'L -35.2 5 C -34.4 2.2, -34.8 -0.4, -36.2 -2 Z" '
            f'fill="{d["body"]}"{edge_attr}/>'
            f'<path d="M -35 5.3 L -36.4 7.2 M -33.6 5.6 L -34.2 7.8 '
            f'M -32.3 5.4 L -32.2 7.6" fill="none" stroke="{d["horn"]}" '
            'stroke-width="0.9" stroke-linecap="round"/></g>')
    return "\n".join(parts)


def _tail_inner(pal, scheme):
    d = pal[scheme]
    return ('      <g transform="translate(54.5,0)">'
            '<path d="M -52 2 C -57 -1.5, -59.5 -5, -59 -9.5 '
            'C -55.8 -6.2, -54.6 -3.4, -55 -0.2 C -53.2 -1.8, -51.4 -2, '
            '-49.8 -1 C -51.4 0.6, -51.8 2.4, -51 4.6 C -53 3.9, '
            f'-53.6 3.2, -52 2 Z" fill="{d["fin"]}" opacity="0.95"/></g>')


def dragon_chain(pal, scheme, duration, total_len, path_d, flip_values,
                 flip_key_times, base_begin=0.0):
    """A whole dragon: tail, body stations (tail-most first), head last.

    The head station rides at base_begin (0 for the lead, -D/2 for the
    trailing dragon); every other station is offset further back by its
    arc-length distance behind the head.
    """
    parts = [_station_wrap(duration, total_len, path_d, flip_values,
                           flip_key_times, base_begin, TAIL_DIST,
                           _tail_inner(pal, scheme))]
    for dist, r in reversed(BODY_STATIONS):
        parts.append(_station_wrap(duration, total_len, path_d,
                                   flip_values, flip_key_times, base_begin,
                                   dist,
                                   _body_station_inner(pal, scheme, dist, r)))
    parts.append(_station_wrap(duration, total_len, path_d, flip_values,
                               flip_key_times, base_begin, 0.0,
                               head_inner(pal, scheme)))
    return "\n".join(parts)



# -------------------------------------------------------------------- svg
def _burn_animate(level_color, empty_color, t_burn, duration):
    """Fill animation: hold level colour, flash flame, fade to empty."""
    marks = [
        (0.0, level_color),
        (t_burn - 0.05, level_color),
        (t_burn + 0.10, FLAME_BRIGHT),
        (t_burn + 0.45, FLAME_EMBER),
        (t_burn + 0.80, FLAME_DARK),
        (t_burn + 1.30, empty_color),
        (duration, empty_color),
    ]
    fixed = []
    last_t = -1.0
    for t, v in marks:
        t = min(max(t, 0.0), duration)
        if t <= last_t:
            if v == level_color and fixed:
                continue  # drop the pre-burn hold point if it collides
            t = last_t + 0.002
            if t > duration:
                continue
        fixed.append((t, v))
        last_t = t
    if fixed[-1][0] < duration:
        fixed.append((duration, empty_color))
    values = ";".join(v for _t, v in fixed)
    key_times = ";".join(f"{t / duration:.4f}" for t, _v in fixed)
    return ('<animate attributeName="fill" dur="' + f"{duration:.2f}s"
            + '" repeatCount="indefinite" values="' + values
            + '" keyTimes="' + key_times + '"/>')


def build_svg(weeks, pal, duration, total_len, path_d, flip_values,
              flip_key_times, burns):
    parts = []
    parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {SVG_W} {SVG_H}" '
        f'width="{SVG_W}" height="{SVG_H}" role="img">')
    parts.append("<title>Contribution graph with two Chinese dragons</title>")
    parts.append("""<defs>
    <linearGradient id="plumeGrad" x1="0" y1="0" x2="1" y2="0">
      <stop offset="0" stop-color="#fffbe6"/>
      <stop offset="0.4" stop-color="#ffe08a"/>
      <stop offset="0.75" stop-color="#f5b921"/>
      <stop offset="1" stop-color="#f5b921" stop-opacity="0"/>
    </linearGradient>
  </defs>""")
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    prev_month = None
    for c, week in enumerate(weeks):
        first_date = next((d for d in week if d[0]), None)
        if not first_date:
            continue
        m = int(first_date[0][5:7])
        if m != prev_month:
            prev_month = m
            x = GRID_X0 + c * PITCH
            parts.append(
                f'<text x="{x}" y="{MONTH_LABEL_Y}" font-size="9" '
                f'fill="{pal["label"]}" font-family="Helvetica, Arial, '
                f'sans-serif">{months[m - 1]}</text>')
    for c, week in enumerate(weeks):
        for r in range(ROWS):
            dstr, count, level = week[r]
            x = GRID_X0 + c * PITCH
            y = GRID_Y + r * PITCH
            color = pal["empty"] if level == 0 else pal["levels"][level - 1]
            title = ""
            if dstr:
                plural = "s" if count != 1 else ""
                title = f"<title>{dstr}: {count} contribution{plural}</title>"
            anim = ""
            if count > 0 and (c, r) in burns:
                anim = _burn_animate(color, pal["empty"], burns[(c, r)],
                                     duration)
            parts.append(
                f'<rect x="{x}" y="{y}" width="{CELL}" height="{CELL}" '
                f'rx="2" fill="{color}">{title}{anim}</rect>')
    parts.append(dragon_chain(pal, "follow", duration, total_len, path_d,
                              flip_values, flip_key_times,
                              base_begin=-duration / 2))
    parts.append(dragon_chain(pal, "lead", duration, total_len, path_d,
                              flip_values, flip_key_times))
    parts.append("</svg>")
    return "\n".join(parts)


# -------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--user", default="LlvallVeiz")
    ap.add_argument("--outdir", default="dist")
    args = ap.parse_args()

    weeks, source = fetch_calendar(args.user)
    total_contrib = sum(cell[1] for week in weeks for cell in week)
    print(f"[dragon] user={args.user} source={source} "
          f"contributions(last ~53 weeks)={total_contrib}")

    pts, pass_ranges = build_route()
    segs = catmull_segments(pts)
    samples, total_len = sample_route(segs)
    duration = min(max(total_len / SPEED, 12.0), 20.0)
    path_d = "M " + f"{segs[0][0][0]:.1f} {segs[0][0][1]:.1f} " + " ".join(
        f"C {s[1][0]:.1f} {s[1][1]:.1f}, {s[2][0]:.1f} {s[2][1]:.1f}, "
        f"{s[3][0]:.1f} {s[3][1]:.1f}" for s in segs)
    flip_values, flip_key_times = flip_timeline(segs, samples, total_len)
    burns = burn_times(segs, samples, pass_ranges, duration, total_len)
    print(f"[dragon] path={total_len:.0f}px duration={duration:.1f}s "
          f"burning cells={len(burns)}")

    os.makedirs(args.outdir, exist_ok=True)
    outputs = {
        "github-contribution-grid-dragon.svg": PALETTES["light"],
        "github-contribution-grid-dragon-dark.svg": PALETTES["dark"],
    }
    for name, pal in outputs.items():
        svg = build_svg(weeks, pal, duration, total_len, path_d,
                        flip_values, flip_key_times, burns)
        path = os.path.join(args.outdir, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(svg)
        print(f"[dragon] wrote {path} ({len(svg)} bytes)")


if __name__ == "__main__":
    main()
