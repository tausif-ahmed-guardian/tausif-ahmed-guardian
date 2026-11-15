#!/usr/bin/env python3
"""Build Tausif's GitHub profile README and its animated, themed SVG panels. Standard library only.

    python3 tools/build_profile.py [--offline]

Inputs (nothing about the person is typed into this file):
  profile.json            biography, links, focus areas (sourced from his own README)
  GitHub search (via gh)  every public issue/PR he opened in other people's repositories
Outputs:
  assets/{light,dark}/*.svg   desktop (840 px) and mobile (-m, 400 px) panels
  README.md                   the profile, using <picture> for theme and width
  preview-{light,dark,dark-m}.html   local previews of exactly what README.md shows

Only items GitHub already shows publicly are listed; closed-unmerged ones are dropped. "Merged"
comes only from GitHub's own merged flag. Runs on his Mac and in .github/workflows/profile.yml.

Motion: CSS keyframes inside each SVG (GitHub serves SVG through <img>, where CSS and SMIL
animation run but script does not). Every animated element's un-animated base state is its
final, fully visible state, so prefers-reduced-motion (animation:none) still shows everything.
"""
import argparse
import csv
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys
from xml.sax.saxutils import escape

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
GH = os.path.expanduser("~/.local/bin/gh") if os.path.exists(os.path.expanduser("~/.local/bin/gh")) else "gh"
W_D, W_M = 840, 400
SANS = "-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif"
MONO = "ui-monospace,SFMono-Regular,Menlo,Consolas,monospace"

THEMES = {
    "dark": {"bg": "#0d1117", "panel": "#11161d", "panel2": "#161b22", "line": "#262d38", "ink": "#e6edf3",
             "sub": "#9aa4b1", "mute": "#6e7681", "acc": "#a99cff", "acc2": "#6d5cf0", "ok": "#3fb950",
             "on_acc": "#0d1117", "glow": "#6d5cf0"},
    "light": {"bg": "#ffffff", "panel": "#f8f9fb", "panel2": "#f1f3f6", "line": "#d8dee4", "ink": "#1f2328",
              "sub": "#57606a", "mute": "#8c959f", "acc": "#6d5cf0", "acc2": "#5443d6", "ok": "#1a7f37",
              "on_acc": "#ffffff", "glow": "#b9b0ff"},
}
PUBLIC = ("PROPOSED", "OPEN", "MERGED", "ACCEPTED", "ISSUE")
STATUS_LABEL = {"merged": "MERGED", "accepted": "ACCEPTED", "open": "OPEN PR", "proposed": "PROPOSED"}
MERGED_SET = ("merged", "accepted")


# ---------- text helpers ----------
def tw(s, size, mono=False, bold=False):
    """Approximate rendered width of s in px (no font metrics available offline)."""
    if mono:
        k = 0.61
    elif bold:
        k = 0.585
    else:
        k = 0.545
    return len(s) * size * k


def wrap(s, maxw, size, mono=False, bold=False):
    lines, cur = [], ""
    for word in s.split():
        t = (cur + " " + word).strip()
        if tw(t, size, mono, bold) <= maxw or not cur:
            cur = t
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def x(s):
    return escape(str(s), {'"': "&quot;"})


def d(sec):
    """animation-delay attribute fragment."""
    return ' style="animation-delay:%.2fs"' % sec


def text(px, py, s, size, fill, mono=False, weight=400, anchor="start", cls="", extra=""):
    fam = MONO if mono else SANS
    c = ' class="%s"' % cls if cls else ""
    return ('<text x="%.1f" y="%.1f" font-family="%s" font-size="%s" font-weight="%d" fill="%s" '
            'text-anchor="%s"%s%s>%s</text>' % (px, py, fam, size, weight, fill, anchor, c, extra, x(s)))


def lines_el(px, py, lines, size, fill, lh, mono=False, weight=400, cls="", delay=0.0, step=0.06):
    out = []
    for i, ln in enumerate(lines):
        out.append(text(px, py + i * lh, ln, size, fill, mono, weight, cls=cls,
                        extra=d(delay + i * step) if cls else ""))
    return "".join(out)


STYLE = ("<style>"
         "@keyframes fi{from{opacity:0;transform:translateY(8px)}}"
         "@keyframes fo{from{opacity:0}}"
         "@keyframes dr{from{stroke-dashoffset:1}}"
         "@keyframes gx{from{transform:scaleX(.001)}}"
         "@keyframes gy{from{transform:scaleY(.001)}}"
         "@keyframes pp{from{opacity:0;transform:scale(.3)}}"
         "@keyframes pl{50%{opacity:.3}}"
         "@keyframes ring{0%{opacity:.75;transform:scale(1)}70%,100%{opacity:0;transform:scale(2.8)}}"
         "@keyframes bl{50%{opacity:0}}"
         "@keyframes tr{to{stroke-dashoffset:-1}}"
         "@keyframes sl{to{transform:translate(-70px,26px)}}"
         "@keyframes wv{0%,100%{transform:scaleY(.3)}50%{transform:scaleY(1)}}"
         "@keyframes spin{to{transform:rotate(360deg)}}"
         ".fi{animation:fi .8s cubic-bezier(.2,.7,.2,1) both}"
         ".fo{animation:fo .9s ease both}"
         ".dr{stroke-dasharray:1;animation:dr 1.6s cubic-bezier(.6,0,.2,1) both}"
         ".gx{transform-box:fill-box;transform-origin:left center;animation:gx 1.2s cubic-bezier(.2,.7,.2,1) both}"
         ".gy{transform-box:fill-box;transform-origin:center bottom;animation:gy 1s cubic-bezier(.2,.7,.2,1) both}"
         ".pp{transform-box:fill-box;transform-origin:center;animation:pp .45s cubic-bezier(.3,1.5,.5,1) both}"
         ".pl{animation:pl 2.6s ease-in-out infinite}"
         ".ring{transform-box:fill-box;transform-origin:center;animation:ring 3.2s ease-out infinite}"
         ".bl{animation:bl 1.05s steps(1) infinite}"
         ".tr{stroke-dasharray:.12 .88;animation:tr 7s linear infinite}"
         ".sl{animation:sl 9s ease-in-out infinite alternate}"
         ".wv{transform-box:fill-box;transform-origin:center bottom;animation:wv 1.8s ease-in-out infinite}"
         "@media (prefers-reduced-motion:reduce){*{animation:none!important}}"
         "</style>")


