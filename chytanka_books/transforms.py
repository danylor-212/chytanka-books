"""Generic structural transforms on WS Export XHTML (lxml trees).

Kept separate from epub.py so they can be unit-tested on small snippets
(tests/test_transforms.py).

  * unwrap_entity_spans  — P3: Parsoid wraps every &nbsp; (and other entities) in
                            <span typeof="mw:Entity">; unwrap to plain text.
  * unwrap_bare_spans    — attribute-less <span> wrappers left after cleanup.
  * collapse_toc_tables  — P1: `table.ws-summary` (and similar dotted-leader
                            TOC tables) -> a plain <ul class="toc"> of links.
  * split_document       — P2: split an over-large XHTML body into parts,
                            preferring heading boundaries.
  * chapter_title_map / is_own_chapter — contamination guard helpers.
"""

from __future__ import annotations

import copy
import re

from lxml import etree

XHTML = "http://www.w3.org/1999/xhtml"
HEADINGS = {f"{{{XHTML}}}h{i}" for i in range(1, 7)}


def q(tag: str) -> str:
    return f"{{{XHTML}}}{tag}"


def classes(el) -> set[str]:
    return set((el.get("class") or "").split())


def text_of(el) -> str:
    return re.sub(r"\s+", " ", "".join(el.itertext())).strip()


def _unwrap(el) -> None:
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
            children[-1].tail = (children[-1].tail or "") + tail
        else:
            p2 = el.getprevious()
            if p2 is not None:
                p2.tail = (p2.tail or "") + tail
            else:
                parent.text = (parent.text or "") + tail
    parent.remove(el)


# --------------------------------------------------------------------------- P3

def unwrap_entity_spans(root) -> int:
    n = 0
    for sp in list(root.iter(q("span"))):
        if sp.get("typeof") == "mw:Entity":
            _unwrap(sp)
            n += 1
    return n


def unwrap_bare_spans(root) -> int:
    """Unwrap <span> elements that carry no attributes at all (pure wrapper noise)."""
    n = 0
    for sp in list(root.iter(q("span"))):
        if not sp.attrib:
            _unwrap(sp)
            n += 1
    return n


# --------------------------------------------------------------------------- P1

_LEADER_RE = re.compile(r"[.·…_\s]{3,}$")
_PAGE_NO_RE = re.compile(r"(?:[.·…_\s]+|\s)(?:\d{1,4}|[IVXLC]{1,6})\s*$")


def _is_toc_table(tbl) -> bool:
    c = classes(tbl)
    if any(x.startswith("ws-summary") for x in c) or "ws-toc" in c:
        return True
    # Heuristic: a table whose rows are mostly "link … page number".
    rows = tbl.findall(".//" + q("tr"))
    if len(rows) < 3:
        return False
    linked = sum(1 for r in rows if any(not (a.get("href") or "").startswith(("http", "#"))
                                        for a in r.iter(q("a")) if a.get("href")))
    return linked >= 0.6 * len(rows)


def _clean_label(s: str) -> str:
    s = re.sub(r"\s+", " ", s).strip()
    s = _PAGE_NO_RE.sub("", s).strip()
    s = _LEADER_RE.sub("", s).strip()
    return s.rstrip(" .·…")


_PAGE_CELL_RE = re.compile(r"^(?:\d{1,4}|[IVXLCivxlc]{1,7})\.?$")


def _cell_text(td) -> str:
    """Text of a TOC cell without the dotted-leader overlays (absolutely positioned divs)."""
    parts = []

    def walk(el):
        st = (el.get("style") or "").replace(" ", "")
        if "position:absolute" in st:
            if el.tail:
                parts.append(el.tail)
            return
        if el.text:
            parts.append(el.text)
        for c in el:
            walk(c)
        if el is not td and el.tail:
            parts.append(el.tail)

    walk(td)
    t = re.sub(r"\s+", " ", "".join(parts)).strip()
    return _LEADER_RE.sub("", t).strip()


def _row_to_li(tr):
    cells = [c for c in tr if isinstance(c.tag, str) and c.tag in (q("td"), q("th"))]
    link = None
    for a in tr.iter(q("a")):
        href = a.get("href") or ""
        if href and not href.startswith(("http://", "https://", "//", "#", "./")):
            link = a
            break
    texts = [_cell_text(c) for c in cells] if cells else [_clean_label(text_of(tr))]
    texts = [t for t in texts if t]
    # trailing page-number cell is meaningless in an e-book
    if len(texts) > 1 and _PAGE_CELL_RE.match(texts[-1]):
        texts = texts[:-1]
    li = etree.Element(q("li"))
    if link is not None:
        label = _LEADER_RE.sub("", text_of(link)).strip() or " ".join(texts)
        if not label:
            return None
        # keep a leading number cell ("1.", "I.") as plain text before the link
        if len(texts) > 1 and re.fullmatch(r"[\dIVXLC]{1,6}\.?", texts[0]) and texts[0] not in label:
            li.text = texts[0] + " "
        a = etree.SubElement(li, q("a"), href=link.get("href"))
        a.text = label
        return li
    if not texts:
        return None
    label = (" ".join(texts[:-1]) + " — " + texts[-1]) if len(texts) > 1 else texts[0]
    if re.fullmatch(r"[\d\s.]+", label) or len(label) > 400:
        return None
    li.set("class", "toc-head")
    li.text = label
    return li


