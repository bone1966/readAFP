"""Tests for PTOCA decoding, page extraction and SVG rendering."""

from pathlib import Path

import pytest

from readafp.parser import iter_fields, parse_file
from readafp.ptoca import (
    ControlSequence,
    extract_pages,
    iter_control_sequences,
    parse_mdr_fonts,
    _decode_trn,
)
from readafp.render import page_to_svg

TESTDATA = Path(__file__).parent.parent / "testdata"
HEALTH_SAMPLE = TESTDATA / "sample1_health" / "01_Health_Coverage.afp"

# Escape, then a chain: AMI(0x0064) chained -> AMB(0x00C8) chained ->
# TRN("AB" in EBCDIC) unchained.
CHAIN = bytes.fromhex("2bd3" "04c70064" "04d300c8" "04dac1c2")


def test_iter_control_sequences_follows_chaining() -> None:
    seqs = list(iter_control_sequences(CHAIN))
    assert [s.cs_type for s in seqs] == [0xC6, 0xD2, 0xDA]
    assert seqs[0].params == bytes.fromhex("0064")


def test_unchained_sequence_requires_new_escape() -> None:
    # TRN unchained, followed by garbage that is not an escape: stop.
    data = bytes.fromhex("2bd3" "04dac1c2" "04c70064")
    seqs = list(iter_control_sequences(data))
    assert len(seqs) == 1
    assert seqs[0].cs_type == 0xDA


def test_decode_trn_utf16be() -> None:
    assert _decode_trn("John".encode("utf-16-be")) == "John"


def test_decode_trn_ebcdic() -> None:
    assert _decode_trn("John".encode("cp500")) == "John"


def test_control_sequence_names() -> None:
    assert "TRN" in ControlSequence(cs_type=0xDA, params=b"").name
    assert "Unknown" in ControlSequence(cs_type=0x10, params=b"").name


def test_extract_pages_health_sample() -> None:
    if not HEALTH_SAMPLE.exists():
        pytest.skip("test corpus not present")
    pages = extract_pages(parse_file(str(HEALTH_SAMPLE)))
    assert len(pages) == 1
    page = pages[0]
    # Letter at 1440 units/inch, from the PGD.
    assert (page.width, page.height) == (12240, 15840)
    assert page.units_per_inch == 1440
    text = page.plain_text
    assert "John" in text and "Doe" in text
    assert "Health" in text
    # The first PTX draws the page border rules.
    assert page.rules


def test_health_sample_heading_is_colored() -> None:
    if not HEALTH_SAMPLE.exists():
        pytest.skip("test corpus not present")
    page = extract_pages(parse_file(str(HEALTH_SAMPLE)))[0]
    colors = {run.color for run in page.texts}
    assert len(colors) > 1  # heading color differs from body text


def test_page_to_svg_escapes_and_positions() -> None:
    if not HEALTH_SAMPLE.exists():
        pytest.skip("test corpus not present")
    page = extract_pages(parse_file(str(HEALTH_SAMPLE)))[0]
    svg = page_to_svg(page)
    assert svg.startswith("<svg")
    assert 'viewBox="0 0 12240 15840"' in svg
    assert "John" in svg
    assert "&" not in svg.replace("&amp;", "").replace("&lt;", "").replace(
        "&gt;", ""
    ).replace("&quot;", "").replace("&#", "")


def test_mdr_font_mapping_health_sample() -> None:
    if not HEALTH_SAMPLE.exists():
        pytest.skip("test corpus not present")
    fields = parse_file(str(HEALTH_SAMPLE))
    mdr = next(f for f in fields if f.sf_id == 0xD3ABC3)
    fonts = parse_mdr_fonts(mdr.data)
    assert fonts[1].family == "Arial" and fonts[1].weight == "bold"
    assert fonts[2].family == "Segoe UI"
    assert fonts[3].family == "Arial" and fonts[3].weight == "normal"
    # 9pt and 27pt, in L-units (1pt = 20 units at 1440/inch).
    assert fonts[1].size == 180
    assert fonts[2].size == 540


def test_text_runs_carry_mapped_fonts() -> None:
    if not HEALTH_SAMPLE.exists():
        pytest.skip("test corpus not present")
    page = extract_pages(parse_file(str(HEALTH_SAMPLE)))[0]
    john = next(r for r in page.texts if r.text == "John")
    assert john.font_weight == "bold"
    assert john.font_size == 180
    heading = next(r for r in page.texts if "Continuing" in r.text)
    assert heading.font_family == "Segoe UI"
    assert heading.font_size == 540


def test_table_band_sits_behind_white_labels() -> None:
    if not HEALTH_SAMPLE.exists():
        pytest.skip("test corpus not present")
    page = extract_pages(parse_file(str(HEALTH_SAMPLE)))[0]
    options = next(r for r in page.texts if r.text == "Options")
    assert options.color == "#ffffff"
    band = [
        r
        for r in page.rules
        if r.color == "#2196f3" and r.axis == "I" and r.thickness >= 100
    ]
    assert band, "blue header band rules missing"
    # Rules extend downward from their position: the white label's
    # baseline must fall inside the band's vertical span.
    cell = band[0]
    assert cell.y <= options.y <= cell.y + cell.thickness


