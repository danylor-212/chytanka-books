"""English classics: fetch from Standard Ebooks (GitHub source + `se build`) or
Project Gutenberg, and apply a *minimal* set of changes.

Policy (see README «English books»):
  * The text, markup, CSS, imprint, colophon and Uncopyright of Standard Ebooks
    editions are left untouched; the Project Gutenberg header and full licence are
    left untouched (PG licence §1.E.1/§1.E.4 require the licence to travel with the file).
  * We replace the cover with a Chytanka typographic cover (SE cover paintings are only
    "thought to be PD in the US"; the artists' death years are not checked by SE, and the
    covers are ~0.6 MB each), strip embedded fonts if any, and add an «About this edition»
    page that says the file is *derived from* — not an official release of — Standard
    Ebooks / Project Gutenberg.
  * dc:identifier becomes urn:chytanka:book:<slug> (so reading apps don't confuse our file
    with the upstream release); the upstream identifier is kept as dc:source.

Standard Ebooks' robots.txt disallows AI user agents on /ebooks/*/downloads/*, so this
module never touches standardebooks.org: it clones https://github.com/standardebooks/<repo>
(CC0 source) and builds the EPUB with SE's own toolset (`se build`, pip package
`standardebooks`) — exactly what their site serves as the "compatible" EPUB.
"""

from __future__ import annotations

import io
import os
import posixpath
import re
import shutil
import subprocess
import tempfile
import time
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path

from lxml import etree

from chytanka_books.epub import DC, NS, OPF, XML_PARSER, Report, cover_xhtml, esc, q, serialize_xhtml

SE_GITHUB = "https://github.com/standardebooks/{repo}.git"
PG_EPUB = "https://www.gutenberg.org/cache/epub/{id}/pg{id}-images-3.epub"
FONT_TYPES = ("font/", "application/font", "application/x-font", "application/vnd.ms-opentype")
MAX_IMG_PX = 800           # longest side of an illustration (the X4 panel is 480×800)
SHRINK_OVER_BYTES = 150_000


# --------------------------------------------------------------------------- fetch

def _run(cmd: list[str], **kw) -> str:
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **kw).stdout


def se_head_sha(repo: str) -> str:
    out = _run(["git", "ls-remote", SE_GITHUB.format(repo=repo), "HEAD"])
    return out.split()[0]


def fetch_se(repo: str, cache: Path, refresh: bool = False, se_bin: str | None = None) -> tuple[bytes, str]:
    """Return (compatible EPUB bytes, source commit sha). Cached per commit."""
    built = cache / "built"
    built.mkdir(parents=True, exist_ok=True)
    stamp = built / f"{repo}.sha"
    epub = built / f"{repo}.epub"
    if epub.exists() and stamp.exists() and not refresh:
        return epub.read_bytes(), stamp.read_text().strip()
    sha = se_head_sha(repo)
    if epub.exists() and stamp.exists() and stamp.read_text().strip() == sha:
        return epub.read_bytes(), sha
    src = cache / "src" / repo
    if src.exists():
        shutil.rmtree(src)
    src.parent.mkdir(parents=True, exist_ok=True)
    _run(["git", "clone", "-q", "--depth", "1", SE_GITHUB.format(repo=repo), str(src)])
    sha = _run(["git", "-C", str(src), "rev-parse", "HEAD"]).strip()
    se = se_bin or os.environ.get("SE_BIN") or shutil.which("se") or "se"
    with tempfile.TemporaryDirectory() as tmp:
        _run([se, "build", f"--output-dir={tmp}", str(src)])
        # SE names the file from the identifier, which can differ from the repo name
        # (multi-author books: "karl-marx-friedrich-engels_…"); take the non-"advanced" build.
        built_files = [f for f in Path(tmp).glob("*.epub") if not f.name.endswith("_advanced.epub")]
        if len(built_files) != 1:
            raise RuntimeError(f"se build produced {[f.name for f in Path(tmp).glob('*.epub')]}")
        shutil.copyfile(built_files[0], epub)
    stamp.write_text(sha)
    shutil.rmtree(src, ignore_errors=True)
    time.sleep(1)
    return epub.read_bytes(), sha


def fetch_pg(ebook: int, cache: Path, ua: str, refresh: bool = False) -> bytes:
    p = cache / "pg" / f"pg{ebook}.epub"
    if p.exists() and not refresh:
        return p.read_bytes()
    p.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(PG_EPUB.format(id=ebook), headers={"User-Agent": ua})
    with urllib.request.urlopen(req, timeout=120) as r:
        data = r.read()
    if not data.startswith(b"PK"):
        raise RuntimeError(f"PG #{ebook}: not an EPUB")
    p.write_bytes(data)
    time.sleep(2)
    return data