def collapse_toc_tables(root) -> int:
    """Replace TOC tables by <ul class="toc"><li><a href=…>Label</a></li>…</ul>.

    Rows with an in-book link keep that link (first one in the row); rows without
    a link but with text (part headings such as «ЧАСТИНА ПЕРША», or publisher
    lists) become plain items. Dotted leaders and page-number cells are removed.
    Adjacent single-row TOC tables (a common Wikisource pattern) are merged
    into one list.
    """
    n = 0
    for tbl in list(root.iter(q("table"))):
        if tbl.getparent() is None or not _is_toc_table(tbl):
            continue
        anc, nested = tbl.getparent(), False
        while anc is not None:
            if anc.tag == q("table"):
                nested = True
                break
            anc = anc.getparent()
        if nested:
            continue
        ul = etree.Element(q("ul"), attrib={"class": "toc"})
        prev_key = None
        for tr in tbl.iter(q("tr")):
            li = _row_to_li(tr)
            if li is None:
                continue
            key = (li.text, tuple((a.get("href"), a.text) for a in li))
            if key != prev_key:
                ul.append(li)
            prev_key = key
        ul.tail = tbl.tail
        parent = tbl.getparent()
        if len(ul):
            parent.replace(tbl, ul)
        else:
            idx_prev = tbl.getprevious()
            parent.remove(tbl)
            if ul.tail:
                if idx_prev is not None:
                    idx_prev.tail = (idx_prev.tail or "") + ul.tail
                else:
                    parent.text = (parent.text or "") + ul.tail
        n += 1
    # merge adjacent lists (only whitespace between them)
    for ul in list(root.iter(q("ul"))):
        if "toc" not in classes(ul) or ul.getparent() is None:
            continue
        nxt = ul.getnext()
        while nxt is not None and nxt.tag == q("ul") and "toc" in classes(nxt) and not (ul.tail or "").strip():
            for li in list(nxt):
                ul.append(li)
            ul.tail = nxt.tail
            nxt.getparent().remove(nxt)
            nxt = ul.getnext()
    return n


# --------------------------------------------------------------------------- XHTML5 content-model fixes

INLINE = {q(t) for t in ("span", "a", "b", "i", "em", "strong", "sup", "sub", "small", "u", "s", "font", "big",
                         "abbr", "cite", "q", "code", "label")}
BLOCKS = {q(t) for t in ("div", "p", "center", "blockquote")}
MATHML = "http://www.w3.org/1998/Math/MathML"


def fix_content_model(root) -> int:
    """Repair markup that is valid on the wiki but not in EPUB 3 XHTML:
      * MathML (used on Wikisource only to draw big braces in cast lists) -> plain text;
      * <dl> without <dt> (wiki «:» indentation) -> <div class="dl"> of <div class="dd">;
      * block elements nested in inline ones (a <div> inside a footnote <span>) -> <span style="display:block">.
    Returns the number of fixes."""
    n = 0
    for m in list(root.iter(f"{{{MATHML}}}math")):
        sp = etree.Element(q("span"), attrib={"class": "math"})
        sp.text = "".join(m.itertext()).strip()
        sp.tail = m.tail
        m.getparent().replace(m, sp)
        n += 1
    for dl in list(root.iter(q("dl"))):
        if dl.find(q("dt")) is None:
            dl.tag = q("div")
            dl.set("class", ((dl.get("class") or "") + " dl").strip())
            for dd in dl.findall(q("dd")):
                dd.tag = q("div")
                dd.set("class", ((dd.get("class") or "") + " dd").strip())
            n += 1
    # tables/lists inside <p> or inline elements: promote the ancestors (up to the block
    # container) to <div> — a table can't be demoted to inline content.
    for el in list(root.iter(q("table"), q("ul"), q("ol"), q("dl"), q("blockquote"))):
        anc = el.getparent()
        chain = []
        while anc is not None and (anc.tag in INLINE or anc.tag == q("p")):
            chain.append(anc)
            anc = anc.getparent()
        for a in chain:
            a.tag = q("div")
            n += 1
    for el in list(root.iter()):
        if not isinstance(el.tag, str) or el.tag not in BLOCKS:
            continue
        anc = el.getparent()
        while anc is not None and anc.tag not in INLINE and anc.tag not in (q("p"),) :
            if anc.tag in BLOCKS or anc.tag in (q("body"), q("li"), q("td"), q("section")):
                anc = None
                break
            anc = anc.getparent()
        if anc is not None:
            el.tag = q("span")
            st = (el.get("style") or "").strip().rstrip(";")
            el.set("style", ("display:block; " + st).strip("; ") if st else "display:block")
            n += 1
    # obsolete presentational attributes -> CSS (or dropped)
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        css = []
        for attr, prop in (("valign", "vertical-align"), ("align", "text-align"), ("bgcolor", None),
                           ("width", None), ("height", None), ("border", None), ("cellpadding", None),
                           ("cellspacing", None), ("nowrap", None), ("color", None), ("face", None), ("size", None)):
            if attr in el.attrib and not (attr in ("width", "height") and el.tag in (q("img"),)):
                v = el.attrib.pop(attr)
                if prop and v:
                    css.append(f"{prop}:{v}")
                n += 1
        if css:
            st = (el.get("style") or "").strip().rstrip(";")
            el.set("style", "; ".join(([st] if st else []) + css))
    return n


