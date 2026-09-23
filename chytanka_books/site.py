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


def book_entry(b: dict, base: str) -> str:
    slug = b["slug"]
    ed = b["edition"]
    issued = f"\n    <dc:issued>{ed['year']}</dc:issued>" if ed.get("year") else ""
    ed_bits = ", ".join(str(x) for x in (ed.get("city"), ed.get("publisher"), ed.get("year")) if x)
    src = b.get("source_note") or ed_bits or "Вікіджерела"
    content = f"{b['summary']} Джерело тексту: {src}. Рік смерті автора: {b['author_died']}."
    return f"""  <entry>
    <id>urn:chytanka:book:{esc(slug)}</id>
    <title>{esc(b['title'])}</title>
    <author><name>{esc(b['author'])}</name></author>
    <updated>{iso(b['_updated'])}</updated>
    <dc:language>uk</dc:language>{issued}
    <dc:publisher>Читанка</dc:publisher>
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


def landing_html(site: dict, books: list[dict]) -> str:
    base = site["base_url"]
    cards = []
    for b in books:
        ed = b["edition"]
        ed_bits = ", ".join(str(x) for x in (ed.get("city"), ed.get("year")) if x)
        cards.append(f"""      <li class="book">
        <a class="cover" href="books/{esc(b['slug'])}.epub"><img src="covers/{esc(b['slug'])}.jpg" width="150" height="225" alt="Обкладинка: {esc(b['title'])}" loading="lazy"></a>
        <div class="meta">
          <p class="author">{esc(b['author'])}</p>
          <h3>{esc(b['title'])}</h3>
          <p class="summary">{esc(b['summary'])}</p>
          <p class="facts">{esc(ed_bits if ed.get('city') else (b.get('source_note') or 'Вікіджерела'))} · <a href="{esc(b['_source_url'])}">джерело</a></p>
          <a class="dl" href="books/{esc(b['slug'])}.epub">Завантажити EPUB · {human_size(b['_size'])}</a>
        </div>
      </li>""")
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
  h2 {{ font-size:1.25rem; margin: 36px 0 12px; }}
  ul.books {{ list-style:none; padding:0; margin:0; display:grid; gap:18px; }}
  .book {{ display:flex; gap:18px; background:var(--card); border:1px solid var(--line); border-radius:10px; padding:16px; }}
  .book .cover img {{ display:block; width:110px; height:auto; border:1px solid var(--line); }}
  .book h3 {{ margin: 0 0 6px; font-size:1.2rem; }}
  .author {{ margin:0; color:var(--muted); font-size:.9rem; letter-spacing:.04em; text-transform:uppercase; }}
  .summary {{ margin: 0 0 6px; }}
  .facts {{ margin:0 0 10px; color:var(--muted); font-size:.9rem; }}
  a {{ color:inherit; }}
  a.dl {{ display:inline-block; padding:6px 12px; border:1.5px solid var(--ink); border-radius:6px; text-decoration:none; font-size:.95rem; }}
  footer {{ margin-top:48px; color:var(--muted); font-size:.92rem; border-top:1px solid var(--line); padding-top:18px; }}
  @media (max-width: 520px) {{ .book {{ flex-direction:column; }} }}
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

  <h2>Книжки ({len(books)})</h2>
  <ul class="books">
{chr(10).join(cards)}
  </ul>

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
    for b in books:
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