def svg(w, h, body, label):
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d" role="img" '
            'aria-label="%s"><title>%s</title>%s%s</svg>\n' % (w, h, w, h, x(label), x(label), STYLE, body))


def card(w, h, th, r=12, fill=None, stroke=None, xo=0.5, yo=0.5, dash=False):
    return '<rect x="%s" y="%s" width="%s" height="%s" rx="%d" fill="%s" stroke="%s"%s/>' % (
        xo, yo, w - 2 * xo, h - 2 * yo, r, fill or th["panel"], stroke or th["line"],
        ' stroke-dasharray="4 4"' if dash else "")


def tracer(w, h, th, r=12, delay=0.0, dur=7):
    """A short accent segment that travels around a card's border (pathLength-normalised)."""
    return ('<rect x="0.5" y="0.5" width="%s" height="%s" rx="%d" fill="none" stroke="%s" stroke-width="1.6" '
            'pathLength="1" class="tr" style="animation-duration:%ds;animation-delay:%.2fs" stroke-opacity=".9"/>'
            % (w - 1, h - 1, r, th["acc"], dur, -delay))


ICONS = {
    "in": '<rect x="1" y="1" width="14" height="14" rx="3"/><path d="M4.5 7v4.5M4.5 4.6v.1M7.5 11.5V7m0 1.8c0-1 .8-1.8 1.8-1.8s1.7.8 1.7 1.8v2.7"/>',
    "globe": '<circle cx="8" cy="8" r="6.5"/><path d="M1.5 8h13M8 1.5c1.8 2 2.7 4.2 2.7 6.5S9.8 12.5 8 14.5M8 1.5C6.2 3.5 5.3 5.7 5.3 8s.9 4.5 2.7 6.5"/>',
    "shield": '<path d="M8 1.2 2.5 3.4v4.2c0 3.3 2.4 6 5.5 7.2 3.1-1.2 5.5-3.9 5.5-7.2V3.4z"/><path d="m5.6 8 1.7 1.7 3.2-3.3"/>',
    "ledger": '<rect x="2" y="1.5" width="12" height="13" rx="2"/><path d="M5 5h6M5 8h6M5 11h3.5"/>',
    "lattice": '<path d="M3 3l10 10M13 3 3 13"/>',
    "loop": '<path d="M13.5 6A6 6 0 1 0 14 9.5"/><path d="M14 2.5V6h-3.5"/>',
    "code": '<path d="m5.5 4.5-4 3.5 4 3.5M10.5 4.5l4 3.5-4 3.5"/>',
}


def icon(kind, cx, cy, th, s=1.0, draw=False, delay=0.0):
    a = th["acc"]
    body = ICONS.get(kind, '<circle cx="8" cy="8" r="6"/>')
    if draw:  # stroke-draw each shape in: normalise its length to 1
        body = re.sub(r"<(path|rect|circle)\b", r'<\1 pathLength="1" class="dr"%s' % d(delay), body)
    if kind == "lattice":
        body += ''.join('<circle cx="%d" cy="%d" r="1.3" fill="%s" class="pp"%s/>' % (
            3 + 5 * i, 3 + 5 * j, a, d(delay + 0.3 + 0.05 * (i + j))) for i in range(3) for j in range(3))
    return ('<g transform="translate(%.1f %.1f) scale(%.2f)" fill="none" stroke="%s" stroke-width="1.6" '
            'stroke-linecap="round" stroke-linejoin="round">%s</g>' % (cx - 8 * s, cy - 8 * s, s, a, body))


# ---------- data ----------
def load_profile():
    prof = json.load(open(os.path.join(ROOT, "profile.json"), encoding="utf-8"))
    if prof.get("photo"):
        import base64
        raw = open(os.path.join(ROOT, prof["photo"]), "rb").read()
        if raw[:3] != b"\xff\xd8\xff":
            raise SystemExit("photo is not a JPEG: %s" % prof["photo"])
        prof["_photo_b64"] = base64.b64encode(raw).decode("ascii")
    return prof


URL_RE = re.compile(r"https://github\.com/[\w.-]+/[\w.-]+/(?:issues|pull)/\d+")


def parse_ref(url):
    m = re.match(r"https://github\.com/([^/]+/[^/]+)/(issues|pull)/(\d+)", url)
    return (m.group(1), m.group(2), int(m.group(3))) if m else (None, None, None)


def gh_json(path, offline):
    if offline:
        return None
    try:
        r = subprocess.run([GH, "api", path], capture_output=True, text=True, timeout=30)
        return json.loads(r.stdout) if r.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None


def upstream_items(prof, offline):
    """Every public issue and PR he opened in someone else's repository, read from GitHub search.

    GitHub is the single source, so this runs identically on his Mac and in the profile repo's
    scheduled workflow. Closed-unmerged items are not shown. "Merged" is GitHub's own merged flag.
    """
    if offline:
        raise SystemExit("--offline is no longer supported: the item list comes from GitHub search")
    items, seen, page, total = [], set(), 1, 0
    q = "author:%s -user:%s is:public" % (prof["login"], prof["login"])
    while True:
        j = gh_json("search/issues?q=%s&per_page=100&page=%d" % (q.replace(" ", "+").replace(":", "%3A"), page), False)
        if j is None:
            raise SystemExit("GitHub search failed: refusing to publish an empty upstream section")
        batch = j.get("items", [])
        total = j.get("total_count", 0)
        for it in batch:
            repo, kind, num = parse_ref(it["html_url"])
            if not repo or it["html_url"] in seen:
                continue
            seen.add(it["html_url"])
            pr = it.get("pull_request") or {}
            if pr.get("merged_at"):
                status = "merged"
            elif it["state"] == "closed":
                continue  # closed without merge: not shown
            else:
                status = "open" if kind == "pull" else "proposed"
            items.append({"repo": repo, "name": repo.split("/")[1], "kind": kind, "num": num,
                          "url": it["html_url"], "title": it["title"], "date": it["created_at"][:10],
                          "status": status, "area": prof["areas"].get(repo, "")})
        if len(batch) < 100 or page >= 10:
            break
        page += 1
    items.sort(key=lambda it: it["date"], reverse=True)
    return items, total, total - len(items), len(items)


