"""readAFP web app: upload an AFP file, inspect its structure, render pages."""

import logging
import platform
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List

from flask import Flask, abort, render_template, request

from readafp import __version__
from readafp.parser import AfpParseError, StructuredField, iter_fields
from readafp.ptoca import extract_pages
from readafp.render import pages_to_svgs
from readafp.triplets import (
    MCF_FORMAT_1,
    MCF_FORMAT_2,
    codec_for_codepage_name,
    describe_field,
    mcf_resource_names,
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

_SAMPLES_DIR = Path(__file__).parent / "samples"

# Bundled sample files shown on the landing page.
SAMPLES = [
    {
        "name": "health_coverage",
        "file": "health_coverage.afp",
        "label": "Health Coverage letter",
        "desc": (
            "A real-world insurance letter produced by a mainframe print "
            "system. Shows how AFP encodes TrueType text runs, colored rules, "
            "table borders, and multiple font weights — all positioned to the "
            "nearest point using PTOCA control sequences."
        ),
    },
    {
        "name": "goca_demo",
        "file": "goca_demo.afp",
        "label": "GOCA vector graphics",
        "desc": (
            "Four graphic objects drawn entirely with AFP's binary vector "
            "drawing orders (GOCA): a filled rectangle, a zigzag polyline, "
            "an ellipse, and an S-curve Bézier. Good for seeing how AFP "
            "represents vector art without any raster images."
        ),
    },
    {
        "name": "ioca_image",
        "file": "ioca_image.afp",
        "label": "IOCA color photo",
        "desc": (
            "A full-color photograph stored as an IOCA image object. AFP "
            "splits color into four grayscale ink planes (cyan, magenta, "
            "yellow, black); readAFP recomposes them in the browser with "
            "SVG color filters and multiply blending to rebuild the photo."
        ),
    },
    {
        "name": "foca_font",
        "file": "foca_font.afp",
        "label": "FOCA raster font",
        "desc": (
            "An AFP font resource that embeds its letters as bitmaps. "
            "readAFP decodes the FOCA pattern data — the actual 1-bit-per-"
            "pixel glyph shapes — and lays them out as a specimen sheet, "
            "one page per embedded typeface (Times-Roman and Courier)."
        ),
    },
    {
        "name": "bcoca_qr",
        "file": "bcoca_qr.afp",
        "label": "BCOCA QR bar code",
        "desc": (
            "A bar code object (BBC/BDD/BDA) describing a QR Code symbol — "
            "AFP stores the symbology, module size and data, not a picture. "
            "readAFP reads the descriptor, generates the matrix with segno, "
            "and draws it crisp. Scan it: it points to this project's repo."
        ),
    },
    {
        "name": "overlay_demo",
        "file": "overlay_demo.afp",
        "label": "Page overlay (letterhead)",
        "desc": (
            "One page whose letterhead, rule and footer come from a reusable "
            "overlay (BMO/EMO) defined once and pulled in with a single "
            "Include Page Overlay (IPO) field — how AFP shares forms across "
            "many pages. readAFP composites the overlay beneath the body text."
        ),
    },
]


def create_app() -> Flask:
    """Build the Flask application."""
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES

    @app.context_processor
    def inject_version() -> Dict[str, str]:
        """Expose the app and runtime Python versions to every template."""
        return {
            "app_version": __version__,
            "python_version": platform.python_version(),
        }

    @app.get("/")
    def index() -> str:
        return render_template(
            "index.html",
            fields=None,
            error=None,
            codepages=CODEPAGES,
            codepage="cp500",
            samples=SAMPLES,
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
                samples=SAMPLES,
            )
        data = upload.read()
        return _render_inspect(data, upload.filename, codepage)

    @app.get("/inspect-sample/<name>")
    def inspect_sample(name: str) -> str:
        sample = next((s for s in SAMPLES if s["name"] == name), None)
        if sample is None:
            abort(404)
        path = _SAMPLES_DIR / sample["file"]
        data = path.read_bytes()
        return _render_inspect(data, sample["file"], "cp500")

    return app


def _render_inspect(data: bytes, filename: str, codepage: str) -> str:
    """Parse AFP bytes and render the full inspect/render template."""
    try:
        parsed = list(iter_fields(data))
    except AfpParseError as exc:
        logger.warning("Failed to parse %s: %s", filename, exc)
        return render_template(
            "index.html",
            fields=None,
            error=f"Not a valid AFP file: {exc}",
            codepages=CODEPAGES,
            codepage=codepage,
            samples=SAMPLES,
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
        filename=filename,
        filesize=len(data),
        summary=summary.most_common(),
        page_svgs=page_svgs,
        page_texts=[p.plain_text for p in pages[: len(page_svgs)]],
        page_total=len(pages),
        codepages=CODEPAGES,
        codepage=codepage,
        mcf_note=_mcf_codepage_note(parsed),
        resource_kind=_resource_kind(parsed),
        missing_resources=_missing_resources(parsed),
        samples=SAMPLES,
    )


# Begin-fields that mark a stream as a stand-alone AFP *resource* rather
# than a document, in priority order (most specific first). SF ID is
# 0xD3 + type 0xA8 (Begin) + the resource category code.
_RESOURCE_KINDS = [
    (0xD3A889, "font character set"),
    (0xD3A88A, "coded font"),
    (0xD3A887, "code page"),
    (0xD3A8DF, "page overlay"),
    (0xD3A85F, "page segment"),
    (0xD3A892, "object container"),
    (0xD3A8C6, "resource group"),
    (0xD3A8CE, "object resource"),
]


def _resource_kind(parsed: List[StructuredField]) -> str:
    """Classify a page-less AFP stream by its resource type.

    Returns "" when the stream has real document pages (a BPG Begin Page)
    — including a normal BDT document — otherwise the human-readable
    resource kind. A BDT envelope that carries only fonts has no BPG, so
    keying on the page (not the document) catches both the bare resource
    and the resource-wrapped-in-a-document case, letting the UI warn that
    the file has no pages and most viewers can't open it.
    """
    ids = {field.sf_id for field in parsed}
    if 0xD3A8AF in ids:  # BPG present -> it has real document pages
        return ""
    return next((label for sid, label in _RESOURCE_KINDS if sid in ids), "")


# Begin-fields that embed each font-resource kind, keyed to match the
# resource kinds an MCF references.
_EMBED_FIELDS = {
    "code page": 0xD3A887,  # BCP
    "character set": 0xD3A889,  # BFN
    "coded font": 0xD3A88A,  # BCF
}


def _missing_resources(parsed: List[StructuredField]) -> List[Dict[str, Any]]:
    """Font resources a document references via MCF but does not embed.

    Lists each missing coded font / code page / character set, the way an
    AFP viewer reports "Missing Resource" — so the user understands why
    text using those resources falls back to substitute fonts. Code pages
    that resolve to a built-in codec still note it (the text decodes even
    though the resource itself is absent).
    """
    refs: Dict[str, set] = {
        "coded font": set(), "code page": set(), "character set": set()
    }
    for field in parsed:
        if field.sf_id in (MCF_FORMAT_1, MCF_FORMAT_2):
            for kind, names in mcf_resource_names(
                field.data, field.sf_id == MCF_FORMAT_1
            ).items():
                refs[kind] |= names
    embedded = {
        kind: {f.token_name for f in parsed
               if f.sf_id == sid and f.token_name}
        for kind, sid in _EMBED_FIELDS.items()
    }
    missing: List[Dict[str, Any]] = []
    for kind in ("coded font", "code page", "character set"):
        for name in sorted(refs[kind] - embedded[kind]):
            codec = (
                codec_for_codepage_name(name) if kind == "code page" else None
            )
            missing.append({"kind": kind, "name": name, "codec": codec})
    return missing


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
