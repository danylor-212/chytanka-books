"""Post-processing of WS Export EPUB3 files for the Chytanka catalogue.

Input:  raw EPUB from https://ws-export.wmcloud.org (lang=uk, format=epub-3)
Output: EPUB3 with EPUB2 fallbacks, no embedded fonts, own cover, credits page,
        normalized image names; for US-only editions — no illustrations and
        no editorial footnotes.

Everything that is removed is recorded in a `Report` so the build log (and the
human reviewer) can see exactly what changed per book.
"""

from __future__ import annotations

import html
import posixpath
import re
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import quote

from lxml import etree

from chytanka_books import transforms as tf

XHTML = "http://www.w3.org/1999/xhtml"
EPUB_NS = "http://www.idpf.org/2007/ops"
OPF = "http://www.idpf.org/2007/opf"
DC = "http://purl.org/dc/elements/1.1/"
NCX = "http://www.daisy.org/z3986/2005/ncx/"
NS = {"x": XHTML, "opf": OPF, "dc": DC, "ncx": NCX, "epub": EPUB_NS}

MEDIA_EXT = {"image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif", "image/svg+xml": ".svg", "image/webp": ".webp"}
KNOWN_EXT = tuple(set(MEDIA_EXT.values()) | {".jpeg"})

# Parsoid / MediaWiki noise that is useless on an e-reader.
NOISE_ATTRS = {"about", "typeof", "resource", "decoding", "loading", "srcset", "data-mw", "data-file-type",
               "data-file-width", "data-file-height", "data-mw-section-id", "data-mw-footnote-number",
               "data-mw-parsoid-version", "data-mw-html-version", "data-page-number"}

# Largest XHTML we emit (bytes). «Ніоба» at 462 KB is the known-good ceiling on the
# device; we aim lower so page indexing stays quick on the ESP32-C3.
SPLIT_LIMIT = 280_000

XML_PARSER = etree.XMLParser(resolve_entities=False, remove_blank_text=False, strip_cdata=False, huge_tree=True)


@dataclass
class Report:
    slug: str
    size_raw: int = 0
    size_out: int = 0
    fonts_removed: list[str] = field(default_factory=list)
    fonts_bytes: int = 0
    images_removed: list[str] = field(default_factory=list)
    images_renamed: list[tuple[str, str]] = field(default_factory=list)
    notes_removed: list[str] = field(default_factory=list)
    notes_kept: list[str] = field(default_factory=list)
    licence_boxes_removed: int = 0
    links_unwrapped: int = 0
    css_dropped: int = 0
    entity_spans_unwrapped: int = 0
    toc_tables_collapsed: int = 0
    chapters_dropped: list[str] = field(default_factory=list)
    chapters_split: list[str] = field(default_factory=list)
    notes_in_text: int = 0          # footnotes left in the book (non-stripped editions)
    largest_xhtml: int = 0
    other_removed: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    release_ready: bool = True
    not_ready_reasons: list[str] = field(default_factory=list)

    def block(self, reason: str) -> None:
        self.release_ready = False
        self.not_ready_reasons.append(reason)


# --------------------------------------------------------------------------- helpers

def q(tag: str, ns: str = XHTML) -> str:
    return f"{{{ns}}}{tag}"


def classes(el) -> set[str]:
    return set((el.get("class") or "").split())


def text_of(el) -> str:
    return re.sub(r"\s+", " ", "".join(el.itertext())).strip()


def remove_keep_tail(el) -> None:
    """Remove element but keep its tail text in the tree."""
    parent = el.getparent()
    if parent is None:
        return
    tail = el.tail
    if tail:
        prev = el.getprevious()
        if prev is not None:
            prev.tail = (prev.tail or "") + tail
        else:
            parent.text = (parent.text or "") + tail
    parent.remove(el)


def unwrap(el) -> None:
    """Replace element by its children (text + subelements), preserving tail."""
    parent = el.getparent()
    if parent is None:
        return
    idx = parent.index(el)
    prev = el.getprevious()
    lead = el.text or ""
    if lead:
        if prev is not None:
            prev.tail = (prev.tail or "") + lead
        else:
            parent.text = (parent.text or "") + lead
    children = list(el)
    for i, ch in enumerate(children):
        parent.insert(idx + i, ch)
    tail = el.tail or ""
    if tail:
        if children:
            last = children[-1]
            last.tail = (last.tail or "") + tail
        else:
            p2 = el.getprevious()
            if p2 is not None:
                p2.tail = (p2.tail or "") + tail
            else:
                parent.text = (parent.text or "") + tail
    parent.remove(el)


def is_empty(el) -> bool:
    if len(el):
        return all(is_empty(c) and not (c.tail or "").strip() for c in el) and not (el.text or "").strip() \
            and not any(c.tag in (q("img"), q("br"), q("hr")) for c in el.iter())
    return not (el.text or "").strip() and el.tag not in (q("img"), q("br"), q("hr"))


