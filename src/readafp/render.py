"""Render extracted pages as SVG markup.

A deliberately rough first pass: text runs are drawn at their PTOCA
coordinates in a substitute font (no embedded FOCA/TrueType metrics yet),
rules are drawn as rectangles. Coordinates are L-units, so the SVG
viewBox is the page size and the browser scales it.
"""

import logging
from typing import List
from xml.sax.saxutils import escape, quoteattr

from readafp.ptoca import Page

logger = logging.getLogger(__name__)


def page_to_svg(page: Page) -> str:
    """Build an SVG document string for one page."""
    parts: List[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {page.width} {page.height}" '
        f'font-family="Arial, Helvetica, sans-serif">',
        f'<rect width="{page.width}" height="{page.height}" fill="#ffffff"/>',
    ]
    for rule in page.rules:
        if rule.axis == "I":
            x, y = rule.x, rule.y - rule.thickness // 2
            w, h = rule.length, rule.thickness
        else:
            x, y = rule.x - rule.thickness // 2, rule.y
            w, h = rule.thickness, rule.length
        if w < 0:
            x, w = x + w, -w
        if h < 0:
            y, h = y + h, -h
        parts.append(
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" '
            f'fill={quoteattr(rule.color)}/>'
        )
    for run in page.texts:
        parts.append(
            f'<text x="{run.x}" y="{run.y}" font-size="{run.font_size}" '
            f'fill={quoteattr(run.color)}>{escape(run.text)}</text>'
        )
    parts.append("</svg>")
    return "".join(parts)


def pages_to_svgs(pages: List[Page], limit: int) -> List[str]:
    """Render at most ``limit`` pages to SVG strings."""
    if len(pages) > limit:
        logger.info("rendering first %d of %d pages", limit, len(pages))
    return [page_to_svg(p) for p in pages[:limit]]
