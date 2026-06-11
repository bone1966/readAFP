"""readAFP web app: upload an AFP file and inspect its structure."""

import logging
from collections import Counter
from typing import Any, Dict, List

from flask import Flask, render_template, request

from readafp.parser import AfpParseError, iter_fields

logger = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 64 * 1024 * 1024


def create_app() -> Flask:
    """Build the Flask application."""
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES

    @app.get("/")
    def index() -> str:
        return render_template("index.html", fields=None, error=None)

    @app.post("/inspect")
    def inspect() -> str:
        upload = request.files.get("afpfile")
        if upload is None or not upload.filename:
            return render_template(
                "index.html", fields=None, error="Choose an AFP file first."
            )
        data = upload.read()
        try:
            fields = _field_rows(data)
        except AfpParseError as exc:
            logger.warning("Failed to parse %s: %s", upload.filename, exc)
            return render_template(
                "index.html",
                fields=None,
                error=f"Not a valid AFP file: {exc}",
            )
        summary = Counter(row["name"] for row in fields)
        return render_template(
            "index.html",
            fields=fields,
            error=None,
            filename=upload.filename,
            filesize=len(data),
            summary=summary.most_common(),
        )

    return app


def _field_rows(data: bytes) -> List[Dict[str, Any]]:
    """Flatten structured fields into display rows with nesting depth."""
    rows: List[Dict[str, Any]] = []
    depth = 0
    for field in iter_fields(data):
        if field.type_code == 0xA9 and depth > 0:  # End fields close a level
            depth -= 1
        rows.append(
            {
                "offset": field.offset,
                "sf_id": f"0x{field.sf_id:06X}",
                "name": field.name,
                "token": field.token_name or "",
                "length": len(field.data),
                "depth": depth,
                "preview": field.data[:16].hex(" "),
            }
        )
        if field.type_code == 0xA8:  # Begin fields open a level
            depth += 1
    return rows
