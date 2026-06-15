"""Tests for FOCA raster-font decoding and specimen rendering."""

import struct
from pathlib import Path

import pytest

from readafp.foca import PATTECH_RASTER, parse_fonts
from readafp.parser import iter_fields, parse_file
from readafp.ptoca import extract_pages
from readafp.render import page_to_svg

TESTDATA = Path(__file__).parent.parent / "testdata"
SAMPLE1 = TESTDATA / "Sample Files" / "Sample 1.afp"
OUTLINE = TESTDATA / "github-samples" / "afplib" / "C0X00006.afp"
FOCA_SAMPLE = TESTDATA / "foca_sample.afp"


def _png_dims(png: bytes) -> tuple:
    assert png.startswith(b"\x89PNG")
    return struct.unpack(">II", png[16:24])


def test_parse_raster_fonts_from_sample1() -> None:
    if not SAMPLE1.exists():
        pytest.skip("Sample 1 not present")
    fonts = parse_fonts(parse_file(str(SAMPLE1)))
    raster = [f for f in fonts if f.is_raster and f.glyphs]
    assert raster, "no raster fonts decoded"
    times = max(raster, key=lambda f: len(f.glyphs))
    assert times.pattern_tech == PATTECH_RASTER
    assert "TIMES" in times.typeface.upper()
    # Glyphs carry a GCGID, an advance and a real bitmap.
    g = next(g for g in times.glyphs if g.gcgid == "LF010000")  # 'f'
    assert g.char_increment > 0
    w, h = _png_dims(g.png)
    assert (w, h) == (g.width, g.height) and w > 1 and h > 1


def test_outline_font_has_no_glyph_bitmaps() -> None:
    if not OUTLINE.exists():
        pytest.skip("outline font fixture not present")
    fonts = parse_fonts(parse_file(str(OUTLINE)))
    assert fonts and not fonts[0].is_raster
    assert fonts[0].glyphs == []  # Type 1 outline data is not rasterized
    assert fonts[0].typeface  # descriptor name still decoded


def test_typeface_name_strips_grid_suffix() -> None:
    if not SAMPLE1.exists():
        pytest.skip("Sample 1 not present")
    fonts = parse_fonts(parse_file(str(SAMPLE1)))
    assert all("@" not in f.typeface for f in fonts)


def test_font_resource_renders_specimen_pages() -> None:
    if not FOCA_SAMPLE.exists():
        pytest.skip("FOCA sample not generated")
    pages = extract_pages(list(iter_fields(FOCA_SAMPLE.read_bytes())))
    assert len(pages) >= 1
    page = pages[0]
    # A specimen page is a title run plus one image per glyph.
    assert page.images
    assert len(page.texts) == len(page.images) + 1
    assert page.texts[0].text.startswith("Embedded raster font:")
    svg = page_to_svg(page)
    assert "data:image/png;base64," in svg


def test_no_fonts_yields_no_specimen() -> None:
    # A document with a real page must not be hijacked by the specimen path.
    sf = lambda i, d=b"": b"\x5a" + struct.pack(
        ">H", len(d) + 8
    ) + i.to_bytes(3, "big") + b"\x00\x00\x00" + d
    ptx = bytes.fromhex("2bd3") + bytes([3, 0xDA]) + b"\xc1"
    doc = sf(0xD3A8A8) + sf(0xD3A8AF) + sf(0xD3EE9B, ptx) + sf(
        0xD3A9AF
    ) + sf(0xD3A9A8)
    pages = extract_pages(list(iter_fields(doc)))
    assert len(pages) == 1
    assert pages[0].texts[0].text == "A"  # normal render, not a specimen
