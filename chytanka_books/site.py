"""OPDS 1.2 (Atom) feed + landing page generation.

Firmware constraints (chytanka-main lib/OpdsParser, src/util/UrlUtils.cpp):
  * Atom only; books = <link rel="...opds-spec.org/acquisition..."
    type="application/epub+zip">, preferably with ".epub" in href.
  * at most MAX_OPDS_FEED_ENTRIES (50) entries per page -> rel="next".
  * UrlUtils::buildUrl does NOT resolve relative URLs per RFC 3986 -> every
    href in the feed is ABSOLUTE.
  * search only via rel="search" with a {searchTerms} template — a static
    site can't search, so no search link is emitted.
  * the parser matches element names with strstr(name, ":id") etc., so avoid
    prefixed elements such as dc:identifier (":id" is a substring).
"""

from __future__ import annotations

import html
import re
from datetime import datetime

ATOM_NS = "http://www.w3.org/2005/Atom"
ACQ = "application/atom+xml;profile=opds-catalog;kind=acquisition"
NAV = "application/atom+xml;profile=opds-catalog;kind=navigation"


_APOS_RE = re.compile(r"(?<=[^\W\d_])['\u02bc`\u00b4](?=[^\W\d_])")


def typographic_apostrophes(text):
    """Ukrainian apostrophe: ASCII ' (and ʼ ` ´) between letters -> U+2019 ’. Non-strings pass through."""
    return _APOS_RE.sub("\u2019", text) if isinstance(text, str) else text


# Human-readable fields drawn on covers / written to OPF, OPDS and the landing page.
# Never `page` (wiki title), `slug` or URLs.
DISPLAY_FIELDS = ("title", "author", "subtitle", "summary", "pd_status", "source_note", "cover_title", "feed_title",
                  "series", "genre")


def normalize_book(book: dict) -> dict:
    b = dict(book)
    for k in DISPLAY_FIELDS:
        if k in b:
            b[k] = typographic_apostrophes(b[k])
    if isinstance(b.get("edition"), dict):
        b["edition"] = {k: typographic_apostrophes(v) for k, v in b["edition"].items()}
    return b


def esc(s: str) -> str:
    return html.escape(str(s), quote=True)


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


FW_TITLE_BYTES = 160  # OpdsParser MAX_TITLE_CHARS counts BYTES and may cut a UTF-8 sequence in half


def fit_bytes(text: str, limit: int = FW_TITLE_BYTES) -> str:
    """Shorten at a word boundary (with «…») so the UTF-8 form fits the firmware limit."""
    if len(text.encode("utf-8")) <= limit:
        return text
    words, out = text.split(), ""
    for w in words:
        trial = f"{out} {w}".strip()
        if len((trial + "…").encode("utf-8")) > limit:
            break
        out = trial
    return out.rstrip(" ,.;:") + "…"


def human_size(n: int) -> str:
    return f"{n / 1024:.0f} КБ" if n < 1024 * 1024 else f"{n / 1024 / 1024:.1f} МБ"


GENRE_ORDER = ["Проза", "Поезія", "Драма", "Дитяча", "Спогади, есеї, нонфікшн"]


def surname(author: str) -> str:
    parts = author.split()
    return author if author in ("Леся Українка", "Марко Вовчок", "Панас Мирний", "Дніпрова Чайка") or len(parts) < 2 \
        else parts[-1]


UK_ALPHABET = "абвгґдеєжзиіїйклмнопрстуфхцчшщьюя"


def uk_key(text: str) -> tuple:
    """Ukrainian alphabetical order (Python's code-point order puts є/і/ї/ґ after я)."""
    return tuple(UK_ALPHABET.index(c) if c in UK_ALPHABET else 100 + ord(c) for c in text.lower())


def sort_books(books: list[dict]) -> list[dict]:
    """Catalogue order: genre (fixed order), then author surname, then title."""
    def key(b):
        g = b.get("genre") or "Інше"
        gi = GENRE_ORDER.index(g) if g in GENRE_ORDER else len(GENRE_ORDER)
        return (gi, uk_key(surname(b["author"])), uk_key(b["title"]))
    return sorted(books, key=key)


def genre_slug(g: str) -> str:
    return {"Проза": "proza", "Поезія": "poeziia", "Драма": "drama", "Дитяча": "dytiacha",
            "Спогади, есеї, нонфікшн": "nonfiction"}.get(g, "inshe")


def licence_txt(site: dict, books: list[dict], books_en: list[dict] | None = None) -> str:
    lines = [
        f"{site['title']} — атрибуції та ліцензії / attributions and licences",
        "=" * 60,
        "",
        "УКРАЇНСЬКІ КНИЖКИ",
        "Тексти творів — суспільне надбання. Оцифрування та вичитка: волонтери Вікіджерел",
        "(https://uk.wikisource.org), CC BY-SA. EPUB-видання «Читанки» (без вбудованих шрифтів,",
        "з обкладинкою і сторінкою «Про це видання») — CC BY-SA 4.0:",
        "https://creativecommons.org/licenses/by-sa/4.0/deed.uk",
        "Обкладинки: шрифт Literata (SIL OFL 1.1), знак «Читанки».",
        "",
    ]
    for b in sort_books(books):
        ed = b["edition"]
        ed_bits = ", ".join(str(x) for x in (ed.get("city"), ed.get("publisher"), ed.get("year")) if x)
        rev = f" (ревізія {b['_revid']})" if b.get("_revid") else ""
        lines += [
            f"«{b['title']}», {b['author']} (†{b['author_died']}).",
            f"  Видання-джерело: {b.get('source_note') or ed_bits or 'не вказано'}.",
            f"  Статус: {b['pd_status']}",
            f"  Вікіджерела: {b['_source_url']}{rev}",
            f"  Файл: {site['base_url']}books/{b['slug']}.epub",
            "",
        ]
    if books_en:
        lines += [
            "",
            "ENGLISH BOOKS",
            "Public-domain texts (every author and translator died before 1954; all texts are public domain",
            "in the US). Derived from Standard Ebooks editions (CC0 1.0; built from github.com/standardebooks",
            "with Standard Ebooks' own `se build`) or Project Gutenberg eBooks (the full Project Gutenberg",
            "License is kept inside each such file). These are NOT official Standard Ebooks / Project Gutenberg",
            "releases: Chytanka replaced the cover with its own typographic cover and added an «About this",
            "edition» page. Chytanka's changes are dedicated to the public domain (CC0 1.0).",
            "",
        ]
        for b in sorted(books_en, key=lambda b: (b.get("author_sort") or b["author"], b["title"])):
            tr = f"; tr. {b['translator']}" + (f" (d. {b['translator_died']})" if b.get("translator_died") else "") \
                if b.get("translator") else ""
            origin = (f"Standard Ebooks {b['_source_url']} (source commit {str(b.get('_commit') or '')[:12]})"
                      if b.get("se") else f"Project Gutenberg eBook #{b['gutenberg']} {b['_source_url']}")
            lines += [f"{b['title']}, {b['author']} (d. {b['author_died']}){tr}.",
                      f"  Source: {origin}",
                      f"  File: {site['base_url']}books/{b['slug']}.epub", ""]
    return "\n".join(lines)
