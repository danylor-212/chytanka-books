"""Structural EPUB checks in pure Python.

This is NOT epubcheck (which needs Java). It catches the failure modes that
matter for the Chytanka firmware and most readers:
  * zip: `mimetype` is the first entry, STORED, content `application/epub+zip`
  * container.xml -> OPF path exists
  * every XML/XHTML/OPF/NCX file is well-formed
  * every manifest item exists in the zip; no stray files outside the manifest
  * spine idrefs resolve; nav + ncx present
  * internal src/href references resolve to existing files
  * dc:language == uk, dc:title / dc:creator non-empty
  * cover-image item present and a real JPEG
  * no embedded fonts left
"""

from __future__ import annotations

import io
import posixpath
import re
import zipfile

from lxml import etree

OPF = "http://www.idpf.org/2007/opf"
DC = "http://purl.org/dc/elements/1.1/"
NS = {"opf": OPF, "dc": DC}


def validate_epub(data: bytes) -> list[str]:
    errors: list[str] = []
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        return [f"not a zip: {e}"]
    infos = z.infolist()
    if not infos or infos[0].filename != "mimetype":
        errors.append("mimetype is not the first zip entry")
    else:
        if infos[0].compress_type != zipfile.ZIP_STORED:
            errors.append("mimetype is compressed (must be STORED)")
        if z.read("mimetype") != b"application/epub+zip":
            errors.append("mimetype content is wrong")
        if infos[0].extra:
            errors.append("mimetype has zip extra field")
    names = set(z.namelist())
    bad = z.testzip()
    if bad:
        errors.append(f"CRC error in {bad}")

    parser = etree.XMLParser(resolve_entities=False, huge_tree=True)
    for n in names:
        if n.endswith((".xml", ".xhtml", ".opf", ".ncx", ".html", ".svg")):
            try:
                etree.fromstring(z.read(n), parser)
            except etree.XMLSyntaxError as e:
                errors.append(f"not well-formed: {n}: {e}")
        if "/fonts/" in "/" + n or n.lower().endswith((".ttf", ".otf", ".woff", ".woff2")):
            errors.append(f"embedded font left: {n}")

    try:
        cont = etree.fromstring(z.read("META-INF/container.xml"))
        opf_path = cont.find(".//{urn:oasis:names:tc:opendocument:xmlns:container}rootfile").get("full-path")
    except Exception as e:  # noqa: BLE001
        return errors + [f"container.xml unusable: {e}"]
    if opf_path not in names:
        return errors + [f"OPF missing: {opf_path}"]
    base = posixpath.dirname(opf_path)
    opf = etree.fromstring(z.read(opf_path))

    def full(h: str) -> str:
        return posixpath.normpath(posixpath.join(base, h))

    items = {it.get("id"): it for it in opf.findall("opf:manifest/opf:item", NS)}
    manifest_files = set()
    for iid, it in items.items():
        p = full(it.get("href"))
        manifest_files.add(p)
        if p not in names:
            errors.append(f"manifest item missing from zip: {p}")
    stray = names - manifest_files - {"mimetype", opf_path} - {n for n in names if n.startswith("META-INF/")}
    for s in sorted(stray):
        errors.append(f"file not in manifest: {s}")

    spine = opf.find("opf:spine", NS)
    if spine is None or not spine.findall("opf:itemref", NS):
        errors.append("empty spine")
    else:
        for ir in spine.findall("opf:itemref", NS):
            if ir.get("idref") not in items:
                errors.append(f"spine idref not in manifest: {ir.get('idref')}")
        toc = spine.get("toc")
        if toc and toc not in items:
            errors.append(f"spine toc={toc} not in manifest")
    if not any("nav" in (it.get("properties") or "").split() for it in items.values()):
        errors.append("no EPUB3 nav document")

    md = opf.find("opf:metadata", NS)
    lang = [e.text for e in md.findall("dc:language", NS)]
    if lang != ["uk"]:
        errors.append(f"dc:language is {lang}, expected ['uk']")
    for tag in ("title", "creator", "identifier"):
        vals = [e.text for e in md.findall(f"dc:{tag}", NS) if (e.text or "").strip()]
        if not vals:
            errors.append(f"dc:{tag} missing")
    uid_id = opf.get("unique-identifier")
    if not any(e.get("id") == uid_id for e in md.findall("dc:identifier", NS)):
        errors.append("unique-identifier does not point at a dc:identifier")
    if not md.findall("opf:meta[@property='dcterms:modified']", NS):
        errors.append("dcterms:modified missing")

    covers = [it for it in items.values() if "cover-image" in (it.get("properties") or "").split()]
    if len(covers) != 1:
        errors.append(f"expected exactly 1 cover-image item, found {len(covers)}")
    else:
        cp = full(covers[0].get("href"))
        if cp in names and not z.read(cp).startswith(b"\xff\xd8"):
            errors.append("cover-image is not a JPEG")
        meta_cover = md.find("opf:meta[@name='cover']", NS)
        if meta_cover is None or meta_cover.get("content") != covers[0].get("id"):
            errors.append("EPUB2 <meta name=cover> does not point at the cover image item")

    for iid, it in items.items():
        mt = it.get("media-type") or ""
        href = it.get("href") or ""
        if mt.startswith("image/") and not re.search(r"\.(jpe?g|png|gif|svg|webp)$", href, re.I):
            errors.append(f"image without extension: {href}")
        if mt in ("application/xhtml+xml",):
            p = full(href)
            if p not in names:
                continue
            doc = z.read(p).decode("utf-8", "replace")
            for m in re.finditer(r'(?:src|href)="([^"]+)"', doc):
                ref = m.group(1)
                if re.match(r"^[a-z]+:", ref) or ref.startswith("#"):
                    continue
                target = posixpath.normpath(posixpath.join(posixpath.dirname(p), ref.split("#")[0]))
                if target not in names:
                    errors.append(f"broken internal reference in {p}: {ref}")
    return errors
