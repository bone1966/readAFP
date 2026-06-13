"""Render extracted pages as SVG markup.

A deliberately rough first pass: text runs are drawn at their PTOCA
coordinates in a substitute font (no embedded FOCA/TrueType metrics yet),
rules are drawn as rectangles. Coordinates are L-units, so the SVG
viewBox is the page size and the browser scales it.
"""

import base64
import logging
from typing import List
from xml.sax.saxutils import escape, quoteattr

from readafp.ptoca import MAX_RUNS_PER_PAGE, ImageRef, Page

logger = logging.getLogger(__name__)

# Each CMYK plane JPEG is grayscale (R=G=B = ink amount). The filters
# map a plane to its complement color (C ink absorbs red, ...), so
# multiply-blending the four planes composes the inks optically:
# R = (1-C)(1-K), G = (1-M)(1-K), B = (1-Y)(1-K).
_INK_FILTERS = (
    "<defs>"
    '<filter id="ink-c" color-interpolation-filters="sRGB">'
    '<feColorMatrix values="-1 0 0 0 1  0 0 0 0 1  0 0 0 0 1  0 0 0 1 0"/>'
    "</filter>"
    '<filter id="ink-m" color-interpolation-filters="sRGB">'
    '<feColorMatrix values="0 0 0 0 1  0 -1 0 0 1  0 0 0 0 1  0 0 0 1 0"/>'
    "</filter>"
    '<filter id="ink-y" color-interpolation-filters="sRGB">'
    '<feColorMatrix values="0 0 0 0 1  0 0 0 0 1  0 0 -1 0 1  0 0 0 1 0"/>'
    "</filter>"
    '<filter id="ink-k" color-interpolation-filters="sRGB">'
    '<feColorMatrix values="-1 0 0 0 1  0 -1 0 0 1  0 0 -1 0 1  0 0 0 1 0"/>'
    "</filter>"
    "</defs>"
)


def _image_markup(img: ImageRef) -> str:
    """One placed image: a plain <image>, or a CMYK plane composite."""
    box = (
        f'x="{img.x}" y="{img.y}" width="{img.width}" '
        f'height="{img.height}" preserveAspectRatio="xMidYMid meet"'
    )
    if not img.bands:
        b64 = base64.b64encode(img.data).decode("ascii")
        crisp = ' style="image-rendering:pixelated"' if img.crisp else ""
        return f'<image {box}{crisp} href="data:{img.mime};base64,{b64}"/>'
    parts = ['<g style="isolation:isolate">']
    for ink, blob in zip("cmyk", img.bands):
        b64 = base64.b64encode(blob).decode("ascii")
        # The first (opaque) plane is the blend base for the rest.
        blend = "" if ink == "c" else ' style="mix-blend-mode:multiply"'
        parts.append(
            f'<image {box} filter="url(#ink-{ink})"{blend} '
            f'href="data:image/jpeg;base64,{b64}"/>'
        )
    parts.append("</g>")
    return "".join(parts)


def _fit(texts, i) -> str:
    """Stretch a run to the width the AFP's own positioning implies.

    If the next run sits on the same baseline, the gap between their
    start positions minus one space is the width the producer gave this
    run's glyphs. Substitute fonts render a few percent off, which makes
    underlines overshoot and trailing punctuation drift; textLength
    pins the run to the intended extent. The ratio guard keeps column
    gaps and short runs from triggering visible distortion.
    """
    run = texts[i]
    nxt = texts[i + 1] if i + 1 < len(texts) else None
    if nxt is None or nxt.y != run.y or nxt.x <= run.x:
        return ""
    if len(run.text.strip()) < 4:
        return ""
    avail = nxt.x - run.x - int(0.3 * run.font_size)  # minus one space
    est = len(run.text) * 0.52 * run.font_size
    if est <= 0 or not 0.7 <= avail / est <= 1.4:
        return ""
    return f' textLength="{avail}" lengthAdjust="spacing"'


def page_to_svg(page: Page) -> str:
    """Build an SVG document string for one page."""
    parts: List[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {page.width} {page.height}" '
        f'data-upi="{page.units_per_inch}" '
        f'font-family="Arial, Helvetica, sans-serif">',
        f'<rect width="{page.width}" height="{page.height}" fill="#ffffff"/>',
    ]
    if any(img.bands for img in page.images):
        parts.append(_INK_FILTERS)
    for rule in page.rules:
        # Rules extend from the current position in the +I/+B direction
        # (negative length or width extends the other way).
        if rule.axis == "I":
            x, y = rule.x, rule.y
            w, h = rule.length, rule.thickness
        else:
            x, y = rule.x, rule.y
            w, h = rule.thickness, rule.length
        if w < 0:
            x, w = x + w, -w
        if h < 0:
            y, h = y + h, -h
        src = f' data-src="{rule.src}"' if rule.src is not None else ""
        parts.append(
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}"{src} '
            f'fill={quoteattr(rule.color)}/>'
        )
    for img in page.images:
        parts.append(_image_markup(img))
    for i, run in enumerate(page.texts):
        weight = ' font-weight="bold"' if run.font_weight == "bold" else ""
        family = (
            f" font-family={quoteattr(run.font_family)}"
            if run.font_family != "Arial"
            else ""
        )
        src = f' data-src="{run.src}"' if run.src is not None else ""
        parts.append(
            f'<text x="{run.x}" y="{run.y}" font-size="{run.font_size}"'
            f"{family}{weight}{src}{_fit(page.texts, i)} "
            f'fill={quoteattr(run.color)}>{escape(run.text)}</text>'
        )
    if page.truncated:
        parts.append(
            f'<text x="60" y="{page.height - 80}" font-size="200" '
            f'fill="#9aa0b8">[render truncated: page content exceeds '
            f"{MAX_RUNS_PER_PAGE} runs]</text>"
        )
    parts.append("</svg>")
    return "".join(parts)


def pages_to_svgs(
    pages: List[Page], limit: int, content_budget: int = 50000
) -> List[str]:
    """Render up to ``limit`` pages, stopping early if the cumulative
    element count exceeds ``content_budget`` (keeps the embedded SVG
    payload bounded for dense documents)."""
    out: List[str] = []
    used = 0
    for page in pages[:limit]:
        out.append(page_to_svg(page))
        used += len(page.texts) + len(page.rules)
        if used > content_budget:
            break
    if len(out) < len(pages):
        logger.info("rendering first %d of %d pages", len(out), len(pages))
    return out
