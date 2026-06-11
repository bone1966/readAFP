"""Tests for PTOCA decoding, page extraction and SVG rendering."""

from pathlib import Path

import pytest

from readafp.parser import parse_file
from readafp.ptoca import (
    ControlSequence,
    extract_pages,
    iter_control_sequences,
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


def test_extract_pages_empty_document() -> None:
    sample = TESTDATA / "alpheus-corpus" / "minimal.afp"
    if not sample.exists():
        pytest.skip("test corpus not present")
    assert extract_pages(parse_file(str(sample))) == []