# ---------- panels ----------
def typewriter(words, px, py, size, th, slot=4.0):
    """Type each word in, hold, erase, next. Base state (no animation): word 0 fully shown."""
    n = len(words)
    total = n * slot
    css, out = [], []
    for i, wd in enumerate(words):
        ww = tw(wd, size, bold=True) + 4
        typed = min(1.4, 0.07 * len(wd))
        a, b, c = typed / total * 100, (slot - 0.8) / total * 100, (slot - 0.3) / total * 100
        css.append("@keyframes ty%d{0%%{transform:scaleX(0);animation-timing-function:steps(%d,end)}"
                   "%.2f%%{transform:scaleX(1);animation-timing-function:linear}"
                   "%.2f%%{transform:scaleX(1);animation-timing-function:steps(%d,end)}"
                   "%.2f%%,100%%{transform:scaleX(0)}}" % (i, len(wd), a, b, len(wd), c))
        css.append("@keyframes tc%d{0%%{transform:translateX(0);animation-timing-function:steps(%d,end)}"
                   "%.2f%%{transform:translateX(%.1fpx);animation-timing-function:linear}"
                   "%.2f%%{transform:translateX(%.1fpx);animation-timing-function:steps(%d,end)}"
                   "%.2f%%,100%%{transform:translateX(0)}}" % (i, len(wd), a, ww, b, ww, len(wd), c))
        css.append("@keyframes vi%d{0%%,%.2f%%{opacity:1}%.2f%%,100%%{opacity:0}}" % (i, c + 2, c + 2.2))
        delay = i * slot - total if i else 0  # negative delays: every word runs from t=0, in its own slot
        anim = "animation:%%s %.1fs infinite;animation-delay:%.2fs" % (total, delay)
        out.append('<clipPath id="cp%d"><rect x="%.1f" y="%.1f" width="%.1f" height="%d" '
                   'style="transform-box:fill-box;transform-origin:left center;%s"/></clipPath>' % (
                       i, px - 2, py - size, ww + 2, size + 10, anim % ("ty%d" % i)))
        out.append('<g style="%s%s">' % (anim % ("vi%d" % i), "" if i == 0 else ";opacity:0"))
        out.append('<g clip-path="url(#cp%d)">%s</g>' % (i, text(px, py, wd, size, th["acc"], weight=700)))
        out.append('<g style="%s"><rect x="%.1f" y="%.1f" width="2.5" height="%d" fill="%s" class="bl"/></g>' % (
            anim % ("tc%d" % i), px + (ww if i == 0 else 0) - (ww if i == 0 else 0), py - size + 4, size + 2, th["acc"]))
        out.append("</g>")
    return "<style>%s</style>" % "".join(css) + "".join(out)


def portrait(prof, th, cx, cy, r):
    """Circular photo, embedded as a data URI (SVG served through <img> cannot fetch external images)."""
    return ('<defs><clipPath id="ph"><circle cx="%d" cy="%d" r="%d"/></clipPath></defs>'
            '<circle cx="%d" cy="%d" r="%d" fill="none" stroke="%s" class="ring" style="animation-duration:4s"/>'
            '<g class="pp"%s><circle cx="%d" cy="%d" r="%d" fill="%s"/>'
            '<image href="data:image/jpeg;base64,%s" x="%d" y="%d" width="%d" height="%d" clip-path="url(#ph)" '
            'preserveAspectRatio="xMidYMid slice"/>'
            '<circle cx="%d" cy="%d" r="%d" fill="none" stroke="%s" stroke-width="2"/></g>'
            '<circle cx="%d" cy="%d" r="%d" fill="none" stroke="%s" stroke-width="1.5" stroke-dasharray="3 7" '
            'style="transform-box:fill-box;transform-origin:center;animation:spin 24s linear infinite"/>'
            '<circle cx="%d" cy="%d" r="%d" fill="none" stroke="%s" stroke-width="2" pathLength="1" '
            'stroke-dasharray=".18 .82" class="tr" style="animation-duration:5s"/>'
            % (cx, cy, r, cx, cy, r, th["acc"], d(0.4), cx, cy, r + 4, th["panel"], prof["_photo_b64"],
               cx - r, cy - r, 2 * r, 2 * r, cx, cy, r, th["acc"], cx, cy, r + 9, th["acc2"],
               cx, cy, r + 9, th["acc"]))


