"""PTOCA presentation-text decoding and page extraction.

PTX structured fields carry chains of PTOCA control sequences:

    0x2B 0xD3            escape introducing a chain
    u8 length            counts the length byte, type byte and parameters
    u8 type              function type; low bit set = chained (the next
                         sequence follows immediately, without an escape)
    params               (length - 2 bytes)

Coordinates are in L-units along the inline (I, across the line) and
baseline (B, down the page) axes, scaled by the PGD/PTD units-per-base
(typically 14400 per 10 inches, i.e. 1440/inch).

Reference: PTOCA Reference, AFPC-0009-04 (docs/specs/ptoca-reference-04.pdf).
"""

import logging
from dataclasses import dataclass, field
from typing import Dict, Iterator, List, Optional, Tuple

from readafp.parser import StructuredField

logger = logging.getLogger(__name__)

ESCAPE = b"\x2b\xd3"

# Control sequence function types, keyed by the unchained (even) value.
CS_NAMES = {
    0x74: "STC (Set Text Color)",
    0x80: "SEC (Set Extended Text Color)",
    0xC0: "SIM (Set Inline Margin)",
    0xC2: "SIA (Set Intercharacter Adjustment)",
    0xC4: "SVI (Set Variable Space Increment)",
    0xC6: "AMI (Absolute Move Inline)",
    0xC8: "RMI (Relative Move Inline)",
    0xD0: "SBI (Set Baseline Increment)",
    0xD2: "AMB (Absolute Move Baseline)",
    0xD4: "RMB (Relative Move Baseline)",
    0xD8: "BLN (Begin Line)",
    0xDA: "TRN (Transparent Data)",
    0xE4: "DIR (Draw I-axis Rule)",
    0xE6: "DBR (Draw B-axis Rule)",
    0xF0: "SCFL (Set Coded Font Local)",
    0xF2: "BSU (Begin Suppression)",
    0xF4: "ESU (End Suppression)",
    0xF6: "STO (Set Text Orientation)",
    0xF8: "NOP (No Operation)",
}

# STC two-byte standard color values -> CSS color.
_STC_COLORS = {
    0x0001: "#0000ff",  # blue
    0x0002: "#ff0000",  # red
    0x0003: "#ff00ff",  # pink/magenta
    0x0004: "#00ff00",  # green
    0x0005: "#00ffff",  # turquoise/cyan
    0x0006: "#ffff00",  # yellow
    0x0008: "#000000",  # black
    0x0010: "#a52a2a",  # brown
    0xFF07: "#000000",  # "color of medium" default
}

DEFAULT_COLOR = "#000000"

# Rough glyph metrics used only to advance the inline coordinate after a
# text run when the producer does not issue an explicit move. 240 L-units
# is 12pt at 1440 units/inch.
DEFAULT_FONT_SIZE = 240
_CHAR_ADVANCE_RATIO = 0.55


@dataclass
class ControlSequence:
    """One decoded PTOCA control sequence."""

    cs_type: int  # unchained (even) function type
    params: bytes

    @property
    def name(self) -> str:
        return CS_NAMES.get(self.cs_type, f"Unknown (0x{self.cs_type:02X})")


@dataclass
class FontInfo:
    """A font mapped to a local id by MDR/MCF, as far as we can decode it."""

    family: str = "Arial"
    weight: str = "normal"
    size: Optional[int] = None  # L-units (at 1440/inch, 1pt = 20 units)


@dataclass
class TextRun:
    """A run of text positioned on a page, in page L-units."""

    x: int
    y: int
    text: str
    color: str = DEFAULT_COLOR
    font_id: Optional[int] = None
    font_size: int = DEFAULT_FONT_SIZE
    font_family: str = "Arial"
    font_weight: str = "normal"


@dataclass
class Rule:
    """A solid rule (line) on a page, in page L-units."""

    x: int
    y: int
    length: int
    thickness: int
    axis: str  # "I" (horizontal) or "B" (vertical)
    color: str = DEFAULT_COLOR


@dataclass
class Page:
    """One page's geometry and rough presentation-text content."""

    width: int = 12240  # letter at 1440/inch
    height: int = 15840
    units_per_inch: int = 1440
    texts: List[TextRun] = field(default_factory=list)
    rules: List[Rule] = field(default_factory=list)

    @property
    def plain_text(self) -> str:
        """Text runs joined in reading order (top-down, then left-right)."""
        ordered = sorted(self.texts, key=lambda r: (r.y, r.x))
        lines: List[str] = []
        last_y: Optional[int] = None
        for run in ordered:
            if last_y is not None and abs(run.y - last_y) > 40:
                lines.append("\n")
            elif lines:
                lines.append(" ")
            lines.append(run.text)
            last_y = run.y
        return "".join(lines)


