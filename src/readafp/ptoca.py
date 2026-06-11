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

# Cap content per page so pathological files (perf_ptx.afp carries 65k
# PTX fields in one unbracketed document) produce a bounded SVG.
MAX_RUNS_PER_PAGE = 5000


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
    src: Optional[int] = None  # offset of the PTX field that produced it


@dataclass
class ImageRef:
    """A raster image placed on a page, in page L-units."""

    x: int
    y: int
    width: int
    height: int
    mime: str
    data: bytes


@dataclass
class Rule:
    """A solid rule (line) on a page, in page L-units."""

    x: int
    y: int
    length: int
    thickness: int
    axis: str  # "I" (horizontal) or "B" (vertical)
    color: str = DEFAULT_COLOR
    src: Optional[int] = None  # offset of the PTX field that produced it


@dataclass
class Page:
    """One page's geometry and rough presentation-text content."""

    width: int = 12240  # letter at 1440/inch
    height: int = 15840
    units_per_inch: int = 1440
    texts: List[TextRun] = field(default_factory=list)
    rules: List[Rule] = field(default_factory=list)
    images: List[ImageRef] = field(default_factory=list)
    truncated: bool = False  # content dropped after MAX_RUNS_PER_PAGE

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


def _decode_trn(params: bytes, codepage: str = "cp500") -> str:
    """Decode TRN text bytes: UTF-16BE for TrueType flows, else EBCDIC.

    ``codepage`` selects the EBCDIC decoder ring (user override until
    MCF label support lands). The UTF-16BE heuristic stays: text over
    Latin scripts has a zero high byte for nearly every character.
    """
    if len(params) >= 2 and len(params) % 2 == 0:
        high_zeros = sum(1 for b in params[0::2] if b == 0)
        if high_zeros >= len(params) // 2 * 0.8:
            try:
                return params.decode("utf-16-be")
            except UnicodeDecodeError:
                pass
    try:
        return params.decode(codepage)
    except (UnicodeDecodeError, LookupError):
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

    def __init__(
        self,
        fonts: Optional[Dict[int, FontInfo]] = None,
        codepage: str = "cp500",
    ) -> None:
        self.codepage = codepage
        self.i = 0
        self.b = 0
        self.inline_margin = 0
        self.baseline_increment = 0
        self.color = DEFAULT_COLOR
        self.font_id: Optional[int] = None
        self.field_offset: Optional[int] = None
        # For implicit pages (no BPG/EPG): flow text and wrap at this
        # inline position, like a text dump, instead of letting runs
        # without explicit moves pile up on one endless line.
        self.wrap_width: Optional[int] = None
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
            text = _decode_trn(p, self.codepage)
            info = self.fonts.get(self.font_id, FontInfo())
            size = info.size or DEFAULT_FONT_SIZE
            if (
                self.wrap_width is not None
                and self.i > 0
                and self.i + len(text) * size * _CHAR_ADVANCE_RATIO
                > self.wrap_width
            ):
                self.i = 240
                self.b += int(size * 1.4)
            if text.strip():
                if len(page.texts) < MAX_RUNS_PER_PAGE:
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
                            src=self.field_offset,
                        )
                    )
                else:
                    page.truncated = True
            self.i += int(len(text) * size * _CHAR_ADVANCE_RATIO)
        elif t == 0xE4 and len(p) >= 2:  # DIR: horizontal rule
            self._add_rule(page, p, axis="I")
        elif t == 0xE6 and len(p) >= 2:  # DBR: vertical rule
            self._add_rule(page, p, axis="B")

    def _add_rule(self, page: Page, p: bytes, axis: str) -> None:
        if len(page.rules) >= MAX_RUNS_PER_PAGE:
            page.truncated = True
            return
        length = _s16(p)
        thickness = _s16(p, 2) if len(p) >= 4 else 20
        if abs(thickness) < 10:  # keep hairlines visible once scaled
            thickness = 10 if thickness >= 0 else -10
        page.rules.append(
            Rule(
                x=self.i,
                y=self.b,
                length=length,
                thickness=thickness,
                axis=axis,
                color=self.color,
                src=self.field_offset,
            )
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


_IMAGE_MAGICS = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG", "image/png"),
    (b"GIF8", "image/gif"),
)


def _sniff_image(data: bytes) -> Optional[str]:
    """Return the MIME type if the bytes look like a known raster format."""
    for magic, mime in _IMAGE_MAGICS:
        if data.startswith(magic):
            return mime
    return None


