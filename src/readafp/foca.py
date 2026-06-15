"""FOCA (Font Object Content Architecture) raster-font decoding.

An AFP font character set is bracketed by BFN...EFN and carries the
actual glyph shapes. For raster (bitmap) fonts the relevant fields are:

    FNC (Font Control)       resolution, pattern-data alignment
    FND (Font Descriptor)    typeface description name
    FNI (Font Index)         per character: GCGID, increment, FNM index
    FNM (Font Patterns Map)  per pattern: box width/height, data offset
    FNG (Font Patterns)      the concatenated 1-bit-per-pel bitmaps

This module groups those fields into Font objects whose glyph bitmaps
are rebuilt as PNGs (dark pel on white). Outline fonts (Type 1 / CID)
carry vendor shape data we do not interpret; they parse to metrics-only
Font objects with no glyph images.

Reference: Font Object Content Architecture Reference, AFPC-0001-06
(docs/specs/foca-reference-06.pdf), structured fields FNC/FNI/FNM/FNG.
"""

import logging
import struct
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from readafp.ioca import pack_png
from readafp.parser import StructuredField

logger = logging.getLogger(__name__)

# FOCA structured-field identifiers (category X'89').
BFN = 0xD3A889  # Begin Font
EFN = 0xD3A989  # End Font
FNC = 0xD3A789  # Font Control
FND = 0xD3A689  # Font Descriptor
FNI = 0xD38C89  # Font Index
FNM = 0xD3A289  # Font Patterns Map
FNG = 0xD3EE89  # Font Patterns

# FNC byte 1, Pattern Technology Identifier.
PATTECH_RASTER = 0x05  # Laser Matrix N-bit Wide (bitmap glyphs)

# Pattern-data alignment factor, keyed by FNC byte 16 (PatAlign).
_ALIGN = {0x00: 1, 0x02: 4, 0x03: 8}

# Bounds so a pathological font cannot blow up the render.
_MAX_GLYPHS = 1024
_MAX_PATTERN_BYTES = 1 << 20  # 1 MiB per glyph bitmap


@dataclass
class Glyph:
    """One raster glyph rebuilt as a PNG bitmap."""

    gcgid: str  # Graphic Character Global Identifier, e.g. "LF010000"
    width: int  # pels
    height: int
    char_increment: int  # inline advance, in the font's metric units
    png: bytes  # 1-bit grayscale PNG, dark pel on white


@dataclass
class Font:
    """One BFN...EFN font character set."""

    name: str  # BFN token name
    typeface: str  # FND descriptive name, e.g. "TIMES-ROMAN"
    pattern_tech: int  # FNC PatTech
    glyphs: List[Glyph] = field(default_factory=list)

    @property
    def is_raster(self) -> bool:
        return self.pattern_tech == PATTECH_RASTER


def _decode_name(raw: bytes) -> str:
    """Decode an EBCDIC name field, trimmed to its printable run."""
    try:
        text = raw.decode("cp500")
    except UnicodeDecodeError:
        return ""
    out = []
    for ch in text:
        if ch.isprintable() and ch not in "\x00":
            out.append(ch)
        else:
            break
    return "".join(out).strip()


def _glyph_png(pattern: bytes, width: int, height: int) -> Optional[bytes]:
    """Build a 1-bpp PNG from a FOCA raster pattern (1 = toned/dark).

    PNG bit-depth-1 grayscale treats 0 as black, so each pel bit is
    inverted: a toned FOCA pel becomes a cleared (dark) PNG bit.
    """
    row_bytes = (width + 7) // 8
    if row_bytes * height > _MAX_PATTERN_BYTES:
        return None
    out = bytearray(b"\xff" * row_bytes * height)
    for ry in range(height):
        src = ry * row_bytes
        for col in range(row_bytes):
            i = src + col
            if i < len(pattern):
                out[ry * row_bytes + col] = pattern[i] ^ 0xFF
    return pack_png(width, height, 1, 0, row_bytes, bytes(out))


def _decode_raster_glyphs(
    fnc: bytes, fni: bytes, fnm: bytes, fng: bytes
) -> List[Glyph]:
    """Decode raster glyphs by joining FNI metrics to FNM/FNG patterns."""
    align = _ALIGN.get(fnc[16], 1) if len(fnc) > 16 else 1
    fni_rg = fnc[15] if len(fnc) > 15 else 28

    # FNM index -> GCGID/increment, from the Font Index.
    by_pattern: Dict[int, tuple] = {}
    if fni_rg >= 18:
        for i in range(0, len(fni) - fni_rg + 1, fni_rg):
            gcgid = _decode_name(fni[i : i + 8])
            char_inc = struct.unpack(">H", fni[i + 8 : i + 10])[0]
            fnm_index = struct.unpack(">H", fni[i + 16 : i + 18])[0]
            by_pattern.setdefault(fnm_index, (gcgid, char_inc))

    glyphs: List[Glyph] = []
    count = len(fnm) // 8
    for idx in range(min(count, _MAX_GLYPHS)):
        off = idx * 8
        box_w = struct.unpack(">H", fnm[off : off + 2])[0] + 1
        box_h = struct.unpack(">H", fnm[off + 2 : off + 4])[0] + 1
        pat_off = struct.unpack(">I", fnm[off + 4 : off + 8])[0] * align
        row_bytes = (box_w + 7) // 8
        pattern = fng[pat_off : pat_off + row_bytes * box_h]
        png = _glyph_png(pattern, box_w, box_h)
        if png is None:
            continue
        gcgid, char_inc = by_pattern.get(idx, ("", 0))
        glyphs.append(
            Glyph(
                gcgid=gcgid,
                width=box_w,
                height=box_h,
                char_increment=char_inc,
                png=png,
            )
        )
    return glyphs


def parse_fonts(fields: List[StructuredField]) -> List[Font]:
    """Extract every BFN...EFN font character set from a parsed file.

    Raster fonts gain decoded glyph bitmaps; outline fonts parse to a
    metrics-only Font (typeface name and technology, no glyph images).
    """
    fonts: List[Font] = []
    name = ""
    fnc = fnd = b""
    fni = fnm = fng = b""
    in_font = False

    for f in fields:
        if f.sf_id == BFN:
            in_font = True
            name = _decode_name(f.data[:8])
            fnc = fnd = b""
            fni = fnm = fng = b""
        elif not in_font:
            continue
        elif f.sf_id == FNC:
            fnc = f.data
        elif f.sf_id == FND:
            fnd = f.data
        elif f.sf_id == FNI:
            fni += f.data
        elif f.sf_id == FNM:
            fnm += f.data
        elif f.sf_id == FNG:
            fng += f.data
        elif f.sf_id == EFN:
            in_font = False
            tech = fnc[1] if len(fnc) > 1 else 0
            typeface = _decode_name(fnd[:32]) if fnd else ""
            typeface = typeface.split("@")[0].strip()  # drop GRID suffix
            glyphs: List[Glyph] = []
            if tech == PATTECH_RASTER and fnm and fng:
                try:
                    glyphs = _decode_raster_glyphs(fnc, fni, fnm, fng)
                except (struct.error, IndexError) as exc:
                    logger.warning("FOCA glyph decode failed for %s: %s",
                                   name, exc)
            fonts.append(
                Font(
                    name=name,
                    typeface=typeface,
                    pattern_tech=tech,
                    glyphs=glyphs,
                )
            )
    return fonts