def iter_control_sequences(data: bytes) -> Iterator[ControlSequence]:
    """Yield PTOCA control sequences from PTX field data.

    Malformed tails are logged and skipped rather than raised: a partial
    decode of real-world PTX is more useful than none.
    """
    pos = 0
    chained = False
    while pos < len(data):
        if not chained:
            esc = data.find(ESCAPE, pos)
            if esc < 0:
                break
            pos = esc + 2
        if pos + 2 > len(data):
            logger.warning("truncated control sequence at offset %d", pos)
            break
        length, cs_type = data[pos], data[pos + 1]
        if length < 2 or pos + length > len(data):
            logger.warning(
                "bad control sequence length %d at offset %d", length, pos
            )
            break
        yield ControlSequence(
            cs_type=cs_type & 0xFE, params=bytes(data[pos + 2 : pos + length])
        )
        chained = bool(cs_type & 0x01)
        pos += length


def _decode_trn(params: bytes) -> str:
    """Decode TRN text bytes: UTF-16BE for TrueType flows, else EBCDIC.

    Without the font's code page (mapped via MCF/MDR triplets, milestone 2)
    we use a heuristic: UTF-16BE text over Latin scripts has a zero high
    byte for nearly every character.
    """
    if len(params) >= 2 and len(params) % 2 == 0:
        high_zeros = sum(1 for b in params[0::2] if b == 0)
        if high_zeros >= len(params) // 2 * 0.8:
            try:
                return params.decode("utf-16-be")
            except UnicodeDecodeError:
                pass
    try:
        return params.decode("cp500")
    except UnicodeDecodeError:
        return params.decode("cp500", errors="replace")


def _u16(b: bytes, off: int = 0) -> int:
    return int.from_bytes(b[off : off + 2], "big")


def _s16(b: bytes, off: int = 0) -> int:
    return int.from_bytes(b[off : off + 2], "big", signed=True)


def iter_triplets(data: bytes) -> Iterator[Tuple[int, bytes]]:
    """Yield (triplet id, triplet data) from a run of MO:DCA triplets."""
    pos = 0
    while pos + 2 <= len(data):
        length, tid = data[pos], data[pos + 1]
        if length < 2 or pos + length > len(data):
            break
        yield tid, bytes(data[pos + 2 : pos + length])
        pos += length


def parse_mdr_fonts(data: bytes) -> Dict[int, FontInfo]:
    """Extract font name/size per local id from an MDR (Map Data Resource).

    The MDR body is repeating groups (u16 length includes itself), each
    holding triplets. For data-object fonts the useful ones are:

    - 0x02 FQN type 0xDE: the font's full name (e.g. "Arial Bold")
    - 0x8B data-object font descriptor: point size in 1/20 pt at offset 2,
      which at 1440 L-units/inch is the size in L-units directly
    - 0x02 FQN type 0xBE: the local id PTX SCFL sequences select
    """
    fonts: Dict[int, FontInfo] = {}
    pos = 0
    while pos + 2 <= len(data):
        group_len = _u16(data, pos)
        if group_len < 2 or pos + group_len > len(data):
            break
        name: Optional[str] = None
        size: Optional[int] = None
        local_id: Optional[int] = None
        for tid, tdata in iter_triplets(data[pos + 2 : pos + group_len]):
            if tid == 0x02 and len(tdata) >= 3:
                if tdata[0] == 0xDE:
                    name = _decode_trn(tdata[2:]).strip()
                elif tdata[0] == 0xBE:
                    local_id = tdata[-1]
            elif tid == 0x8B and len(tdata) >= 4:
                size = _u16(tdata, 2)
        if local_id is not None:
            family = name or "Arial"
            weight = "normal"
            for marker in (" Bold", " bold"):
                if marker in family:
                    family = family.replace(marker, "")
                    weight = "bold"
            fonts[local_id] = FontInfo(family=family, weight=weight, size=size)
        pos += group_len
    return fonts


def _sec_color(params: bytes) -> Optional[str]:
    """Decode an SEC (Set Extended Color) RGB value, if that's what it is."""
    # reserved(1) colorspace(1) reserved(4) sizes(4) value(...)
    if len(params) >= 13 and params[1] == 0x01 and params[6:9] == b"\x08\x08\x08":
        r, g, b = params[10], params[11], params[12]
        return f"#{r:02x}{g:02x}{b:02x}"
    return None


