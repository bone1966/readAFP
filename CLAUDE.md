# readAFP — AFP Viewer/Inspector

Flask web app that parses, inspects, and renders AFP (MO:DCA) files to SVG.
Upload an AFP; get a split-pane view: structured-field tree on the left, SVG render on the right.

## Run & Test

```bash
python run.py          # http://127.0.0.1:8770
pytest                 # full suite
pytest tests/test_ptoca.py -v   # single module
```

No build step. Dependencies: `flask>=3.0`, `segno>=1.6`, stdlib only (zlib, struct, base64, codecs).

## Project Layout

```
src/readafp/
  parser.py    # MO:DCA byte-stream parser → List[StructuredField]
  ptoca.py     # PTOCA decoder + page extraction → List[Page]
  render.py    # Page → SVG string
  triplets.py  # MO:DCA triplet decoding + describe_field()
  ioca.py      # IOCA image segment decoder → IocaImage / PNG / JPEG
  bcoca.py     # BCOCA bar code decoder → BarCode + QR PNG via segno
  goca.py      # GOCA drawing-order decoder → GocaGraphic / SVG fragment
  foca.py      # FOCA raster-font decoder → Font / Glyph bitmaps (PNG)
  app.py       # Flask app (POST /inspect), create_app()
  templates/index.html   # split-pane UI

tests/
  test_parser.py, test_ptoca.py, test_triplets.py,
  test_ioca.py, test_bcoca.py, test_app.py, test_foca.py, test_goca.py

testdata/
  sample1_health/     # modern TrueType AFP + PDF ground truth (1 page, 306 text runs)
  alpheus-corpus/     # 138 AFP files from alpheusafpparser test suite
  fop-pairs/          # 8 Apache FOP AFP+PDF pairs (multi-page, IOCA images)
  github-samples/     # 16 Apache-2.0 files from afplib and FOP
```

## AFP Structure Hierarchy

```
BDT (Begin Document)
  BRG…ERG  — resource group (fonts, images, overlays)
  BPG…EPG  — page
    BAG…EAG — active environment group
      PGD   — page geometry + units-per-inch
      PTD   — presentation text descriptor
      MCF   — Map Coded Font: local font id → EBCDIC codepage
      MDR   — Map Data Resource: local font id → FQN name + point size
    BPT…EPT / PTX  — presentation text object / control sequences
    BIM…EIM / IPD  — image object / image data
    BBC…EBC / BDD + BDA  — bar code object / descriptor + data
EDT (End Document)
```

## Key Domain Concepts

**L-units** — coordinate system unit; typically 1440/inch (1 pt = 20 L-units). FOP uses 240/inch. Always derived from PGD/PTD `units_per_inch`, never hardcoded.

**Structured fields** — fixed binary records: `0x5A` carriage-control, u16 length, 3-byte SF ID (`0xD3` + type_code + category_code), flags(1), sequence(2), data. Parsed by `iter_fields()`.

**PTOCA control sequences** — inside PTX data; escape `0x2B 0xD3` introduces a sequence, then length/type/params. Low bit of type = chained (next sequence follows without escape). Key sequences: AMI/RMI (inline position), AMB/RMB (baseline position), SBI (baseline increment), TRN (transparent text), SCFL (set font local id), STC/SEC (color), DIR/DBR (draw rule), STO (text orientation).

**Triplets** — self-identifying sub-parameters on structured fields: (u8 length, u8 id, data). Must tile their slot exactly. Key triplets: 0x02 FQN (Fully Qualified Name, with FQN type byte), 0x10 Object Classification, 0x82 Parameter Value.

**MCF/MDR** — MCF declares EBCDIC codepage per font local-id; MDR maps local-id to font family/weight/size via FQN triplets. Both parsed in `ptoca.py`; `parse_mcf_codepages()` also in `triplets.py`.

