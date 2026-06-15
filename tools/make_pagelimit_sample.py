"""Generate a many-page AFP to exercise the render page-limit warning.

Usage:
    python tools/make_pagelimit_sample.py [output_path] [page_count]

Writes testdata/pagelimit_520.afp by default: a minimal document with
520 trivial pages (one positioned text run each). Because this exceeds
the app's MAX_RENDER_PAGES cap of 500, uploading it surfaces the amber
"Showing first 500 of 520 pages" warning while the inspector still
lists every structured field.
"""

import struct
import sys
from pathlib import Path


def _sf(sf_id: int, data: bytes = b"") -> bytes:
    """Build one MO:DCA structured field."""
    body = sf_id.to_bytes(3, "big") + b"\x00\x00\x00" + data
    return b"\x5a" + struct.pack(">H", len(body) + 2) + body


def build_afp(page_count: int = 520) -> bytes:
    """A document of ``page_count`` pages, each with one text run."""
    buf = bytearray()
    buf += _sf(0xD3A8A8)  # BDT
    for i in range(page_count):
        # PTX: AMI(100) -> AMB(200) -> TRN("Pnnn")
        label = f"P{i:03d}".encode("cp500")
        ptx = (
            b"\x2b\xd3"
            + bytes([4, 0xC7]) + b"\x00\x64"            # AMI 100
            + bytes([4, 0xD3]) + b"\x00\xc8"            # AMB 200
            + bytes([2 + len(label), 0xDA]) + label     # TRN
        )
        buf += _sf(0xD3A8AF)            # BPG
        buf += _sf(0xD3EE9B, ptx)       # PTX
        buf += _sf(0xD3A9AF)            # EPG
    buf += _sf(0xD3A9A8)  # EDT
    return bytes(buf)


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(
        "testdata/pagelimit_520.afp"
    )
    count = int(sys.argv[2]) if len(sys.argv) > 2 else 520
    out.parent.mkdir(parents=True, exist_ok=True)
    data = build_afp(count)
    out.write_bytes(data)
    print(f"Wrote {len(data):,} bytes ({count} pages) -> {out}")