_PROP_RE = re.compile(r"^-?[a-zA-Z][a-zA-Z0-9-]*$")
# Wiki-only layout hacks that make no sense (or harm) on a paginated e-reader.
_DROP_PROPS = {"position", "z-index", "right", "left", "top", "bottom", "font-family"}


def sanitize_css_declarations(style: str) -> tuple[str, list[str]]:
    """Return (clean_style, dropped). Template output on Wikisource often leaves
    declarations with empty values (`width:;`), stray tokens or unbalanced
    quotes/parens — invalid CSS that epubcheck rejects (CSS-008)."""
    kept, dropped = [], []
    for decl in style.split(";"):
        decl = decl.strip()
        if not decl:
            continue
        prop, sep, value = decl.partition(":")
        prop, value = prop.strip().lower(), value.strip()
        important = value.lower().endswith("!important")
        if important:
            value = value[: -len("!important")].strip()
        ok = (sep and _PROP_RE.match(prop) and value
              and value.count("(") == value.count(")")
              and value.count('"') % 2 == 0 and value.count("'") % 2 == 0
              and not re.search(r"[{};<>]", value))
        if not ok or prop in _DROP_PROPS:
            dropped.append(decl)
            continue
        kept.append(f"{prop}:{value}{' !important' if important else ''}")
    return "; ".join(kept), dropped


def sanitize_inline_styles(root, report: "Report | None" = None) -> None:
    for el in root.iter():
        if not isinstance(el.tag, str) or "style" not in el.attrib:
            continue
        clean, dropped = sanitize_css_declarations(el.get("style"))
        if report is not None:
            report.css_dropped += len(dropped)
        if clean:
            el.set("style", clean)
        else:
            del el.attrib["style"]


def serialize_xhtml(root) -> bytes:
    body = etree.tostring(root, encoding="utf-8", xml_declaration=False)
    return b'<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE html>\n' + body


def esc(s: str) -> str:
    return html.escape(s, quote=True)


# --------------------------------------------------------------------------- per-document cleanup