def radar(th, w, h, mob):
    """Hero motion, all looping: a drifting dot field, a full-height scanline, and a radar with a rotating
    sweep, blips that light as the beam passes, orbiting nodes and a pulsing shield. CSS + SMIL only, so it
    runs inside GitHub's <img>; the base (un-animated) state is a complete, readable picture."""
    cx, cy, R = (w - 64, 74, 46) if mob else (680, 150, 118)
    period = 4.0
    o = ['<defs>'
         '<pattern id="dots" width="22" height="22" patternUnits="userSpaceOnUse"><circle cx="2" cy="2" r="1" fill="%s"/>'
         '<animateTransform attributeName="patternTransform" type="translate" from="0 0" to="22 22" dur="6s" repeatCount="indefinite"/></pattern>'
         '<radialGradient id="dfade" cx=".72" cy=".5" r=".75"><stop offset="0" stop-color="#fff" stop-opacity=".9"/><stop offset="1" stop-color="#fff" stop-opacity="0"/></radialGradient>'
         '<mask id="dmask"><rect width="%d" height="%d" fill="url(#dfade)"/></mask>'
         '<linearGradient id="scan" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="%s" stop-opacity="0"/>'
         '<stop offset=".85" stop-color="%s" stop-opacity=".22"/><stop offset="1" stop-color="%s" stop-opacity=".55"/></linearGradient>'
         '<linearGradient id="beam" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="%s" stop-opacity="0"/>'
         '<stop offset="1" stop-color="%s" stop-opacity=".55"/></linearGradient>'
         '</defs>' % (th["mute"], w, h, th["acc"], th["acc"], th["acc"], th["acc"], th["acc"])]
    o.append('<g clip-path="url(#hc)"><rect width="%d" height="%d" fill="url(#dots)" mask="url(#dmask)" opacity=".55"/>' % (w, h))
    o.append('<rect x="0" y="-70" width="%d" height="70" fill="url(#scan)" opacity=".7">'
             '<animateTransform attributeName="transform" type="translate" values="0 0;0 %d" dur="5.5s" repeatCount="indefinite"/></rect>'
             % (w, h + 70))
    # radar rings + crosshair
    for k, rr in enumerate((R, R * 2 / 3, R / 3)):
        o.append('<circle cx="%d" cy="%d" r="%.1f" fill="none" stroke="%s" stroke-opacity="%.2f"/>' % (cx, cy, rr, th["acc"], .45 - k * .1))
    o.append('<path d="M%d %dh%d M%d %dv%d" stroke="%s" stroke-opacity=".25"/>' % (cx - R, cy, 2 * R, cx, cy - R, 2 * R, th["acc"]))
    # expanding ping
    o.append('<circle cx="%d" cy="%d" r="%.1f" fill="none" stroke="%s" stroke-width="1.5" class="ring" style="animation-duration:%.1fs"/>'
             % (cx, cy, R / 3, th["acc"], period))
    # rotating sweep wedge (40 degrees) with its leading edge
    import math
    a0 = math.radians(-40)
    o.append('<g><path d="M%d %d L%.1f %.1f A%d %d 0 0 1 %d %d Z" fill="url(#beam)"/>'
             '<path d="M%d %dH%d" stroke="%s" stroke-width="1.6"/>'
             '<animateTransform attributeName="transform" type="rotate" from="0 %d %d" to="360 %d %d" dur="%.1fs" repeatCount="indefinite"/></g>'
             % (cx, cy, cx + R * math.cos(a0), cy + R * math.sin(a0), R, R, cx + R, cy, cx, cy, cx + R, th["acc"], cx, cy, cx, cy, period))
    # blips: lit when the beam passes their angle
    for ang, dist in ((25, .8), (110, .55), (200, .9), (290, .4), (330, .7)):
        bx, by = cx + R * dist * math.cos(math.radians(ang)), cy + R * dist * math.sin(math.radians(ang))
        o.append('<circle cx="%.1f" cy="%.1f" r="3.2" fill="%s"><animate attributeName="opacity" values="1;.12;.12" keyTimes="0;.6;1" '
                 'dur="%.1fs" begin="%.2fs" repeatCount="indefinite"/></circle>' % (bx, by, th["acc"], period, ang / 360.0 * period))
    # orbiting nodes on an outer path
    orb = R + (10 if mob else 22)
    o.append('<circle cx="%d" cy="%d" r="%d" fill="none" stroke="%s" stroke-opacity=".18" stroke-dasharray="2 5"/>' % (cx, cy, orb, th["acc"]))
    o.append('<g>%s<animateTransform attributeName="transform" type="rotate" from="0 %d %d" to="-360 %d %d" dur="16s" repeatCount="indefinite"/></g>' % (
        "".join('<circle cx="%.1f" cy="%.1f" r="%s" fill="%s"/>' % (cx + orb * math.cos(math.radians(t)), cy + orb * math.sin(math.radians(t)),
                                                                   2.6 if i else 3.6, th["acc"]) for i, t in enumerate((0, 120, 240))), cx, cy, cx, cy))
    # centre shield
    s = 1.6 if not mob else 1.0
    o.append('<g transform="translate(%.1f %.1f) scale(%.1f)"><circle cx="8" cy="8" r="11" fill="%s" stroke="%s" stroke-opacity=".6">'
             '<animate attributeName="r" values="11;13;11" dur="2.4s" repeatCount="indefinite"/></circle>'
             '<g fill="none" stroke="%s" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round">%s</g></g>'
             % (cx - 8 * s, cy - 8 * s, s, th["panel2"], th["acc"], th["acc"], ICONS["shield"]))
    o.append('</g>')
    return "".join(o)


def hero(prof, th, w):
    mob = w == W_M
    h = 330 if mob else 300
    pad = 24 if mob else 36
    b = ['<defs><radialGradient id="g" cx=".5" cy=".5" r=".5"><stop offset="0" stop-color="%s" stop-opacity=".55"/>'
         '<stop offset="1" stop-color="%s" stop-opacity="0"/></radialGradient>'
         '<clipPath id="hc"><rect x="1" y="1" width="%d" height="%d" rx="14"/></clipPath></defs>' % (
             th["glow"], th["glow"], w - 2, h - 2), card(w, h, th, 14), tracer(w, h, th, 14, dur=9)]
    b.append('<g clip-path="url(#hc)"><circle cx="%d" cy="0" r="%d" fill="url(#g)" class="sl"/>'
             '<circle cx="%d" cy="%d" r="%d" fill="url(#g)" opacity=".35" class="sl" style="animation-duration:13s"/></g>'
             % (w - 60, 300 if not mob else 220, 80, h, 160))
    b.append(radar(th, w, h, mob))
    if prof.get("_photo_b64"):
        b.append(portrait(prof, th, *((w - 58, 64, 36) if mob else (690, 136, 76))))
    y = pad + 18
    b.append(text(pad, y, "~/" + prof["login"], 12, th["acc"], mono=True, cls="fi"))
    b.append('<rect x="%.1f" y="%d" width="7" height="13" fill="%s" class="bl"/>' % (
        pad + tw("~/" + prof["login"], 12, mono=True) + 4, y - 11, th["acc"]))
    y += 50 if not mob else 44
    b.append('<defs><linearGradient id="shine" gradientUnits="userSpaceOnUse" x1="-260" y1="0" x2="0" y2="0">'
             '<stop offset="0" stop-color="%s"/><stop offset=".5" stop-color="%s"/><stop offset="1" stop-color="%s"/>'
             '<animate attributeName="x1" values="-260;%d;%d" keyTimes="0;.55;1" dur="7s" repeatCount="indefinite"/>'
             '<animate attributeName="x2" values="0;%d;%d" keyTimes="0;.55;1" dur="7s" repeatCount="indefinite"/>'
             '</linearGradient></defs>' % (th["ink"], th["acc"], th["ink"], w, w, w + 260, w + 260))
    b.append(text(pad, y, prof["name"], 34 if mob else 44, "url(#shine)", weight=800, cls="fi", extra=d(0.12)))
    y += 28
    b.append(text(pad, y, prof["title"], 15, th["sub"], weight=600, cls="fi", extra=d(0.24)))
    y += 44
    big = 22 if mob else 26
    verb = prof["verb"]
    b.append(text(pad, y, verb, big, th["ink"], weight=700, cls="fi", extra=d(0.36)))
    tx = pad if mob else pad + tw(verb, big, bold=True) + 10
    b.append(typewriter(prof["rotating"], tx, y + (30 if mob else 0), big, th))
    y += 74 if mob else 46
    ql = wrap(prof["question"], w - 2 * pad - (0 if mob else 280), 14)
    b.append('<rect x="%d" y="%d" width="3" height="%d" rx="1.5" fill="%s" class="gy"%s/>' % (
        pad, y - 14, 21 * len(ql), th["acc"], d(0.6)))
    b.append(lines_el(pad + 14, y, ql, 14, th["sub"], 21, cls="fi", delay=0.7))
    label = "%s. %s. %s %s" % (prof["name"], prof["title"], prof["tagline"], prof["question"])
    return svg(w, h, "".join(b), label)