# --------------------------------------------------------------------------- credits page

def credits_en(book: dict, *, source: str, source_url: str, commit: str | None, changes: list[str], site_url: str) -> bytes:
    people = [f"{book['author']} (d. {fmt_year(book['author_died'])})"]
    for role in ("translator", "editor", "illustrator"):
        if book.get(role):
            died = book.get(f"{role}_died")
            people.append(f"{role}: {book[role]}" + (f" (d. {fmt_year(died)})" if died else ""))
    li = "\n".join(f"<li>{esc(c)}</li>" for c in changes)
    if source == "se":
        origin = (f'<p>This ebook is derived from the <a href="{esc(source_url)}">Standard Ebooks</a> edition, built '
                  f'with Standard Ebooks’ own tools from their public source repository '
                  f'<a href="https://github.com/standardebooks/{esc(book["se"])}">github.com/standardebooks/{esc(book["se"])}</a>'
                  + (f" (commit {esc(commit[:12])})" if commit else "") + ". "
                  "Standard Ebooks dedicates its work to the public domain (CC0 1.0).</p>"
                  "<p><b>This is not an official Standard Ebooks release.</b> Their imprint, colophon and Uncopyright "
                  "pages are kept unchanged; the colophon’s description of the cover refers to the original "
                  "Standard Ebooks cover, which this edition does not include.</p>")
    else:
        origin = (f'<p>This ebook is derived from <a href="{esc(source_url)}">Project Gutenberg eBook #{book["gutenberg"]}</a>. '
                  "The Project Gutenberg header and the full Project Gutenberg License are kept unchanged inside "
                  "this file, as that licence requires.</p>"
                  "<p><b>This is not an official Project Gutenberg release.</b></p>")
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="en" lang="en">
<head><meta charset="UTF-8"/><title>About this edition</title></head>
<body epub:type="backmatter">
<section epub:type="colophon" id="chytanka-about">
<h2>About this edition</h2>
<p><b>{esc(book['title'])}</b>, {esc(book['author'])}. First published {esc(fmt_year(book.get('published')))}.</p>
<p>{esc('; '.join(people))}. The text is in the public domain in the United States and in countries
whose copyright term is the author’s life plus 70 years or less.</p>
{origin}
<p><b>Changes made by Chytanka:</b></p>
<ul>
{li}
</ul>
<p>Chytanka’s changes are likewise dedicated to the public domain (CC0 1.0).
Catalogue: <a href="{esc(site_url)}">{esc(site_url)}</a></p>
</section>
</body>
</html>
""".encode()


def fmt_year(y) -> str:
    if y is None:
        return "unknown"
    y = int(y)
    return f"{-y} BC" if y < 0 else str(y)


# --------------------------------------------------------------------------- helpers

def drop_document(files: dict, items: dict, manifest, spine, full, iid: str) -> None:
    it = items.pop(iid)
    guide = manifest.getparent().find("opf:guide", NS)
    if guide is not None:
        for ref in guide.findall("opf:reference", NS):
            if (ref.get("href") or "").split("#")[0] == it.get("href"):
                guide.remove(ref)
    p = full(it.get("href"))
    files.pop(p, None)
    manifest.remove(it)
    for ir in spine.findall("opf:itemref", NS):
        if ir.get("idref") == iid:
            spine.remove(ir)
    name = re.escape(posixpath.basename(p).encode())
    for n in list(files):
        if n.endswith(".ncx"):
            files[n] = re.sub(rb'<navPoint\b(?:(?!<navPoint\b).)*?<content src="[^"]*' + name + rb'[^"]*"\s*/>\s*</navPoint>\s*',
                              b"", files[n], flags=re.S)
            counter = iter(range(1, 100000))
            files[n] = re.sub(rb'playOrder="\d+"', lambda m: b'playOrder="%d"' % next(counter), files[n])
        elif n.endswith((".xhtml", ".html")) and b'epub:type="toc"' in files[n]:
            files[n] = re.sub(rb'<li\b[^>]*>\s*<a href="[^"]*' + name + rb'[^"]*"[^>]*>.*?</a>\s*</li>\s*', b"", files[n],
                              flags=re.S)


def unlink_dangling_fragments(files: dict) -> int:
    """Unwrap <a href="doc#id"> whose id no longer exists (e.g. after removing figures)."""
    ids = {n: set(re.findall(rb'\sid="([^"]+)"', d)) for n, d in files.items() if n.endswith((".xhtml", ".html"))}
    link = re.compile(rb'<a\b([^>]*?)\shref="([^"#]*)#([^"]+)"([^>]*)>(.*?)</a>', re.S)
    total = 0
    for n in list(ids):
        here = posixpath.dirname(n)

        def fix(m):
            nonlocal total
            f = m.group(2).decode()
            target = posixpath.normpath(posixpath.join(here, f)) if f else n
            if target in ids and m.group(3) not in ids[target]:
                total += 1
                return m.group(5)
            return m.group(0)

        files[n] = link.sub(fix, files[n])
    return total


def gc_images(files: dict, items: dict, manifest, full, report: Report) -> int:
    """Remove image files that no XHTML/CSS references any more (never our cover)."""
    n = 0
    for iid, it in list(items.items()):
        if not it.get("media-type", "").startswith("image/") or iid == "chytanka-cover-image":
            continue
        if "cover-image" in (it.get("properties") or ""):
            continue
        p = full(it.get("href"))
        pat = re.compile(rb'["/(]' + re.escape(posixpath.basename(p).encode()) + rb'["#)\s,]')
        if not any(pat.search(d) for k, d in files.items() if k.endswith((".xhtml", ".css", ".html")) and k != p):
            files.pop(p, None)
            if it.getparent() is manifest:
                manifest.remove(it)
            del items[iid]
            report.images_removed.append(posixpath.basename(p))
            n += 1
    return n


def shrink_images(files: dict, items: dict, full, skip: set) -> int:
    """Grayscale + downscale big raster illustrations in place (same name/format)."""
    from PIL import Image

    n = 0
    for it in items.values():
        mt = it.get("media-type", "")
        if mt not in ("image/jpeg", "image/png", "image/gif"):
            continue
        p = full(it.get("href"))
        if p in skip or p not in files or "logo" in p or "titlepage" in p:
            continue
        data = files[p]
        try:
            im = Image.open(io.BytesIO(data))
            im.load()
        except Exception:  # noqa: BLE001
            continue
        if len(data) < SHRINK_OVER_BYTES // 3 and max(im.size) <= MAX_IMG_PX:
            continue
        has_alpha = im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info)
        if has_alpha:
            # SE line art is black-on-transparent: flatten onto white paper
            rgba = im.convert("RGBA")
            bg = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
            bg.alpha_composite(rgba)
            im = bg
        im = im.convert("L")
        if max(im.size) > MAX_IMG_PX:
            im.thumbnail((MAX_IMG_PX, MAX_IMG_PX), Image.LANCZOS)
        buf = io.BytesIO()
        if mt == "image/jpeg":
            im.save(buf, "JPEG", quality=80, optimize=True, progressive=False)
        else:
            # 16 grey levels = what the e-ink panel shows; 4-bit palette PNG is tiny
            im.quantize(16, dither=Image.Dither.NONE).save(buf, "PNG", optimize=True)
            if mt == "image/gif":
                buf = io.BytesIO()
                im.quantize(16, dither=Image.Dither.NONE).save(buf, "GIF")
        if buf.tell() < len(data):
            files[p] = buf.getvalue()
            n += 1
    return n


def split_large(files: dict, items: dict, manifest, spine, full, report: Report, limit: int = 280_000) -> int:
    """Split XHTML spine documents over `limit` bytes (tf.split_document) and fix every link to moved ids."""
    from chytanka_books import transforms as tf

    families: dict[str, list[str]] = {}  # full path of original -> [full paths of parts]
    for ir in list(spine.findall("opf:itemref", NS)):
        it = items.get(ir.get("idref"))
        if it is None or it.get("media-type") != "application/xhtml+xml":
            continue
        p = full(it.get("href"))
        if len(files.get(p, b"")) <= limit:
            continue
        root = etree.fromstring(files[p], XML_PARSER)
        parts = tf.split_document(root, limit=limit)
        if len(parts) < 2:
            continue
        stem, href = p.rsplit(".", 1)[0], it.get("href")
        hstem = href.rsplit(".", 1)[0]
        paths = [p] + [f"{stem}-p{k}.xhtml" for k in range(2, len(parts) + 1)]
        families[p] = paths
        files[p] = serialize_xhtml(parts[0])
        prev_it, prev_ref = it, ir
        for k, part in enumerate(parts[1:], 2):
            files[paths[k - 1]] = serialize_xhtml(part)
            nid = f"{it.get('id')}-p{k}"
            new_it = etree.Element(q("item", OPF), id=nid, href=f"{hstem}-p{k}.xhtml",
                                   attrib={"media-type": "application/xhtml+xml"})
            prev_it.addnext(new_it)
            new_ref = etree.Element(q("itemref", OPF), idref=nid)
            prev_ref.addnext(new_ref)
            items[nid] = new_it
            prev_it, prev_ref = new_it, new_ref
        report.chapters_split.append(f"{posixpath.basename(p)} -> {len(parts)} parts")
    if not families:
        return 0
    ids = {}
    for fam in families.values():
        for fp in fam:
            ids[fp] = set(re.findall(rb'\sid="([^"]+)"', files[fp]))
    fam_of = {fp: fam for fam in families.values() for fp in fam}
    link_re = re.compile(rb'((?:href|src)=")([^"#]*)#([^"]+)(")')
    for name in [n for n in files if n.endswith((".xhtml", ".ncx", ".html"))]:
        here = posixpath.dirname(name)

        def fix(m):
            f, frag = m.group(2).decode(), m.group(3)
            target = posixpath.normpath(posixpath.join(here, f)) if f else name
            if target not in fam_of or frag in ids.get(target, set()):
                return m.group(0)
            owner = next((fp for fp in fam_of[target] if frag in ids[fp]), None)
            if owner is None:
                return m.group(0)
            rel = "" if owner == name else posixpath.relpath(owner, here or ".")
            return m.group(1) + rel.encode() + b"#" + frag + m.group(4)

        new = link_re.sub(fix, files[name])
        files[name] = new
    return len(families)


# --------------------------------------------------------------------------- processing

def process_en(raw: bytes, book: dict, *, cover_jpeg: bytes, source_url: str, commit: str | None, site_url: str,
               modified: datetime) -> tuple[bytes, Report]:
    report = Report(slug=book["slug"], size_raw=len(raw))
    zin = zipfile.ZipFile(io.BytesIO(raw))
    files = {n: zin.read(n) for n in zin.namelist() if not n.endswith("/")}
    cont = etree.fromstring(files["META-INF/container.xml"], XML_PARSER)
    opf_path = cont.find(".//{urn:oasis:names:tc:opendocument:xmlns:container}rootfile").get("full-path")
    base = posixpath.dirname(opf_path)

    def full(h: str) -> str:
        return posixpath.normpath(posixpath.join(base, h)) if base else h

    def rel_from(doc_href: str, target_href: str) -> str:
        return posixpath.relpath(target_href, posixpath.dirname(doc_href) or ".")

    opf = etree.fromstring(files[opf_path], XML_PARSER)
    manifest, spine, md = opf.find("opf:manifest", NS), opf.find("opf:spine", NS), opf.find("opf:metadata", NS)
    items = {it.get("id"): it for it in manifest.findall("opf:item", NS)}
    changes: list[str] = []

    # 1. embedded fonts (SE "compatible" builds normally have none)
    for iid, it in list(items.items()):
        if it.get("media-type", "").startswith(FONT_TYPES):
            p = full(it.get("href"))
            report.fonts_removed.append(p)
            report.fonts_bytes += len(files.pop(p, b""))
            manifest.remove(it)
            del items[iid]
    if report.fonts_removed:
        for iid, it in items.items():
            if it.get("media-type") == "text/css":
                p = full(it.get("href"))
                files[p] = re.sub(rb"@font-face\s*\{[^}]*\}\s*", b"", files[p])
        changes.append(f"removed {len(report.fonts_removed)} embedded font file(s)")

    # 2. cover: drop the old cover image, add ours, point both EPUB3 and EPUB2 cover markers at it
    old_cover = next((it for it in items.values() if "cover-image" in (it.get("properties") or "").split()), None)
    if old_cover is None:
        meta = md.find("opf:meta[@name='cover']", NS)
        if meta is not None:
            old_cover = items.get(meta.get("content"))
    new_href = "images/chytanka-cover.jpg"
    new_full = full(new_href)
    files[new_full] = cover_jpeg
    cover_item = etree.SubElement(manifest, q("item", OPF), id="chytanka-cover-image", href=new_href,
                                  attrib={"media-type": "image/jpeg", "properties": "cover-image"})
    items["chytanka-cover-image"] = cover_item
    for m in md.findall("opf:meta[@name='cover']", NS):
        md.remove(m)
    etree.SubElement(md, q("meta", OPF), name="cover", content="chytanka-cover-image")
    old_full = None
    if old_cover is not None:
        old_full = full(old_cover.get("href"))
        props = [p for p in (old_cover.get("properties") or "").split() if p != "cover-image"]
        if props:
            old_cover.set("properties", " ".join(props))
        elif "properties" in old_cover.attrib:
            del old_cover.attrib["properties"]

    # cover *page*: rewrite any spine document that only shows the old cover image
    # (Project Gutenberg's wrap0000.xhtml); otherwise add our own cover page first.
    cover_page_done = False
    for ir in spine.findall("opf:itemref", NS):
        it = items.get(ir.get("idref"))
        if it is None or it.get("media-type") != "application/xhtml+xml" or old_full is None:
            continue
        p = full(it.get("href"))
        doc = files[p].decode("utf-8", "replace")
        text = re.sub(r"<[^>]+>|\s+", "", re.sub(r"<title>.*?</title>", "", doc, flags=re.S))
        if posixpath.basename(old_full) in doc and len(text) < 40:
            files[p] = cover_xhtml(book["title"], lang="en", img_src=rel_from(p, new_full))
            props = [x for x in (it.get("properties") or "").split() if x not in ("svg",)]
            if props:
                it.set("properties", " ".join(props))
            elif "properties" in it.attrib:
                del it.attrib["properties"]
            cover_page_done = True
            break
    if not cover_page_done:
        cp = full("chytanka-cover.xhtml")
        files[cp] = cover_xhtml(book["title"], lang="en", img_src=rel_from(cp, new_full))
        etree.SubElement(manifest, q("item", OPF), id="chytanka-cover", href="chytanka-cover.xhtml",
                         attrib={"media-type": "application/xhtml+xml"})
        spine.insert(0, etree.Element(q("itemref", OPF), idref="chytanka-cover", linear="yes"))
    # drop the old cover image if nothing references it any more
    if old_full is not None:
        pat = re.compile(rb'["/(]' + re.escape(posixpath.basename(old_full).encode()) + rb'["\')]')
        still = any(pat.search(data) for n, data in files.items()
                    if n.endswith((".xhtml", ".html", ".htm", ".css", ".ncx")) and "chytanka-" not in n)
        if not still:
            report.images_removed.append(posixpath.basename(old_full))
            files.pop(old_full, None)
            manifest.remove(old_cover)
            items.pop(old_cover.get("id"), None)
    changes.append("replaced the cover with a Chytanka typographic cover")

    # 2b. illustrations: strip (strip_images: artists died after 1953 or unverifiable) or shrink for e-ink
    xhtml_items = [it for it in items.values() if it.get("media-type") == "application/xhtml+xml"]
    if book.get("strip_images"):
        n = 0
        for it in xhtml_items:
            p = full(it.get("href"))
            if p == new_full or "chytanka-" in p:
                continue
            root = etree.fromstring(files[p], XML_PARSER)
            body = root.find(q("body"))
            changed = False
            for img in list(body.iter(q("img"))) if body is not None else []:
                target = img
                anc = img.getparent()
                while anc is not None and anc is not body:
                    if anc.tag == q("figure"):
                        target = anc
                    anc = anc.getparent()
                if target.getparent() is None:
                    continue
                parent = target.getparent()
                tail = target.tail
                prev = target.getprevious()
                parent.remove(target)
                if tail:
                    if prev is not None:
                        prev.tail = (prev.tail or "") + tail
                    else:
                        parent.text = (parent.text or "") + tail
                n += 1
                changed = True
            if changed:
                files[p] = serialize_xhtml(root)
        # the List of Illustrations now points at nothing: drop it (spine, manifest, nav, NCX)
        for iid, it in list(items.items()):
            p = full(it.get("href"))
            if it.get("media-type") == "application/xhtml+xml" and p in files and (
                    posixpath.basename(p) == "loi.xhtml" or re.search(rb'epub:type="[^"]*\bloi\b', files[p])):
                drop_document(files, items, manifest, spine, full, iid)
                report.other_removed.append(f"list of illustrations removed: {posixpath.basename(p)}")
        unlink_dangling_fragments(files)
        gc_images(files, items, manifest, full, report)
        if n:
            changes.append(f"removed {n} illustrations (their artists died after 1953 or could not be verified)")
    # @2x variants (srcset) are pointless on a 480×800 e-ink panel: drop srcset, then GC unreferenced images
    nsrc = 0
    for it in xhtml_items:
        p = full(it.get("href"))
        if p in files:
            new_data, k = re.subn(rb'\ssrcset="[^"]*"', b"", files[p])
            if k:
                files[p] = new_data
                nsrc += k
    gc = gc_images(files, items, manifest, full, report)
    if nsrc and gc:
        changes.append(f"dropped {gc} high-resolution @2x image variants (srcset)")
    shrunk = shrink_images(files, items, full, skip={new_full})
    if shrunk:
        changes.append(f"reduced {shrunk} illustrations to e-ink size (grayscale, at most {MAX_IMG_PX} px)")

    # 2c. split XHTML documents that are too big for the device (same rule as the Ukrainian books)
    nsplit = split_large(files, items, manifest, spine, full, report)
    if nsplit:
        changes.append(f"split {nsplit} very long file(s) into parts for the reader's memory limits")

    # 3. «About this edition» page, before the upstream end matter
    about = full("chytanka-about.xhtml")
    src_kind = "se" if book.get("se") else "pg"
    changes.append("added this page")
    files[about] = credits_en(book, source=src_kind, source_url=source_url, commit=commit, changes=changes,
                              site_url=site_url)
    etree.SubElement(manifest, q("item", OPF), id="chytanka-about", href="chytanka-about.xhtml",
                     attrib={"media-type": "application/xhtml+xml"})
    spine.append(etree.Element(q("itemref", OPF), idref="chytanka-about", linear="yes"))
    nav_item = next((it for it in items.values() if "nav" in (it.get("properties") or "").split()), None)
    if nav_item is not None:
        np_ = full(nav_item.get("href"))
        nav = etree.fromstring(files[np_], XML_PARSER)
        toc = next((n for n in nav.iter(q("nav")) if "toc" in (n.get("{http://www.idpf.org/2007/ops}type") or "")), None)
        ol = toc.find(q("ol")) if toc is not None else None
        if ol is not None:
            li = etree.SubElement(ol, q("li"))
            a = etree.SubElement(li, q("a"), href=rel_from(np_, about))
            a.text = "About this edition"
            files[np_] = serialize_xhtml(nav)

    # 4. metadata: our identifier (upstream id kept as dc:source), modified date, language check
    uid_attr = opf.get("unique-identifier")
    ident = next((e for e in md.findall("dc:identifier", NS) if e.get("id") == uid_attr), None)
    if ident is not None:
        src = etree.Element(q("source", DC), nsmap={"dc": DC})
        src.text = ident.text
        ident.addnext(src)
        ident.text = f"urn:chytanka:book:{book['slug']}"
        # EPUB2 NCX must carry the same unique identifier (epubcheck NCX-001)
        for n in [k for k in files if k.endswith(".ncx")]:
            files[n] = re.sub(rb'(<meta\s+(?:content="[^"]*"\s+)?name="dtb:uid"(?:\s+content="[^"]*")?)',
                              lambda m: re.sub(rb'content="[^"]*"', b'content="' + ident.text.encode() + b'"', m.group(1)),
                              files[n])
    for m in md.findall("opf:meta[@property='dcterms:modified']", NS):
        m.text = modified.strftime("%Y-%m-%dT%H:%M:%SZ")
    langs = [e.text for e in md.findall("dc:language", NS)]
    if not langs:
        el = etree.SubElement(md, q("language", DC), nsmap={"dc": DC})
        el.text = "en"
    files[opf_path] = b'<?xml version="1.0" encoding="utf-8"?>\n' + etree.tostring(opf, encoding="utf-8")

    # 5. write (mimetype first & stored, fixed timestamps)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zo:
        zi = zipfile.ZipInfo("mimetype", date_time=(2026, 1, 1, 0, 0, 0))
        zi.compress_type = zipfile.ZIP_STORED
        zo.writestr(zi, b"application/epub+zip")
        order = ["META-INF/container.xml", opf_path] + sorted(n for n in files if n not in ("mimetype", "META-INF/container.xml", opf_path))
        for n in order:
            zi = zipfile.ZipInfo(n, date_time=(2026, 1, 1, 0, 0, 0))
            zi.compress_type = zipfile.ZIP_STORED if n.endswith((".jpg", ".png", ".gif")) else zipfile.ZIP_DEFLATED
            zo.writestr(zi, files[n], compresslevel=9)
    data = out.getvalue()
    report.size_out = len(data)
    report.largest_xhtml = max((len(v) for k, v in files.items() if k.endswith((".xhtml", ".html", ".htm"))), default=0)
    report.other_removed = changes
    return data, report
