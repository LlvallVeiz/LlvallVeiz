#!/usr/bin/env python3
"""Flying-dragon contribution graph generator.

Fetches a GitHub user's contribution calendar and emits an animated SVG in
which a stylised dragon flies a serpentine route across the contribution
grid, breathing fire; every cell it passes over "burns" (flashes ember
orange) and fades to empty, in sync with the flight. Pure SMIL animation,
so it works when embedded via <img> in a GitHub README.

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
HEAD = 29                     # snout offset from motion origin, px
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
        "body": "#1D2A32",
        "body_stroke": "none",
        "wing": "#20A145",
        "wing_far": "#177233",
        "belly": "rgba(255,255,255,0.20)",
        "horn": "#c9b458",
    },
    "dark": {
        "empty": "#161b22",
        "levels": ["#0e4429", "#006d32", "#26a641", "#39d353"],
        "label": "#7d8590",
        "body": "#1D2A32",
        "body_stroke": "#20A145",
        "wing": "#20A145",
        "wing_far": "#14532d",
        "belly": "rgba(255,255,255,0.14)",
        "horn": "#e8dcc8",
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
            steps = 6
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
def dragon_inner(pal):
    """Dragon artwork (flip-group content), facing +x around the origin.

    Side-profile silhouette: swept horns, scalloped flapping wing (plus a
    dimmer far wing), spade tail, dangling legs, flickering fire breath.
    Wing flap and fire flicker are SMIL and run continuously.
    """
    body = pal["body"]
    stroke_attrs = ""
    if pal["body_stroke"] != "none":
        stroke_attrs = (' stroke="' + pal["body_stroke"]
                        + '" stroke-width="0.9" stroke-opacity="0.6"')
    wing = pal["wing"]
    wing_far = pal["wing_far"]
    belly = pal["belly"]
    horn = pal["horn"]
    return f"""
      <!-- far wing (behind body) -->
      <g opacity="0.55">
        <path d="M -1 -2 C -4 -10, -9 -16, -15 -19 C -12 -13, -11 -10, -12 -7 C -9 -8, -7 -7, -6 -4 C -3 -5, -1 -4, -1 -2 Z" fill="{wing_far}"/>
        <animateTransform attributeName="transform" type="rotate" values="9 0 -3; -11 0 -3; 9 0 -3" dur="0.85s" repeatCount="indefinite"/>
      </g>
      <!-- tail spade -->
      <path d="M -25 1.5 L -33 -3 L -30 2 L -33 7.5 Z" fill="{body}"{stroke_attrs}/>
      <!-- body silhouette -->
      <path d="M -27 3 C -22 -1, -16 -3.2, -10 -4.2 C -4 -5.2, 3 -6.8, 9 -6.8 C 11.5 -6.8, 12.8 -6.1, 14.6 -6.5 C 17 -7.5, 18.2 -7.9, 19.6 -7.2 C 24 -6.3, 28.5 -5.2, 31.5 -3.2 C 32.1 -1.6, 30.5 -0.4, 28 0.2 C 25.5 0.9, 24.5 1.2, 23.5 2.3 C 21.5 3.6, 18.5 4.6, 14.5 5.1 C 6.5 6.0, -4 6.4, -12 5.2 C -18 4.4, -23 4, -27 3 Z" fill="{body}"{stroke_attrs}/>
      <!-- back spikes -->
      <path d="M -13 -4.4 L -10 -9.5 L -5.5 -5.2 Z M -1.5 -6.4 L 2.5 -11.5 L 6 -6.4 Z M 9 -6.9 L 13 -11.5 L 15.2 -6.6 Z" fill="{body}"/>
      <!-- horn -->
      <path d="M 17.5 -7 C 13.5 -11, 9.5 -13, 4.5 -14.2 C 9 -10.2, 12.5 -7.6, 14.2 -5.4 Z" fill="{horn}"/>
      <!-- legs -->
      <path d="M 8 5.2 L 13 5 L 12 11 L 9.3 11 Z M -13 5.4 L -8 5.2 L -9 11 L -11.6 11 Z" fill="{body}"/>
      <!-- belly sheen -->
      <path d="M -14 4.6 C -4 6.6, 8 6.1, 15 3.9 C 8 8.1, -6 8.3, -14 4.6 Z" fill="{belly}"/>
      <!-- near wing (flaps) -->
      <g>
        <path d="M 1 -3 C -2 -12, -8 -19, -16 -23 C -13.5 -16.5, -12.5 -13, -13.5 -9.5 C -10.5 -11, -8.5 -10, -7.5 -6.5 C -4.5 -8, -2 -6.5, 1 -3 Z" fill="{wing}"/>
        <path d="M 1 -3 C -2 -12, -8 -19, -16 -23" fill="none" stroke="{body}" stroke-width="1.6" stroke-linecap="round"/>
        <animateTransform attributeName="transform" type="rotate" values="-11 0 -4; 15 0 -4; -11 0 -4" dur="0.85s" repeatCount="indefinite"/>
      </g>
      <!-- eye -->
      <circle cx="22.3" cy="-3.6" r="1.7" fill="#fbbf24">
        <animate attributeName="opacity" values="1;0.45;1" dur="1.7s" repeatCount="indefinite"/>
      </circle>
      <!-- fire breath -->
      <g transform="translate(31,-1.9)">
        <animate attributeName="opacity" values="0.9;0.35;1;0.5;0.9" dur="0.45s" repeatCount="indefinite"/>
        <g>
          <animateTransform attributeName="transform" type="scale" values="1 1;0.8 0.85;1.08 1.05;0.88 0.92;1 1" dur="0.45s" repeatCount="indefinite"/>
          <path d="M 0 -1.2 C 6 -3.4, 12 -3.8, 18.5 -2.2 C 14.5 -0.8, 14.5 0.8, 18.5 2.2 C 12 3.8, 6 3.4, 0 1.2 Z" fill="url(#fireGrad)"/>
          <path d="M 0 -0.7 C 4 -1.8, 8 -2, 11.5 -1.1 C 8.8 0, 8.8 0, 11.5 1.1 C 8 2, 4 1.8, 0 0.7 Z" fill="#fff3b0" opacity="0.9"/>
          <circle cx="15" cy="-4.4" r="0.9" fill="{FLAME_EMBER}"/>
          <circle cx="20" cy="2.4" r="0.65" fill="{FLAME_EMBER}"/>
        </g>
      </g>"""


def dragon_group(pal, duration, path_d, flip_values, flip_key_times):
    kt = ";".join(f"{v:.4f}" for v in flip_key_times)
    vals = ";".join(flip_values)
    inner = dragon_inner(pal)
    return f"""
  <g transform="translate({PASS_XS},{row_cy(0):.0f})">
    <animateMotion dur="{duration:.2f}s" repeatCount="indefinite" path="{path_d}"/>
    <g>
      <animateTransform attributeName="transform" type="scale" calcMode="discrete" values="{vals}" keyTimes="{kt}" dur="{duration:.2f}s" repeatCount="indefinite"/>
{inner}
    </g>
  </g>"""


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


def build_svg(weeks, pal, duration, path_d, flip_values, flip_key_times,
              burns):
    parts = []
    parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {SVG_W} {SVG_H}" '
        f'width="{SVG_W}" height="{SVG_H}" role="img">')
    parts.append("<title>Contribution graph with a flying dragon</title>")
    parts.append("""<defs>
    <linearGradient id="fireGrad" x1="0" y1="0" x2="1" y2="0">
      <stop offset="0" stop-color="#fff6bf"/>
      <stop offset="0.35" stop-color="#fbbf24"/>
      <stop offset="0.7" stop-color="#f97316"/>
      <stop offset="1" stop-color="#f97316" stop-opacity="0"/>
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
    parts.append(dragon_group(pal, duration, path_d, flip_values,
                              flip_key_times))
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
        svg = build_svg(weeks, pal, duration, path_d, flip_values,
                        flip_key_times, burns)
        path = os.path.join(args.outdir, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(svg)
        print(f"[dragon] wrote {path} ({len(svg)} bytes)")


if __name__ == "__main__":
    main()