**TRN decoding** — if high byte of first two bytes is `0x00`, treat as UTF-16BE (TrueType). Otherwise decode as EBCDIC using the codepage for the active font (from MCF), falling back to the user-selected codepage.

**IOCA images** — BIM…EIM bracket; IPD fields carry concatenated self-defining fields (SDFs). Key SDFs: 0x94 Image Size, 0x95 Image Encoding, 0x96 IDE Size, 0xFE92 Image Data, 0xFE9C Band Image Data (CMYK planes). Compressions: 0x03 = uncompressed, 0x83 = JPEG. Bilevel inverted (IOCA 1 = mark/dark).

**CMYK composite** — four grayscale JPEG planes (one per ink). Renderer applies `feColorMatrix` filters to invert each plane to its complement color, then `mix-blend-mode: multiply` to optically compose: R=(1-C)(1-K), G=(1-M)(1-K), B=(1-Y)(1-K).

**BCOCA / QR** — BDD (descriptor) + BDA (data) bytes parsed by `parse_barcode()`. Only QR (`type=0x1C`) in corpus. BDA byte 5 bit 0 triggers EBCDIC→ASCII translation (codec from byte 6). Version = byte 7 (0 = auto), EC level = byte 8 (0-3 = L/M/Q/H). Symbol generated with `segno`.

## Data Model (ptoca.py)

```python
TextRun(x, y, text, color, font_id, font_size, font_family, font_weight, src)
Rule(x, y, length, thickness, axis, color, src)   # axis: "I"=horiz, "B"=vert
ImageRef(x, y, width, height, mime, data, bands, crisp)   # bands = CMYK list
Page(width, height, units_per_inch, texts, rules, images, truncated)
```

`Page.plain_text` joins text runs in reading order.

## SVG Rendering (render.py)

- `page_to_svg(page)` — builds SVG; viewBox in L-units.
- Rules: `<rect>` extending in +I (right) or +B (down) direction from position.
- Text: substitute fonts (Arial); `textLength` + `lengthAdjust="spacing"` stretches run to AFP-implied width based on next run's position (`_fit()`). Ratio-guarded to avoid excessive distortion.
- Images: `<image>` with base64 data URI. CMYK planes use `<filter>` + `mix-blend-mode: multiply`. Bilevel/barcode: `image-rendering: pixelated` (`crisp=True`).

## Rendering Fidelity Principle

Render only what the AFP contains. No invented features (no auto-linkified URLs, no assumed defaults beyond what PGD/MDR specify). If data is missing, skip or log — never fabricate.

## SF Type and Category Codes

Type codes: `0xA8`=Begin, `0xA9`=End, `0xA6`=Descriptor, `0xAB`=Map, `0xAC`=Position, `0xAF`=Include, `0xEE`=Data, `0xB1`=Migration.

Category codes: `0xA8`=Document, `0xAF`=Page, `0x9B`=Presentation Text, `0xFB`=Image, `0xBB`=Graphics, `0xEB`=Bar Code, `0x92`=Object Container, `0xC6`/`0xCE`=Resource.

## Corpus Notes

- `alpheus-corpus/`: 138 files, mostly structural shells; all parse without error.
- `sample1_health/`: primary render target — 1-page modern TrueType AFP with PDF ground truth.
- `fop-pairs/`: multi-page AFP with IOCA images; paired PDF ground truth for visual comparison.
- `github-samples/`: edge cases including FOCA raster fonts, unbracketed PTX, IOCA variants.

## FOCA (font objects)

