#!/usr/bin/env python3
"""Outline JAWNIX from the real faces so logos do not depend on webfonts."""

from pathlib import Path
from fontTools.ttLib import TTFont
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.misc.transform import Transform
from fontTools.varLib.mutator import instantiateVariableFont

ROOT = Path("/root/jawnix/brand-books")
FONTS = Path("/tmp/jnx-fonts")

# Optical pair kerns in em, added on top of tracking.
# Match: one word. Close A–W (diagonals already hole) and N–I (I floats).
MATCH_KERN = {"AW": -0.055, "JA": -0.02, "WN": -0.02, "NI": -0.055, "IX": -0.03}
METAB_KERN = {"AW": 0.02, "NI": -0.03, "JA": -0.01}


def instance(path, axes=None):
    font = TTFont(path)
    if axes and "fvar" in font:
        font = instantiateVariableFont(font, axes)
    return font


def glyph_path(font, name, dx, dy, scale):
    glyph_set = font.getGlyphSet()
    pen = SVGPathPen(glyph_set)
    t = Transform(scale, 0, 0, -scale, dx, dy)
    tp = TransformPen(pen, t)
    glyph_set[name].draw(tp)
    return pen.getCommands()


def word_path(font, text, tracking_em, target_h, kern=None):
    kern = kern or {}
    cmap = font.getBestCmap()
    glyph_set = font.getGlyphSet()
    upem = font["head"].unitsPerEm
    os2 = font["OS/2"]
    cap = os2.sCapHeight if hasattr(os2, "sCapHeight") and os2.sCapHeight else None
    if not cap:
        from fontTools.pens.boundsPen import BoundsPen
        bp = BoundsPen(glyph_set)
        glyph_set["H"].draw(bp)
        cap = bp.bounds[3] - bp.bounds[1]
    scale = target_h / cap
    tracking = tracking_em * upem * scale
    x = 0
    parts = []
    for i, ch in enumerate(text):
        gname = cmap[ord(ch)]
        parts.append(glyph_path(font, gname, x, target_h, scale))
        adv = glyph_set[gname].width * scale
        extra = 0
        if i < len(text) - 1:
            extra = kern.get(ch + text[i + 1], 0) * upem * scale
            x += adv + tracking + extra
        else:
            x += adv
    return " ".join(parts), x, target_h


def match_mark(ox, oy, scale, ping="#4C8DFF", bid="#3DDC97", post="#E8EDF0", line="#8B969E", wall_m=0.5, ticks=True):
    """One silhouette: 14M × 6M plate. 5M ping window, 3M bid hole, 6M post mass."""
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
    x1, x2 = ox + ping_w, ox + ping_w + bid_w
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
    tick = ""
    if ticks:
        tw = max(1.0, 1.1 * scale)
        tick = (
            f'<line x1="{x1:.2f}" y1="{oy:.2f}" x2="{x1:.2f}" y2="{oy + h:.2f}" '
            f'stroke="{line}" stroke-width="{tw:.2f}"/>'
            f'<line x1="{x2:.2f}" y1="{oy:.2f}" x2="{x2:.2f}" y2="{oy + h:.2f}" '
            f'stroke="{line}" stroke-width="{tw:.2f}"/>'
        )
    return body + outer + tick + ping_s + bid_s, w, h


