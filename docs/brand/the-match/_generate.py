#!/usr/bin/env python3
"""Regenerate The Match SVGs from the accepted plate law (16 Aug 2026).

Do not invent a third mark. Module M=8. One 14M×6M evenodd plate.
Ping window left, bid hole center, post mass right.
Wordmark: DM Sans 600, tracking −0.02em, optical pair kerns from the book.
"""

from __future__ import annotations

from pathlib import Path

from fontTools.misc.transform import Transform
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont
from fontTools.varLib.mutator import instantiateVariableFont

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[2]
FRONTEND = REPO / "frontend" / "src" / "brand"
FONT = Path("/tmp/jnx-fonts/DMSans.ttf")

MATCH_KERN = {"AW": 0.06, "NI": -0.08, "JA": -0.03, "WN": 0.01, "IX": -0.02}
TRACKING = -0.02
CAP = 56.0


def match_mark(ox, oy, scale, ping="#4C8DFF", bid="#3DDC97", post="#E8EDF0", line="#8B969E", wall_m=0.5):
    m = 8 * scale
    sw = 2.4 * scale
    wall = wall_m * m
    ping_w, bid_w, post_s = 5 * m, 3 * m, 6 * m
    h = 6 * m
    w = ping_w + bid_w + post_s
    px, py = ox + wall, oy + wall
    pw, ph = ping_w - 2 * wall, h - 2 * wall
    cx = ox + ping_w + bid_w / 2
    cy = oy + h / 2
    r = (bid_w / 2) - wall
    body = (
        f'<path fill-rule="evenodd" fill="{post}" d="'
        f"M{ox:.2f} {oy:.2f}h{w:.2f}v{h:.2f}h{-w:.2f}z "
        f"M{px:.2f} {py:.2f}h{pw:.2f}v{ph:.2f}h{-pw:.2f}z "
        f"M{cx + r:.2f} {cy:.2f}a{r:.2f} {r:.2f} 0 1 1 {-2 * r:.2f} 0"
        f'a{r:.2f} {r:.2f} 0 1 1 {2 * r:.2f} 0z"/>'
    )
    outer = (
        f'<rect x="{ox:.2f}" y="{oy:.2f}" width="{w:.2f}" height="{h:.2f}" '
        f'fill="none" stroke="{line}" stroke-width="{sw:.2f}"/>'
    )
    ping_s = (
        f'<rect x="{px:.2f}" y="{py:.2f}" width="{pw:.2f}" height="{ph:.2f}" '
        f'fill="none" stroke="{ping}" stroke-width="{sw:.2f}"/>'
    )
    bid_s = (
        f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="{r:.2f}" fill="none" '
        f'stroke="{bid}" stroke-width="{sw:.2f}"/>'
    )
    return body + outer + ping_s + bid_s, w, h


def svg(w, h, body, title, pad=2.4):
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{-pad:.1f} {-pad:.1f} '
        f'{w + 2 * pad:.1f} {h + 2 * pad:.1f}" role="img" aria-label="{title}">\n'
        f"  <title>{title}</title>\n"
        f"{body}\n"
        f"</svg>\n"
    )


def outline_jawnix():
    font = instantiateVariableFont(TTFont(FONT), {"wght": 600, "opsz": 14})
    glyph_set = font.getGlyphSet()
    upem = font["head"].unitsPerEm
    cmap = font.getBestCmap()
    os2 = font["OS/2"]
    cap_em = os2.sCapHeight or 0.7 * upem
    scale = CAP / cap_em
    tracking = TRACKING * upem * scale
    x = 0.0
    parts = []
    text = "JAWNIX"
    for i, ch in enumerate(text):
        name = cmap[ord(ch)]
        glyph = glyph_set[name]
        pen = SVGPathPen(glyph_set)
        tp = TransformPen(pen, Transform(scale, 0, 0, -scale, x, CAP))
        glyph.draw(tp)
        parts.append(pen.getCommands())
        adv = glyph.width * scale
        if i < len(text) - 1:
            extra = MATCH_KERN.get(ch + text[i + 1], 0) * upem * scale
            x += adv + tracking + extra
        else:
            x += adv
    return " ".join(parts), x, CAP


