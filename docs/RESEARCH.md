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
| **AFP Explorer / AFPviewer** (compulsivecode.com) | Freeware structure explorer — tree of structured fields, exactly what our first milestone does in the browser. |
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
  the AFP reference manuals themselves rendered as AFP (text-heavy,
  ~66k PTX fields), `minimal.afp`, `perf_ptx.afp` (performance),
  `large_ibm273.afp` (German EBCDIC code page), plus per-architecture
  folders (GOCA, BCOCA, IOCA, line data, …).
- Note: 7 files under `alpheus-corpus/external/` are saved HTML error
  pages, not AFP — keep as negative-test fixtures.

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
5. **Quality-of-life** — page thumbnails, search, export page as
   PNG/PDF, drag-and-drop upload (BTB feature parity, but in browser).
