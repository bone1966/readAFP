# GitHub-sourced AFP test files

Both sources are Apache-2.0 licensed; files unchanged, gathered 2026-06-12.

## afplib/ — from [yan74/afplib](https://github.com/yan74/afplib)

| File | What it exercises |
|---|---|
| `bim.afp` | **IOCA image object** (BIM/IPD, 13 data fields, 260KB) — primary IOCA fixture |
| `IPDSpan.afp` | IOCA image with IPD data spanning multiple fields |
| `C0X00006.afp` | Coded-font resource (FOCA) — fields our parser doesn't name yet |
| `fnirg10.afp` | Font resource with FNI repeating groups (FOCA) |
| `cs.afp` | Overlay + MCF coded-font mapping, 1 page with text |
| `asciiComment.afp`, `asciiAndEbcdicComment.afp` | NOP comments in ASCII/EBCDIC |
| `repeatingGroupVariableLength.afp` | Variable-length repeating groups |
| `unknownSF.afp` | Deliberately unknown structured field (negative fixture) |
| `hello.afp` | Minimal BDT/EDT |

(`ende.afp` / `start.afp` from the same repo already live in
`../alpheus-corpus/external/` as `afplib_ende.afp` / `afplib_start.afp`.)

## fop/ — from [apache/xmlgraphics-fop](https://github.com/apache/xmlgraphics-fop)

`fop-core/src/test/resources/org/apache/fop/afp/`: six resource-handling
test files, all containing IOCA image objects (BIM/IPD) wrapped in
resource groups / page segments.

## Note on matched AFP+PDF pairs

None of these have PDF ground truth. Apache FOP can *generate* matched
pairs (same XSL-FO source rendered to both AFP and PDF) — requires a
Java runtime, not currently installed on this machine.
