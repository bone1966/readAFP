"""IOCA image segment decoding.

An image object's IPD fields concatenate into one IOCA image segment: a
stream of self-defining fields (SDFs) in long format (code, u8 length,
params) or extended format (0xFE, code, u16 length, params). The fields
of interest here:

    0x94    Image Size      unit base, resolution, size in pixels
    0x95    Image Encoding  compression and recording algorithms
    0x96    IDE Size        bits per image data element
    0xFE92  Image Data      raw IDEs (may repeat; concatenated)
    0xFE9C  Band Image Data BANDNUM(1) reserved(2) then IDEs

The decoders cover what the corpus actually contains: uncompressed
bilevel and 8-bit grayscale rebuilt as PNG (stdlib zlib only),
JPEG-compressed data passed through untouched, and band-interleaved
CMYK (one grayscale JPEG per ink plane) handed to the renderer as four
plane blobs for optical composition.

Reference: IOCA Reference, AFPC-0003-09 (docs/specs/ioca-reference-09.pdf).
"""

import logging
import struct
import zlib
from dataclasses import dataclass, field
from typing import Dict, Iterator, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Image Encoding (0x95) compression algorithm identifiers.
COMPRESSION_NONE = 0x03
COMPRESSION_JPEG = 0x83


def iter_sdfs(data: bytes) -> Iterator[Tuple[int, bytes]]:
    """Yield (code, params) self-defining fields from an image segment.

    Extended-format codes are yielded as 0xFExx so callers can tell
    Image Data (0xFE92) from any long-format 0x92. A declared length
    of zero on an image-data SDF consumes the rest of the stream:
    afplib's IPDSpan fixture writes ``FE92 0000`` and then carries the
    pixel data in the following IPD field with no further framing.
    """
    pos = 0
    while pos < len(data):
        if data[pos] == 0xFE:
            if pos + 4 > len(data):
                logger.warning("truncated extended SDF at offset %d", pos)
                break
            code = 0xFE00 | data[pos + 1]
            length = int.from_bytes(data[pos + 2 : pos + 4], "big")
            pos += 4
        else:
            if pos + 2 > len(data):
                break
            code = data[pos]
            length = data[pos + 1]
            pos += 2
        if length == 0 and code in (0xFE92, 0xFE9C):
            length = len(data) - pos
        yield code, data[pos : pos + length]
        pos += length


@dataclass
class IocaImage:
    """One decoded IOCA image segment's parameters and pixel data."""

    width: int = 0  # pixels; 0 when the segment is tiled and omits 0x94
    height: int = 0
    hres: int = 0  # pixels per unit base (0x94 unit base 0 = 10 inches)
    vres: int = 0
    compression: int = COMPRESSION_NONE
    bits: int = 1  # IDE size: bits per image data element
    data: bytes = b""
    bands: Dict[int, bytes] = field(default_factory=dict)


def parse_image_segment(data: bytes) -> Optional[IocaImage]:
    """Parse concatenated IPD bytes into an IocaImage, or None if empty."""
    img = IocaImage()
    bands: Dict[int, bytearray] = {}
    chunks = bytearray()
    seen = False
    for code, params in iter_sdfs(data):
        if code == 0x94 and len(params) >= 9:  # Image Size
            seen = True
            img.hres = int.from_bytes(params[1:3], "big")
            img.vres = int.from_bytes(params[3:5], "big")
            img.width = int.from_bytes(params[5:7], "big")
            img.height = int.from_bytes(params[7:9], "big")
        elif code == 0x95 and len(params) >= 1:  # Image Encoding
            seen = True
            img.compression = params[0]
        elif code == 0x96 and params:  # IDE Size
            img.bits = params[0]
        elif code == 0xFE92:  # Image Data
            seen = True
            chunks += params
        elif code == 0xFE9C and len(params) >= 3:  # Band Image Data
            seen = True
            bands.setdefault(params[0], bytearray()).extend(params[3:])
    if not seen:
        return None
    img.data = bytes(chunks)
    img.bands = {num: bytes(buf) for num, buf in bands.items()}
    if not img.data and len(img.bands) == 1:
        # Single-band data (e.g. a JPEG stream in band 1) is just image
        # data delivered through the banded SDF.
        img.data = next(iter(img.bands.values()))
    return img


def _png_chunk(tag: bytes, body: bytes) -> bytes:
    raw = tag + body
    return struct.pack(">I", len(body)) + raw + struct.pack(
        ">I", zlib.crc32(raw)
    )


def _png(width: int, height: int, bit_depth: int, color_type: int,
         row_bytes: int, pixels: bytes) -> bytes:
    """Pack raw scanlines into a minimal PNG (filter 0 on every row)."""
    ihdr = struct.pack(">IIBBBBB", width, height, bit_depth, color_type,
                       0, 0, 0)
    raster = bytearray()
    for row in range(height):
        raster.append(0)
        raster += pixels[row * row_bytes : (row + 1) * row_bytes]
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", zlib.compress(bytes(raster)))
        + _png_chunk(b"IEND", b"")
    )


def image_blob(img: IocaImage) -> Optional[Tuple[str, bytes]]:
    """Turn a parsed image into (mime, bytes) a browser can display.

    Returns None for combinations the corpus has never shown us
    (CCITT fax compressions, banded uncompressed color, ...) — the
    caller should log and skip rather than render something invented.
    """
    if img.compression == COMPRESSION_JPEG:
        start = img.data.find(b"\xff\xd8")
        if start >= 0:
            return "image/jpeg", img.data[start:]
        return None
    if img.compression != COMPRESSION_NONE:
        return None
    if not img.data or img.width <= 0 or img.height <= 0:
        return None
    if img.bits == 1:
        row_bytes = (img.width + 7) // 8
        if len(img.data) < row_bytes * img.height:
            return None
        # IOCA bilevel: 1 = mark (black). PNG grayscale: 0 = black.
        inverted = bytes(b ^ 0xFF for b in img.data)
        return "image/png", _png(img.width, img.height, 1, 0, row_bytes,
                                 inverted)
    if img.bits == 8:
        if len(img.data) < img.width * img.height:
            return None
        return "image/png", _png(img.width, img.height, 8, 0, img.width,
                                 img.data)
    if img.bits == 24:
        row_bytes = img.width * 3
        if len(img.data) < row_bytes * img.height:
            return None
        return "image/png", _png(img.width, img.height, 8, 2, row_bytes,
                                 img.data)
    return None


def cmyk_jpeg_bands(img: IocaImage) -> Optional[List[bytes]]:
    """Return [C, M, Y, K] plane JPEGs from a 4-band JPEG image.

    FS45 band-interleaved CMYK carries one complete grayscale JPEG per
    Band Image Data band (BANDNUM 1-4 = C, M, Y, K). The renderer can
    composite the planes optically without decoding them.
    """
    if img.compression != COMPRESSION_JPEG or set(img.bands) != {1, 2, 3, 4}:
        return None
    bands = [img.bands[n] for n in (1, 2, 3, 4)]
    if not all(b.startswith(b"\xff\xd8") for b in bands):
        return None
    return bands