def clean_content_doc(root, *, strip_notes: bool, strip_images: bool, keep_markers: list[str], report: Report,
                      fname: str, book_title: str = "") -> None:
    body = root.find(q("body"))
    head = root.find(q("head"))

    # P3: Parsoid <span typeof="mw:Entity"> around every &nbsp; — unwrap to text first.
    report.entity_spans_unwrapped += tf.unwrap_entity_spans(root)

    # 0. WS Export puts the collection path in <title> ("Твори. Том II — …").
    t = head.find(q("title")) if head is not None else None
    if t is not None and book_title and re.sub(r"\s", "", t.text or "").startswith("Твори"):
        t.text = book_title

    # 1. templatestyles <style> blocks in <head> (crop rules for scan thumbnails etc.)
    if head is not None:
        for st in head.findall(q("style")):
            head.remove(st)

    # 2. Wikisource licence boxes ({{PD-auto}} etc.). All nodes from one template
    #    transclusion share the same `about` id — remove them together.
    lic_abouts = set()
    for el in body.iter():
        is_box = any(c.startswith("licFrame") for c in classes(el)) or (
            el.tag in (q("table"), q("div")) and "суспільному надбанні" in text_of(el)
            and len(text_of(el)) < 3000 and el.find(".//" + q("section")) is None)
        if is_box:
            anc = el
            while anc is not None and anc.get("about") is None:
                anc = anc.getparent()
            if anc is not None:
                lic_abouts.add(anc.get("about"))
    for ab in lic_abouts:
        for el in list(body.iter()):
            if el.get("about") == ab and (el.getparent() is None or el.getparent().get("about") != ab):
                remove_keep_tail(el)
        report.licence_boxes_removed += 1

    # P1: dotted-leader TOC tables (ws-summary) -> plain list of links.
    report.toc_tables_collapsed += tf.collapse_toc_tables(root)

    # 3. Illustrations. US-only / strip_editorial / strip_images: remove every image.
    #    Others: drop only PD icons (they belong to the licence boxes removed above).
    for im in list(body.iter(q("img"))):
        if im.getroottree().getroot() is not root:
            continue  # already detached with an earlier wrapper
        res = im.get("resource") or im.get("src") or ""
        if not (strip_images or "PD-icon" in res):
            continue
        if strip_images:
            report.images_removed.append(f"{fname}: {res.lstrip('./')}")
        target = im
        anc = im.getparent()
        while anc is not None and anc is not body:
            if anc.tag == q("figure") or (anc.get("typeof") or "").startswith("mw:File"):
                target = anc
            anc = anc.getparent()
        parent = target.getparent()
        remove_keep_tail(target)
        while parent is not None and parent is not body and is_empty(parent):
            nxt = parent.getparent()
            remove_keep_tail(parent)
            parent = nxt

    # 4a. Continuation markers of notes split across scan pages (hidden by CSS on the wiki,
    #     but a plain e-reader would show a duplicate "[n]").
    for sup in list(body.iter(q("sup"))):
        if "mw-ref-follow" in classes(sup):
            remove_keep_tail(sup)

    # 4. Footnotes: in US-only / strip_editorial editions keep only explicit author notes.
    if not strip_notes:
        report.notes_in_text += sum(1 for li in body.iter(q("li")) if (li.get("id") or "").startswith("cite_note"))
    if strip_notes:
        notes = {}
        for li in body.iter(q("li")):
            nid = li.get("id") or ""
            if nid.startswith("cite_note"):
                notes[nid] = li
        keep = {nid for nid, li in notes.items() if any(m in text_of(li) for m in keep_markers)}
        for nid, li in notes.items():
            label = text_of(li).lstrip("↑ ").strip()
            if nid in keep:
                report.notes_kept.append(label)
            else:
                report.notes_removed.append(label)
                remove_keep_tail(li)
        # references in text
        new_num: dict[str, int] = {}
        for sup in list(body.iter(q("sup"))):
            if "mw-ref" not in classes(sup):
                continue
            a = sup.find(q("a"))
            target = (a.get("href") or "").lstrip("#") if a is not None else ""
            if target not in keep:
                remove_keep_tail(sup)
                continue
            n = new_num.setdefault(target, len(new_num) + 1)
            # rewrite the visible label: [n]
            for span in sup.iter(q("span")):
                if "mw-reflink-text" in classes(span):
                    for c in list(span):
                        span.remove(c)
                    span.text = f"[{n}]"
        # empty reference lists + the {{bar}} rule that precedes them
        for ol in list(body.iter(q("ol"))):
            if "references" in classes(ol) and not ol.findall(q("li")):
                wrapper = ol.getparent()
                target = wrapper if wrapper is not None and "references" in classes(wrapper) else ol
                prev = target.getprevious()
                remove_keep_tail(target)
                if prev is not None and re.fullmatch(r"[—\-\s]*", text_of(prev) or "-") and text_of(prev):
                    remove_keep_tail(prev)
                    report.other_removed.append(f"{fname}: footnote separator rule")

    # 5. Page-number anchors from the scans (<span class="pagenum">), and their empty <p>.
    for sp in list(body.iter(q("span"))):
        if "pagenum" in classes(sp) or "ws-pagenum" in classes(sp):
            parent = sp.getparent()
            remove_keep_tail(sp)
            while parent is not None and parent is not body and is_empty(parent) and parent.tag in (q("span"), q("p")):
                nxt = parent.getparent()
                remove_keep_tail(parent)
                parent = nxt

    # 6. Links out to the wiki: the device can't follow them. Keep in-book links.
    for a in list(body.iter(q("a"))):
        href = a.get("href") or ""
        if href.startswith(("http://", "https://", "./", "//")):
            unwrap(a)
            report.links_unwrapped += 1
        elif (a.get("rel") or "").startswith("mw:referencedBy"):
            pass

    # 6b. XHTML5 content-model repairs (MathML braces, <dl> without <dt>, blocks inside inline).
    tf.fix_content_model(root)

    # 7. Parsoid attributes.
    sanitize_inline_styles(root, report)
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        for k in list(el.attrib):
            if k in NOISE_ATTRS or k.startswith("data-mw"):
                del el.attrib[k]
        if el.get("title", "").startswith("%D0"):
            del el.attrib["title"]
        if el.tag == q("body"):
            el.set("class", "chytanka")

    # empty <span></span> leftovers from transclusions
    for sp in list(body.iter(q("span"))):
        if (not sp.attrib or "mw-empty-elt" in classes(sp)) and not len(sp) and not (sp.text or "").strip():
            remove_keep_tail(sp)
    # P3: attribute-less wrapper spans (after Parsoid attrs were stripped)
    tf.unwrap_bare_spans(root)


# --------------------------------------------------------------------------- generated pages

