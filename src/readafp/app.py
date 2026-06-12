"""readAFP web app: upload an AFP file, inspect its structure, render pages."""

import logging
from collections import Counter
from typing import Any, Dict, List

from flask import Flask, render_template, request

from readafp.parser import AfpParseError, StructuredField, iter_fields
from readafp.ptoca import extract_pages
from readafp.render import pages_to_svgs
from readafp.triplets import (
    MCF_FORMAT_1,
    MCF_FORMAT_2,
    describe_field,
    parse_mcf_codepages,
)

logger = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 64 * 1024 * 1024
# Page-count ceiling; pages_to_svgs also stops early on dense documents
# once its element budget is spent, whichever comes first.
MAX_RENDER_PAGES = 500

# EBCDIC code pages offered for text decoding. MCF-labeled fonts decode
# with their declared code page; this manual choice covers the rest.
# Keys are Python codec names.
CODEPAGES = [
    ("cp500", "cp500 — international (default)"),
    ("cp037", "cp037 — US / Canada"),
    ("cp273", "cp273 — Germany / Austria"),
    ("cp1047", "cp1047 — Latin-1 open systems"),
    ("cp1141", "cp1141 — Germany (euro)"),
]


def create_app() -> Flask:
    """Build the Flask application."""
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES

    @app.get("/")
    def index() -> str:
        return render_template(
            "index.html",
            fields=None,
            error=None,
            codepages=CODEPAGES,
            codepage="cp500",
        )

    @app.post("/inspect")
    def inspect() -> str:
        codepage = request.form.get("codepage", "cp500")
        if codepage not in {name for name, _ in CODEPAGES}:
            codepage = "cp500"
        upload = request.files.get("afpfile")
        if upload is None or not upload.filename:
            return render_template(
                "index.html",
                fields=None,
                error="Choose an AFP file first.",
                codepages=CODEPAGES,
                codepage=codepage,
            )
        data = upload.read()
        try:
            parsed = list(iter_fields(data))
        except AfpParseError as exc:
            logger.warning("Failed to parse %s: %s", upload.filename, exc)
            return render_template(
                "index.html",
                fields=None,
                error=f"Not a valid AFP file: {exc}",
                codepages=CODEPAGES,
                codepage=codepage,
            )
        fields = _field_rows(parsed)
        summary = Counter(row["name"] for row in fields)
        pages = extract_pages(parsed, codepage)
        bracketed = sum(1 for row in fields if row["sf_id"] == "0xD3A8AF")
        if len(pages) > bracketed:  # loose PTX flowed onto implicit pages
            src_to_page: dict = {}
            for idx, page in enumerate(pages):
                for item in page.texts + page.rules:
                    if item.src is not None:
                        src_to_page.setdefault(item.src, idx)
            for row in fields:
                if row["page"] is None and row["sf_id"] == "0xD3EE9B":
                    row["page"] = src_to_page.get(row["offset"])
        page_svgs = pages_to_svgs(pages, MAX_RENDER_PAGES)
        return render_template(
            "index.html",
            fields=fields,
            error=None,
            filename=upload.filename,
            filesize=len(data),
            summary=summary.most_common(),
            page_svgs=page_svgs,
            page_texts=[p.plain_text for p in pages[: len(page_svgs)]],
            page_total=len(pages),
            codepages=CODEPAGES,
            codepage=codepage,
            mcf_note=_mcf_codepage_note(parsed),
        )

    return app


def _mcf_codepage_note(parsed: List[StructuredField]) -> str:
    """Summarize the code pages MCF fields label their fonts with.

    The manual code-page dropdown only applies to fonts the file leaves
    unlabeled; this note tells the user which decoding the file itself
    declared, e.g. "T1V10500 → cp500".
    """
    labels: List[str] = []
    for field in parsed:
        if field.sf_id not in (MCF_FORMAT_1, MCF_FORMAT_2):
            continue
        for cp in parse_mcf_codepages(
            field.data, format1=field.sf_id == MCF_FORMAT_1
        ).values():
            label = f"{cp.name} → {cp.codec}" if cp.codec else cp.name
            if label not in labels:
                labels.append(label)
    return ", ".join(labels)


def _field_rows(parsed: List[StructuredField]) -> List[Dict[str, Any]]:
    """Flatten structured fields into display rows with nesting depth.

    Each row also carries the index of the page (BPG...EPG bracket) it
    belongs to, so the UI can link inspector rows to rendered pages.
    """
    rows: List[Dict[str, Any]] = []
    depth = 0
    page_idx = -1
    current_page: Any = None
    for field in parsed:
        if field.type_code == 0xA9 and depth > 0:  # End fields close a level
            depth -= 1
        if field.sf_id == 0xD3A8AF:  # BPG
            page_idx += 1
            current_page = page_idx
        rows.append(
            {
                "offset": field.offset,
                "sf_id": f"0x{field.sf_id:06X}",
                "name": field.name,
                "token": field.token_name or "",
                "length": len(field.data),
                "depth": depth,
                "preview": field.data[:16].hex(" "),
                "page": current_page,
                "triplets": describe_field(field),
            }
        )
        if field.sf_id == 0xD3A9AF:  # EPG
            current_page = None
        if field.type_code == 0xA8:  # Begin fields open a level
            depth += 1
    return rows