def pill(link, th):
    lw = tw(link["label"], 13, bold=True)
    w, h = int(lw + 52), 38
    b = [card(w, h, th, 19, fill=th["panel2"]), tracer(w, h, th, 19, delay=len(link["label"]) % 5, dur=9),
         icon(link["icon"], 22, 19, th, 0.95, draw=True, delay=0.2),
         text(38, 24, link["label"], 13, th["ink"], weight=600, cls="fi", extra=d(0.1))]
    return svg(w, h, "".join(b), link["label"])


def ticker(items, th, w):
    mob = w == W_M
    h = 40
    b = [card(w, h, th, 10, fill=th["panel2"]),
         '<circle cx="18" cy="20" r="4" fill="none" stroke="%s" class="ring"/>' % th["acc"],
         '<circle cx="18" cy="20" r="4" fill="%s"/>' % th["acc"],
         text(30, 24, "LATEST", 11, th["acc"], mono=True, weight=700)]
    parts = ["%s %s #%d" % (STATUS_LABEL[i["status"]].lower(), i["repo"], i["num"]) for i in items[: (1 if mob else 2)]]
    if not parts:
        parts = ["first contributions in progress"]
    px = 90
    for k, p in enumerate(parts):
        if k:
            b.append(text(px, 24, "/", 12, th["mute"], mono=True, cls="fo", extra=d(0.3 + k * 0.5)))
            px += tw("/  ", 12, mono=True) + 6
        b.append(text(px, 24, p, 12, th["sub"], mono=True, cls="fi", extra=d(0.2 + k * 0.5)))
        px += tw(p + "  ", 12, mono=True)
    if not mob and items:
        b.append(text(w - 16, 24, items[0]["date"], 11, th["mute"], mono=True, anchor="end", cls="fo", extra=d(0.9)))
    return svg(w, h, "".join(b), "Latest public activity: " + " / ".join(parts))


def intro(prof, th, w):
    mob = w == W_M
    pad = 20 if mob else 28
    ls = wrap(prof["about"], w - 2 * pad, 15)
    h = pad * 2 + len(ls) * 24 + 4
    b = [card(w, h, th, 12), tracer(w, h, th, 12, dur=10),
         '<rect x="0.5" y="%d" width="3" height="%d" fill="%s" class="gy"/>' % (pad, len(ls) * 24 - 6, th["acc"]),
         lines_el(pad, pad + 16, ls, 15, th["ink"], 24, cls="fi", delay=0.1, step=0.09)]
    return svg(w, h, "".join(b), prof["about"])


def section(kicker, title, meta, th, w):
    h = 66
    b = [text(0, 16, kicker, 12, th["acc"], mono=True, cls="fo"),
         '<rect x="%.1f" y="6" width="7" height="12" fill="%s" class="bl"/>' % (tw(kicker, 12, mono=True) + 4, th["acc"]),
         text(0, 46, title, 26, th["ink"], weight=800, cls="fi", extra=d(0.1)),
         '<rect x="0" y="56" width="%d" height="3" rx="1.5" fill="%s" class="gx"%s/>' % (
             tw(title, 26, bold=True), th["acc"], d(0.3)),
         '<path d="M0 64.5h%d" stroke="%s" pathLength="1" class="dr"%s/>' % (w, th["line"], d(0.2)),
         '<path d="M0 64.5h%d" stroke="%s" stroke-width="1.4" pathLength="1" class="tr" '
         'style="animation-duration:8s"/>' % (w, th["acc"])]
    if meta and w == W_D:
        b.append(text(w, 46, meta, 12, th["mute"], mono=True, anchor="end", cls="fo", extra=d(0.5)))
    return svg(w, h, "".join(b), "%s. %s" % (title, meta))


def sub(title, meta, filled, th, w):
    h = 36
    b = []
    if filled:
        b.append('<circle cx="6" cy="18" r="5" fill="none" stroke="%s" class="ring"/>' % th["acc"])
    b += ['<circle cx="6" cy="18" r="5" fill="%s" stroke="%s" stroke-width="1.5" class="pp"/>' % (
        th["acc"] if filled else "none", th["acc"]), text(20, 23, title, 15, th["ink"], weight=700, cls="fi", extra=d(0.1)),
        text(w, 23, meta, 11, th["mute"], mono=True, anchor="end", cls="fo", extra=d(0.3))]
    return svg(w, h, "".join(b), "%s. %s" % (title, meta))


