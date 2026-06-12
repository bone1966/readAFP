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

from readafp.ptoca import MAX_RUNS_PER_PAGE, Page

logger = logging.getLogger(__name__)


def page_to_svg(page: Page) -> str:
    """Build an SVG document string for one page."""
    parts: List[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {page.width} {page.height}" '
        f'data-upi="{page.units_per_inch}" '
        f'font-family="Arial, Helvetica, sans-serif">',
        f'<rect width="{page.width}" height="{page.height}" fill="#ffffff"/>',
    ]
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
        b64 = base64.b64encode(img.data).decode("ascii")
        parts.append(
            f'<image x="{img.x}" y="{img.y}" width="{img.width}" '
            f'height="{img.height}" preserveAspectRatio="xMidYMid meet" '
            f'href="data:{img.mime};base64,{b64}"/>'
        )
    for run in page.texts:
        weight = ' font-weight="bold"' if run.font_weight == "bold" else ""
        family = (
            f" font-family={quoteattr(run.font_family)}"
            if run.font_family != "Arial"
            else ""
        )
        src = f' data-src="{run.src}"' if run.src is not None else ""
        parts.append(
            f'<text x="{run.x}" y="{run.y}" font-size="{run.font_size}"'
            f"{family}{weight}{src} "
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
