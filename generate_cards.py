#!/usr/bin/env python3
"""Animated GitHub stat cards generator (self-hosted, stdlib only).

Fetches real data for a GitHub user and emits two animated SVG cards in
the GitHub dark theme (#0d1117) with tokyonight-ish accents and VOLTREAD
green (#20A145):

    card-overview-dark.svg    repos / followers / stars / contributions
                              (last year) + shine sweep + contribution
                              ring that sweeps to min(total/500, 1)
    card-languages-dark.svg   top 5 languages with bars that animate
                              from 0 to their share + gradient flow

Data sources:
  * GitHub REST API (api.github.com) -- user, repos, per-repo languages.
    Uses GITHUB_TOKEN from the environment when present, else anonymous.
  * github-contributions-api.jogruber.de -- contribution totals (no
    token needed).

Usage:
    python generate_cards.py --user LlvallVeiz --outdir dist-cards

Pure SMIL animation, so the cards animate when embedded via <img> in a
GitHub README. Standard library only.
"""

import argparse
import json
import os
import sys
import urllib.request
from collections import Counter

GREEN = "#20A145"
BG = "#0d1117"
BORDER = "#30363d"
TRACK = "#21262d"
TEXT = "#e6edf3"
MUTED = "#8b949e"
FAINT = "#7d8590"
ACCENTS = ["#20A145", "#7aa2f7", "#bb9af7"]
RING_GOAL = 500  # contributions/year that fills the ring completely

LANG_COLORS = {
    "Python": "#3572A5",
    "JavaScript": "#f1e05a",
    "TypeScript": "#3178c6",
    "HTML": "#e34c26",
    "CSS": "#663399",
    "Jupyter Notebook": "#DA5B0B",
    "C": "#555555",
    "C++": "#f34b7d",
    "C#": "#178600",
    "Java": "#b07219",
    "PHP": "#4F5D95",
    "Ruby": "#701516",
    "Go": "#00ADD8",
    "Rust": "#dea584",
    "Shell": "#89e051",
    "PowerShell": "#012456",
    "Vue": "#41b883",
    "Kotlin": "#A97BFF",
    "Swift": "#F05138",
    "Dart": "#00B4AB",
}
DEFAULT_LANG_COLOR = "#8b949e"

FONT = "Segoe UI, Helvetica, Arial, sans-serif"


# ---------------------------------------------------------------- fetching
def _http_json(url, token=None):
    req = urllib.request.Request(url)
    req.add_header("User-Agent", "profile-stat-cards/1.0")
    req.add_header("Accept", "application/vnd.github+json")
    if token:
        req.add_header("Authorization", "bearer " + token)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_all(user, token):
    """Return (profile dict, stats dict, languages list, lang_mode)."""
    profile = _http_json(f"https://api.github.com/users/{user}", token)
    repos = []
    page = 1
    while True:
        batch = _http_json(
            f"https://api.github.com/users/{user}/repos"
            f"?per_page=100&page={page}&sort=updated", token)
        repos.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    own = [r for r in repos if not r.get("fork")]
    stars = sum(int(r.get("stargazers_count", 0)) for r in own)

    # Languages by bytes via each repo's languages endpoint; fall back to
    # counting the primary `language` field if that yields nothing.
    byte_totals = Counter()
    for r in own:
        try:
            langs = _http_json(r["languages_url"], token)
        except Exception:
            langs = {}
        for name, size in (langs or {}).items():
            byte_totals[name] += int(size)
    if byte_totals:
        languages = byte_totals.most_common(5)
        lang_mode = "bytes"
        lang_total = sum(byte_totals.values())
    else:
        counts = Counter(r["language"] for r in own if r.get("language"))
        languages = counts.most_common(5)
        lang_mode = "repos"
        lang_total = sum(counts.values())

    contributions = 0
    try:
        data = _http_json(
            "https://github-contributions-api.jogruber.de/v4/"
            + user + "?y=last")
        contributions = sum(int(c.get("count", 0))
                            for c in data.get("contributions", []))
    except Exception as exc:
        print(f"[cards] contributions fetch failed: {exc}",
              file=sys.stderr)

    stats = {
        "repos": int(profile.get("public_repos", len(own))),
        "followers": int(profile.get("followers", 0)),
        "stars": stars,
        "contributions": contributions,
    }
    return profile, stats, languages, lang_total, lang_mode