def chart(items, th, w, today):
    mob = w == W_M
    days = 14
    start = today - dt.timedelta(days=days - 1)
    h, pl, pr, top, base = 150, 18, 18, 46, 116
    b = [card(w, h, th, 12)]
    cnt = {k: sum(1 for i in items if i["status"] == k) for k in STATUS_LABEL}
    lg = [("merged / accepted", cnt["merged"] + cnt["accepted"], True), ("open PR", cnt["open"], None),
          ("proposed", cnt["proposed"], False)]
    lx = pl
    for k, (name, n, filled) in enumerate(lg):
        b.append('<circle cx="%d" cy="22" r="4.5" fill="%s" stroke="%s" stroke-width="1.5"%s class="pp"%s/>' % (
            lx + 4, th["acc"] if filled else "none", th["acc"], ' stroke-dasharray="2 1.5"' if filled is None else "",
            d(0.1 + k * 0.1)))
        lab = "%d %s" % (n, name)
        b.append(text(lx + 14, 26, lab, 11, th["sub"], mono=True, cls="fo", extra=d(0.15 + k * 0.1)))
        lx += tw(lab, 11, mono=True) + 30
    span = w - pl - pr
    b.append('<path d="M%d %d.5h%d" stroke="%s" pathLength="1" class="dr"/>' % (pl, base, span, th["line"]))
    for dd in range(days):
        cx = pl + span * (dd + 0.5) / days
        b.append('<path d="M%.1f %dv%d" stroke="%s" stroke-dasharray="2 3" class="gy"%s/>' % (
            cx, top, base - top, th["line"], d(0.2 + dd * 0.03)))
        if not mob or dd % 2 == 0:
            b.append(text(cx, base + 18, "%02d" % (start + dt.timedelta(days=dd)).day, 10, th["mute"], mono=True,
                          anchor="middle", cls="fo", extra=d(0.3 + dd * 0.03)))
    b.append(text(pl, base + 32, start.strftime("%b %Y").upper(), 9, th["mute"], mono=True))
    # One bar per day, scaled so the busiest day fits the plot (a dot per item overflowed the card
    # once a day held more than 4 items). Segments bottom-up: merged/accepted, open PR, proposed.
    perday = {}
    for i in items:
        try:
            off = (dt.date.fromisoformat(i["date"]) - start).days
        except ValueError:
            continue
        if 0 <= off < days:
            perday.setdefault(off, []).append(i["status"])
    peak = max([len(v) for v in perday.values()] or [1])
    unit = (base - top - 16) / float(peak)
    bw = min(18.0, span / days * 0.5)
    order = (("merged", "accepted"), ("open",), ("proposed",))
    for n, (off, sts) in enumerate(sorted(perday.items())):
        cx = pl + span * (off + 0.5) / days
        y = float(base)
        for g, group in enumerate(order):
            k = sum(1 for st in sts if st in group)
            if not k:
                continue
            hh = max(3.0, k * unit)
            y -= hh
            # same encoding as the legend: merged filled, open PR dashed, proposed plain outline
            fill = th["acc"] if g == 0 else (th["panel2"] if g == 1 else "none")
            dash = ' stroke-dasharray="2 1.5"' if g == 1 else ""
            b.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="2" fill="%s" stroke="%s" '
                     'stroke-width="1.2"%s class="gy"%s/>' % (cx - bw / 2, y, bw, hh - 1, fill, th["acc"], dash,
                                                             d(0.6 + n * 0.12 + g * 0.08)))
        b.append(text(cx, y - 5, str(len(sts)), 10, th["ink"], mono=True, weight=700, anchor="middle", cls="fo",
                      extra=d(0.9 + n * 0.12)))
    label = "Contributions to other people's repositories over the last %d days: %d merged or accepted, %d open PRs, %d proposals." % (
        days, cnt["merged"] + cnt["accepted"], cnt["open"], cnt["proposed"])
    return svg(w, h, "".join(b), label)


def badge(px, py, status, th, delay=0.0):
    lab = STATUS_LABEL[status]
    bw = tw(lab, 10, mono=True, bold=True) + 16
    filled = status in MERGED_SET
    stroke = th["acc"] if (filled or status == "open") else th["mute"]
    return ('<g class="pp"%s><rect x="%.1f" y="%.1f" width="%.1f" height="20" rx="10" fill="%s" stroke="%s"/>' % (
        d(delay), px, py, bw, th["acc"] if filled else "none", stroke)
        + text(px + bw / 2, py + 14, lab, 10, th["on_acc"] if filled else th["sub"], mono=True, weight=700,
               anchor="middle") + "</g>")


def row_name(i, sfx=""):
    return "row-%s-%d%s" % (re.sub(r"[^a-z0-9]+", "-", i["name"].lower()), i["num"], sfx)


def name_size(name, maxw, size=15, floor=10):
    """Largest font size (down to floor) at which a repo name fits the left column."""
    while size > floor and tw(name, size, bold=True) > maxw:
        size -= 0.5
    return size


def row(i, th, w, idx=0):
    mob = w == W_M
    ref = "%s #%d" % ("PR" if i["kind"] == "pull" else "issue", i["num"])
    if mob:
        ls = wrap(i["title"], w - 32, 13)[:3]
        h = 74 + 19 * len(ls)
        b = [card(w, h, th, 10), tracer(w, h, th, 10, delay=idx * 2.3, dur=9), badge(16, 14, i["status"], th),
             text(w - 16, 28, ref, 11, th["acc"], mono=True, weight=700, anchor="end", cls="fo", extra=d(0.3)),
             text(16, 56, i["name"], 15, th["ink"], weight=700, cls="fi", extra=d(0.1)),
             lines_el(16, 78, ls, 13, th["sub"], 19, cls="fi", delay=0.2)]
    else:
        ls = wrap(i["title"], 430, 13)[:3]
        h = max(78, 30 + 19 * len(ls))
        b = [card(w, h, th, 10), tracer(w, h, th, 10, delay=idx * 2.3, dur=9), badge(16, 14, i["status"], th),
             text(16, 52, i["name"], name_size(i["name"], 222), th["ink"], weight=700, cls="fi", extra=d(0.1)),
             text(16, 68, i["area"], 10, th["mute"], mono=True, cls="fo", extra=d(0.25)),
             lines_el(250, h / 2 - (len(ls) - 1) * 9.5 + 4, ls, 13, th["sub"], 19, cls="fi", delay=0.2),
             text(w - 18, h / 2 + 4, ref, 11, th["acc"], mono=True, weight=700, anchor="end", cls="fo", extra=d(0.35))]
    label = "%s: %s. %s (%s)" % (STATUS_LABEL[i["status"]].title(), i["name"], i["title"], ref)
    return svg(w, h, "".join(b), label)


