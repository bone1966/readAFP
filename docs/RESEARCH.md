# AFP Research Notes

Research for building **readAFP**, a web app that reads AFP (Advanced
Function Presentation) files. Gathered 2026-06-11.

## 1. What AFP is

AFP is IBM's device-independent document/print architecture, born on
mainframes and still dominant in high-volume transactional printing
(bank statements, insurance policies, utility bills). An AFP document
is a binary stream of **structured fields** defined by **MO:DCA**
(Mixed Object Document Content Architecture). Content inside the
document is carried by per-type "Object Content Architectures":

| Architecture | Carries | Spec in `docs/specs/` |
|---|---|---|
| MO:DCA | document/page structure, resources | `modca-reference-10.pdf` |
| PTOCA | presentation text (the actual words) | `ptoca-reference-04.pdf` |
| IOCA | raster images | `ioca-reference-09.pdf` |
| GOCA | vector graphics | `afp-goca-reference-03.pdf` |
| BCOCA | bar codes | `bcoca-reference-11.pdf` |
| FOCA | fonts (raster + outline metadata) | `foca-reference-06.pdf` |
| Line Data | legacy line-mode data + page defs | `linedata-reference-05.pdf` |

The specs are maintained by the **AFP Consortium** and are free PDFs:
<https://www.afpconsortium.org/publications.html>. Also published
there but not downloaded: IPDS (printer protocol — not needed for a
reader), CMOCA (color management), MOCA (metadata).

## 2. The format, verified against real files

Every structured field record is:

```
0x5A        carriage-control byte (constant)
u16 length  big-endian, counts everything AFTER the 0x5A
u24 sf_id   0xD3, then type code, then category code
u8  flags
u16 sequence
data        (length - 8 bytes)
```

Verified by hex-dumping `testdata/alpheus-corpus/minimal.afp`:

```
5a 0010 d3a8a8 00 0000 c4d6c3f0f0f0f0f1   BDT "DOC00001"
5a 0010 d3a9a8 00 0000 c4d6c3f0f0f0f0f1   EDT "DOC00001"
```

Key facts learned:

- **Type code** (2nd ID byte): A8=Begin, A9=End, A6=Descriptor,
  AB=Map, AC=Position, AF=Include, EE=Data, A0=Attribute, B1=Migration.
- **Category code** (3rd ID byte): A8=Document, AF=Page, 9B=Presentation
  Text, FB=Image, BB=Graphics, EB=Bar Code, 92=Object Container,
  C6/CE=Resource Group/Resource, CC/CD=Medium/Form Map, C9=Active
  Environment Group, 5F=Page Segment, DF=Overlay.
- **Names are EBCDIC** (code page 500/037): Begin/End fields start
  with an 8-byte token name, e.g. `C4D6C3F0F0F0F0F1` = "DOC00001".
- **Hierarchy by bracketing**: BDT…EDT contains BPG…EPG pages, which
  contain BAG…EAG (environment setup: PGD page size, PTD text
  descriptor, MCF/MDR font maps) then object brackets (BPT…EPT with
  PTX text data, BIM…EIM with IPD image data, etc.).
- **Triplets**: many fields carry self-describing
  `(u8 length, u8 id, data)` sub-parameters — e.g. Fully Qualified
  Name (0x02), Object Classification (0x10), font references.
- **Resources travel in front**: print files often open with
  BRG/BRS…ERS/ERG resource groups (fonts, overlays, images as object
  containers — the health sample embeds TrueType fonts that way, mapped
  into pages via MDR and placed via IOB).
- **PTX is where text lives**: PTOCA control sequences (set coordinate,
  set font, transparent data…) inside PTX fields — this is the key
  architecture to implement for extracting/rendering readable text.
  Measurements are in "L-units" defined by the PGD/PTD (e.g. 1440/inch).
- **Page geometry**: PGD gives page extents; OBD/OBP place objects.

Facts verified while building the renderer (against
`sample1_health/01_Health_Coverage.afp`):

- **PTOCA rules extend in the +B/+I direction** from the current
  position, not centered on it. Producers draw filled bands (e.g. blue
  table headers) as thick rules and then place text *inside* the band:
  band at B=7095 with width 480 spans 7095–7575, and the white label's
  baseline lands at 7395. Get the direction wrong and white-on-color
  text silently disappears against the white page.
