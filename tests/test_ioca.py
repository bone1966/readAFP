"""Tests for IOCA image segment decoding and page placement."""

import struct
import zlib
from pathlib import Path

import pytest

from readafp.ioca import (
    cmyk_jpeg_bands,
    image_blob,
    iter_sdfs,
    parse_image_segment,
)
from readafp.parser import parse_file
from readafp.ptoca import extract_pages
from readafp.render import page_to_svg

TESTDATA = Path(__file__).parent.parent / "testdata"
FOP_IMAGES = TESTDATA / "fop-pairs" / "images.afp"
BIM_SAMPLE = TESTDATA / "github-samples" / "afplib" / "bim.afp"
IPD_SPAN = TESTDATA / "github-samples" / "afplib" / "IPDSpan.afp"
RESOURCE_ONLY = TESTDATA / "github-samples" / "fop" / "resource_name_match.afp"


def _segment(
    width: int,
    height: int,
    bits: int,
    pixel_data: bytes,
    compression: int = 0x03,
) -> bytes:
    """Build a minimal IOCA image segment around the given pixel data."""
    return (
        bytes.fromhex("7000" "9101ff")
        + bytes([0x94, 9, 0])
        + struct.pack(">HHHH", 720, 720, width, height)
        + bytes([0x95, 2, compression, 0x01])
        + bytes([0x96, 1, bits])
        + b"\xfe\x92"
        + struct.pack(">H", len(pixel_data))
        + pixel_data
        + bytes.fromhex("9300" "7100")
    )


def _png_size(blob: bytes) -> tuple:
    assert blob.startswith(b"\x89PNG")
    return struct.unpack(">II", blob[16:24])


def _png_pixels(blob: bytes) -> bytes:
    """Inflate the IDAT raster (single chunk, as our encoder writes it)."""
    pos = 8
    raster = b""
    while pos < len(blob):
        (length,) = struct.unpack(">I", blob[pos : pos + 4])
        tag = blob[pos + 4 : pos + 8]
        if tag == b"IDAT":
            raster += blob[pos + 8 : pos + 8 + length]
        pos += 12 + length
    return zlib.decompress(raster)


def test_iter_sdfs_long_and_extended() -> None:
    data = bytes.fromhex("9101ff") + b"\xfe\x92\x00\x03abc" + b"\x93\x00"
    sdfs = list(iter_sdfs(data))
    assert sdfs == [(0x91, b"\xff"), (0xFE92, b"abc"), (0x93, b"")]


def test_iter_sdfs_zero_length_image_data_takes_rest() -> None:
    # afplib's IPDSpan fixture writes FE92 0000 and carries the pixel
    # bytes in the next IPD field with no further framing.
    data = b"\x96\x01\x01" + b"\xfe\x92\x00\x00" + b"\x01\x02\x03\x04"
    sdfs = dict(iter_sdfs(data))
    assert sdfs[0xFE92] == b"\x01\x02\x03\x04"


def test_parse_image_segment_parameters() -> None:
    img = parse_image_segment(_segment(4, 2, 8, bytes(8)))
    assert (img.width, img.height) == (4, 2)
    assert (img.hres, img.vres) == (720, 720)
    assert img.compression == 0x03
    assert img.bits == 8
    assert img.data == bytes(8)


def test_parse_image_segment_concatenates_data_fields() -> None:
    seg = (
        b"\x95\x02\x03\x01"
        + b"\xfe\x92\x00\x02ab"
        + b"\xfe\x92\x00\x02cd"
    )
    assert parse_image_segment(seg).data == b"abcd"


def test_parse_image_segment_bands_strip_headers() -> None:
    # Band Image Data: BANDNUM(1) reserved(2) then data; continuation
    # SDFs for the same band repeat the header.
    seg = (
        b"\x95\x02\x83\x01"
        + b"\xfe\x9c\x00\x05\x01\x00\x00ab"
        + b"\xfe\x9c\x00\x05\x01\x00\x00cd"
        + b"\xfe\x9c\x00\x05\x02\x00\x00ef"
    )
    img = parse_image_segment(seg)
    assert img.bands == {1: b"abcd", 2: b"ef"}