def _parse_iob(data: bytes, resources: Dict[str, bytes]) -> Optional[ImageRef]:
    """Build an ImageRef from an IOB (Include Object) field, if renderable.

    IOB layout: name(8) reserved(1) ObjType(1) XoaOset(3) YoaOset(3)
    orientation(4) XocaOset(3) YocaOset(3) RefCSys(1), then triplets —
    of which 0x4C (Object Area Size) carries the placed extent.
    """
    if len(data) < 27:
        return None
    try:
        name = data[:8].decode("cp500").strip()
    except UnicodeDecodeError:
        return None
    blob = resources.get(name)
    if blob is None:
        return None
    mime = _sniff_image(blob)
    if mime is None:
        return None
    x = int.from_bytes(data[10:13], "big", signed=True)
    y = int.from_bytes(data[13:16], "big", signed=True)
    width = height = 0
    for tid, tdata in iter_triplets(data[27:]):
        if tid == 0x4C and len(tdata) >= 7:  # Object Area Size
            width = int.from_bytes(tdata[1:4], "big")
            height = int.from_bytes(tdata[4:7], "big")
    if width <= 0 or height <= 0:
        return None
    return ImageRef(x=x, y=y, width=width, height=height, mime=mime, data=blob)


def _parse_pgd(data: bytes) -> Tuple[int, int, int]:
    """Return (width, height, units_per_inch) from PGD field data."""
    # XpgBase(1) YpgBase(1) XpgUnits(2) YpgUnits(2) XpgSize(3) YpgSize(3)
    units = _u16(data, 2)
    width = int.from_bytes(data[6:9], "big")
    height = int.from_bytes(data[9:12], "big")
    units_per_inch = units // 10 if units else 1440  # unit base 00 = 10 in
    return width, height, units_per_inch


def extract_pages(
    fields: List[StructuredField], codepage: str = "cp500"
) -> List[Page]:
    """Walk a parsed document and build a rough page model per BPG...EPG.

    PTX fields outside any page bracket (some synthetic files put text
    directly under BDT) are collected onto one implicit page, appended
    after the bracketed pages and grown to fit its content.

    This is a first-pass renderer's view: text runs and rules with
    positions and colors, default font metrics, no IOCA/GOCA content yet.
    """
    pages: List[Page] = []
    current: Optional[Page] = None
    state: Optional[_TextState] = None
    implicit: Optional[Page] = None
    implicit_state: Optional[_TextState] = None
    pgd_default: Optional[Tuple[int, int, int]] = None
    fonts: Dict[int, FontInfo] = {}
    resources: Dict[str, bytes] = {}
    container: Optional[str] = None

    for f in fields:
        if f.sf_id == 0xD3A892:  # BOC opens an object container resource
            container = f.token_name
        elif f.sf_id == 0xD3A992:  # EOC
            container = None
        elif f.sf_id == 0xD3EE92 and container:  # OCD carries its bytes
            resources[container] = resources.get(container, b"") + f.data
        elif f.sf_id == 0xD3AFC3 and current is not None:  # IOB places one
            image = _parse_iob(f.data, resources)
            if image is not None:
                current.images.append(image)
        elif f.sf_id == 0xD3A8AF:  # BPG
            current = Page()
            if pgd_default:
                current.width, current.height, current.units_per_inch = pgd_default
            state = _TextState(fonts, codepage)
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
        elif f.sf_id == 0xD3EE9B:  # PTX
            if current is None:
                if implicit is None:
                    implicit = Page()
                    if pgd_default:
                        (
                            implicit.width,
                            implicit.height,
                            implicit.units_per_inch,
                        ) = pgd_default
                    implicit_state = _TextState(fonts, codepage)
                    implicit_state.wrap_width = implicit.width - 480
                    implicit_state.i = 240
                    implicit_state.b = 320
                target, target_state = implicit, implicit_state
            else:
                target, target_state = current, state
            target_state.field_offset = f.offset
            for cs in iter_control_sequences(f.data):
                target_state.apply(cs, target)

    if implicit is not None and (implicit.texts or implicit.rules):
        _estimate_font_sizes(implicit, fonts)
        pages.extend(_paginate_implicit(implicit))
    return pages


def _paginate_implicit(page: Page) -> List[Page]:
    """Split an implicit page's flowed content into page-height chunks."""
    xs = [r.x for r in page.texts] + [r.x for r in page.rules]
    if xs:  # explicitly positioned content may still exceed the width
        page.width = max(page.width, max(xs) + 6 * DEFAULT_FONT_SIZE)
    usable = page.height - 320
    chunks: Dict[int, Page] = {}
    for kind in ("texts", "rules"):
        for item in getattr(page, kind):
            idx = item.y // usable
            chunk = chunks.setdefault(
                idx,
                Page(
                    width=page.width,
                    height=page.height,
                    units_per_inch=page.units_per_inch,
                ),
            )
            item.y -= idx * usable
            getattr(chunk, kind).append(item)
    ordered = [chunks[idx] for idx in sorted(chunks)]
    if ordered and page.truncated:
        ordered[-1].truncated = True
    return ordered