def cover_xhtml(title: str, lang: str = "uk", img_src: str = "images/cover.jpg") -> bytes:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="{lang}" lang="{lang}">
<head><meta charset="UTF-8"/><title>{esc(title)}</title>
<style>html,body{{margin:0;padding:0;text-align:center}} img{{max-width:100%;max-height:100%}}</style></head>
<body epub:type="cover"><div class="cover"><img src="{esc(img_src)}" alt="{esc(title)}"/></div></body>
</html>
""".encode()


def credits_xhtml(book: dict, *, source_url: str, revid: int | None, rev_ts: str | None,
                  changes: list[str], site_url: str) -> bytes:
    ed = book["edition"]
    ed_parts = [p for p in (ed.get("city"), ed.get("publisher"), str(ed["year"]) if ed.get("year") else None) if p]
    ed_line = book.get("source_note") or (", ".join(ed_parts) if ed_parts else "—")
    rev = ""
    if revid:
        perma = f"https://uk.wikisource.org/w/index.php?oldid={revid}"
        rev = f' (ревізія <a href="{esc(perma)}">{revid}</a> від {esc((rev_ts or "")[:10])})'
    li = "\n".join(f"<li>{esc(c)}</li>" for c in changes)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="uk" lang="uk">
<head><meta charset="UTF-8"/><title>Про це видання</title><link type="text/css" rel="stylesheet" href="main.css"/></head>
<body epub:type="backmatter">
<section epub:type="colophon">
<h2>Про це видання</h2>
<p><b>«{esc(book['title'])}»</b>, {esc(book['author'])}.</p>
<p>Джерело тексту: {esc(ed_line)}.</p>
<p><b>Статус:</b> {esc(book['pd_status'])} Текст твору — суспільне надбання.</p>
<p><b>Оцифрування та вичитка:</b> волонтери Вікіджерел —
<a href="{esc(source_url)}">{esc(source_url)}</a>{rev}.
Шар Вікіджерел (транскрипція, розмітка, структура) поширюється за ліцензією
Creative Commons «Із зазначенням авторства — Поширення на тих самих умовах» (CC BY-SA).</p>
<p><b>Це видання «Читанки»</b> поширюється за ліцензією
<a href="https://creativecommons.org/licenses/by-sa/4.0/deed.uk">CC BY-SA 4.0</a>
(дозволено версією 3.0 Unported, під якою WS Export віддає файл). Можна вільно копіювати,
змінювати й поширювати, зазначивши авторство і зберігши ту саму ліцензію.</p>
<p><b>Зміни «Читанки»:</b></p>
<ul>
{li}
</ul>
<p>Список учасників Вікіджерел — на наступній сторінці («Опис»).</p>
<p>Каталог «Читанка — Книжки»: <a href="{esc(site_url)}">{esc(site_url)}</a></p>
</section>
</body>
</html>
""".encode()


def nav_xhtml(title: str, items: list[tuple[str, str]], landmarks: list[tuple[str, str, str]]) -> bytes:
    toc = "\n".join(f'<li><a href="{esc(h)}">{esc(l)}</a></li>' for l, h in items)
    lm = "\n".join(f'<li><a epub:type="{t}" href="{esc(h)}">{esc(l)}</a></li>' for t, l, h in landmarks)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="uk" lang="uk">
<head><meta charset="UTF-8"/><title>{esc(title)}</title></head>
<body>
<nav epub:type="toc" id="toc"><h1>Зміст</h1>
<ol>
{toc}
</ol>
</nav>
<nav epub:type="landmarks" id="landmarks" hidden="hidden">
<ol>
{lm}
</ol>
</nav>
</body>
</html>
""".encode()


def toc_ncx(uid: str, title: str, author: str, items: list[tuple[str, str]]) -> bytes:
    pts = "\n".join(
        f'<navPoint id="np{i}" playOrder="{i}"><navLabel><text>{esc(l)}</text></navLabel><content src="{esc(h)}"/></navPoint>'
        for i, (l, h) in enumerate(items, 1)
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1" xml:lang="uk">
<head>
<meta name="dtb:uid" content="{esc(uid)}"/>
<meta name="dtb:depth" content="1"/>
<meta name="dtb:totalPageCount" content="0"/>
<meta name="dtb:maxPageNumber" content="0"/>
</head>
<docTitle><text>{esc(title)}</text></docTitle>
<docAuthor><text>{esc(author)}</text></docAuthor>
<navMap>
{pts}
</navMap>
</ncx>
""".encode()


# --------------------------------------------------------------------------- main transform