def match_construction():
    """Dimensioned 14M plate. M = 8. Drawn, not a pasted lockup."""
    m = 8
    s = 2.0
    ox, oy = 56, 72
    mark, w, h = match_mark(0, 0, s, ping="#1B4FD6", bid="#0F8A5A", post="#0E1114", line="#8B969E")
    wm, hm = w, h
    ink, mute, dim, stage = "#0E1114", "#5C666C", "#8B969E", "#EEF1F3"
    ping_w, bid_w, post_s = 5 * m * s, 3 * m * s, 6 * m * s
    lines = [
        f'<rect width="900" height="340" fill="{stage}"/>',
        f'<text x="24" y="28" fill="{mute}" font-family="DM Mono, monospace" font-size="11" letter-spacing="1.4">PLATE 05.1 — CONSTRUCTION · M=8 · MASTER 112 × 48 · SHOWN AT 2×</text>',
        f'<g transform="translate({ox} {oy})">{mark}</g>',
    ]
    # width dimension
    y0 = oy - 18
    for x, lab, span in (
        (ox, "5M PING", ping_w),
        (ox + ping_w, "3M BID", bid_w),
        (ox + ping_w + bid_w, "6M POST", post_s),
    ):
        lines.append(
            f'<line x1="{x}" y1="{y0}" x2="{x + span}" y2="{y0}" stroke="{ink}" stroke-width="1"/>'
            f'<line x1="{x}" y1="{y0 - 4}" x2="{x}" y2="{y0 + 4}" stroke="{ink}" stroke-width="1"/>'
            f'<line x1="{x + span}" y1="{y0 - 4}" x2="{x + span}" y2="{y0 + 4}" stroke="{ink}" stroke-width="1"/>'
            f'<text x="{x + span / 2}" y="{y0 - 8}" text-anchor="middle" fill="{ink}" font-family="DM Mono, monospace" font-size="10">{lab}</text>'
        )
    # height
    xh = ox - 16
    lines.append(
        f'<line x1="{xh}" y1="{oy}" x2="{xh}" y2="{oy + hm}" stroke="{ink}" stroke-width="1"/>'
        f'<text x="{xh - 8}" y="{oy + hm / 2}" text-anchor="middle" fill="{ink}" font-family="DM Mono, monospace" font-size="10" transform="rotate(-90 {xh - 8} {oy + hm / 2})">6M</text>'
    )
    # gap + word cap
    gx = ox + wm + 16
    lines.append(
        f'<text x="{gx}" y="{oy + 18}" fill="{ink}" font-family="DM Sans, sans-serif" font-size="13">Wall 0.5M. Stroke 2.4. Evenodd cuts.</text>'
        f'<text x="{gx}" y="{oy + 40}" fill="{ink}" font-family="DM Sans, sans-serif" font-size="13">Word: DM Sans 600 opsz 40 · cap 56 · track −0.015em</text>'
        f'<text x="{gx}" y="{oy + 62}" fill="{ink}" font-family="DM Sans, sans-serif" font-size="13">Pairs: JA −0.02 · AW −0.055 · WN −0.02 · NI −0.055 · IX −0.03</text>'
        f'<text x="{gx}" y="{oy + 84}" fill="{ink}" font-family="DM Sans, sans-serif" font-size="13">Lockup gap plate→word = 28. Clear = one mark height.</text>'
        f'<text x="{gx}" y="{oy + 106}" fill="{ink}" font-family="DM Sans, sans-serif" font-size="13">One word. Do not open A–W into JA / WNIX.</text>'
        f'<text x="{gx}" y="{oy + 128}" fill="{mute}" font-family="DM Mono, monospace" font-size="11">Minima 24 mm print / 48 px screen — the plate, not live type.</text>'
        f'<text x="24" y="320" fill="{dim}" font-family="DM Mono, monospace" font-size="11">Ticks at 5M and 8M keep the three chambers readable in one ink.</text>'
    )
    return "\n".join("  " + ln for ln in lines)


