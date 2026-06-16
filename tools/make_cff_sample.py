"""Generate CFF / Type 2 test fixtures for readAFP.

readAFP parses CFF with the standard library alone; fontTools is used here
only at fixture-build time and, in the tests, as an independent oracle.

Outputs:
  testdata/cff_sample.otf       plain (non-CID) CFF OpenType font
  testdata/cff_cid_sample.cff   hand-built CID-keyed CFF (raw CFF bytes)
  testdata/foca_cff_sample.afp  FOCA outline font whose FNG embeds the CFF

The plain font's glyph set mixes diagonal lines, axis-aligned lines and
curves so fontTools' charstring specializer emits a spread of Type 2
operators (rlineto, vlineto, hhcurveto, vhcurveto). The CID font is built
byte-by-byte so its FDArray/FDSelect routing (two font dicts, two glyphs
with different advances) is exercised. The FOCA file drives the full
foca -> cff specimen path.

Run:  python tools/make_cff_sample.py
"""

import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

OUT = Path(__file__).resolve().parent.parent / "testdata"
UPM = 1000


# ---------------------------------------------------------------------------
# Plain CFF OpenType font (via fontTools FontBuilder)
# ---------------------------------------------------------------------------
def _draw_notdef(pen):
    pen.moveTo((0, 0)); pen.lineTo((0, 700))
    pen.lineTo((500, 700)); pen.lineTo((500, 0)); pen.closePath()


def _draw_A(pen):  # diagonal lines -> rlineto
    pen.moveTo((100, 0)); pen.lineTo((250, 700)); pen.lineTo((400, 0))
    pen.lineTo((350, 0)); pen.lineTo((300, 200)); pen.lineTo((200, 200))
    pen.lineTo((150, 0)); pen.closePath()


def _draw_H(pen):  # axis-aligned -> hlineto / vlineto
    pen.moveTo((100, 0)); pen.lineTo((100, 700)); pen.lineTo((180, 700))
    pen.lineTo((180, 420)); pen.lineTo((420, 420)); pen.lineTo((420, 700))
    pen.lineTo((500, 700)); pen.lineTo((500, 0)); pen.lineTo((420, 0))
    pen.lineTo((420, 280)); pen.lineTo((180, 280)); pen.lineTo((180, 0))
    pen.closePath()


def _draw_O(pen):  # two oval contours -> vh/hh/vvcurveto
    pen.moveTo((450, 350))
    pen.curveTo((450, 540), (360, 700), (250, 700))
    pen.curveTo((140, 700), (50, 540), (50, 350))
    pen.curveTo((50, 160), (140, 0), (250, 0))
    pen.curveTo((360, 0), (450, 160), (450, 350))
    pen.closePath()
    pen.moveTo((370, 350))
    pen.curveTo((370, 200), (310, 80), (250, 80))
    pen.curveTo((190, 80), (130, 200), (130, 350))
    pen.curveTo((130, 500), (190, 620), (250, 620))
    pen.curveTo((310, 620), (370, 500), (370, 350))
    pen.closePath()


def _draw_period(pen):  # dot -> hhcurveto
    pen.moveTo((100, 0))
    pen.curveTo((160, 0), (160, 120), (100, 120))
    pen.curveTo((40, 120), (40, 0), (100, 0))
    pen.closePath()


GLYPHS = {
    ".notdef": (_draw_notdef, 500),
    "A": (_draw_A, 500),
    "H": (_draw_H, 600),
    "O": (_draw_O, 550),
    "period": (_draw_period, 250),
}
CMAP = {ord("A"): "A", ord("H"): "H", ord("O"): "O", ord("."): "period"}


def build_plain_otf(path: Path) -> bytes:
    """Build a self-consistent plain CFF OTF; return its raw CFF bytes."""
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.t2CharStringPen import T2CharStringPen
    from fontTools.ttLib import TTFont

    fb = FontBuilder(UPM, isTTF=False)
    fb.setupGlyphOrder(list(GLYPHS))
    fb.setupCharacterMap(CMAP)
    charstrings, metrics = {}, {}
    for name, (draw, adv) in GLYPHS.items():
        pen = T2CharStringPen(adv, None)  # width == hmtx for consistency
        draw(pen)
        charstrings[name] = pen.getCharString()
        metrics[name] = (adv, 0)
    fb.setupCFF("readAFPTestCFF", {"FullName": "readAFP Test CFF"},
                charstrings, {})
    fb.setupHorizontalMetrics(metrics)
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    fb.setupNameTable({"familyName": "readAFP Test CFF",
                       "styleName": "Regular"})
    fb.setupOS2()
    fb.setupPost()
    fb.save(str(path))
    return TTFont(str(path)).reader["CFF "]