def write(path: Path, contents: str):
    path.write_text(contents)
    print(path)


def main():
    ROOT.mkdir(parents=True, exist_ok=True)
    FRONTEND.mkdir(parents=True, exist_ok=True)
    d_match, ww, wh = outline_jawnix()
    pad = 2.4
    gap = 32
    wh_pad = 4

    mark, mw, mh = match_mark(0, 0, 1.0)
    write(ROOT / "mark.svg", svg(mw, mh, f"  <g>{mark}</g>", "JAWNIX mark", pad))

    mark_sm, mw_sm, mh_sm = match_mark(0, 0, 1.0, wall_m=0.75)
    write(ROOT / "mark-sm.svg", svg(mw_sm, mh_sm, f"  <g>{mark_sm}</g>", "JAWNIX mark small", pad))

    mark2, mw2, mh2 = match_mark(0, 0, 1.0)
    lh = max(mh2, wh + wh_pad) + 4
    my = (lh - mh2) / 2
    ty = (lh - (wh + wh_pad)) / 2 + 2
    body = (
        f'  <g transform="translate(0 {my:.1f})">{mark2}</g>\n'
        f'  <g transform="translate({mw2 + gap:.1f} {ty:.1f})" fill="#E8EDF0">'
        f'<path d="{d_match}"/></g>'
    )
    write(ROOT / "logo.svg", svg(mw2 + gap + ww, lh, body, "JAWNIX", pad))

    mark_l, _, _ = match_mark(0, 0, 1.0, ping="#1B4FD6", bid="#0F8A5A", post="#0E1114", line="#5C666C")
    body_l = (
        f'  <g transform="translate(0 {my:.1f})">{mark_l}</g>\n'
        f'  <g transform="translate({mw2 + gap:.1f} {ty:.1f})" fill="#0E1114">'
        f'<path d="{d_match}"/></g>'
    )
    write(ROOT / "logo-light.svg", svg(mw2 + gap + ww, lh, body_l, "JAWNIX", pad))

    mark_m, _, _ = match_mark(0, 0, 1.0, ping="#E8EDF0", bid="#E8EDF0", post="#E8EDF0", line="#8B969E")
    body_m = (
        f'  <g transform="translate(0 {my:.1f})">{mark_m}</g>\n'
        f'  <g transform="translate({mw2 + gap:.1f} {ty:.1f})" fill="#E8EDF0">'
        f'<path d="{d_match}"/></g>'
    )
    write(ROOT / "logo-mono.svg", svg(mw2 + gap + ww, lh, body_m, "JAWNIX mono", pad))

    gap_y = 16
    sh = mh2 + gap_y + wh + wh_pad
    sx = max(mw2, ww)
    stack = (
        f'  <g transform="translate({(sx - mw2) / 2:.1f} 0)">{mark2}</g>\n'
        f'  <g transform="translate({(sx - ww) / 2:.1f} {mh2 + gap_y + 2:.1f})" fill="#E8EDF0">'
        f'<path d="{d_match}"/></g>'
    )
    write(ROOT / "logo-stack.svg", svg(sx, sh, stack, "JAWNIX stacked", pad))
    write(
        ROOT / "wordmark.svg",
        svg(ww, wh + wh_pad, f'  <g transform="translate(0 2)" fill="#E8EDF0"><path d="{d_match}"/></g>', "JAWNIX"),
    )

    for name in (
        "mark.svg",
        "mark-sm.svg",
        "logo.svg",
        "logo-light.svg",
        "logo-stack.svg",
        "logo-mono.svg",
        "wordmark.svg",
    ):
        dest = FRONTEND / name
        dest.write_text((ROOT / name).read_text())
        print(dest)


if __name__ == "__main__":
    main()