# ------------------------------------------------------------------ helpers
def _lighten(hex_color, frac=0.35):
    """Mix a #rrggbb colour towards white by `frac`."""
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    r = round(r + (255 - r) * frac)
    g = round(g + (255 - g) * frac)
    b = round(b + (255 - b) * frac)
    return f"#{r:02x}{g:02x}{b:02x}"


def _esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


# -------------------------------------------------------------------- cards
def overview_card(user, stats):
    total = stats["contributions"]
    frac = min(total / RING_GOAL, 1.0)
    circ = 2 * 3.14159265 * 36
    offset = circ * (1 - frac)
    rows = []
    labels = [("Public Repos", stats["repos"]),
              ("Followers", stats["followers"]),
              ("Total Stars", stats["stars"])]
    for i, (label, value) in enumerate(labels):
        ybase = 78 + i * 40
        rows.append(
            f'<circle cx="176" cy="{ybase - 4}" r="3.5" '
            f'fill="{ACCENTS[i]}"/>'
            f'<text x="188" y="{ybase}" font-size="10" fill="{MUTED}">'
            f'{label}</text>'
            f'<text x="188" y="{ybase + 21}" font-size="18" '
            f'font-weight="bold" fill="{TEXT}">{value}</text>')
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 495 195" width="495" height="195" role="img">
  <title>GitHub overview for {_esc(user)}</title>
  <defs>
    <linearGradient id="ovAccent" x1="0" y1="0" x2="1" y2="0">
      <stop offset="0" stop-color="{GREEN}"/>
      <stop offset="1" stop-color="#7aa2f7"/>
    </linearGradient>
    <linearGradient id="ovShine" x1="0" y1="0" x2="1" y2="0">
      <stop offset="0" stop-color="#ffffff" stop-opacity="0"/>
      <stop offset="0.5" stop-color="#ffffff" stop-opacity="0.09"/>
      <stop offset="1" stop-color="#ffffff" stop-opacity="0"/>
    </linearGradient>
    <clipPath id="ovClip"><rect width="495" height="195" rx="10"/></clipPath>
  </defs>
  <rect width="495" height="195" rx="10" fill="{BG}" stroke="{BORDER}"/>
  <g clip-path="url(#ovClip)">
    <rect width="495" height="3" fill="url(#ovAccent)"/>
    <g transform="rotate(18 250 100)">
      <rect x="-260" y="-60" width="150" height="320" fill="url(#ovShine)">
        <animateTransform attributeName="transform" type="translate" values="0 0; 980 0; 980 0" keyTimes="0; 0.3; 1" dur="6.5s" repeatCount="indefinite"/>
      </rect>
    </g>
  </g>
  <text x="24" y="36" font-family="{FONT}" font-size="15" font-weight="bold" fill="{TEXT}">GitHub Overview</text>
  <text x="24" y="54" font-family="{FONT}" font-size="10.5" fill="{FAINT}">@{_esc(user)} &#183; live data</text>
  <circle cx="88" cy="124" r="36" fill="none" stroke="{TRACK}" stroke-width="9"/>
  <circle cx="88" cy="124" r="36" fill="none" stroke="{GREEN}" stroke-width="9" stroke-linecap="round" stroke-dasharray="{circ:.2f}" stroke-dashoffset="{circ:.2f}" transform="rotate(-90 88 124)">
    <animate attributeName="stroke-dashoffset" values="{circ:.2f}; {offset:.2f}" dur="1.6s" begin="0.2s" fill="freeze"/>
  </circle>
  <text x="88" y="122" font-family="{FONT}" font-size="19" font-weight="bold" fill="{TEXT}" text-anchor="middle">{total}</text>
  <text x="88" y="138" font-family="{FONT}" font-size="8.5" fill="{FAINT}" text-anchor="middle">contributions</text>
  <text x="88" y="180" font-family="{FONT}" font-size="8" fill="{FAINT}" text-anchor="middle">last year &#183; ring goal: {RING_GOAL}</text>
  <g font-family="{FONT}">
    {''.join(rows)}
  </g>