# --------------------------------------------------------------------------- contamination guard

def chapter_title_map(docs: dict[str, "etree._Element"]) -> dict[str, str]:
    """basename(href) -> wiki page title, from rel=mw:WikiLink anchors in all docs."""
    m: dict[str, str] = {}
    for root in docs.values():
        for a in root.iter(q("a")):
            href = (a.get("href") or "").split("#")[0]
            t = a.get("title")
            if href.endswith(".xhtml") and t and "/" not in href and href not in m:
                m[href] = t
    return m


def is_own_chapter(title: str | None, fname: str, root_title: str, root_fname: str) -> bool:
    """True if a chapter belongs to the root work (is the root or one of its subpages).

    Uses the wiki page title when known; otherwise compares WS Export's
    transliterated file names (c<N>_<translit(title)>.xhtml).
    """
    if title:
        t = title.replace("_", " ").strip()
        r = root_title.replace("_", " ").strip()
        return t == r or t.startswith(r + "/")
    stem = re.sub(r"^c\d+_", "", fname.rsplit(".", 1)[0])
    rstem = re.sub(r"^c\d+_", "", root_fname.rsplit(".", 1)[0])
    return stem == rstem or stem.startswith(rstem + "_")


# --------------------------------------------------------------------------- P2

def _size(el) -> int:
    return len(etree.tostring(el, encoding="utf-8"))


def _is_heading(el) -> bool:
    if not isinstance(el.tag, str):
        return False
    if el.tag in HEADINGS:
        return True
    # Wikisource centred headings: short block, centred, (bold or larger font)
    if el.tag in (q("div"), q("p"), q("center")):
        st = (el.get("style") or "").replace(" ", "")
        cls = classes(el)
        txt = text_of(el)
        centred = "text-align:center" in st or "center" in cls or "tiInherit" in cls or el.tag == q("center")
        if centred and 0 < len(txt) <= 80 and not el.findall(".//" + q("br"))[3:]:
            return True
    return False


def heading_text(el) -> str:
    return text_of(el)[:80]


def _flatten(el, chain: tuple, limit: int, out: list) -> None:
    """Collect (ancestor_chain, block) items; expand blocks that are too big."""
    for ch in el:
        if not isinstance(ch.tag, str):
            continue
        if len(ch) and _size(ch) > limit // 2 and ch.tag not in {q("table"), q("ul"), q("ol")} | HEADINGS:
            _flatten(ch, chain + (ch,), limit, out)
        else:
            out.append((chain, ch))


def split_document(root, limit: int = 280_000, min_part: int = 60_000) -> list:
    """Split `root` (an <html>) into several <html> trees whose serialized size
    stays under `limit` where possible. Returns [root] if no split is needed.

    Break points: before a heading once the current part is >= min_part, or
    before any block once adding it would exceed `limit`.
    """
    if _size(root) <= limit:
        return [root]
    body = root.find(q("body"))
    items: list = []
    _flatten(body, (), limit, items)
    if len(items) < 2:
        return [root]

    chunks: list[list] = [[]]
    cur = 0
    for chain, el in items:
        s = _size(el)
        if chunks[-1] and ((cur >= min_part and _is_heading(el)) or cur + s > limit):
            chunks.append([])
            cur = 0
        chunks[-1].append((chain, el))
        cur += s
    # fold tiny trailing fragments (closing wrappers, a date line…) into the previous part
    while len(chunks) > 1 and sum(_size(el) for _, el in chunks[-1]) < 8_000:
        chunks[-2].extend(chunks.pop())
    if len(chunks) == 1:
        return [root]

    head = root.find(q("head"))
    out = []
    for chunk in chunks:
        new = etree.Element(root.tag, attrib=dict(root.attrib), nsmap=root.nsmap)
        new.append(copy.deepcopy(head))
        nb = etree.SubElement(new, q("body"), attrib=dict(body.attrib))
        stack: list = []  # [(orig, copy)]
        for chain, el in chunk:
            k = 0
            while k < len(stack) and k < len(chain) and stack[k][0] is chain[k]:
                k += 1
            del stack[k:]
            for orig in chain[k:]:
                parent_copy = stack[-1][1] if stack else nb
                c = etree.SubElement(parent_copy, orig.tag, attrib=dict(orig.attrib))
                stack.append((orig, c))
            parent_copy = stack[-1][1] if stack else nb
            parent_copy.append(el)  # moves el out of the original tree
        out.append(new)
    return out