# ---------------------------------------------------------------------------
# CID-keyed CFF (hand-built raw bytes)
# ---------------------------------------------------------------------------
def _cff_index(objs):
    if not objs:
        return b"\x00\x00"
    offs = [1]
    for o in objs:
        offs.append(offs[-1] + len(o))
    last = offs[-1]
    osz = 1 if last < 0x100 else 2 if last < 0x10000 else 3 if last < (1 << 24) else 4
    enc = b"".join(v.to_bytes(osz, "big") for v in offs)
    return struct.pack(">H", len(objs)) + bytes([osz]) + enc + b"".join(objs)


def _cff_int(v):
    if -107 <= v <= 107:
        return bytes([v + 139])
    if 108 <= v <= 1131:
        v -= 108
        return bytes([(v >> 8) + 247, v & 0xFF])
    if -1131 <= v <= -108:
        v = -v - 108
        return bytes([(v >> 8) + 251, v & 0xFF])
    return bytes([28]) + struct.pack(">h", v)


def _cff_entry(operands, op):
    b = b"".join(_cff_int(v) for v in operands)
    return b + (bytes([12, op - 1200]) if op >= 1200 else bytes([op]))


def build_cid_cff() -> bytes:
    """Hand-build a 3-glyph CID-keyed CFF with two FDArray entries.

    Glyph 0 is .notdef; glyph 1 (CID 1) is routed to FD 0 and glyph 2
    (CID 2) to FD 1 by the FDSelect, with distinct advance widths so the
    routing is observable.
    """
    n = _cff_int
    cs_notdef = bytes([14])  # endchar
    cs1 = (n(400) + n(100) + n(0) + bytes([21])  # w=400, rmoveto(100,0)
           + n(200) + n(0) + bytes([5]) + bytes([14]))  # rlineto, endchar
    cs2 = (n(250) + n(50) + n(0) + bytes([21])  # w=250, rmoveto(50,0)
           + n(0) + n(300) + bytes([5]) + bytes([14]))
    charstrings = _cff_index([cs_notdef, cs1, cs2])

    priv0 = _cff_entry([0], 20) + _cff_entry([0], 21)  # default/nominal W = 0
    priv1 = _cff_entry([0], 20) + _cff_entry([0], 21)

    charset = bytes([0]) + struct.pack(">HH", 1, 2)  # format 0: CIDs 1, 2
    # FDSelect format 3: glyphs 0,1 -> FD0; glyph 2 -> FD1; sentinel 3.
    fdselect = (bytes([3]) + struct.pack(">H", 2)
                + struct.pack(">H", 0) + bytes([0])
                + struct.pack(">H", 2) + bytes([1])
                + struct.pack(">H", 3))
    strings = _cff_index([b"Adobe", b"Identity"])  # SIDs 391, 392
    gsubrs = _cff_index([])
    header = bytes([1, 0, 4, 1])
    names = _cff_index([b"CIDFont"])

    # Iterate to a fixed point: offsets forced to 2-byte encoding stay stable.
    off_charset = off_fdselect = off_charstrings = off_fdarray = 10000
    for _ in range(4):
        top = (_cff_entry([391, 392, 0], 1230)        # ROS
               + _cff_entry([3], 1234)                 # CIDCount
               + _cff_entry([off_charset], 15)
               + _cff_entry([off_charstrings], 17)
               + _cff_entry([off_fdarray], 1236)       # FDArray
               + _cff_entry([off_fdselect], 1237))     # FDSelect
        topindex = _cff_index([top])
        pos = len(header) + len(names) + len(topindex) + len(strings) + len(gsubrs)
        off_charset = pos; pos += len(charset)
        off_fdselect = pos; pos += len(fdselect)
        off_charstrings = pos; pos += len(charstrings)
        p0 = pos; pos += len(priv0)
        p1 = pos; pos += len(priv1)
        fdarray = _cff_index([_cff_entry([len(priv0), p0], 18),
                              _cff_entry([len(priv1), p1], 18)])
        off_fdarray = pos
    return (header + names + topindex + strings + gsubrs + charset
            + fdselect + charstrings + priv0 + priv1 + fdarray)


