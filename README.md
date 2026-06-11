# readAFP 🔍

A web app for reading AFP (Advanced Function Presentation) files.

Current state: **structured-field inspector** — upload an AFP file and
see its full MO:DCA structure as a nested tree (field IDs, EBCDIC token
names, sizes, hex previews, per-type counts).

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
│   ├── app.py                # Flask app
│   └── templates/index.html
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
