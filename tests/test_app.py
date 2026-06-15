"""Tests for the Flask app's inspect endpoint."""

from pathlib import Path

import pytest

from readafp.app import (
    create_app,
    _field_rows,
    _missing_resources,
    _resource_kind,
)
from readafp.parser import parse_file

TESTDATA = Path(__file__).parent.parent / "testdata"
HEALTH_SAMPLE = TESTDATA / "sample1_health" / "01_Health_Coverage.afp"
OUTLINE_FONT = TESTDATA / "github-samples" / "afplib" / "C0X00006.afp"
CS_OVERLAY = TESTDATA / "github-samples" / "afplib" / "cs.afp"
SAMPLE1 = TESTDATA / "Sample Files" / "Sample 1.afp"


def test_field_rows_tag_page_membership() -> None:
    if not HEALTH_SAMPLE.exists():
        pytest.skip("test corpus not present")
    rows = _field_rows(parse_file(str(HEALTH_SAMPLE)))
    by_name = {row["name"]: row for row in rows}
    # Resource group fields come before any page.
    assert by_name["BRG (Begin Resource Group)"]["page"] is None
    # Page bracket and its contents belong to page 0.
    assert by_name["BPG (Begin Page)"]["page"] == 0
    assert by_name["PTX (Presentation Text Data)"]["page"] == 0
    assert by_name["EPG (End Page)"]["page"] == 0
    # The document close comes after the page.
    assert by_name["EDT (End Document)"]["page"] is None


def test_resource_kind_flags_pageless_font() -> None:
    if not OUTLINE_FONT.exists():
        pytest.skip("outline font fixture not present")
    # A font character set has no BPG, so it is flagged as a resource.
    assert _resource_kind(parse_file(str(OUTLINE_FONT))) == "font character set"


def test_resource_kind_silent_for_document() -> None:
    if not HEALTH_SAMPLE.exists():
        pytest.skip("test corpus not present")
    # A real document has pages, so no resource warning fires.
    assert _resource_kind(parse_file(str(HEALTH_SAMPLE))) == ""


def test_inspect_resource_shows_banner() -> None:
    if not OUTLINE_FONT.exists():
        pytest.skip("outline font fixture not present")
    client = create_app().test_client()
    with OUTLINE_FONT.open("rb") as handle:
        response = client.post(
            "/inspect",
            data={"afpfile": (handle, OUTLINE_FONT.name)},
            content_type="multipart/form-data",
        )
    html = response.get_data(as_text=True)
    assert "resource-banner" in html
    assert "font character set" in html


def test_missing_resources_format2_matches_viewer() -> None:
    if not CS_OVERLAY.exists():
        pytest.skip("cs.afp sample not present")
    missing = _missing_resources(parse_file(str(CS_OVERLAY)))
    names = {r["name"] for r in missing}
    # The same external resources a real AFP viewer reports as missing.
    assert "T1EX0000" in names  # code page
    assert {"C0EX0460", "C0EX04U0"} <= names  # character sets
    assert all(r["codec"] is None for r in missing if r["kind"] == "code page")


def test_missing_resources_notes_builtin_codec() -> None:
    if not SAMPLE1.exists():
        pytest.skip("Sample 1 not present")
    missing = _missing_resources(parse_file(str(SAMPLE1)))
    # The bulk text's code page is external, but resolves to a built-in
    # codec; the embedded character sets are not reported missing.
    cps = [r for r in missing if r["kind"] == "code page"]
    assert any(r["name"] == "T1001140" and r["codec"] == "cp1140" for r in cps)
    assert not any(r["kind"] == "character set" for r in missing)


def test_no_missing_resources_for_clean_document() -> None:
    if not HEALTH_SAMPLE.exists():
        pytest.skip("test corpus not present")
    assert _missing_resources(parse_file(str(HEALTH_SAMPLE))) == []


def test_inspect_endpoint_links_rows_to_pages() -> None:
    if not HEALTH_SAMPLE.exists():
        pytest.skip("test corpus not present")
    client = create_app().test_client()
    with HEALTH_SAMPLE.open("rb") as handle:
        response = client.post(
            "/inspect",
            data={"afpfile": (handle, HEALTH_SAMPLE.name)},
            content_type="multipart/form-data",
        )
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'data-page="0"' in html
    assert 'id="sf-table"' in html
    assert "data:image/jpeg;base64," in html  # logo made it through
    # Plain text export: buttons present and page text embedded.
    assert 'id="copy-text"' in html and 'id="download-text"' in html
    assert "John Doe" in html  # plain_text joins runs in reading order


def test_inspect_endpoint_renders_all_implicit_pages() -> None:
    sample = TESTDATA / "alpheus-corpus" / "large_ibm273.afp"
    if not sample.exists():
        pytest.skip("test corpus not present")
    client = create_app().test_client()
    with sample.open("rb") as handle:
        response = client.post(
            "/inspect",
            data={"afpfile": (handle, sample.name)},
            content_type="multipart/form-data",
        )
    html = response.get_data(as_text=True)
    # All 109 flowed pages fit the content budget, so go-to-last works.
    assert 'max="109"' in html
    assert "of 109</span>" in html
    # Loose PTX rows map to the flowed page their text landed on (each
    # PTX spans ~2 pages and starts on an even one), not all to page 1.
    assert 'data-page="2"' in html and 'data-page="50"' in html


def test_inspect_endpoint_codepage_override() -> None:
    sample = TESTDATA / "alpheus-corpus" / "large_ibm273.afp"
    if not sample.exists():
        pytest.skip("test corpus not present")
    client = create_app().test_client()
    with sample.open("rb") as handle:
        response = client.post(
            "/inspect",
            data={"afpfile": (handle, sample.name), "codepage": "cp273"},
            content_type="multipart/form-data",
        )
    html = response.get_data(as_text=True)
    assert "Hällö Wörld" in html  # the fixture's German text, decoded
    assert 'value="cp273" selected' in html


def test_inspect_endpoint_shows_triplet_details() -> None:
    if not HEALTH_SAMPLE.exists():
        pytest.skip("test corpus not present")
    client = create_app().test_client()
    with HEALTH_SAMPLE.open("rb") as handle:
        response = client.post(
            "/inspect",
            data={"afpfile": (handle, HEALTH_SAMPLE.name)},
            content_type="multipart/form-data",
        )
    html = response.get_data(as_text=True)
    # The MDR's decoded triplets are embedded as an expandable detail row.
    assert 'class="trip-detail"' in html
    assert "Fully Qualified Name" in html
    assert "Arial Bold" in html


def test_inspect_endpoint_notes_mcf_codepage() -> None:
    sample = TESTDATA / "fop-pairs" / "simple.afp"
    if not sample.exists():
        pytest.skip("FOP pairs not present")
    client = create_app().test_client()
    with sample.open("rb") as handle:
        response = client.post(
            "/inspect",
            data={"afpfile": (handle, sample.name)},
            content_type="multipart/form-data",
        )
    html = response.get_data(as_text=True)
    assert "code page from MCF: T1V10500 → cp500" in html


def test_inspect_endpoint_rejects_non_afp() -> None:
    client = create_app().test_client()
    response = client.post(
        "/inspect",
        data={"afpfile": (__import__("io").BytesIO(b"not afp"), "x.afp")},
        content_type="multipart/form-data",
    )
    assert b"Not a valid AFP file" in response.data