# ---------------------------------------------------------------------------
# FOCA outline font embedding the CFF program
# ---------------------------------------------------------------------------
def _sf(sf_id: int, data: bytes = b"") -> bytes:
    body = sf_id.to_bytes(3, "big") + b"\x00\x00\x00" + data
    return b"\x5a" + struct.pack(">H", len(body) + 2) + body


def _ebcdic8(text: str) -> bytes:
    return text.encode("cp500")[:8].ljust(8, b"\x40")


def build_foca_cff_afp(cff_bytes: bytes) -> bytes:
    """Wrap the plain CFF in a FOCA CID outline font resource AFP.

    GCGIDs map through the Font Name Map (FNN) to the CFF glyph names so the
    decoder resolves each character to a real outline.
    """
    from readafp.foca import BFN, FNC, FND, FNI, FNN, FNG, EFN

    glyphs = [("LA010000", "A", 500), ("LH010000", "H", 600),
              ("LO010000", "O", 550), ("PD010000", "period", 250)]

    # FNC: byte1 = PatTech (0x1F CID outline); byte15 = FNI record length.
    fnc = bytearray(20)
    fnc[1] = 0x1F
    struct.pack_into(">H", fnc, 10, 999)  # MaxW-1
    struct.pack_into(">H", fnc, 12, 999)  # MaxH-1
    fnc[15] = 10                           # 10-byte FNI records
    fnc[16] = 0x00                         # alignment factor 1
    struct.pack_into(">H", fnc, 18, 1000)  # patterns size

    fnd = bytearray(36)
    fnd[:32] = "HELVETICA CFF".encode("cp500").ljust(32, b"\x40")
    fnd[32] = 5  # weight
    fnd[33] = 5  # width
    struct.pack_into(">H", fnd, 34, 100)  # max vert size (10pt)

    fni = b"".join(_ebcdic8(g) + struct.pack(">H", adv)
                   for g, _name, adv in glyphs)

    # FNN: 2-byte header, 12-byte (GCGID, offset) records, then name pool.
    header = b"\x00\x00"
    rec_size = 12 * len(glyphs)
    pool = bytearray()
    offsets = []
    for _g, name, _adv in glyphs:
        offsets.append(2 + rec_size + len(pool))
        encoded = name.encode("ascii")
        pool += bytes([len(encoded) + 1]) + encoded  # length counts itself
    records = b"".join(_ebcdic8(g) + struct.pack(">I", off)
                       for (g, _n, _a), off in zip(glyphs, offsets))
    fnn = header + records + bytes(pool)

    out = bytearray()
    out += _sf(0xD3A8A8, b"CFFFONT\x00")  # BDT
    out += _sf(BFN, _ebcdic8("C0CFF001"))
    out += _sf(FNC, bytes(fnc))
    out += _sf(FND, bytes(fnd))
    out += _sf(FNI, fni)
    out += _sf(FNN, fnn)
    out += _sf(FNG, cff_bytes)
    out += _sf(EFN, _ebcdic8("C0CFF001"))
    out += _sf(0xD3A9A8, b"CFFFONT\x00")  # EDT
    return bytes(out)


def main() -> None:
    OUT.mkdir(exist_ok=True)
    plain = OUT / "cff_sample.otf"
    cff_bytes = build_plain_otf(plain)
    print(f"wrote {plain.name} (CFF {len(cff_bytes)} bytes)")

    cid = OUT / "cff_cid_sample.cff"
    cid_bytes = build_cid_cff()
    cid.write_bytes(cid_bytes)
    print(f"wrote {cid.name} ({len(cid_bytes)} bytes)")

    afp = OUT / "foca_cff_sample.afp"
    afp.write_bytes(build_foca_cff_afp(cff_bytes))
    print(f"wrote {afp.name}")


if __name__ == "__main__":
    main()
