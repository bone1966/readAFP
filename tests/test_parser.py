"""Tests for the MO:DCA structured-field parser."""

from pathlib import Path

import pytest

from readafp.parser import AfpParseError, StructuredField, iter_fields

TESTDATA = Path(__file__).parent.parent / "testdata"

# A minimal document: BDT "DOC00001" followed by EDT "DOC00001".
MINIMAL_AFP = bytes.fromhex(
    "5a0010d3a8a8000000c4d6c3f0f0f0f0f1"
    "5a0010d3a9a8000000c4d6c3f0f0f0f0f1"
)


def test_iter_fields_parses_minimal_document() -> None:
    fields = list(iter_fields(MINIMAL_AFP))
    assert len(fields) == 2
    assert fields[0].name == "BDT (Begin Document)"
    assert fields[1].name == "EDT (End Document)"


def test_token_name_decodes_ebcdic() -> None:
    fields = list(iter_fields(MINIMAL_AFP))
    assert fields[0].token_name == "DOC00001"
    assert fields[1].token_name == "DOC00001"


def test_offsets_and_sequence() -> None:
    fields = list(iter_fields(MINIMAL_AFP))
    assert fields[0].offset == 0
    assert fields[1].offset == 17


def test_unknown_sf_id_is_reported_in_hex() -> None:
    field = StructuredField(offset=0, sf_id=0xD30000, flags=0, sequence=0, data=b"")
    assert field.name == "Unknown (0xD30000)"


def test_code_page_and_image_map_fields_are_named() -> None:
    # The code-page object family and the Map IO Image field were
    # previously surfacing as "Unknown".
    cases = {
        0xD3ABFB: "MIO", 0xD3A887: "BCP", 0xD3A987: "ECP",
        0xD3A787: "CPC", 0xD3A687: "CPD", 0xD38C87: "CPI",
    }
    for sf_id, acronym in cases.items():
        field = StructuredField(
            offset=0, sf_id=sf_id, flags=0, sequence=0, data=b""
        )
        assert field.name.startswith(acronym + " "), field.name


def test_bad_carriage_control_raises() -> None:
    with pytest.raises(AfpParseError, match="carriage-control"):
        list(iter_fields(b"\x0d\x0anot afp"))


def test_truncated_field_raises() -> None:
    with pytest.raises(AfpParseError, match="bad length"):
        list(iter_fields(MINIMAL_AFP[:10]))


def test_empty_stream_yields_nothing() -> None:
    assert list(iter_fields(b"")) == []


def test_corpus_minimal_file_parses() -> None:
    sample = TESTDATA / "alpheus-corpus" / "minimal.afp"
    if not sample.exists():
        pytest.skip("test corpus not present")
    fields = list(iter_fields(sample.read_bytes()))
    assert [f.name for f in fields] == [
        "BDT (Begin Document)",
        "EDT (End Document)",
    ]