def test_image_blob_bilevel_inverts_to_png() -> None:
    # 8x2: all-ones rows (IOCA 1 = black mark) -> PNG gray 0 = black.
    img = parse_image_segment(_segment(8, 2, 1, b"\xff\xff"))
    mime, blob = image_blob(img)
    assert mime == "image/png"
    assert _png_size(blob) == (8, 2)
    # Filter byte 0 then one raster byte per row; black = 0x00.
    assert _png_pixels(blob) == b"\x00\x00\x00\x00"


def test_image_blob_grayscale_png() -> None:
    img = parse_image_segment(_segment(2, 2, 8, bytes([0, 85, 170, 255])))
    mime, blob = image_blob(img)
    assert mime == "image/png"
    assert _png_size(blob) == (2, 2)
    assert _png_pixels(blob) == b"\x00\x00\x55\x00\xaa\xff"


def test_image_blob_jpeg_passthrough() -> None:
    jpeg = b"\xff\xd8\xff\xe0fake-jpeg"
    img = parse_image_segment(_segment(2, 2, 24, jpeg, compression=0x83))
    assert image_blob(img) == ("image/jpeg", jpeg)


def test_image_blob_rejects_unknown_compression() -> None:
    img = parse_image_segment(_segment(8, 2, 1, b"\xff\xff", compression=0x82))
    assert image_blob(img) is None


def test_image_blob_rejects_short_data() -> None:
    img = parse_image_segment(_segment(100, 100, 8, bytes(10)))
    assert image_blob(img) is None


def test_cmyk_jpeg_bands_requires_four_jpeg_planes() -> None:
    img = parse_image_segment(
        b"\x95\x02\x83\x01"
        + b"".join(
            b"\xfe\x9c\x00\x07" + bytes([n, 0, 0]) + b"\xff\xd8jp"
            for n in (1, 2, 3, 4)
        )
    )
    bands = cmyk_jpeg_bands(img)
    assert bands == [b"\xff\xd8jp"] * 4
    img.bands.pop(4)
    assert cmyk_jpeg_bands(img) is None


def test_fop_pairs_images_placed_on_pages() -> None:
    if not FOP_IMAGES.exists():
        pytest.skip("test corpus not present")
    pages = extract_pages(parse_file(str(FOP_IMAGES)))
    assert len(pages) == 5
    assert len(pages[0].images) == 5
    first = pages[0].images[0]
    # IOB places RES00001 at (706, 460); its 737x320 object area is
    # declared in the same 240 upi units as the page.
    assert (first.x, first.y) == (706, 460)
    assert (first.width, first.height) == (737, 320)
    assert first.mime == "image/png"
    assert _png_size(first.data) == (221, 96)


def test_ipdspan_standalone_object_becomes_page() -> None:
    if not IPD_SPAN.exists():
        pytest.skip("test corpus not present")
    pages = extract_pages(parse_file(str(IPD_SPAN)))
    assert len(pages) == 1
    assert (pages[0].width, pages[0].height) == (1344, 30)
    assert pages[0].images[0].mime == "image/png"
    assert _png_size(pages[0].images[0].data) == (1344, 30)


def test_resource_only_file_shows_its_image() -> None:
    if not RESOURCE_ONLY.exists():
        pytest.skip("test corpus not present")
    pages = extract_pages(parse_file(str(RESOURCE_ONLY)))
    assert len(pages) == 1
    assert _png_size(pages[0].images[0].data) == (608, 200)


def test_bim_cmyk_planes_render_as_composite() -> None:
    if not BIM_SAMPLE.exists():
        pytest.skip("test corpus not present")
    pages = extract_pages(parse_file(str(BIM_SAMPLE)))
    assert len(pages) == 1
    img = pages[0].images[0]
    assert img.bands is not None and len(img.bands) == 4
    assert all(b.startswith(b"\xff\xd8") for b in img.bands)
    svg = page_to_svg(pages[0])
    assert svg.count("<image") == 4
    assert 'filter="url(#ink-k)"' in svg
    assert "mix-blend-mode:multiply" in svg


def test_plain_image_svg_has_data_url() -> None:
    if not FOP_IMAGES.exists():
        pytest.skip("test corpus not present")
    pages = extract_pages(parse_file(str(FOP_IMAGES)))
    svg = page_to_svg(pages[0])
    assert svg.count('href="data:image/png;base64,') == 5
    assert "ink-c" not in svg  # no CMYK filters when nothing is banded
