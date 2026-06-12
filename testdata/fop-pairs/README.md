# Matched AFP + PDF pairs, generated with Apache FOP

Each `<name>.afp` / `<name>.pdf` pair was rendered from the **same
XSL-FO source** (Apache FOP 2.11's `examples/fo/basic/<name>.fo`), so
the PDF is pixel-grade ground truth for the AFP. Generated 2026-06-12.
FOP and its examples are Apache-2.0.

Notable properties of FOP's AFP output (vs. the health sample):

- **240 L-units/inch** (the health sample uses 1440) — caught a
  resolution assumption in our font-size defaults.
- Fonts mapped via **MCF** (not MDR), with no declared point sizes —
  exercises the baseline-pitch size estimation.
- `images.afp` carries **IOCA image objects** (BIM/IPD) — render
  fixture for the IOCA backlog item, with PDF ground truth.
- Real **multi-page** documents (list: 12 pages, readme: 9, table: 7)
  — the only bracketed multi-page files in the corpus.

## Regenerating / adding pairs

```powershell
winget install EclipseAdoptium.Temurin.21.JRE   # once
# FOP unzipped at ~\tools\fop-2.11 (dlcdn.apache.org/xmlgraphics/fop/binaries)
$env:JAVA_HOME = "C:\Program Files\Eclipse Adoptium\jre-21.0.11.10-hotspot"
$env:PATH = "$env:JAVA_HOME\bin;$env:PATH"
$fop = "$env:USERPROFILE\tools\fop-2.11\fop\fop.bat"
& $fop -q -fo doc.fo -pdf doc.pdf
& $fop -q -fo doc.fo -afp doc.afp
```

Any XSL-FO document works — write a .fo exercising a feature, render
both ways, and the pair is its own test.
