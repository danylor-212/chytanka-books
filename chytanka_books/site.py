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
from datetime import datetime

ATOM_NS = "http://www.w3.org/2005/Atom"
ACQ = "application/atom+xml;profile=opds-catalog;kind=acquisition"
NAV = "application/atom+xml;profile=opds-catalog;kind=navigation"


def esc(s: str) -> str:
    return html.escape(str(s), quote=True)


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def page_url(base: str, n: int) -> str:
    return f"{base}opds/index.xml" if n == 1 else f"{base}opds/all-{n}.xml"


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


def book_entry(b: dict, base: str) -> str:
    slug = b["slug"]
    ed = b["edition"]
    issued = f"\n    <dc:issued>{ed['year']}</dc:issued>" if ed.get("year") else ""
    ed_bits = ", ".join(str(x) for x in (ed.get("city"), ed.get("publisher"), ed.get("year")) if x)
    src = b.get("source_note") or ed_bits or "Вікіджерела"
    content = f"{b['summary']} Джерело тексту: {src}. Рік смерті автора: {b['author_died']}."
    cat = f'\n    <category term="{esc(b["genre"])}" label="{esc(b["genre"])}"/>' if b.get("genre") else ""
    return f"""  <entry>
    <id>urn:chytanka:book:{esc(slug)}</id>
    <title>{esc(fit_bytes(b.get('feed_title') or b['title']))}</title>
    <author><name>{esc(b['author'])}</name></author>
    <updated>{iso(b['_updated'])}</updated>
    <dc:language>uk</dc:language>{issued}
    <dc:publisher>Читанка</dc:publisher>{cat}
    <rights>Текст — суспільне надбання. Оцифрування: Вікіджерела. Видання: CC BY-SA 4.0</rights>
    <summary type="text">{esc(b['summary'])}</summary>
    <content type="text">{esc(content)}</content>
    <link rel="http://opds-spec.org/image" type="image/jpeg" href="{base}covers/{esc(slug)}-600.jpg"/>
    <link rel="http://opds-spec.org/image/thumbnail" type="image/jpeg" href="{base}covers/{esc(slug)}.jpg"/>
    <link rel="http://opds-spec.org/acquisition/open-access" type="application/epub+zip" href="{base}books/{esc(slug)}.epub" length="{b['_size']}" title="EPUB"/>
    <link rel="alternate" type="text/html" href="{esc(b['_source_url'])}" title="Вікіджерела"/>
  </entry>
"""


