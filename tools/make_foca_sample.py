"""Build a standalone FOCA raster-font resource AFP for the specimen demo.

Usage:
    python tools/make_foca_sample.py [output_path]

Reads the embedded raster fonts from testdata/Sample Files/Sample 1.afp,
keeps the ones rich enough to make an interesting specimen, and writes
them as a font-resource file (BDT wrapping BFN...EFN brackets, no pages).
Opening it in readAFP renders a specimen sheet of the actual glyph
bitmaps stored in the font.

Sample 1.afp is a TrueType/raster AFP fixture distributed in the project
test corpus.
"""

import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from readafp.foca import BFN, EFN, parse_fonts  # noqa: E402
from readafp.parser import iter_fields  # noqa: E402

SOURCE = Path("testdata/Sample Files/Sample 1.afp")
MIN_GLYPHS = 20  # keep only fonts with a meaty glyph set


def _sf(sf_id: int, data: bytes = b"") -> bytes:
    body = sf_id.to_bytes(3, "big") + b"\x00\x00\x00" + data
    return b"\x5a" + struct.pack(">H", len(body) + 2) + body


def build_afp() -> bytes:
    raw = SOURCE.read_bytes()
    fields = list(iter_fields(raw))

    # Collect each BFN...EFN bracket as its raw byte span.
    brackets = []
    start = None
    for f in fields:
        if f.sf_id == BFN:
            start = f.offset
        elif f.sf_id == EFN and start is not None:
            end = f.offset + 9 + len(f.data)  # 0x5A + len(2) + id(3) +
            brackets.append((start, end))      # flags(1) + seq(2) + data
            start = None

    out = bytearray()
    out += _sf(0xD3A8A8, b"FONTRES\x00")  # BDT (Begin Document)
    kept = 0
    for s, e in brackets:
        span = raw[s:e]
        fonts = parse_fonts(list(iter_fields(span)))
        if fonts and fonts[0].is_raster and len(fonts[0].glyphs) >= MIN_GLYPHS:
            out += span
            kept += 1
    out += _sf(0xD3A9A8, b"FONTRES\x00")  # EDT (End Document)
    if not kept:
        raise SystemExit("no raster fonts with enough glyphs found")
    print(f"kept {kept} raster font(s)")
    return bytes(out)


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(
        "testdata/foca_sample.afp"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    data = build_afp()
    out.write_bytes(data)
    print(f"Wrote {len(data):,} bytes -> {out}")