def test_object_container_jpeg_is_placed() -> None:
    if not HEALTH_SAMPLE.exists():
        pytest.skip("test corpus not present")
    page = extract_pages(parse_file(str(HEALTH_SAMPLE)))[0]
    assert len(page.images) == 1
    logo = page.images[0]
    assert logo.mime == "image/jpeg"
    assert logo.data.startswith(b"\xff\xd8\xff")
    assert (logo.x, logo.y) == (8715, 975)
    assert (logo.width, logo.height) == (2385, 720)
    svg = page_to_svg(page)
    assert "data:image/jpeg;base64," in svg


def _sf(sf_id: int, data: bytes = b"") -> bytes:
    """Build one structured-field record."""
    body = sf_id.to_bytes(3, "big") + b"\x00\x00\x00" + data
    return b"\x5a" + (len(body) + 2).to_bytes(2, "big") + body


def test_extract_pages_multipage_document() -> None:
    def page(text: str) -> bytes:
        ptx = bytes.fromhex("2bd3" "04c70064" "04d300c8") + bytes(
            [2 + len(text), 0xDA]
        ) + text.encode("cp500")
        return (
            _sf(0xD3A8AF, b"\x00" * 8)  # BPG
            + _sf(0xD3EE9B, ptx)  # PTX
            + _sf(0xD3A9AF, b"\x00" * 8)  # EPG
        )

    doc = (
        _sf(0xD3A8A8, b"\x00" * 8)
        + page("First")
        + page("Second")
        + _sf(0xD3A9A8, b"\x00" * 8)
    )
    pages = extract_pages(list(iter_fields(doc)))
    assert len(pages) == 2
    assert pages[0].texts[0].text == "First"
    assert pages[1].texts[0].text == "Second"
    assert pages[1].texts[0].x == 0x64 and pages[1].texts[0].y == 0xC8


def test_unbracketed_ptx_lands_on_implicit_page() -> None:
    ptx = bytes.fromhex("2bd3" "04c70064" "04d300c8") + bytes(
        [2 + 5, 0xDA]
    ) + "Loose".encode("cp500")
    doc = (
        _sf(0xD3A8A8, b"\x00" * 8)
        + _sf(0xD3EE9B, ptx)
        + _sf(0xD3A9A8, b"\x00" * 8)
    )
    pages = extract_pages(list(iter_fields(doc)))
    assert len(pages) == 1
    assert pages[0].texts[0].text == "Loose"


def test_codepage_override_decodes_ibm273() -> None:
    sample = TESTDATA / "alpheus-corpus" / "large_ibm273.afp"
    if not sample.exists():
        pytest.skip("test corpus not present")
    fields = parse_file(str(sample))
    # The fixture's text is German: "Hällö Wörld" in cp273. cp500 maps
    # those bytes to {/¦ instead.
    garbled = extract_pages(fields, codepage="cp500")[0].texts[0].text
    assert "H{ll" in garbled
    readable = extract_pages(fields, codepage="cp273")[0].texts[0].text
    assert "Hällö Wörld" in readable


def test_implicit_page_corpus_large_ibm273() -> None:
    sample = TESTDATA / "alpheus-corpus" / "large_ibm273.afp"
    if not sample.exists():
        pytest.skip("test corpus not present")
    pages = extract_pages(parse_file(str(sample)))
    # Unpositioned text flows, wraps at the page width and paginates.
    assert len(pages) > 1
    assert all(p.width == 12240 for p in pages)
    assert pages[0].texts
    assert all(0 <= r.y <= pages[0].height for r in pages[0].texts)


def test_run_cap_marks_page_truncated() -> None:
    from readafp.ptoca import MAX_RUNS_PER_PAGE

    one_trn = bytes([2 + 2, 0xDB]) + "AB".encode("cp500")  # chained TRN
    ptx = bytes.fromhex("2bd3") + one_trn * (MAX_RUNS_PER_PAGE + 10)
    doc = (
        _sf(0xD3A8A8, b"\x00" * 8)
        + _sf(0xD3EE9B, ptx)
        + _sf(0xD3A9A8, b"\x00" * 8)
    )
    pages = extract_pages(list(iter_fields(doc)))
    assert sum(len(p.texts) for p in pages) == MAX_RUNS_PER_PAGE
    assert pages[-1].truncated
    assert "[render truncated" in page_to_svg(pages[-1])


def test_runs_carry_source_field_offset() -> None:
    if not HEALTH_SAMPLE.exists():
        pytest.skip("test corpus not present")
    page = extract_pages(parse_file(str(HEALTH_SAMPLE)))[0]
    # All text comes from the second PTX (offset 6559); the border and
    # band rules come from the first (offset 6039).
    assert {r.src for r in page.texts} == {6559}
    assert 6039 in {r.src for r in page.rules}
    svg = page_to_svg(page)
    assert 'data-src="6559"' in svg and 'data-src="6039"' in svg


def test_extract_pages_empty_document() -> None:
    sample = TESTDATA / "alpheus-corpus" / "minimal.afp"
    if not sample.exists():
        pytest.skip("test corpus not present")
    assert extract_pages(parse_file(str(sample))) == []
