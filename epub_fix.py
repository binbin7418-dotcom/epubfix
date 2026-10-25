#!/usr/bin/env python3
"""
EPUBFix — EPUB publication pre-check, repair & re-check engine.

Audit (deterministic, stdlib only):
  - archive structure: mimetype entry (must exist, be first, be stored
    uncompressed, exact content), container.xml present & parseable,
    rootfile points at an existing OPF
  - OPF: parseable, metadata has dc:title / dc:language / dc:identifier,
    manifest ids unique, every manifest href exists in the archive,
    every spine itemref resolves to a manifest id, NCX/nav declared
  - archive hygiene: files not referenced by the manifest (orphans)

Repairs (only the ones with exactly one correct form):
  - rebuild the archive with the mimetype first & stored, exact content
  - rewrite container.xml when it is missing/broken AND exactly one OPF
    exists in the archive

Everything else is reported in plain language, never fabricated:
a missing chapter or a missing language tag cannot be invented.
"""
import os
import re
import sys
import zipfile
import xml.etree.ElementTree as ET

MIMETYPE = b"application/epub+zip"
CONTAINER = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
    '  <rootfiles>\n'
    '    <rootfile full-path="{opf}" media-type="application/oebps-package+xml"/>\n'
    '  </rootfiles>\n'
    '</container>\n'
)
NS = {
    "opf": "http://www.idpf.org/2007/opf",
    "dc": "http://purl.org/dc/elements/1.1/",
    "ct": "urn:oasis:names:tc:opendocument:xmlns:container",
}


def _issue(lst, sev, where, msg):
    lst.append({"sev": sev, "where": where, "msg": msg})


def audit_epub(path):
    """Returns dict with issues list and facts about the book."""
    issues, facts = [], {"opf_path": None, "entries": []}
    try:
        zf = zipfile.ZipFile(path)
    except Exception as e:
        _issue(issues, "ERROR", "archive", f"not a readable EPUB (zip) file: {e}")
        return issues, facts
    names = zf.namelist()
    facts["entries"] = names

    # mimetype rules
    if "mimetype" not in names:
        _issue(issues, "ERROR", "mimetype", "mimetype entry is missing — required by the EPUB spec; many reading systems will reject the file")
    else:
        info = zf.getinfo("mimetype")
        if names[0] != "mimetype":
            _issue(issues, "ERROR", "mimetype", "mimetype is not the first entry in the archive — required by the EPUB spec")
        if info.compress_type != zipfile.ZIP_STORED:
            _issue(issues, "ERROR", "mimetype", "mimetype entry is compressed — the spec requires it stored uncompressed")
        if zf.read("mimetype") != MIMETYPE:
            _issue(issues, "ERROR", "mimetype", "mimetype content is not exactly 'application/epub+zip'")

    # container.xml -> OPF
    opf_path = None
    if "META-INF/container.xml" not in names:
        _issue(issues, "ERROR", "META-INF/container.xml", "container.xml is missing — reading systems cannot find the book's package file")
    else:
        try:
            root = ET.fromstring(zf.read("META-INF/container.xml"))
            rf = root.find(".//ct:rootfile", NS)
            if rf is None or not rf.get("full-path"):
                _issue(issues, "ERROR", "META-INF/container.xml", "container.xml has no rootfile entry pointing at the OPF")
            else:
                opf_path = rf.get("full-path")
                if opf_path not in names:
                    _issue(issues, "ERROR", "META-INF/container.xml", f"container.xml points at '{opf_path}', which is not in the archive")
                    opf_path = None
        except ET.ParseError as e:
            _issue(issues, "ERROR", "META-INF/container.xml", f"container.xml is not valid XML: {e}")
    if opf_path is None:
        opfs = [n for n in names if n.lower().endswith(".opf")]
        if len(opfs) == 1:
            opf_path = opfs[0]
            facts["opf_recoverable"] = True
    facts["opf_path"] = opf_path
    if not opf_path:
        return issues, facts

    # OPF audit
    try:
        opf = ET.fromstring(zf.read(opf_path))
    except ET.ParseError as e:
        _issue(issues, "ERROR", opf_path, f"the OPF package file is not valid XML: {e}")
        return issues, facts
    base = os.path.dirname(opf_path)

    def full(href):
        return os.path.normpath(os.path.join(base, href)).replace(os.sep, "/") if base else href

    meta = opf.find("opf:metadata", NS)
    for tag, label in (("title", "dc:title"), ("language", "dc:language"), ("identifier", "dc:identifier")):
        el = meta.find(f"dc:{tag}", NS) if meta is not None else None
        if el is None or not (el.text or "").strip():
            _issue(issues, "ERROR", opf_path, f"metadata is missing {label} — required by the EPUB spec; stores reject or mislabel the book")

    manifest = {}
    man_el = opf.find("opf:manifest", NS)
    if man_el is not None:
        for item in man_el.findall("opf:item", NS):
            iid, href = item.get("id"), item.get("href")
            if iid in manifest:
                _issue(issues, "ERROR", opf_path, f"duplicate manifest id '{iid}' — ids must be unique")
            manifest[iid] = (href, item.get("media-type"), item.get("properties") or "")
            if href and full(href) not in names:
                _issue(issues, "ERROR", opf_path, f"manifest item '{iid}' points at '{href}', which is missing from the archive — the book will fail validation and may not open")

    spine = opf.find("opf:spine", NS)
    if spine is not None:
        for ref in spine.findall("opf:itemref", NS):
            if ref.get("idref") not in manifest:
                _issue(issues, "ERROR", opf_path, f"spine references idref '{ref.get('idref')}', which is not in the manifest")

    has_nav = any("nav" in (props.split()) for _, (_, _, props) in
                  [(k, v) for k, v in manifest.items()])
    has_ncx = any((mt or "").endswith("ncx+xml") or (href or "").endswith(".ncx")
                  for href, mt, _ in manifest.values())
    if not has_nav and not has_ncx:
        _issue(issues, "WARN", opf_path, "no navigation document (EPUB 3 nav) or NCX declared — older reading systems will show no table of contents")

    referenced = {full(h) for h, _, _ in manifest.values() if h}
    keep = {"mimetype", "META-INF/container.xml", opf_path}
    orphans = [n for n in names if n not in referenced and n not in keep and not n.endswith("/")]
    for n in orphans:
        _issue(issues, "WARN", n, "file is in the archive but not referenced by the manifest — it bloats the book and some validators flag it")
    return issues, facts