def process_epub(raw: bytes, book: dict, *, cover_jpeg: bytes, source_url: str, revid: int | None,
                 rev_ts: str | None, site_url: str, modified: datetime | None = None) -> tuple[bytes, Report]:
    import io

    slug = book["slug"]
    report = Report(slug=slug, size_raw=len(raw))
    us_only = bool(book.get("us_only_edition"))
    strip_editorial = bool(book.get("strip_editorial"))
    strip_notes = us_only or strip_editorial
    strip_images = strip_notes or bool(book.get("strip_images"))
    keep_markers = list(book.get("keep_note_markers") or [])
    modified = modified or datetime.now(timezone.utc).replace(microsecond=0)

    zin = zipfile.ZipFile(io.BytesIO(raw))
    names = zin.namelist()
    files: dict[str, bytes] = {n: zin.read(n) for n in names}

    container = etree.fromstring(files["META-INF/container.xml"], XML_PARSER)
    opf_path = container.find(".//{urn:oasis:names:tc:opendocument:xmlns:container}rootfile").get("full-path")
    base = posixpath.dirname(opf_path)

    def full(href: str) -> str:
        return posixpath.normpath(posixpath.join(base, href)) if base else href

    opf = etree.fromstring(files[opf_path], XML_PARSER)
    manifest = opf.find("opf:manifest", NS)
    spine = opf.find("opf:spine", NS)
    metadata = opf.find("opf:metadata", NS)
    # WS Export sometimes lists a subpage twice (manifest + spine) — keep the first.
    items = {}
    for it in manifest.findall("opf:item", NS):
        if it.get("id") in items:
            manifest.remove(it)
            report.other_removed.append(f"duplicate manifest item: {it.get('id')}")
        else:
            items[it.get("id")] = it
    seen_refs = set()
    for ir in spine.findall("opf:itemref", NS):
        if ir.get("idref") in seen_refs:
            spine.remove(ir)
        seen_refs.add(ir.get("idref"))

    # ---- 1. fonts
    for iid, it in list(items.items()):
        if it.get("media-type", "").startswith(("font/", "application/font", "application/x-font", "application/vnd.ms-opentype")) \
                or "/fonts/" in "/" + it.get("href", ""):
            p = full(it.get("href"))
            report.fonts_removed.append(p)
            report.fonts_bytes += len(files.pop(p, b""))
            manifest.remove(it)
            del items[iid]
    for n in list(files):
        if "/fonts/" in "/" + n:
            report.fonts_bytes += len(files.pop(n))
            report.fonts_removed.append(n)
    for iid, it in items.items():
        if it.get("media-type") == "text/css":
            p = full(it.get("href"))
            css = files[p].decode("utf-8")
            css = re.sub(r"@font-face\s*\{[^}]*\}\s*", "", css)
            css = re.sub(r'body\s*\{\s*font-family:\s*"FreeSerif"\s*;?\s*\}\s*', "", css)
            css += ("\n/* Chytanka */\n.center, .tiInherit { text-indent: 0; }\n.cover img { max-width: 100%; }\n"
                    "div.dd { margin-left: 1.5em; }\nul.toc { list-style: none; padding-left: 0; }\n"
                    "ul.toc li { margin: 0.2em 0; }\nli.toc-head { font-weight: bold; margin-top: 0.6em; }\n")
            files[p] = css.encode("utf-8")
    # iBooks "use embedded fonts" flag is meaningless now.
    files.pop("META-INF/com.apple.ibooks.display-options.xml", None)

    # ---- 2. WS title page (Wikisource logo as de-facto cover) -> our cover page
    title_item = items.get("title")
    if title_item is not None:
        files.pop(full(title_item.get("href")), None)
        manifest.remove(title_item)
        del items["title"]
        report.other_removed.append("title.xhtml (титульна сторінка WS Export з логотипом Вікіджерел)")
        for ir in spine.findall("opf:itemref", NS):
            if ir.get("idref") == "title":
                spine.remove(ir)

    # ---- 3. content documents
    spine_ids = [ir.get("idref") for ir in spine.findall("opf:itemref", NS)]
    content_ids = [iid for iid in spine_ids if iid in items and iid != "about"
                   and items[iid].get("media-type") == "application/xhtml+xml"]
    docs = {iid: etree.fromstring(files[full(items[iid].get("href"))], XML_PARSER) for iid in content_ids}

    # 3a. Contamination guard: WS Export pulls in every main-namespace page the
    #     root links to (publisher ads, other works). Keep only root + subpages.
    if content_ids:
        root_id = content_ids[0]
        root_href = posixpath.basename(items[root_id].get("href"))
        titles = tf.chapter_title_map(docs)
        root_title = book["page"]
        dropped_hrefs = set()
        for iid in content_ids[1:]:
            href = posixpath.basename(items[iid].get("href"))
            title = titles.get(href)
            excluded = next((x for x in (book.get("exclude_pages") or []) if title and x in title), None)
            if excluded or not tf.is_own_chapter(title, href, root_title, root_href):
                label = title or href
                report.chapters_dropped.append(label + (" [exclude_pages у books.yaml]" if excluded else ""))
                dropped_hrefs.add(href)
                files.pop(full(items[iid].get("href")), None)
                manifest.remove(items[iid])
                del items[iid]
                del docs[iid]
                for ir in spine.findall("opf:itemref", NS):
                    if ir.get("idref") == iid:
                        spine.remove(ir)
        if dropped_hrefs:
            for root in docs.values():
                for a in list(root.iter(q("a"))):
                    if (a.get("href") or "").split("#")[0] in dropped_hrefs:
                        unwrap(a)
        content_ids = [i for i in content_ids if i in docs]

    for iid in content_ids:
        p = full(items[iid].get("href"))
        clean_content_doc(docs[iid], strip_notes=strip_notes, strip_images=strip_images, keep_markers=keep_markers,
                          report=report, fname=posixpath.basename(p), book_title=book["title"])

    # 3b. P2: split over-large documents (firmware-friendly size), fix fragment links.
    part_labels: dict[str, str | None] = {}
    families: dict[str, list[str]] = {}   # original href -> [part hrefs]
    for iid in list(content_ids):
        it = items[iid]
        href = it.get("href")
        parts = tf.split_document(docs[iid], limit=SPLIT_LIMIT)
        if len(parts) == 1:
            continue
        stem = href.rsplit(".", 1)[0]
        hrefs = [href] + [f"{stem}-p{k}.xhtml" for k in range(2, len(parts) + 1)]
        families[href] = hrefs
        report.chapters_split.append(f"{posixpath.basename(href)} -> {len(parts)} parts")
        docs[iid] = parts[0]
        prev_item, prev_ref = it, next(ir for ir in spine.findall("opf:itemref", NS) if ir.get("idref") == iid)
        for k, (ph, part) in enumerate(zip(hrefs[1:], parts[1:]), 2):
            pid = f"{iid}-p{k}"
            new_it = etree.Element(q("item", OPF), id=pid, href=ph, attrib={"media-type": "application/xhtml+xml"})
            prev_item.addnext(new_it)
            new_ref = etree.Element(q("itemref", OPF), idref=pid, linear="yes")
            prev_ref.addnext(new_ref)
            prev_item, prev_ref = new_it, new_ref
            items[pid] = new_it
            docs[pid] = part
            body = part.find(q("body"))
            first = next((e for e in body.iter() if e is not body and isinstance(e.tag, str)
                          and tf.text_of(e)), None)
            part_labels[ph] = tf.heading_text(first) if first is not None and tf._is_heading(first) else None
        content_ids = [i for i in (ir.get("idref") for ir in spine.findall("opf:itemref", NS)) if i in docs]
    if families:
        ids_in = {items[i].get("href"): {e.get("id") for e in docs[i].iter() if isinstance(e.tag, str) and e.get("id")}
                  for i in docs}
        fam_of = {h: fam for fam in families.values() for h in fam}
        for iid, root in docs.items():
            own = items[iid].get("href")
            for a in root.iter(q("a")):
                href = a.get("href") or ""
                if "#" not in href or href.startswith(("http://", "https://")):
                    continue
                f, frag = href.split("#", 1)
                target = f or own
                if target in fam_of and frag not in ids_in.get(target, ()):
                    owner = next((h for h in fam_of[target] if frag in ids_in[h]), None)
                    if owner:
                        a.set("href", ("" if owner == own else owner) + "#" + frag)

    for iid, root in docs.items():
        data = serialize_xhtml(root)
        files[full(items[iid].get("href"))] = data
        report.largest_xhtml = max(report.largest_xhtml, len(data))

    # WS credits page is kept verbatim, but its inline CSS gets the same sanitiser.
    if "about" in items:
        p = full(items["about"].get("href"))
        root = etree.fromstring(files[p], XML_PARSER)
        sanitize_inline_styles(root, report)
        files[p] = serialize_xhtml(root)

    # ---- 4. images: garbage-collect unreferenced, give the rest real extensions
    referenced: set[str] = set()
    for iid, it in items.items():
        if it.get("media-type") == "application/xhtml+xml":
            p = full(it.get("href"))
            doc = files[p].decode("utf-8")
            for m in re.finditer(r'(?:src|href)="([^"#]+)', doc):
                referenced.add(posixpath.normpath(posixpath.join(posixpath.dirname(p), m.group(1))))
    img_counter = 0
    renames: dict[str, str] = {}
    for iid, it in list(items.items()):
        mt = it.get("media-type", "")
        if not mt.startswith("image/"):
            continue
        p = full(it.get("href"))
        if p not in referenced:
            files.pop(p, None)
            manifest.remove(it)
            del items[iid]
            report.other_removed.append(f"unreferenced file dropped: {posixpath.basename(p)}")
            continue
        href = it.get("href")
        if not href.lower().endswith(KNOWN_EXT):
            img_counter += 1
            new_href = f"images/img{img_counter:02d}{MEDIA_EXT.get(mt, '.bin')}"
            renames[p] = full(new_href)
            files[full(new_href)] = files.pop(p)
            it.set("href", new_href)
            it.set("id", f"img{img_counter:02d}")
            report.images_renamed.append((posixpath.basename(p), new_href))
    if renames:
        for iid, it in items.items():
            if it.get("media-type") == "application/xhtml+xml":
                p = full(it.get("href"))
                doc = files[p].decode("utf-8")
                for old, new in renames.items():
                    rel_old = posixpath.relpath(old, posixpath.dirname(p))
                    rel_new = posixpath.relpath(new, posixpath.dirname(p))
                    doc = doc.replace(f'"{rel_old}"', f'"{rel_new}"')
                files[p] = doc.encode("utf-8")

    # ---- 5. cover image + cover page + credits page
    files[full("images/cover.jpg")] = cover_jpeg
    cov = etree.SubElement(manifest, q("item", OPF), id="cover-image", href="images/cover.jpg",
                           attrib={"media-type": "image/jpeg", "properties": "cover-image"})
    files[full("cover.xhtml")] = cover_xhtml(book["title"])
    etree.SubElement(manifest, q("item", OPF), id="cover", href="cover.xhtml", attrib={"media-type": "application/xhtml+xml"})

    changes = ["видалено вбудовані шрифти FreeSerif (≈4 МБ)",
               "замінено титульну сторінку з логотипом Вікіджерел на обкладинку «Читанки»",
               "прибрано службову розмітку MediaWiki, номери сторінок сканів і посилання на вікі"]
    if report.licence_boxes_removed:
        changes.append("ліцензійні плашки Вікіджерел замінено цією сторінкою")
    if report.images_renamed:
        changes.append("нормалізовано імена файлів зображень")
    kept = f"; авторські виноски ({len(report.notes_kept)}) збережено" if report.notes_kept else ""
    if us_only:
        if report.images_removed:
            changes.append(f"прибрано ілюстрації видання ({len(report.images_removed)}) — вони не є суспільним надбанням поза США")
        if report.notes_removed:
            changes.append(f"прибрано редакторські виноски видання ({len(report.notes_removed)}){kept}")
    elif strip_editorial:
        if report.images_removed:
            changes.append(f"прибрано ілюстрації видання ({len(report.images_removed)})")
        if report.notes_removed:
            changes.append(f"прибрано редакторські виноски видання ({len(report.notes_removed)}){kept}")
    elif report.images_removed:
        changes.append(f"прибрано ілюстрації й фотографії видання ({len(report.images_removed)}), щоб зменшити файл")
    if report.chapters_dropped:
        changes.append(f"вилучено сторінки, що не належать до твору ({len(report.chapters_dropped)}): "
                       + "; ".join(report.chapters_dropped[:5]) + ("…" if len(report.chapters_dropped) > 5 else ""))
    if report.toc_tables_collapsed:
        changes.append("таблиці змісту перетворено на простий список")
    if report.chapters_split:
        changes.append("завеликі розділи поділено на кілька файлів")
    files[full("chytanka-credits.xhtml")] = credits_xhtml(book, source_url=source_url, revid=revid, rev_ts=rev_ts,
                                                          changes=changes, site_url=site_url)
    etree.SubElement(manifest, q("item", OPF), id="chytanka-credits", href="chytanka-credits.xhtml",
                     attrib={"media-type": "application/xhtml+xml"})
    # keep manifest order tidy: move 'about' to the end
    if "about" in items:
        manifest.remove(items["about"])
        manifest.append(items["about"])

    # spine: cover first, credits before WS "about"
    spine.insert(0, etree.Element(q("itemref", OPF), idref="cover", linear="yes"))
    about_ref = next((ir for ir in spine.findall("opf:itemref", NS) if ir.get("idref") == "about"), None)
    cred_ref = etree.Element(q("itemref", OPF), idref="chytanka-credits", linear="yes")
    if about_ref is not None:
        about_ref.addprevious(cred_ref)
    else:
        spine.append(cred_ref)

    # ---- 6. metadata
    uid = f"urn:chytanka:book:{slug}"
    for el in list(metadata):
        tag = etree.QName(el).localname if isinstance(el.tag, str) else ""
        if tag == "date":
            # WS copies the wiki «рік» field verbatim («1920-ті», «192?») — keep only W3CDTF years.
            yr = (book.get("edition") or {}).get("year")
            m = re.match(r"^\d{4}(-\d\d(-\d\d)?)?$", (el.text or "").strip())
            if yr:
                el.text = str(yr)
            elif not m:
                metadata.remove(el)
            continue
        if tag in ("identifier", "title", "creator", "rights", "language", "publisher", "description", "subject"):
            metadata.remove(el)
        elif tag == "meta" and (el.get("name") == "cover" or el.get("property") in ("dcterms:modified",)
                                or el.get("refines") in ("#meta-title", "#meta-aut")):
            metadata.remove(el)
        elif tag == "link" and el.get("rel") == "cc:license":
            metadata.remove(el)

    def dc(tag, text, **attrs):
        el = etree.Element(q(tag, DC), nsmap={"dc": DC})
        el.text = text
        for k, v in attrs.items():
            el.set(k, v)
        return el

    new = [
        dc("identifier", uid, id="uid"),
        dc("title", book["title"], id="meta-title"),
        dc("creator", book["author"], id="meta-aut"),
        dc("language", "uk"),
        dc("publisher", "Читанка"),
        dc("description", book["summary"]),
        dc("rights", "Текст — суспільне надбання. Оцифрування: Вікіджерела (CC BY-SA). Це видання: CC BY-SA 4.0."),
    ]
    for i, el in enumerate(new):
        metadata.insert(i, el)
    m = etree.SubElement(metadata, q("meta", OPF), refines="#meta-title", property="title-type"); m.text = "main"
    m = etree.SubElement(metadata, q("meta", OPF), refines="#meta-aut", property="role", scheme="marc:relators"); m.text = "aut"
    m = etree.SubElement(metadata, q("meta", OPF), refines="#meta-aut", property="file-as"); m.text = file_as(book["author"])
    m = etree.SubElement(metadata, q("meta", OPF), property="dcterms:modified"); m.text = modified.strftime("%Y-%m-%dT%H:%M:%SZ")
    etree.SubElement(metadata, q("link", OPF), rel="cc:license", href="https://creativecommons.org/licenses/by-sa/4.0/")
    etree.SubElement(metadata, q("meta", OPF), name="cover", content="cover-image")  # EPUB2 readers
    opf.set("unique-identifier", "uid")
    opf.set(q("lang", "http://www.w3.org/XML/1998/namespace"), "uk")

    # EPUB2 guide (cover page) — harmless for EPUB3, used by older readers.
    guide = opf.find("opf:guide", NS)
    if guide is None:
        guide = etree.SubElement(opf, q("guide", OPF))
    for r in list(guide):
        guide.remove(r)
    etree.SubElement(guide, q("reference", OPF), type="cover", title="Обкладинка", href="cover.xhtml")

    # ---- 7. nav + ncx regenerated from the spine
    labels = {}
    old_ncx_item = next((it for it in items.values() if it.get("media-type") == "application/x-dtbncx+xml"), None)
    if old_ncx_item is not None:
        ncx = etree.fromstring(files[full(old_ncx_item.get("href"))], XML_PARSER)
        for np_ in ncx.iter(q("navPoint", NCX)):
            src = np_.find(".//ncx:content", NS).get("src")
            labels[src.split("#")[0]] = text_of(np_.find(".//ncx:navLabel", NS))
    ws_title = labels.get(next(iter(labels), ""), "")
    toc_items: list[tuple[str, str]] = [("Обкладинка", "cover.xhtml")]
    for ir in spine.findall("opf:itemref", NS):
        iid = ir.get("idref")
        if iid in ("cover",):
            continue
        it = items.get(iid) if iid not in ("chytanka-credits",) else None
        href = it.get("href") if it is not None else "chytanka-credits.xhtml"
        label = labels.get(href)
        if href in part_labels:
            if part_labels[href]:
                toc_items.append((part_labels[href], href))
            continue
        if iid == "chytanka-credits":
            label = "Про це видання"
        elif iid == "about":
            label = "Опис (Вікіджерела)"
        elif not label or label == ws_title or "—" in label and label.endswith(book["title"].split(" (")[0]) \
                or re.sub(r"\s", "", label).startswith("Твори"):
            label = book["title"]
        toc_items.append((re.sub(r"(?<=[^\W\d_])'(?=[^\W\d_])", "\u2019", label), href))
    first_body = next((h for l, h in toc_items[1:]), "cover.xhtml")
    landmarks = [("cover", "Обкладинка", "cover.xhtml"), ("bodymatter", "Текст", first_body),
                 ("colophon", "Про це видання", "chytanka-credits.xhtml")]
    nav_item = next((it for it in items.values() if "nav" in (it.get("properties") or "").split()), None)
    if nav_item is not None:
        files[full(nav_item.get("href"))] = nav_xhtml(book["title"], toc_items, landmarks)
    if old_ncx_item is not None:
        files[full(old_ncx_item.get("href"))] = toc_ncx(uid, book["title"], book["author"], toc_items)

    files[opf_path] = b'<?xml version="1.0" encoding="UTF-8"?>\n' + etree.tostring(opf, encoding="utf-8", pretty_print=True)

    # ---- 8. write zip (mimetype first, stored)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zo:
        zi = zipfile.ZipInfo("mimetype", date_time=(2026, 1, 1, 0, 0, 0))
        zi.compress_type = zipfile.ZIP_STORED
        zo.writestr(zi, b"application/epub+zip")
        order = ["META-INF/container.xml", opf_path] + sorted(n for n in files if n not in ("mimetype", "META-INF/container.xml", opf_path))
        for n in order:
            zi = zipfile.ZipInfo(n, date_time=(2026, 1, 1, 0, 0, 0))
            zi.compress_type = zipfile.ZIP_STORED if n.endswith((".jpg", ".png")) else zipfile.ZIP_DEFLATED
            zo.writestr(zi, files[n], compresslevel=9)
    data = out.getvalue()
    report.size_out = len(data)
    return data, report


def file_as(name: str) -> str:
    parts = name.split()
    if len(parts) == 2 and name != "Леся Українка":
        return f"{parts[1]}, {parts[0]}"
    return name


def ws_url(page: str) -> str:
    return "https://uk.wikisource.org/wiki/" + quote(page.replace(" ", "_"), safe="/(),'–")