Embedded raster fonts (BFN…EFN) are decoded in `foca.py`: FNI metrics
(GCGID, character increment, FNM index) join FNM box dimensions and FNG
1-bit-per-pel pattern data to rebuild each glyph as a PNG (FOCA toned
pel = 1, inverted to PNG's 0=black). Font-resource files with no
document pages render a specimen sheet (one page per raster font, glyph
grid labeled by GCGID) via `_font_specimen_pages()` in `ptoca.py`.

Outline fonts (Type 1 PFB / CID) have no bitmaps. The embedded font
program in the FNG is interpreted by `type1.py` (PFB extraction, eexec +
charstring decrypt, a Type 1 charstring interpreter with callsubr / flex
/ hint-replacement / `seac` accent composition) into outline paths.
`_decode_outlines()` in `foca.py` decodes each character's glyph keyed by
GCGID, attaching `outline_glyphs` + `units_per_em` to the `Font`.
`_outline_glyph_page()` then draws the **actual glyph shapes** as SVG
`<path>`s (one `VectorGraphic` per glyph, baseline-aligned, labeled by
name) — the font's true printed outline. If the outlines can't be
decoded (a real CFF/CID font, or a decode failure), `_outline_font_page()`
falls back to a metadata sheet: typeface, the technology sniffed from the
FNG header (`%!PS-AdobeFont` ⇒ Adobe Type 1 PFB), the character count, and
a `GCGID · glyph name · increment` grid. FNI records repeat per rotation,
so `_dedup_orientations()` keeps one entry per GCGID and reports the
orientation count; `_fnn_glyph_names()` resolves each GCGID to its
readable name via the Font Name Map (FNN). Specimen text sets
`TextRun.fit = False` so the fixed grid is never width-fitted by
`render._fit()`. Decoded advance widths match the FNI increments exactly,
the independent oracle for interpreter correctness.

## Page Overlays (BMO/EMO + IPO)

`extract_pages` captures a `BMO…EMO` overlay like a page, keyed by its
EBCDIC name. A page's `IPO` (Include Page Overlay) field then composites
that overlay's text/rules/images/graphics onto the page via
`_include_overlay()`, shifted by the IPO X/Y offset (name = 8 EBCDIC
bytes + 3+3 signed offset) and scaled if the overlay declared a
different resolution. IPO references the overlay by name directly; MPO
local-id→name indirection is not yet handled.

## What's Not Yet Implemented

- FOCA outline fonts: Adobe Type 1 (PFB) glyph shapes ARE now rasterized
  to SVG paths for the specimen (`type1.py`). Still open: (a) true CFF /
  CID-keyed (Type 0) fonts use Type 2 charstrings — a different
  interpreter, so those fall back to the metadata sheet; (b) the decoded
  outlines are not yet used for *document* text — PTOCA runs still
  substitute Arial. Feeding embedded outlines into document text (Phase B,
  the real "render what a printer marks" goal) is the next project: it
  needs the code-page → GCGID mapping and per-run scaling/positioning.
- FNI character-increment widths are not yet fed into document text
  fitting (render still uses position-anchored width estimation; the
  primary health-coverage sample embeds no fonts, so it cannot benefit).
- MPO (Map Page Overlay) local-id indirection — IPO-by-id files unhandled.
- GOCA partial-arc (GPARC/GCPARC) and character-string orders — partially implemented; arc sweep direction may be off for some angles.
- Unbracketed PTX fully handled (implicit page captures it, but no environment group).

Done recently: STO text orientation (0/90/180/270°); FOCA raster-glyph
specimens; FOCA outline-font specimens — Type 1 PFB charstrings rasterized
to real glyph shapes (`type1.py`), with metadata-sheet fallback (PFB
detection, orientation dedup, FNN glyph names, fit-exempt grid); a red
"AFP resource" warning banner for page-less streams (font sets, code
pages, overlays…) with standalone-overlay rendering; a "missing resources"
panel listing font resources an MCF references but the file doesn't embed
(`app._missing_resources` / `triplets.mcf_resource_names`), matching what
other AFP viewers report and explaining substitute-font fallback; page
overlays (BMO/EMO + IPO); `__version__ = "1.0.0"` shown in the About modal with
the live Python version. Landing page bundles 6 sample cards (PTOCA /
GOCA / IOCA / FOCA / BCOCA / overlay), each generated by a
`tools/make_*_sample.py` script.