def metab_mark(ox, oy, scale, core="#C4BBAE", ink="#141210", rust="#C24A22", steel="#8B9294", washi="#E8E2D6", form="#8E877A"):
    """Desk tag: short spine + 2.5×4 ticket. Not a tower."""
    u = 8 * scale
    sw = 1.4 * scale
    core_w = 1.0 * u
    cap_w, cap_h = 4.0 * u, 2.5 * u
    flange = 0.7 * u
    core_h = cap_h + flange
    cap_y = oy + flange
    parts = []
    parts.append(
        f'<rect x="{ox:.2f}" y="{oy:.2f}" width="{core_w:.2f}" height="{core_h:.2f}" '
        f'fill="{core}" stroke="{ink}" stroke-width="{sw:.2f}"/>'
    )
    parts.append(
        f'<line x1="{ox + core_w * 0.32:.2f}" y1="{oy + 0.35 * u:.2f}" '
        f'x2="{ox + core_w * 0.32:.2f}" y2="{oy + core_h - 0.25 * u:.2f}" '
        f'stroke="{steel}" stroke-width="{1.6 * scale:.2f}"/>'
    )
    parts.append(
        f'<line x1="{ox + core_w * 0.68:.2f}" y1="{oy + 0.35 * u:.2f}" '
        f'x2="{ox + core_w * 0.68:.2f}" y2="{oy + core_h - 0.25 * u:.2f}" '
        f'stroke="{steel}" stroke-width="{1.6 * scale:.2f}"/>'
    )
    parts.append(f'<rect x="{ox:.2f}" y="{oy:.2f}" width="{core_w:.2f}" height="{0.45 * u:.2f}" fill="{rust}"/>')
    parts.append(
        f'<path d="M{ox - 0.45 * u:.2f} {oy:.2f}h{core_w + 0.9 * u:.2f}'
        f'M{ox - 0.45 * u:.2f} {oy - 0.38 * u:.2f}h{core_w + 0.9 * u:.2f}" '
        f'stroke="{ink}" stroke-width="{1.2 * scale:.2f}"/>'
    )
    cx = ox + core_w
    parts.append(
        f'<rect x="{cx:.2f}" y="{cap_y:.2f}" width="{cap_w:.2f}" height="{cap_h:.2f}" '
        f'fill="{washi}" stroke="{ink}" stroke-width="{sw:.2f}"/>'
    )
    parts.append(f'<rect x="{cx:.2f}" y="{cap_y:.2f}" width="{0.4 * u:.2f}" height="{cap_h:.2f}" fill="{rust}"/>')
    pr = 0.72 * u
    px, py = cx + 1.45 * u, cap_y + cap_h / 2
    parts.append(
        f'<circle cx="{px:.2f}" cy="{py:.2f}" r="{pr:.2f}" fill="{form}" '
        f'stroke="{ink}" stroke-width="{1.6 * scale:.2f}"/>'
    )
    parts.append(f'<circle cx="{px:.2f}" cy="{py:.2f}" r="{pr * 0.42:.2f}" fill="{ink}"/>')
    br = 0.2 * u
    for bx, by in (
        (cx + 0.22 * u, cap_y + 0.28 * u),
        (cx + 0.7 * u, cap_y + 0.28 * u),
        (cx + 0.22 * u, cap_y + cap_h - 0.28 * u),
        (cx + 0.7 * u, cap_y + cap_h - 0.28 * u),
    ):
        parts.append(
            f'<circle cx="{bx:.2f}" cy="{by:.2f}" r="{br:.2f}" fill="{rust}" '
            f'stroke="{ink}" stroke-width="{0.6 * scale:.2f}"/>'
        )
    return "".join(parts), core_w + cap_w, core_h + 0.38 * u


def metab_core(ox, oy, scale, core="#C4BBAE", ink="#141210", rust="#C24A22", steel="#8B9294"):
    u = 8 * scale
    sw = 1.4 * scale
    core_w, core_h = 1.0 * u, 3.2 * u
    parts = [
        f'<rect x="{ox:.2f}" y="{oy:.2f}" width="{core_w:.2f}" height="{core_h:.2f}" fill="{core}" stroke="{ink}" stroke-width="{sw:.2f}"/>',
        f'<line x1="{ox + core_w * 0.32:.2f}" y1="{oy + 0.3 * u:.2f}" x2="{ox + core_w * 0.32:.2f}" y2="{oy + core_h - 0.2 * u:.2f}" stroke="{steel}" stroke-width="{1.6 * scale:.2f}"/>',
        f'<line x1="{ox + core_w * 0.68:.2f}" y1="{oy + 0.3 * u:.2f}" x2="{ox + core_w * 0.68:.2f}" y2="{oy + core_h - 0.2 * u:.2f}" stroke="{steel}" stroke-width="{1.6 * scale:.2f}"/>',
        f'<rect x="{ox:.2f}" y="{oy:.2f}" width="{core_w:.2f}" height="{0.45 * u:.2f}" fill="{rust}"/>',
        f'<path d="M{ox - 0.45 * u:.2f} {oy:.2f}h{core_w + 0.9 * u:.2f}M{ox - 0.45 * u:.2f} {oy - 0.38 * u:.2f}h{core_w + 0.9 * u:.2f}" stroke="{ink}" stroke-width="{1.2 * scale:.2f}"/>',
    ]
    return "".join(parts), core_w + 0.9 * u, core_h + 0.38 * u