def build_feeds(site: dict, books: list[dict], page_size: int) -> dict[str, str]:
    """Return {relative_path: xml}. Page 1 is opds/index.xml."""
    base = site["base_url"]
    pages = [books[i:i + page_size] for i in range(0, len(books), page_size)] or [[]]
    out = {}
    updated = max((b["_updated"] for b in books), default=datetime(2026, 1, 1))
    for n, chunk in enumerate(pages, 1):
        links = [
            f'  <link rel="self" type="{ACQ}" href="{page_url(base, n)}"/>',
            f'  <link rel="start" type="{ACQ}" href="{page_url(base, 1)}"/>',
            f'  <link rel="alternate" type="text/html" href="{base}"/>',
        ]
        if n > 1:
            links.append(f'  <link rel="previous" type="{ACQ}" href="{page_url(base, n - 1)}"/>')
        if n < len(pages):
            links.append(f'  <link rel="next" type="{ACQ}" href="{page_url(base, n + 1)}"/>')
        title = site["title"] if len(pages) == 1 else f"{site['title']} ({n}/{len(pages)})"
        xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="{ATOM_NS}" xmlns:dc="http://purl.org/dc/terms/" xmlns:opds="http://opds-spec.org/2010/catalog" xml:lang="uk">
  <id>urn:chytanka:catalog:all:{n}</id>
  <title>{esc(title)}</title>
  <subtitle>{esc(site['subtitle'])}</subtitle>
  <updated>{iso(updated)}</updated>
  <icon>{base}assets/icon.png</icon>
  <author><name>{esc(site['author'])}</name><uri>{base}</uri></author>
  <rights>Тексти — суспільне надбання; оцифрування — Вікіджерела (CC BY-SA); EPUB-видання «Читанки» — CC BY-SA 4.0.</rights>
{chr(10).join(links)}
{''.join(book_entry(b, base) for b in chunk)}</feed>
"""
        out["opds/index.xml" if n == 1 else f"opds/all-{n}.xml"] = xml
    return out


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


def landing_html(site: dict, books: list[dict]) -> str:
    base = site["base_url"]
    books = sort_books(books)
    total = sum(b["_size"] for b in books)
    sections, nav = [], []
    genres: dict[str, list[dict]] = {}
    for b in books:
        genres.setdefault(b.get("genre") or "Інше", []).append(b)
    for g, gbooks in genres.items():
        nav.append(f'<a href="#{genre_slug(g)}">{esc(g)} <span>{len(gbooks)}</span></a>')
        by_author: dict[str, list[dict]] = {}
        for b in gbooks:
            by_author.setdefault(b["author"], []).append(b)
        blocks = []
        for author, abooks in by_author.items():
            items = []
            for b in abooks:
                ed = b["edition"]
                ed_bits = ", ".join(str(x).replace("; ", "–") for x in (ed.get("city"), ed.get("year")) if x) if ed.get("city") or ed.get("year") \
                    else (b.get("source_note") or "Вікіджерела")
                items.append(f"""        <li class="book">
          <a class="cover" href="books/{esc(b['slug'])}.epub"><img src="covers/{esc(b['slug'])}.jpg" width="200" height="300" alt="" loading="lazy"></a>
          <div class="meta">
            <h4><a href="books/{esc(b['slug'])}.epub">{esc(b['title'])}</a></h4>
            <p class="sub">{esc(b.get('subtitle') or '')}</p>
            <p class="summary">{esc(b['summary'])}</p>
            <p class="facts">{esc(ed_bits)} · <a href="{esc(b['_source_url'])}">Вікіджерела</a> · <a class="dl" href="books/{esc(b['slug'])}.epub">EPUB, {human_size(b['_size'])}</a></p>
          </div>
        </li>""")
            blocks.append(f"""      <div class="author-block">
      <h3>{esc(author)} <span class="died">(†{abooks[0]['author_died']})</span></h3>
      <ul class="books">
{chr(10).join(items)}
      </ul>
      </div>""")
        sections.append(f"""  <section class="genre" id="{genre_slug(g)}">
    <h2>{esc(g)}</h2>
{chr(10).join(blocks)}
  </section>""")
    return f"""<!doctype html>