- **TRN text can be UTF-16BE**, not just EBCDIC — TrueType-based flows
  encode it that way (high byte 0x00 for Latin text, an easy sniff).
- **MDR repeating groups map everything the renderer needs about
  fonts**: FQN triplet 0x02 type 0xDE = full font name ("Arial Bold" →
  family + weight), triplet 0x8B byte 2-3 = point size in 1/20pt
  (which *is* L-units at 1440/inch: 180 = 9pt, 540 = 27pt), FQN type
  0xBE = the local id that PTX SCFL sequences select. No font files
  needed for system fonts.
- **Object containers can hold plain raster files**: the OCD data of
  the health sample is a raw JFIF JPEG (the logo) — magic-byte
  sniffing (JPEG/PNG/GIF) is enough to render it.
- **IOB placement**: name(8) reserved(1) ObjType(1, 0x92 = object
  container) XoaOset(3) YoaOset(3) orientation(4) XocaOset(3)
  YocaOset(3) RefCSys(1), then triplets; triplet 0x4C (Object Area
  Size) gives the placed extent in L-units.
- **Colors**: SEC triplet-style params (colorspace 0x01 = RGB with
  8/8/8 component sizes) carry exact RGB; STC has a small fixed
  two-byte palette.
- **Bullets may be drawn, not typed**: the health sample's square
  list bullets are five tiny rules each (a box outline plus a 30×30
  fill) — they render for free if rules are right. Its closing-list
  round bullets exist in the PDF/HTML versions but **not in the AFP
  at all** (no text, no rules at those positions; AFPviewer shows
  none either) — a ground-truth divergence in the sample, not a
  renderer bug.