def empty_row(msg, th, w):
    h = 52
    b = [card(w, h, th, 10, fill="none", dash=True)]
    for k in range(3):  # three "waiting" bars breathing at the right edge
        b.append('<rect x="%d" y="18" width="3" height="16" rx="1.5" fill="%s" class="wv" style="animation-delay:-%.1fs"/>' % (
            w - 40 + k * 8, th["acc"], k * 0.3))
    b.append(text(18, 31, msg, 13, th["mute"], cls="fi"))
    return svg(w, h, "".join(b), msg)


def focus(prof, th, w):
    mob = w == W_M
    cols = 1 if mob else 2
    gap = 12
    cw = (w - gap * (cols - 1)) / cols
    cards, hs = [], []
    for f in prof["focus"]:
        ls = wrap(f["text"], cw - 40, 13)
        hs.append(64 + 20 * len(ls))
        cards.append((f, ls))
    ch = max(hs)
    rows_n = (len(cards) + cols - 1) // cols
    h = rows_n * ch + (rows_n - 1) * gap + 1
    b = []
    for k, (f, ls) in enumerate(cards):
        cx, cy = (k % cols) * (cw + gap), (k // cols) * (ch + gap)
        dl = k * 0.12
        # position and animation on separate groups: a CSS transform would override the SVG one
        b.append('<g transform="translate(%.1f %.1f)"><g class="fi"%s>' % (cx, cy, d(dl)))
        b.append(card(cw, ch, th, 12))
        b.append(tracer(cw, ch, th, 12, delay=k * 1.7, dur=10))
        b.append('<rect x="18" y="18" width="30" height="30" rx="8" fill="%s"/>' % th["panel2"])
        b.append(icon(f["icon"], 33, 33, th, 0.95, draw=True, delay=dl + 0.3))
        b.append(text(60, 38, f["title"], 15, th["ink"], weight=700))
        b.append(lines_el(20, 72, ls, 13, th["sub"], 20, cls="fi", delay=dl + 0.15))
        b.append("</g></g>")
    label = "Focus. " + " ".join("%s: %s" % (f["title"], f["text"]) for f in prof["focus"])
    return svg(w, int(h), "".join(b), label)


def background(prof, th, w):
    mob = w == W_M
    pad = 22
    b = []
    y = pad + 12
    b.append(text(pad, y, "VENTURES", 11, th["acc"], mono=True, weight=700, cls="fo"))
    y += 26
    for k, v in enumerate(prof["ventures"]):
        b.append(text(pad, y, v["name"], 15, th["ink"], weight=700, cls="fi", extra=d(0.1 + k * 0.12)))
        b.append(text(pad, y + 18, v["role"], 11, th["mute"], mono=True, cls="fo", extra=d(0.2 + k * 0.12)))
        if not mob:
            b.append(text(260, y + 4, v["note"], 13, th["sub"], cls="fi", extra=d(0.15 + k * 0.12)))
        y += 44
    colx, cy = (pad, y + 10) if mob else (w / 2 + 120, pad + 12)
    b.append(text(colx, cy, "CREDENTIALS", 11, th["acc"], mono=True, weight=700, cls="fo"))
    cx, cyy = colx, cy + 14
    for k, c in enumerate(prof["credentials"]):
        cw = tw(c, 12, bold=True) + 22
        b.append('<g class="pp"%s><rect x="%.1f" y="%d" width="%.1f" height="26" rx="13" fill="%s" stroke="%s"/>%s</g>' % (
            d(0.4 + k * 0.1), cx, cyy, cw, th["panel2"], th["acc"], text(cx + 11, cyy + 17, c, 12, th["ink"], weight=600)))
        cx += cw + 8
    y = max(y, cyy + 40) + (10 if mob else 0)
    ls = wrap(prof["experience"], w - 2 * pad, 13)
    b.append('<path d="M%d %d.5h%d" stroke="%s" pathLength="1" class="dr"%s/>' % (pad, y - 14, w - 2 * pad, th["line"], d(0.5)))
    b.append(lines_el(pad, y + 6, ls, 13, th["sub"], 20, cls="fi", delay=0.6))
    h = y + 6 + 20 * len(ls) + pad - 6
    label = "Background. " + " ".join("%s, %s: %s." % (v["name"], v["role"], v["note"]) for v in prof["ventures"]) + \
            " Credentials: " + ", ".join(prof["credentials"]) + ". " + prof["experience"]
    return svg(w, int(h), card(w, h, th, 12) + tracer(w, h, th, 12, dur=12) + "".join(b), label)


def foot(th, w, today):
    h = 30
    s = "Generated from GitHub, %s" % today.isoformat()
    b = ['<path d="M0 0.5h%d" stroke="%s" pathLength="1" class="dr"/>' % (w, th["line"]),
         '<path d="M0 0.5h%d" stroke="%s" stroke-width="1.4" pathLength="1" class="tr" style="animation-duration:9s"/>' % (
             w, th["acc"]),
         text(w / 2, 22, s, 11, th["mute"], mono=True, anchor="middle", cls="fo")]
    return svg(w, h, "".join(b), s)


# ---------- assembly ----------
def build(prof, items, today):
    files = {}

    def put(theme, name, content):
        files["assets/%s/%s.svg" % (theme, name)] = content

    merged = [i for i in items if i["status"] in MERGED_SET]
    review = [i for i in items if i["status"] not in MERGED_SET]
    for th_name, th in THEMES.items():
        for w, sfx in ((W_D, ""), (W_M, "-m")):
            put(th_name, "hero" + sfx, hero(prof, th, w))
            put(th_name, "ticker" + sfx, ticker(items, th, w))
            put(th_name, "intro" + sfx, intro(prof, th, w))
            put(th_name, "sec-upstream" + sfx, section("~/open-source", "Upstream work",
                                                       "%d merged  /  %d in review" % (len(merged), len(review)), th, w))
            put(th_name, "chart" + sfx, chart(items, th, w, today))
            put(th_name, "sub-merged" + sfx, sub("Merged and accepted", "%d" % len(merged), True, th, w))
            put(th_name, "sub-review" + sfx, sub("In review", "submitted, not merged", False, th, w))
            put(th_name, "empty-merged" + sfx, empty_row("None yet. The first proposals are in review below.", th, w))
            for idx, i in enumerate(items):
                put(th_name, row_name(i, sfx), row(i, th, w, idx))
            put(th_name, "sec-focus" + sfx, section("~/focus", "What I work on", "", th, w))
            put(th_name, "focus" + sfx, focus(prof, th, w))
            put(th_name, "sec-bg" + sfx, section("~/background", "Background", "", th, w))
            put(th_name, "background" + sfx, background(prof, th, w))
            put(th_name, "foot" + sfx, foot(th, w, today))
        for link in prof["links"]:
            put(th_name, "pill-" + link["id"], pill(link, th))
    return files


def hashed(rel, files):
    """assets/dark/hero.svg -> assets/dark/hero.<sha1[:8]>.svg"""
    return rel[:-4] + "." + hashlib.sha1(files[rel].encode()).hexdigest()[:8] + ".svg"


def alt_of(svgtext):
    m = re.search(r'aria-label="([^"]*)"', svgtext)
    return m.group(1) if m else ""


def readme(prof, items, files, base, mode):
    """mode: 'gh' (picture with theme + width sources) or a fixed theme ('light', 'dark', 'dark-m') for preview."""
    def src(th, name):
        # the content hash is in the FILE NAME: raw.githubusercontent.com ignores query strings when
        # caching (measured 2026-11-14: ?v=new served the old bytes for up to 300 s)
        return base + hashed("assets/%s/%s.svg" % (th, name), files)

    def pic(name, mobile=True, href=None, width="100%"):
        alt = alt_of(files["assets/light/%s.svg" % name])
        if mode == "gh" and prof.get("theme") in THEMES:
            # owner-fixed theme (profile.json "theme"): every viewer sees it, light or dark GitHub
            th = prof["theme"]
            s = '<source media="(max-width: 600px)" srcset="%s">' % src(th, name + "-m") if mobile else ""
            el = '<picture>%s<img src="%s" alt="%s" width="%s"></picture>' % (s, src(th, name), x(alt), width)
        elif mode == "gh":
            s = ""
            if mobile:
                s += '<source media="(max-width: 600px) and (prefers-color-scheme: dark)" srcset="%s">' % src("dark", name + "-m")
                s += '<source media="(max-width: 600px)" srcset="%s">' % src("light", name + "-m")
            s += '<source media="(prefers-color-scheme: dark)" srcset="%s">' % src("dark", name)
            el = '<picture>%s<img src="%s" alt="%s" width="%s"></picture>' % (s, src("light", name), x(alt), width)
        else:
            th, _, m = mode.partition("-")
            el = '<img src="%s" alt="%s" width="%s">' % (src(th, name + ("-m" if (m == "m" and mobile) else "")), x(alt), width)
        return '<a href="%s">%s</a>' % (href, el) if href else el

    pills = " ".join(pic("pill-" + link["id"], mobile=False, href=link["url"], width="") for link in prof["links"])
    merged = [i for i in items if i["status"] in MERGED_SET]
    review = [i for i in items if i["status"] not in MERGED_SET]
    md = ["<!-- Generated by tools/build_profile.py from profile.json and GitHub search. Edit those, not this file. -->",
          pic("hero"), "", "<p>%s</p>" % pills, "", pic("ticker"), "", pic("intro"), "",
          pic("sec-upstream"), "", pic("chart"), "", pic("sub-merged")]
    md += [pic(row_name(i), href=i["url"]) for i in merged] or [pic("empty-merged")]
    md += ["", pic("sub-review")] + [pic(row_name(i), href=i["url"]) for i in review]
    md += ["", "<sub>In review means submitted and not merged. [Every public issue and PR I have opened]"
           "(https://github.com/search?q=author%%3A%s&type=issues).</sub>" % prof["login"], "",
           pic("sec-focus"), "", pic("focus"), "", pic("sec-bg"), "", pic("background"), "",
           pic("foot"), '<p align="center">%s</p>' % pills]
    return "\n".join(md) + "\n"


def write(path, content):
    path = os.path.join(ROOT, path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path) and open(path, encoding="utf-8").read() == content:
        return False
    open(path, "w", encoding="utf-8").write(content)
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true", help=argparse.SUPPRESS)
    a = ap.parse_args()
    prof = load_profile()
    items, total, skipped, live = upstream_items(prof, a.offline)
    today = dt.date.today()
    files = build(prof, items, today)
    keep = set()
    for rel, c in files.items():
        keep.add(hashed(rel, files))
        write(hashed(rel, files), c)
    for th_name in THEMES:  # drop stale generated SVGs so the published folder holds only current ones
        folder = os.path.join(ROOT, "assets", th_name)
        for fn in os.listdir(folder):
            if fn.endswith(".svg") and "assets/%s/%s" % (th_name, fn) not in keep:
                os.remove(os.path.join(folder, fn))
    raw = "https://raw.githubusercontent.com/%s/%s/main/" % (prof["login"], prof["login"])
    write("README.md", readme(prof, items, files, raw, "gh"))
    for mode, bg, wd in (("light", "#ffffff", 860), ("dark", "#0d1117", 860), ("dark-m", "#0d1117", 420)):
        body = readme(prof, items, files, "", mode)
        body = re.sub(r"<sub>(.*?)</sub>", lambda m: "<sub>%s</sub>" % re.sub(
            r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', m.group(1)), body)
        html = ('<!doctype html><meta charset="utf-8"><title>profile preview %s</title>'
                '<body style="margin:0;background:%s;color:#9aa4b1;font:14px %s">'
                '<div style="max-width:%dpx;margin:24px auto;padding:0 10px">%s</div>' % (
                    mode, bg, SANS.replace("'", ""), wd, body.replace("\n\n", "<br>")))
        write("preview-%s.html" % mode, html)
    non_ascii = [rel for rel, c in files.items() if any(ord(ch) > 127 for ch in c)]
    animated = sum(1 for c in files.values() if 'class="' in c and "animation" in c)
    print("GitHub search %d items: %d shown upstream, %d closed unmerged; listed %d of %d; "
          "assets %d (%d animated); non-ascii %d" % (total, len(items), skipped, live, len(items), len(files),
                                                     animated, len(non_ascii)))
    return 1 if non_ascii else 0


if __name__ == "__main__":
    sys.exit(main())