<html lang="uk">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(site['title'])}</title>
<meta name="description" content="{esc(site['subtitle'])}">
<link rel="icon" href="assets/icon.png">
<link rel="alternate" type="{ACQ}" title="{esc(site['title'])}" href="{base}opds/index.xml">
<style>
  :root {{ --ink:#161616; --muted:#5b5b5b; --line:#d9d6cf; --bg:#faf9f6; --card:#fff; }}
  @media (prefers-color-scheme: dark) {{ :root {{ --ink:#ecebe7; --muted:#a8a6a0; --line:#3a3936; --bg:#151514; --card:#1d1d1b; }} }}
  * {{ box-sizing: border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--ink); font:17px/1.55 Literata, Georgia, "Times New Roman", serif; }}
  main {{ max-width: 860px; margin: 0 auto; padding: 48px 20px 80px; }}
  header {{ display:flex; gap:18px; align-items:center; margin-bottom: 28px; }}
  header img {{ width:56px; height:56px; }}
  h1 {{ font-size: 2rem; line-height:1.15; margin:0; font-weight:600; }}
  .lede {{ color:var(--muted); margin:6px 0 0; }}
  .feed {{ background:var(--card); border:1px solid var(--line); border-radius:10px; padding:18px 20px; margin: 28px 0 36px; }}
  .feed code {{ display:block; overflow-x:auto; white-space:nowrap; font: 15px/1.4 ui-monospace, SFMono-Regular, Menlo, monospace; padding:10px 12px; background:var(--bg); border-radius:6px; margin:8px 0; }}
  nav.genres {{ display:flex; flex-wrap:wrap; gap:8px; margin: 8px 0 8px; }}
  nav.genres a {{ text-decoration:none; border:1px solid var(--line); background:var(--card); border-radius:999px; padding:4px 12px; font-size:.95rem; }}
  nav.genres a span {{ color:var(--muted); font-size:.85em; margin-left:2px; }}
  h2 {{ font-size:1.5rem; margin: 44px 0 4px; padding-bottom:6px; border-bottom:2px solid var(--ink); }}
  h3 {{ font-size:.95rem; letter-spacing:.06em; text-transform:uppercase; margin: 26px 0 10px; font-weight:600; }}
  h3 .died {{ color:var(--muted); font-weight:400; letter-spacing:0; text-transform:none; }}
  ul.books {{ list-style:none; padding:0; margin:0; display:grid; gap:12px; }}
  .book {{ display:flex; gap:16px; background:var(--card); border:1px solid var(--line); border-radius:10px; padding:12px 14px; }}
  .book .cover img {{ display:block; width:72px; height:auto; border:1px solid var(--line); }}
  .book h4 {{ margin: 0; font-size:1.1rem; line-height:1.3; }}
  .book h4 a {{ text-decoration:none; }}
  .sub {{ margin:0 0 4px; color:var(--muted); font-style:italic; font-size:.92rem; }}
  .summary {{ margin: 0 0 4px; font-size:.95rem; }}
  .facts {{ margin:0; color:var(--muted); font-size:.88rem; }}
  a {{ color:inherit; }}
  a.dl {{ font-weight:600; color:var(--ink); }}
  footer {{ margin-top:48px; color:var(--muted); font-size:.92rem; border-top:1px solid var(--line); padding-top:18px; }}
  @media (max-width: 520px) {{ .book .cover img {{ width:56px; }} }}
</style>
</head>
<body>
<main>
  <header>
    <img src="assets/mark.png" alt="">
    <div>
      <h1>{esc(site['title'])}</h1>
      <p class="lede">Безкоштовні EPUB української класики в суспільному надбанні — для читалки «Читанка» та будь-якого OPDS-клієнта.</p>
    </div>
  </header>

  <section class="feed">
    <strong>OPDS-каталог</strong> для «Читанки», KOReader, Thorium, Moon+ та інших OPDS-клієнтів:
    <code>{base}opds/index.xml</code>
    <span class="lede">English: OPDS 1.2 catalogue of public-domain Ukrainian classics, generated from uk.wikisource.</span>
  </section>

  <p class="lede">{len(books)} книжок, {human_size(total)} разом. Упорядковано за жанром і автором.</p>
  <nav class="genres">{" ".join(nav)}</nav>

{chr(10).join(sections)}

  <footer>
    <p><strong>Ліцензія.</strong> Тексти творів — суспільне надбання. Оцифрування й вичитка — волонтери
    <a href="https://uk.wikisource.org/">Вікіджерел</a> (CC BY-SA). Ці EPUB-видання «Читанки» поширюються за
    <a href="https://creativecommons.org/licenses/by-sa/4.0/deed.uk">CC BY-SA 4.0</a>; подробиці кожної книжки —
    на сторінці «Про це видання» всередині файлу та в <a href="LICENSE-BOOKS.txt">LICENSE-BOOKS.txt</a>.
    Скрипти збирання — MIT.</p>
    <p>Знайшли помилку в тексті? Виправте її на Вікіджерелах — наступне збирання каталогу підхопить зміни.</p>
  </footer>
</main>
</body>
</html>
"""


def licence_txt(site: dict, books: list[dict]) -> str:
    lines = [
        f"{site['title']} — атрибуції та ліцензії",
        "=" * 60,
        "",
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
    return "\n".join(lines)