def svg(w, h, body, bg=None, label="JAWNIX", pad=0):
    vw, vh = w + 2 * pad, h + 2 * pad
    bg_el = f'  <rect width="{vw:.1f}" height="{vh:.1f}" fill="{bg}"/>\n' if bg else ""
    inner = f'  <g transform="translate({pad:.1f} {pad:.1f})">\n{body}\n  </g>' if pad else body
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {vw:.1f} {vh:.1f}" '
        f'role="img" aria-label="{label}">\n'
        f"  <title>{label}</title>\n"
        f"{bg_el}"
        f"{inner}\n"
        f"</svg>\n"
    )


def write(path, text):
    path.write_text(text)
    print("wrote", path.name, len(text))


def main():
    dm = instance(FONTS / "DMSans.ttf", {"wght": 600, "opsz": 40})
    plex = instance(FONTS / "IBMPlexSansJP-Medium.ttf")
    d_match, ww, wh = word_path(dm, "JAWNIX", -0.015, 56, MATCH_KERN)
    d_metab, ww2, wh2 = word_path(plex, "JAWNIX", 0.16, 52, METAB_KERN)

    mdir = ROOT / "01-the-match"
    mpad = 2.4
    mark, mw, mh = match_mark(0, 0, 1.0, ping="#1B4FD6", bid="#0F8A5A", post="#0E1114", line="#8B969E")
    write(mdir / "mark.svg", svg(mw, mh, f"  <g>{mark}</g>", None, "JAWNIX mark", pad=mpad))
    mark_sm, _, _ = match_mark(0, 0, 1.0, ping="#1B4FD6", bid="#0F8A5A", post="#0E1114", line="#8B969E", wall_m=0.75)
    write(mdir / "mark-sm.svg", svg(mw, mh, f"  <g>{mark_sm}</g>", None, "JAWNIX mark small", pad=mpad))
    mark_d, _, _ = match_mark(0, 0, 1.0, ping="#4C8DFF", bid="#3DDC97", post="#E8EDF0", line="#8B969E")
    write(mdir / "mark-dark.svg", svg(mw, mh, f"  <g>{mark_d}</g>", None, "JAWNIX mark dark", pad=mpad))
    write(mdir / "construction.svg", svg(900, 340, match_construction(), None, "JAWNIX construction", pad=0))

    gap = 28
    wh_pad = 4
    lh = max(mh, wh + wh_pad) + 4
    my = (lh - mh) / 2
    ty = (lh - (wh + wh_pad)) / 2 + 2
    body_l = (
        f'  <g transform="translate(0 {my:.1f})">{mark}</g>\n'
        f'  <g transform="translate({mw + gap:.1f} {ty:.1f})" fill="#0E1114"><path d="{d_match}"/></g>'
    )
    write(mdir / "logo.svg", svg(mw + gap + ww, lh, body_l, None, pad=mpad))
    write(mdir / "logo-light.svg", svg(mw + gap + ww, lh, body_l, None, pad=mpad))
    body_d = (
        f'  <g transform="translate(0 {my:.1f})">{mark_d}</g>\n'
        f'  <g transform="translate({mw + gap:.1f} {ty:.1f})" fill="#E8EDF0"><path d="{d_match}"/></g>'
    )
    write(mdir / "logo-dark.svg", svg(mw + gap + ww, lh, body_d, None, pad=mpad))
    mark_m, _, _ = match_mark(0, 0, 1.0, ping="#0E1114", bid="#0E1114", post="#0E1114", line="#5C666C")
    body_m = (
        f'  <g transform="translate(0 {my:.1f})">{mark_m}</g>\n'
        f'  <g transform="translate({mw + gap:.1f} {ty:.1f})" fill="#0E1114"><path d="{d_match}"/></g>'
    )
    write(mdir / "logo-mono.svg", svg(mw + gap + ww, lh, body_m, None, "JAWNIX mono", pad=mpad))

    gap_y = 16
    st = ww / mw
    mark_st, mw_st, mh_st = match_mark(0, 0, st, ping="#1B4FD6", bid="#0F8A5A", post="#0E1114", line="#8B969E")
    mark_st_d, _, _ = match_mark(0, 0, st, ping="#4C8DFF", bid="#3DDC97", post="#E8EDF0", line="#8B969E")
    sh = mh_st + gap_y + wh + wh_pad
    sx = ww
    stack = (
        f'  <g transform="translate(0 0)">{mark_st}</g>\n'
        f'  <g transform="translate(0 {mh_st + gap_y + 2:.1f})" fill="#0E1114"><path d="{d_match}"/></g>'
    )
    write(mdir / "logo-stack.svg", svg(sx, sh, stack, None, "JAWNIX stacked", pad=mpad))
    stack_d = (
        f'  <g transform="translate(0 0)">{mark_st_d}</g>\n'
        f'  <g transform="translate(0 {mh_st + gap_y + 2:.1f})" fill="#E8EDF0"><path d="{d_match}"/></g>'
    )
    write(mdir / "logo-stack-dark.svg", svg(sx, sh, stack_d, None, "JAWNIX stacked dark", pad=mpad))
    write(mdir / "wordmark.svg", svg(ww, wh + wh_pad, f'  <g transform="translate(0 2)" fill="#0E1114"><path d="{d_match}"/></g>', None, pad=1.2))
    write(mdir / "wordmark-dark.svg", svg(ww, wh + wh_pad, f'  <g transform="translate(0 2)" fill="#E8EDF0"><path d="{d_match}"/></g>', None, "JAWNIX wordmark dark", pad=1.2))

    pdir = ROOT / "02-metabolism"
    mm, mmw, mmh = metab_mark(4, 6, 1.0)
    write(pdir / "mark.svg", svg(mmw + 8, mmh + 8, f"  <g>{mm}</g>", None, "JAWNIX mark"))

    mm2, mmw2, mmh2 = metab_mark(4.2, 4, 1.0)
    gap2 = 22
    wh2_pad = 3
    lh2 = max(mmh2, wh2 + wh2_pad) + 8
    lmy = (lh2 - mmh2) / 2
    lty = (lh2 - (wh2 + wh2_pad)) / 2 + 1
    body2 = (
        f'  <g transform="translate(0 {lmy:.1f})">{mm2}</g>\n'
        f'  <g transform="translate({4.2 + mmw2 + gap2:.1f} {lty:.1f})" fill="#141210">'
        f'<path d="{d_metab}"/></g>'
    )
    write(pdir / "logo.svg", svg(4.2 + mmw2 + gap2 + ww2, lh2, body2, None))

    mmr, _, _ = metab_mark(4.2, 4, 1.0, core="#C4BBAE", ink="#E8E2D6", rust="#C24A22", steel="#8B9294", washi="#141210", form="#8E877A")
    body_r = (
        f'  <g transform="translate(0 {lmy:.1f})">{mmr}</g>\n'
        f'  <g transform="translate({4.2 + mmw2 + gap2:.1f} {lty:.1f})" fill="#E8E2D6">'
        f'<path d="{d_metab}"/></g>'
    )
    write(pdir / "logo-reverse.svg", svg(4.2 + mmw2 + gap2 + ww2, lh2, body_r, None, "JAWNIX reverse"))
    write(pdir / "wordmark.svg", svg(ww2, wh2 + wh2_pad, f'  <g transform="translate(0 1)" fill="#141210"><path d="{d_metab}"/></g>', None))

    mmrv, mmrw, mmrh = metab_mark(4, 6, 1.0, core="#C4BBAE", ink="#E8E2D6", rust="#C24A22", steel="#8B9294", washi="#141210", form="#8E877A")
    write(pdir / "mark-reverse.svg", svg(mmrw + 8, mmrh + 8, f"  <g>{mmrv}</g>", None, "JAWNIX mark reverse"))

    gap_y2 = 14
    sh2 = mmh2 + gap_y2 + wh2 + wh2_pad
    sx2 = max(4.2 + mmw2, ww2)
    stack2 = (
        f'  <g transform="translate({(sx2 - (4.2 + mmw2)) / 2:.1f} 0)">{mm2}</g>\n'
        f'  <g transform="translate({(sx2 - ww2) / 2:.1f} {mmh2 + gap_y2 + 1:.1f})" fill="#141210">'
        f'<path d="{d_metab}"/></g>'
    )
    write(pdir / "logo-stack.svg", svg(sx2, sh2, stack2, None, "JAWNIX stacked"))

    cc, cw, ch = metab_core(6, 6, 1.0)
    write(pdir / "mark-core.svg", svg(cw + 8, ch + 8, f"  <g>{cc}</g>", None, "JAWNIX core"))

    print("match word", ww, "metab word", ww2, "match mark", mw, mh, "metab mark", mmw, mmh)


if __name__ == "__main__":
    main()