class _TextState:
    """Mutable PTOCA interpreter state, carried across PTX fields of a page."""

    def __init__(self, fonts: Optional[Dict[int, FontInfo]] = None) -> None:
        self.i = 0
        self.b = 0
        self.inline_margin = 0
        self.baseline_increment = 0
        self.color = DEFAULT_COLOR
        self.font_id: Optional[int] = None
        # Keep the caller's dict: it may be filled by MDRs seen later
        # (the page's AEG comes after BPG).
        self.fonts = fonts if fonts is not None else {}

    def apply(self, cs: ControlSequence, page: Page) -> None:
        t, p = cs.cs_type, cs.params
        if t == 0xC6 and len(p) >= 2:  # AMI
            self.i = _u16(p)
        elif t == 0xC8 and len(p) >= 2:  # RMI
            self.i += _s16(p)
        elif t == 0xD2 and len(p) >= 2:  # AMB
            self.b = _u16(p)
        elif t == 0xD4 and len(p) >= 2:  # RMB
            self.b += _s16(p)
        elif t == 0xC0 and len(p) >= 2:  # SIM
            self.inline_margin = _u16(p)
        elif t == 0xD0 and len(p) >= 2:  # SBI
            self.baseline_increment = _s16(p)
        elif t == 0xD8:  # BLN
            self.b += self.baseline_increment
            self.i = self.inline_margin
        elif t == 0xF0 and len(p) >= 1:  # SCFL
            self.font_id = p[0]
        elif t == 0x74 and len(p) >= 2:  # STC
            self.color = _STC_COLORS.get(_u16(p), DEFAULT_COLOR)
        elif t == 0x80:  # SEC
            self.color = _sec_color(p) or self.color
        elif t == 0xF6:  # STO resets coordinates with the new orientation
            self.i = 0
            self.b = 0
        elif t == 0xDA:  # TRN
            text = _decode_trn(p)
            info = self.fonts.get(self.font_id, FontInfo())
            size = info.size or DEFAULT_FONT_SIZE
            if text.strip():
                page.texts.append(
                    TextRun(
                        x=self.i,
                        y=self.b,
                        text=text,
                        color=self.color,
                        font_id=self.font_id,
                        font_size=size,
                        font_family=info.family,
                        font_weight=info.weight,
                    )
                )
            self.i += int(len(text) * size * _CHAR_ADVANCE_RATIO)
        elif t == 0xE4 and len(p) >= 2:  # DIR: horizontal rule
            page.rules.append(self._rule(p, axis="I"))
        elif t == 0xE6 and len(p) >= 2:  # DBR: vertical rule
            page.rules.append(self._rule(p, axis="B"))

    def _rule(self, p: bytes, axis: str) -> Rule:
        length = _s16(p)
        thickness = _s16(p, 2) if len(p) >= 4 else 20
        if abs(thickness) < 10:  # keep hairlines visible once scaled
            thickness = 10 if thickness >= 0 else -10
        return Rule(
            x=self.i,
            y=self.b,
            length=length,
            thickness=thickness,
            axis=axis,
            color=self.color,
        )


def _estimate_font_sizes(page: Page, known_fonts: Dict[int, FontInfo]) -> None:
    """Set each text run's font size from observed inter-run spacing.

    Fallback for fonts whose size was not declared by an MDR descriptor:
    most producers position every run with an explicit move, so the gap
    between two consecutive runs on the same baseline, divided by the
    first run's character count (+1 for the implied space), approximates
    that font's character width — and Latin text averages roughly half
    the point size.
    """
    sized = {fid for fid, info in known_fonts.items() if info.size}
    samples: dict = {}
    for a, b in zip(page.texts, page.texts[1:]):
        if a.y == b.y and b.x > a.x and a.text and a.font_id not in sized:
            per_char = (b.x - a.x) / (len(a.text) + 1)
            samples.setdefault(a.font_id, []).append(per_char)
    for run in page.texts:
        if run.font_id in sized:
            continue
        widths = samples.get(run.font_id)
        if widths:
            widths.sort()
            median = widths[len(widths) // 2]
            run.font_size = max(80, min(1200, int(median / 0.52)))


def _parse_pgd(data: bytes) -> Tuple[int, int, int]:
    """Return (width, height, units_per_inch) from PGD field data."""
    # XpgBase(1) YpgBase(1) XpgUnits(2) YpgUnits(2) XpgSize(3) YpgSize(3)
    units = _u16(data, 2)
    width = int.from_bytes(data[6:9], "big")
    height = int.from_bytes(data[9:12], "big")
    units_per_inch = units // 10 if units else 1440  # unit base 00 = 10 in
    return width, height, units_per_inch


def extract_pages(fields: List[StructuredField]) -> List[Page]:
    """Walk a parsed document and build a rough page model per BPG...EPG.

    This is a first-pass renderer's view: text runs and rules with
    positions and colors, default font metrics, no IOCA/GOCA content yet.
    """
    pages: List[Page] = []
    current: Optional[Page] = None
    state: Optional[_TextState] = None
    pgd_default: Optional[Tuple[int, int, int]] = None
    fonts: Dict[int, FontInfo] = {}

    for f in fields:
        if f.sf_id == 0xD3A8AF:  # BPG
            current = Page()
            if pgd_default:
                current.width, current.height, current.units_per_inch = pgd_default
            state = _TextState(fonts)
        elif f.sf_id == 0xD3A9AF:  # EPG
            if current is not None:
                _estimate_font_sizes(current, fonts)
                pages.append(current)
            current, state = None, None
        elif f.sf_id == 0xD3ABC3:  # MDR maps fonts to SCFL local ids
            fonts.update(parse_mdr_fonts(f.data))
        elif f.sf_id == 0xD3A6AF and len(f.data) >= 12:  # PGD
            parsed = _parse_pgd(f.data)
            if current is not None:
                current.width, current.height, current.units_per_inch = parsed
            else:
                pgd_default = parsed
        elif f.sf_id == 0xD3EE9B and current is not None and state is not None:
            for cs in iter_control_sequences(f.data):
                state.apply(cs, current)
    return pages
