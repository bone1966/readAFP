"""Tests for BCOCA bar code decoding and page placement."""

from pathlib import Path

import pytest

from readafp.bcoca import TYPE_QR, barcode_png, parse_barcode
from readafp.parser import parse_file
from readafp.ptoca import extract_pages
from readafp.render import page_to_svg

TESTDATA = Path(__file__).parent.parent / "testdata"
QR_SAMPLE = TESTDATA / "alpheus-corpus" / "external" / "afplib_ende.afp"

# The BDD/BDA bytes of the afplib QR example (the corpus's only BCOCA).
BDD = bytes.fromhex("00000bb80bb8ffffffff00001c00ff000010ffff01ffff")
BDA = bytes.fromhex("8000010001000012001200000000 00".replace(" ", "")) + (
    b"0010010100641000055100000000000000"
)


def test_parse_barcode_fixture_fields() -> None:
    bar = parse_barcode(BDD, BDA)
    assert bar.bc_type == TYPE_QR
    assert bar.upi == 300
    assert bar.module_mils == 16
    assert bar.version == 18
    assert bar.ec_level == 0  # level L
    assert (bar.x, bar.y) == (1, 1)
    assert bar.data == "0010010100641000055100000000000000"


def test_barcode_png_generates_requested_version() -> None:
    png, modules = barcode_png(parse_barcode(BDD, BDA))
    assert modules == 89  # QR version 18 is 89x89 modules
    assert png.startswith(b"\x89PNG")


def test_barcode_png_grows_when_version_too_small() -> None:
    bar = parse_barcode(BDD, BDA)
    bar.version = 1  # 21x21 cannot hold 34 digits at this EC level... it
    bar.data = "9" * 500  # ...can; 500 digits definitely overflows it
    png, modules = barcode_png(bar)
    assert modules > 21


def test_barcode_png_rejects_unknown_symbology() -> None:
    bar = parse_barcode(BDD, BDA)
    bar.bc_type = 0x01  # Code 39: not generated, must not draw wrong
    assert barcode_png(bar) is None


def test_parse_barcode_suppressed_symbol() -> None:
    suppressed = bytes([BDA[0] | 0x04]) + BDA[1:]
    assert parse_barcode(BDD, suppressed) is None


def test_qr_sample_placed_on_page() -> None:
    if not QR_SAMPLE.exists():
        pytest.skip("test corpus not present")
    pages = extract_pages(parse_file(str(QR_SAMPLE)))
    images = [img for page in pages for img in page.images]
    assert len(images) == 1
    qr = images[0]
    assert qr.crisp
    # OBP puts the object area at (45, 2220) in 240 upi page units; the
    # symbol is 89 modules of 16 mils = 1.424 inches = 342 units square.
    assert (qr.x, qr.y) == (45, 2220)
    assert (qr.width, qr.height) == (342, 342)


def test_qr_sample_svg_renders_pixelated() -> None:
    if not QR_SAMPLE.exists():
        pytest.skip("test corpus not present")
    pages = extract_pages(parse_file(str(QR_SAMPLE)))
    page = next(p for p in pages if p.images)
    svg = page_to_svg(page)
    assert "image-rendering:pixelated" in svg
    assert 'href="data:image/png;base64,' in svg
