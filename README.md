# EPUBFix — EPUB Pre-Check, Repair & Re-Check

EPUBFix audits an EPUB book file against the package rules of the EPUB spec,
repairs the structural defects that have exactly one correct form, then
**re-audits the repaired file** so you can see what was fixed and what still
needs your decision. 100% local — your manuscript never leaves your computer.

## What it checks
- mimetype entry: exists, is the **first** archive entry, stored **uncompressed**, exact spec content
- `META-INF/container.xml`: present, valid XML, rootfile points at a real OPF
- OPF package file: valid XML; `dc:title`, `dc:language`, `dc:identifier` present
- Manifest: unique ids; every referenced file actually exists in the archive
- Spine: every itemref resolves to a manifest entry
- Navigation: EPUB 3 nav or NCX declared; orphan files not referenced by the manifest

## What it fixes on its own
- Rebuilds the archive with the mimetype first, stored, exact content
- Creates/rewrites `container.xml` when it is missing or broken and exactly one OPF exists

## What it reports, but never silently changes
A missing chapter file, a missing language tag, a duplicate id — repairing
those would mean inventing content or metadata, so you get the exact location
and a plain-English explanation instead.

## Verified behaviour
On a seeded EPUB with 6 planted defects (compressed & misplaced mimetype,
missing container.xml, a manifest entry pointing at a missing chapter,
missing dc:language, an orphan file), EPUBFix repaired the 2 structural
defects and the re-check confirmed the remaining 3 items (2 blocking errors)
with 0 new issues introduced.

## Run from source
```
python epub_fix.py your_book.epub output_folder
```
Writes `<name>_fixed.epub` and `fix_report.md`. Standard library only.
The Windows app (`gui.py`, packaged with PyInstaller) wraps the same engine.

## Scope
Package structure and spec-required metadata. Content quality, styling and
store-specific category rules are out of scope.