</svg>"""


def languages_card(user, languages, lang_total, lang_mode):
    parts = [f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 495 195" width="495" height="195" role="img">
  <title>Top languages for {_esc(user)}</title>"""]
    grads = []
    for i, (name, _size) in enumerate(languages):
        color = LANG_COLORS.get(name, DEFAULT_LANG_COLOR)
        grads.append(
            f'<linearGradient id="lgGrad{i}" x1="0" y1="0" x2="1" y2="0">'
            f'<stop offset="0" stop-color="{color}"/>'
            f'<stop offset="1" stop-color="{_lighten(color)}"/>'
            f'<animate attributeName="x1" values="0; 0.45; 0" '
            f'dur="7s" begin="{i * 0.6:.1f}s" repeatCount="indefinite"/>'
            f'<animate attributeName="x2" values="1; 1.45; 1" '
            f'dur="7s" begin="{i * 0.6:.1f}s" repeatCount="indefinite"/>'
            f'</linearGradient>')
    parts.append("  <defs>" + "".join(grads) + "</defs>")
    parts.append(
        f'  <rect width="495" height="195" rx="10" fill="{BG}" '
        f'stroke="{BORDER}"/>')
    parts.append(
        f'  <text x="24" y="34" font-family="{FONT}" font-size="15" '
        f'font-weight="bold" fill="{TEXT}">Top Languages</text>')
    basis = ("share of code bytes" if lang_mode == "bytes"
             else "share of repositories")
    parts.append(
        f'  <text x="24" y="50" font-family="{FONT}" font-size="10" '
        f'fill="{FAINT}">@{_esc(user)} &#183; {basis}</text>')
    if not languages:
        parts.append(
            f'  <text x="247" y="118" font-family="{FONT}" font-size="12" '
            f'fill="{MUTED}" text-anchor="middle">'
            f'No language data yet</text>')
    bar_x, bar_w = 150, 258
    for i, (name, size) in enumerate(languages):
        share = (size / lang_total) if lang_total else 0.0
        y = 72 + i * 23.5
        w = share * bar_w
        parts.append(
            f'  <text x="24" y="{y + 8:.1f}" font-family="{FONT}" '
            f'font-size="11.5" fill="{TEXT}">{_esc(name)}</text>'
            f'  <rect x="{bar_x}" y="{y:.1f}" width="{bar_w}" height="9" '
            f'rx="4.5" fill="{TRACK}"/>'
            f'  <rect x="{bar_x}" y="{y:.1f}" width="0" height="9" '
            f'rx="4.5" fill="url(#lgGrad{i})">'
            f'<animate attributeName="width" from="0" to="{w:.1f}" '
            f'dur="1.1s" begin="{0.15 * i:.2f}s" fill="freeze"/></rect>'
            f'  <text x="420" y="{y + 8:.1f}" font-family="{FONT}" '
            f'font-size="11" fill="{MUTED}">{share * 100:.1f}%</text>')
    parts.append("</svg>")
    return "\n".join(parts)


# --------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--user", default="LlvallVeiz")
    ap.add_argument("--outdir", default="dist-cards")
    args = ap.parse_args()

    token = os.environ.get("GITHUB_TOKEN", "").strip() or None
    _profile, stats, languages, lang_total, lang_mode = fetch_all(
        args.user, token)
    print(f"[cards] user={args.user} stats={stats} "
          f"languages={languages} mode={lang_mode}")

    os.makedirs(args.outdir, exist_ok=True)
    outputs = {
        "card-overview-dark.svg": overview_card(args.user, stats),
        "card-languages-dark.svg": languages_card(
            args.user, languages, lang_total, lang_mode),
    }
    for name, svg in outputs.items():
        path = os.path.join(args.outdir, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(svg)
        print(f"[cards] wrote {path} ({len(svg)} bytes)")


if __name__ == "__main__":
    main()