- **Substitute-font drift is visible at line level**: e.g. the
  underlined URL — the AFP draws the underline rule at the exact
  width its font metrics produced, so text rendered a few percent
  narrower overshoots/undershoots it. Fixed by setting SVG
  `textLength` to the extent implied by the next run's position on
  the same baseline (ratio-guarded so column gaps and short runs
  don't distort).

Gotcha found in the corpus: anything not starting `0x5A` isn't a
(document-form) AFP stream. Mainframe-native files can also be wrapped
in record formats (RDW length prefixes) instead of 0x5A — worth
detecting later. MO:DCA also defines interchange sets (IS/3 is the
modern target).

## 3. Existing readers studied

| Reader | Notes for us |
|---|---|
| **BTB AFP Viewer / Browser** (btbnet.de, via afpworld.com) | Freeware Windows viewer the user knows. Loads any PSF/Infoprint AFP, zoom/rotate/fit, exports TIFF/JPEG/BMP/EPS, prints. Registration-walled download. Feature bar to aim for: page navigation, zoom, export. |
| **IBM AFP Workbench Viewer** | IBM's classic free viewer; the compatibility yardstick. |
| **AFP Explorer / AFPviewer** (compulsivecode.com) | Freeware structure explorer — tree of structured fields, exactly what our first milestone does in the browser. Tested 2026-06 on the health sample: its render garbles modern AFP — UTF-16BE TrueType text decoded as single-byte EBCDIC (mojibake), MDR font mapping ignored (heading words collide). readAFP renders the same file correctly — the "modern files" gap is real. |
| **ISIS Papyrus viewer, Compart, CrawfordTech, WinAFP** | Commercial suites (afp2pdf/afp2web conversion). Confirms the market gap: no modern open web viewer. |
| **yan74/afplib** (GitHub, Java) | Read/write library; EMF-generated classes per structured field; good reference for field coverage. |
| **afpdev/alpheusafpparser** (GitHub, Java, GPL) | Full parser covering MO:DCA + all OCAs; source of our 138-file test corpus. |
| **mdneale/afp** (GitHub/PyPI, Python) | Python reader with `dumpafp`/`afp2ascii` utilities — closest prior art to our stack. |
| **Apache FOP AFP renderer** (Java) | Open-source AFP *writer* — clean reference for how valid AFP is constructed (the other direction of our parser). |

Useful link hub: Apache FOP "AFP Resources" wiki
(<https://cwiki.apache.org/confluence/display/XMLGRAPHICSFOP/AFPResources>).

## 4. Test corpus (in `testdata/`)

- `sample1_health/01_Health_Coverage.afp` — modern AFP with embedded
  TrueType fonts, **plus the same document as PDF and HTML** for
  ground-truth comparison (from afpworld.com).
- `alpheus-corpus/` — 138 real AFP files from the Alpheus test suite:
  `minimal.afp`, `perf_ptx.afp` (performance), `large_ibm273.afp`
  (German EBCDIC code page), plus per-architecture folders (GOCA,
  BCOCA, IOCA, line data, …).
- Note: 7 files under `alpheus-corpus/external/` are saved HTML error
  pages, not AFP — keep as negative-test fixtures.
- `github-samples/` — 16 Apache-2.0 files gathered 2026-06-12 from
  yan74/afplib and apache/xmlgraphics-fop (see its README). Headline:
  **9 files with real IOCA image objects** (`bim.afp` is the big one)
  — the missing fixtures for the IOCA backlog item — plus FOCA font
  resources whose fields the parser doesn't name yet. All 16 parse.
  Still no AFP+PDF matched pairs in the wild; the practical route is
  generating them with Apache FOP (same XSL-FO → AFP and PDF), which
  needs a Java runtime.
- `fop-pairs/` — **8 matched AFP+PDF pairs** generated 2026-06-12 with
  FOP 2.11 (see its README for the recipe). First real multi-page
  bracketed files in the corpus, IOCA images with PDF ground truth,
  and two renderer lessons already learned: FOP emits **240
  units/inch** (we assumed 1440-scale defaults — now
  resolution-relative), and its MCF-mapped fonts declare no sizes, so
  size estimation now prefers **baseline pitch** (median distance
  between a font's consecutive baselines ÷ 1.2) over horizontal gaps,
  which table columns pollute.

What the corpus actually contains (learned while building the
renderer — all 138 files parse, but render coverage is thin):

- The per-architecture "reference" folders are mostly **empty shells**:
  BDT/EDT brackets with no pages or content (e.g.
  `modca-reference-10/Chapter_1.afp` is just BDT+EDT). Good parser
  fixtures, useless for rendering.
- `perf_ptx.afp` (65,536 PTX) and `large_ibm273.afp` (444 PTX) carry
  their PTX **directly under BDT with no BPG/EPG page brackets** —
  and no positioning at all (pure TRN chains), so content is flowed
  onto implicit pages like a text dump. `large_ibm273.afp` is "Hello
  World" repeated in cp273; it renders as mojibake until MCF code-page
  mapping lands (see backlog).
- Only `sample1_health/01_Health_Coverage.afp` exercises the full
  render path (1 page, 306 text runs, MDR-mapped TrueType fonts,
  JPEG logo in an object container). There is **no real multi-page
  file** in the corpus; multi-page behaviour is covered by a synthetic
  document in `tests/test_ptoca.py`.

## 5. Web app plan

Stack: Python/Flask (matches our other projects), no heavy deps.

Milestones:
1. ✅ **Structured-field inspector** — parse SF stream, show nested
   tree, token names, hex preview (`src/readafp/parser.py` + Flask app;
   parses 138/138 corpus files).
2. 🔶 **Triplet decoding** + per-field detail view — `iter_triplets()`
   plus MDR font mapping done (FQN font names, data-object font
   descriptor sizes, local ids → family/weight/size for the renderer).
   Still to do: general triplet decoding in the inspector's detail view.
3. ✅ **Text extraction** — PTOCA control sequences decoded from PTX
   fields (`src/readafp/ptoca.py`): moves, text runs, rules, colors
   (STC/SEC), with UTF-16BE/EBCDIC heuristics. Still to do: real code
   page mapping via MCF/MDR triplets (depends on milestone 2).
4. 🔶 **Page rendering** — first pass done (`src/readafp/render.py` +
   split-pane UI): PTX text and rules on an SVG sized from the PGD,
   font family/weight/size mapped from MDR (estimation as fallback),
   and object-container images (JPEG/PNG/GIF in OCD, placed via IOB)
   embedded in the SVG. Still to do: FOCA/embedded-TrueType metrics,
   IOCA images (raw + JPEG/CCITT wrapped), GOCA vectors, BCOCA
   barcodes, text orientation (STO rotations).
5. 🔶 **Quality-of-life** — done: drag-and-drop upload (drop an .afp
   anywhere), copy-page-text and download-all-as-.txt buttons, zoom
   (fit width / 50–400%, where 100% = real size via the page's
   units-per-inch at 96 css px/inch), and an X,Y pointer readout in
   inches for checking positions against the spec. Still to do: page
   thumbnails, search, export page as PNG/PDF (BTB feature parity,
   but in browser).

## 6. Backlog / known limitations

Design principle (decided 2026-06-12): **render what the file says —
don't invent features the format doesn't have.** Example: AFP has no
web hyperlinks (URLs are just glyphs + a rule; MO:DCA's LLE is for
archive-viewer navigation and appears in 0 of our 138 corpus files),
so we deliberately do not auto-linkify URL-looking text. readAFP's
value as a diagnostic tool depends on showing the file's actual
content, nothing more.

Carried over from build sessions, roughly in priority order:

- ✅ **Implicit page for unbracketed PTX** — loose PTX now flows onto
  implicit pages: wrapped at the page width, paginated into
  letter-height chunks, capped at `MAX_RUNS_PER_PAGE` (5000) runs with
  a truncation note (perf_ptx.afp carries 65k PTX fields).
- ✅ **Run-level highlighting** — text runs and rules carry the offset
  of the PTX field that produced them (`data-src` in the SVG);
  clicking an inspector row flashes exactly that content (text
  recolors, rules/images get an outline so background fills don't
  flood the page).
- 🔶 **Code pages via MCF** — manual override done: a code-page
  dropdown (cp500/cp037/cp273/cp1047/cp1141) threads through to TRN
  decoding; `large_ibm273.afp` + cp273 now reads "Hällö Wörld" (the
  fixture's text is German, plus a deliberate full-byte-range stress
  section that correctly renders as symbols). Still to do: honor the
  MCF/CGCSGID label automatically when the file declares one.

  *In plain words:* computers store letters as numbers, and a code
  page is the decoder ring that turns numbers back into letters. IBM
  made different rings for different countries — the German ring
  (cp273) swaps a few numbers compared to the international one
  (cp500) to make room for ä/ö/ü. Decode a file with the wrong ring
  and most letters survive but a few turn to junk: that's why
  `large_ibm273.afp` shows "H{ll[ W¦rld" instead of "Hello World".
  A well-formed file *labels* its ring via MCF/CGCSGID triplets —
  read that and decode correctly; this fixture carries no label at
  all, so the full fix is (a) honor the label when present and (b) a
  manual code-page override in the UI for unlabeled files.
- **Column-aware text ordering** — `Page.plain_text` (used by the
  copy/.txt buttons) sorts runs top-down then left-right, so
  multi-column layouts (e.g. the health sample's options table)
  interleave across columns instead of reading one column at a time.
  Fine for search/reference, not for transcription; would need column
  detection by x-position clustering.
- **Triplet detail view** — `iter_triplets()` exists; the inspector
  should decode and show triplets per field instead of a hex preview.
- **IOCA / GOCA / BCOCA objects** — images beyond object containers
  (IOCA raw + JPEG/CCITT wrapped), vector graphics, barcodes.
- **Font metrics** — embedded TrueType (in object containers) and FOCA
  raster fonts are ignored; system-font substitution can misplace
  advance widths. Spacing-based size estimation remains the fallback
  when no MDR declares sizes.
- **Rotated text** — STO orientations other than 0°/90° are treated as
  0°; rotated pages will render wrong.
- **RDW-wrapped streams** — mainframe record-format AFP (length
  prefixes instead of bare 0x5A stream) isn't detected yet.
- **Render cap** — up to 500 pages (`MAX_RENDER_PAGES` in `app.py`),
  stopping early once a ~50k-element content budget is spent
  (`pages_to_svgs`); dense multi-hundred-page files will still
  truncate, shown as "(first N of M rendered)" in the UI.
