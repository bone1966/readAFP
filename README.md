# readAFP 🔍

A web app for reading AFP (Advanced Function Presentation) files.

Current state: **inspector + first-pass renderer** — upload an AFP file
and see its full MO:DCA structure as a nested tree (field IDs, EBCDIC
token names, sizes, hex previews, per-type counts) side by side with an
SVG render of each page. The split view is resizable, either pane can
be shown on its own, and the panes are linked: clicking a field jumps
the render to its page, paging the render scrolls the inspector, and
field counts filter the table.

## What renders today (and what doesn't)

Works: PTOCA text runs with positions and colors, fonts mapped from MDR
(family/weight/size, e.g. bold and headings come out right), rules
(lines, bands, table borders), and object-container images (JPEG, PNG,
GIF placed via IOB) — enough to closely reproduce a modern
TrueType-based AFP like `testdata/sample1_health/`.

Not yet: FOCA raster / embedded TrueType font metrics, IOCA image
objects, GOCA vector graphics, BCOCA barcodes, rotated text (STO),
EBCDIC code-page selection via MCF (cp500 assumed), and documents that
carry PTX outside BPG/EPG page brackets (these show zero pages — see
the corpus notes and backlog in
[docs/RESEARCH.md](docs/RESEARCH.md)).

## Run

```bash
pip install -r requirements.txt
python run.py
```

Then open <http://127.0.0.1:8770> and upload an `.afp` file — try
`testdata/sample1_health/01_Health_Coverage.afp`.

## Test

```bash
pytest
```

## Project layout

```
readAFP/
├── run.py                    # Launcher
├── src/readafp/
│   ├── parser.py             # MO:DCA structured-field parser
│   ├── ptoca.py              # PTOCA text decoding + page extraction
│   ├── render.py             # Page -> SVG renderer
│   ├── app.py                # Flask app
│   └── templates/index.html  # Split-pane inspect/render UI
├── tests/
├── testdata/                 # 139 real AFP files for testing
│   ├── sample1_health/       # AFP + PDF + HTML of the same doc
│   └── alpheus-corpus/       # Alpheus parser test suite
└── docs/
    ├── RESEARCH.md           # Format study + reader survey + roadmap
    └── specs/                # Official AFP Consortium reference PDFs
```

See [docs/RESEARCH.md](docs/RESEARCH.md) for the AFP format primer,
the survey of existing readers (BTB, IBM Workbench, AFP Explorer…),
and the rendering roadmap.
