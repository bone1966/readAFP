# readAFP 🔍

A web app for reading AFP (Advanced Function Presentation) files.

Current state: **inspector + first-pass renderer** — upload an AFP file
and see its full MO:DCA structure as a nested tree (field IDs, EBCDIC
token names, sizes, hex previews, per-type counts) side by side with a
rough SVG render of each page (PTOCA text runs, rules and colors in a
substitute font; images/graphics not yet drawn). The split view is
resizable, and either pane can be shown on its own.

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