def rebuild(path, out_path, opf_path=None):
    """Rewrite the archive spec-correctly. Returns list of fix descriptions."""
    fixes = []
    zf = zipfile.ZipFile(path)
    names = zf.namelist()
    need_container = "META-INF/container.xml" not in names or opf_path is None
    if opf_path is None:
        opfs = [n for n in names if n.lower().endswith(".opf")]
        if len(opfs) == 1:
            opf_path = opfs[0]
    with zipfile.ZipFile(out_path, "w") as out:
        out.writestr(zipfile.ZipInfo("mimetype"), MIMETYPE, zipfile.ZIP_STORED)
        fixes.append("archive rebuilt with the mimetype entry first, stored uncompressed, with the exact spec content")
        wrote_container = False
        for name in names:
            if name == "mimetype":
                continue
            if name == "META-INF/container.xml" and need_container and opf_path:
                out.writestr(name, CONTAINER.format(opf=opf_path), zipfile.ZIP_DEFLATED)
                wrote_container = True
                fixes.append(f"container.xml rewritten to point at the package file '{opf_path}'")
                continue
            out.writestr(name, zf.read(name), zipfile.ZIP_DEFLATED)
        if not wrote_container and need_container and opf_path and "META-INF/container.xml" not in names:
            out.writestr("META-INF/container.xml", CONTAINER.format(opf=opf_path), zipfile.ZIP_DEFLATED)
            fixes.append(f"container.xml created, pointing at the package file '{opf_path}'")
    return fixes


def run_epubfix(src_path, outdir):
    os.makedirs(outdir, exist_ok=True)
    base = os.path.splitext(os.path.basename(src_path))[0]
    before, facts = audit_epub(src_path)
    fixed_path = os.path.join(outdir, base + "_fixed.epub")
    structural = [i for i in before if i["where"] in ("mimetype", "META-INF/container.xml")]
    fixes = []
    if structural:
        fixes = rebuild(src_path, fixed_path, facts.get("opf_path"))
        after, _ = audit_epub(fixed_path)
    else:
        import shutil
        shutil.copyfile(src_path, fixed_path)
        after = before

    def cnt(lst, sev):
        return sum(1 for i in lst if i["sev"] == sev)

    errs = cnt(after, "ERROR")
    lines = ["# EPUBFix — pre-check, repair & re-check report", "",
             f"Source file: {os.path.basename(src_path)}  ·  Package file: {facts.get('opf_path') or 'not found'}", "",
             "## Summary",
             f"- Problems found: {len(before)} ({cnt(before,'ERROR')} errors, {cnt(before,'WARN')} warnings)",
             f"- Auto-fixed: {len(fixes)} structural repair(s)",
             f"- Remaining after re-check: {len(after)} ({errs} errors, {cnt(after,'WARN')} warnings)", "",
             "## What was fixed automatically"]
    lines += [f"- {f}" for f in fixes] or ["- nothing (no structural defects found)"]
    lines += ["", "## What still needs your input (cannot be auto-fixed)"]
    lines += [f"- [{i['sev']}] {i['where']}: {i['msg']}" for i in after] or \
             ["- nothing — the repaired file passes every check in this audit set"]
    lines += ["", "## Re-check verdict",
              f"- {'PASS with warnings' if errs == 0 else 'NOT READY'}: {errs} blocking errors remain. "
              "Scope note: this audit covers the EPUB package structure and metadata required by the spec; "
              "it does not judge the book's content, styling or store-specific category rules."]
    report_path = os.path.join(outdir, "fix_report.md")
    with open(report_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return {"found": len(before), "fixed": len(fixes), "remaining": len(after),
            "errors_remaining": errs, "fixed_path": fixed_path, "report_path": report_path}


def main(argv):
    if len(argv) != 3:
        print("usage: epub_fix.py <book.epub> <outdir>")
        return 2
    s = run_epubfix(argv[1], argv[2])
    print(f"found={s['found']} fixed={s['fixed']} remaining={s['remaining']} errors_remaining={s['errors_remaining']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
