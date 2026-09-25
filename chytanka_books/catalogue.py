"""OPDS 1.2 catalogue (Language → Author → books) and the landing page.

Feed tree (every href absolute; ≤ MAX_OPDS_FEED_ENTRIES=50 rows per page, then rel="next"):

  opds/index.xml                 navigation root: Українська · English · Усі книжки / All books
  opds/uk/index.xml              navigation: genres (Проза, Поезія…) + authors (Ukrainian collation)
  opds/uk/genre-<g>.xml          acquisition: books of one genre
  opds/uk/<author>.xml           acquisition: books of one author
  opds/en/index.xml              navigation: Fiction, Non-fiction + authors (by surname)
  opds/en/fiction.xml, nonfiction.xml, <author>.xml
  opds/all.xml, all-2.xml, …     flat acquisition feed of everything (the pre-2026-09 layout)

Firmware notes (chytanka-main, OpdsParser + OpdsBookBrowserActivity, verified with
tools/opds_harness/crawl.cpp): an entry is NAVIGATION if it has a link whose type contains
"application/atom+xml" (rel is ignored; the LAST such link wins, so each nav entry carries
exactly one), BOOK if it has an acquisition link typed exactly application/epub+zip.
Navigation rows show only the title (no subtitle), so the book count goes into the title.
"""

from __future__ import annotations

import html
import re
import unicodedata
from datetime import datetime

from chytanka_books.site import (ACQ, GENRE_ORDER, NAV, fit_bytes, genre_slug, human_size, iso, surname, uk_key)

PAGE = 50
PSEUDONYMS = {"Леся Українка", "Марко Вовчок", "Панас Мирний", "Дніпрова Чайка"}
EN_CATEGORIES = [("fiction", "Fiction"), ("non-fiction", "Non-fiction")]

# KMU 2010 transliteration (simplified: no position-dependent є/ї/й/ю/я rules needed for slugs)
_TR = {"а": "a", "б": "b", "в": "v", "г": "h", "ґ": "g", "д": "d", "е": "e", "є": "ie", "ж": "zh", "з": "z",
       "и": "y", "і": "i", "ї": "i", "й": "i", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p",
       "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh",
       "щ": "shch", "ь": "", "ю": "iu", "я": "ia", "’": "", "'": ""}


def esc(s) -> str:
    return html.escape(str(s), quote=True)


def slugify(text: str) -> str:
    t = "".join(_TR.get(c, c) for c in text.lower())
    t = unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", t).strip("-")


def uk_books_word(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return "книжка"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return "книжки"
    return "книжок"


def en_key(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().casefold()


# --------------------------------------------------------------------------- grouping

def uk_author_display(author: str) -> str:
    """«Іван Франко» -> «Франко Іван»; pseudonyms and single names stay as they are."""
    parts = author.split()
    if author in PSEUDONYMS or len(parts) != 2:
        return author
    return f"{parts[1]} {parts[0]}"


def author_groups(books: list[dict], lang: str) -> list[dict]:
    groups: dict[str, dict] = {}
    for b in books:
        if lang == "uk":
            disp = uk_author_display(b["author"])
            key = uk_key(disp)
        else:
            disp = b.get("author_sort") or b["author"]
            key = en_key(disp)
        g = groups.setdefault(disp, {"display": disp, "key": key, "slug": slugify(disp), "died": b.get("author_died"),
                                     "name": b["author"], "books": []})
        g["books"].append(b)
    out = sorted(groups.values(), key=lambda g: g["key"])
    for g in out:
        g["books"].sort(key=lambda b: (uk_key(b["title"]) if lang == "uk" else en_key(b["title"])))
    slugs = [g["slug"] for g in out]
    dup = {s for s in slugs if slugs.count(s) > 1}
    reserved = {"index", "fiction", "nonfiction"} | {f"genre-{genre_slug(x)}" for x in GENRE_ORDER}
    assert not dup and not (set(slugs) & reserved), f"author slug collision: {dup or set(slugs) & reserved}"
    return out


def died_str(y, lang: str) -> str:
    if y is None:
        return ""
    y = int(y)
    if lang == "uk":
        return f"†{y}" if y > 0 else f"†{-y} до н. е."
    return f"d. {y}" if y > 0 else f"d. {-y} BC"


# --------------------------------------------------------------------------- XML pieces

def book_entry(b: dict, base: str) -> str:
    slug, lang = b["slug"], b["_lang"]
    if lang == "uk":
        ed = b["edition"]
        ed_bits = ", ".join(str(x) for x in (ed.get("city"), ed.get("publisher"), ed.get("year")) if x)
        src = b.get("source_note") or ed_bits or "Вікіджерела"
        content = f"{b['summary']} Джерело тексту: {src}. Рік смерті автора: {b['author_died']}."
        issued = ed.get("year")
        category = b.get("genre")
        rights = "Текст — суспільне надбання. Оцифрування: Вікіджерела. Видання: CC BY-SA 4.0"
        alt_title = "Вікіджерела"
    else:
        tr = f" Translated by {b['translator']}." if b.get("translator") and not str(b["translator"]).startswith("anonymous") else ""
        origin = "Standard Ebooks (CC0)" if b.get("se") else f"Project Gutenberg #{b['gutenberg']}"
        content = f"{b['summary']}{tr} Source: {origin}."
        issued = b.get("published") if (b.get("published") or 0) > 0 else None
        category = b.get("subject")
        rights = "Public domain. Chytanka edition derived from " + origin + "."
        alt_title = "Standard Ebooks" if b.get("se") else "Project Gutenberg"
    cat = f'\n    <category term="{esc(category)}" label="{esc(category)}"/>' if category else ""
    issued_xml = f"\n    <dc:issued>{issued}</dc:issued>" if issued else ""
    return f"""  <entry>
    <id>urn:chytanka:book:{esc(slug)}</id>
    <title>{esc(fit_bytes(b.get('feed_title') or b['title']))}</title>
    <author><name>{esc(b['author'])}</name></author>
    <updated>{iso(b['_updated'])}</updated>
    <dc:language>{lang}</dc:language>{issued_xml}
    <dc:publisher>Читанка</dc:publisher>{cat}
    <rights>{esc(rights)}</rights>
    <summary type="text">{esc(b['summary'])}</summary>
    <content type="text">{esc(content)}</content>
    <link rel="http://opds-spec.org/image" type="image/jpeg" href="{base}covers/{esc(slug)}-600.jpg"/>
    <link rel="http://opds-spec.org/image/thumbnail" type="image/jpeg" href="{base}covers/{esc(slug)}.jpg"/>
    <link rel="http://opds-spec.org/acquisition/open-access" type="application/epub+zip" href="{base}books/{esc(slug)}.epub" length="{b['_size']}" title="EPUB"/>
    <link rel="alternate" type="text/html" href="{esc(b['_source_url'])}" title="{alt_title}"/>
  </entry>
"""


def nav_entry(eid: str, title: str, href: str, content: str, updated: datetime, kind: str = ACQ) -> str:
    return f"""  <entry>
    <id>{esc(eid)}</id>
    <title>{esc(fit_bytes(title))}</title>
    <updated>{iso(updated)}</updated>
    <content type="text">{esc(content)}</content>
    <link rel="subsection" type="{kind}" href="{esc(href)}"/>
  </entry>
"""


def paged_feed(*, base: str, path: str, feed_id: str, title: str, subtitle: str, kind: str, entries: list[str],
               updated: datetime, up: str | None, lang: str = "uk") -> dict[str, str]:
    """path like 'opds/uk/index' -> opds/uk/index.xml, opds/uk/index-2.xml, …"""
    pages = [entries[i:i + PAGE] for i in range(0, len(entries), PAGE)] or [[]]
    typ = NAV if kind == "navigation" else ACQ

    def url(n: int) -> str:
        return f"{base}{path}.xml" if n == 1 else f"{base}{path}-{n}.xml"

    out = {}
    for n, chunk in enumerate(pages, 1):
        links = [f'  <link rel="self" type="{typ}" href="{url(n)}"/>',
                 f'  <link rel="start" type="{NAV}" href="{base}opds/index.xml"/>',
                 f'  <link rel="alternate" type="text/html" href="{base}"/>']
        if up:
            links.append(f'  <link rel="up" type="{NAV}" href="{up}"/>')
        if n > 1:
            links.append(f'  <link rel="previous" type="{typ}" href="{url(n - 1)}"/>')
        if n < len(pages):
            links.append(f'  <link rel="next" type="{typ}" href="{url(n + 1)}"/>')
        t = title if len(pages) == 1 else f"{title} ({n}/{len(pages)})"
        out[f"{path}.xml" if n == 1 else f"{path}-{n}.xml"] = f"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:dc="http://purl.org/dc/terms/" xmlns:opds="http://opds-spec.org/2010/catalog" xml:lang="{lang}">
  <id>{esc(feed_id)}:{n}</id>
  <title>{esc(t)}</title>
  <subtitle>{esc(subtitle)}</subtitle>
  <updated>{iso(updated)}</updated>
  <icon>{base}assets/icon.png</icon>
  <author><name>Читанка</name><uri>{base}</uri></author>
{chr(10).join(links)}
{''.join(chunk)}</feed>
"""
    return out


# --------------------------------------------------------------------------- feeds

def build_catalogue(site: dict, uk: list[dict], en: list[dict]) -> dict[str, str]:
    base = site["base_url"]
    everything = uk + en
    upd = max((b["_updated"] for b in everything), default=datetime(2026, 1, 1))
    files: dict[str, str] = {}
    root = f"{base}opds/index.xml"

    def latest(bs):
        return max((b["_updated"] for b in bs), default=upd)

    # uk
    uk_rows = []
    by_genre: dict[str, list[dict]] = {}
    for b in uk:
        by_genre.setdefault(b.get("genre") or "Інше", []).append(b)
    for g in [g for g in GENRE_ORDER if g in by_genre] + [g for g in by_genre if g not in GENRE_ORDER]:
        bs = sorted(by_genre[g], key=lambda b: (uk_key(uk_author_display(b["author"])), uk_key(b["title"])))
        path = f"opds/uk/genre-{genre_slug(g)}"
        uk_rows.append(nav_entry(f"urn:chytanka:nav:uk:genre:{genre_slug(g)}", f"{g} · {len(bs)}", f"{base}{path}.xml",
                                 f"{len(bs)} {uk_books_word(len(bs))}", latest(bs)))
        files.update(paged_feed(base=base, path=path, feed_id=f"urn:chytanka:uk:genre:{genre_slug(g)}", title=g,
                                subtitle="Українська класика", kind="acquisition", entries=[book_entry(b, base) for b in bs],
                                updated=latest(bs), up=f"{base}opds/uk/index.xml"))
    for g in author_groups(uk, "uk"):
        n = len(g["books"])
        path = f"opds/uk/{g['slug']}"
        uk_rows.append(nav_entry(f"urn:chytanka:nav:uk:author:{g['slug']}", f"{g['display']} ({n})", f"{base}{path}.xml",
                                 f"{n} {uk_books_word(n)} · {died_str(g['died'], 'uk')}", latest(g["books"])))
        files.update(paged_feed(base=base, path=path, feed_id=f"urn:chytanka:uk:author:{g['slug']}", title=g["name"],
                                subtitle=f"{n} {uk_books_word(n)} · {died_str(g['died'], 'uk')}", kind="acquisition",
                                entries=[book_entry(b, base) for b in g["books"]], updated=latest(g["books"]),
                                up=f"{base}opds/uk/index.xml"))
    files.update(paged_feed(base=base, path="opds/uk/index", feed_id="urn:chytanka:uk", title="Українська",
                            subtitle="Жанри й автори", kind="navigation", entries=uk_rows, updated=latest(uk), up=root))

    # en
    en_rows = []
    for cat, label in EN_CATEGORIES:
        bs = sorted([b for b in en if b["category"] == cat], key=lambda b: (en_key(b["author_sort"]), en_key(b["title"])))
        if not bs:
            continue
        path = f"opds/en/{cat.replace('-', '')}"
        en_rows.append(nav_entry(f"urn:chytanka:nav:en:{cat}", f"{label} · {len(bs)}", f"{base}{path}.xml",
                                 f"{len(bs)} books", latest(bs)))
        files.update(paged_feed(base=base, path=path, feed_id=f"urn:chytanka:en:{cat}", title=label,
                                subtitle="English classics", kind="acquisition", entries=[book_entry(b, base) for b in bs],
                                updated=latest(bs), up=f"{base}opds/en/index.xml", lang="en"))
    for g in author_groups(en, "en"):
        n = len(g["books"])
        path = f"opds/en/{g['slug']}"
        en_rows.append(nav_entry(f"urn:chytanka:nav:en:author:{g['slug']}", f"{g['display']} ({n})", f"{base}{path}.xml",
                                 f"{n} book{'s' if n != 1 else ''} · {died_str(g['died'], 'en')}", latest(g["books"])))
        files.update(paged_feed(base=base, path=path, feed_id=f"urn:chytanka:en:author:{g['slug']}", title=g["name"],
                                subtitle=f"{n} book{'s' if n != 1 else ''} · {died_str(g['died'], 'en')}",
                                kind="acquisition", entries=[book_entry(b, base) for b in g["books"]],
                                updated=latest(g["books"]), up=f"{base}opds/en/index.xml", lang="en"))
    files.update(paged_feed(base=base, path="opds/en/index", feed_id="urn:chytanka:en", title="English",
                            subtitle="Fiction, non-fiction and authors", kind="navigation", entries=en_rows,
                            updated=latest(en), up=root, lang="en"))

    # flat feed of everything (old layout, kept for existing links)
    files.update(paged_feed(base=base, path="opds/all", feed_id="urn:chytanka:catalog:all", title=f"{site['title']} — усі книжки",
                            subtitle=site["subtitle"], kind="acquisition",
                            entries=[book_entry(b, base) for b in everything], updated=upd, up=root))

    # root
    root_rows = [
        nav_entry("urn:chytanka:nav:uk", f"Українська ({len(uk)})", f"{base}opds/uk/index.xml",
                  f"{len(uk)} {uk_books_word(len(uk))} української класики з Вікіджерел", latest(uk), NAV),
        nav_entry("urn:chytanka:nav:en", f"English ({len(en)})", f"{base}opds/en/index.xml",
                  f"{len(en)} English classics: " + ", ".join(
                      f"{sum(1 for b in en if b['category'] == c)} {lbl.lower()}" for c, lbl in EN_CATEGORIES),
                  latest(en), NAV),
        nav_entry("urn:chytanka:nav:all", f"Усі книжки · All books ({len(everything)})", f"{base}opds/all.xml",
                  "Єдиний список усіх книжок каталогу", upd),
    ]
    files.update(paged_feed(base=base, path="opds/index", feed_id="urn:chytanka:root", title=site["title"],
                            subtitle=site["subtitle"], kind="navigation", entries=root_rows, updated=upd, up=None))
    return files


# --------------------------------------------------------------------------- landing page

def landing_html(site: dict, uk: list[dict], en: list[dict]) -> str:
    base = site["base_url"]
    total = sum(b["_size"] for b in uk + en)

    def card(b: dict) -> str:
        if b["_lang"] == "uk":
            ed = b["edition"]
            facts = (", ".join(str(x).replace("; ", "–") for x in (ed.get("city"), ed.get("year")) if x)
                     if ed.get("city") or ed.get("year") else (b.get("source_note") or "Вікіджерела"))
            g_, st_ = b.get("genre") or "", b.get("subtitle") or ""
            same = g_[:4].lower() == st_[:4].lower()  # «Поезія · Поезії» -> «Поезії»
            src_label, sub = "Вікіджерела", " · ".join(x for x in ((None if same else g_), st_) if x)
        else:
            y = b.get("published")
            facts = (f"{-y} BC" if y and y < 0 else str(y or ""))
            if b.get("translator") and not str(b["translator"]).startswith("anonymous"):
                facts += f" · tr. {b['translator']}"
            src_label = "Standard Ebooks" if b.get("se") else "Project Gutenberg"
            sub = f"{'Fiction' if b['category'] == 'fiction' else 'Non-fiction'} · {b.get('subject')}"
        return f"""          <li class="book">
            <a class="cover" href="books/{esc(b['slug'])}.epub"><img src="covers/{esc(b['slug'])}.jpg" width="200" height="300" alt="" loading="lazy"></a>
            <div class="meta">
              <h4><a href="books/{esc(b['slug'])}.epub">{esc(b['title'])}</a></h4>
              <p class="sub">{esc(sub)}</p>
              <p class="summary">{esc(b['summary'])}</p>
              <p class="facts">{esc(facts)} · <a href="{esc(b['_source_url'])}">{src_label}</a> · <a class="dl" href="books/{esc(b['slug'])}.epub">EPUB, {human_size(b['_size'])}</a></p>
            </div>
          </li>"""

    def section(lang: str, books: list[dict], sid: str, heading: str, lede: str) -> str:
        groups = author_groups(books, lang)
        index = " · ".join(f'<a href="#{sid}-{g["slug"]}">{esc(g["display"])}</a>' for g in groups)
        blocks = []
        for g in groups:
            blocks.append(f"""      <div class="author-block" id="{sid}-{g['slug']}">
        <h3>{esc(g['display'])} <span class="died">({esc(died_str(g['died'], lang))})</span></h3>
        <ul class="books">
{chr(10).join(card(b) for b in g['books'])}
        </ul>
      </div>""")
        return f"""  <section class="lang" id="{sid}" lang="{lang}">
    <h2>{esc(heading)} <span class="count">{len(books)}</span></h2>
    <p class="lede">{esc(lede)}</p>
    <p class="authors">{index}</p>
{chr(10).join(blocks)}
  </section>"""

    n_f = sum(1 for b in en if b["category"] == "fiction")
    uk_sec = section("uk", uk, "uk", "Українська",
                     "Українська класика в суспільному надбанні: тексти Вікіджерел, вичитані за сканами конкретних видань.")
    en_sec = section("en", en, "en", "English",
                     f"{n_f} fiction and {len(en) - n_f} non-fiction classics, from Standard Ebooks (CC0) "
                     "and, where they lack a key work, Project Gutenberg. Authors sorted by surname.")
    return f"""<!doctype html>
<html lang="uk">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(site['title'])}</title>
<meta name="description" content="{esc(site['subtitle'])}">
<link rel="icon" href="assets/icon.png">
<link rel="alternate" type="{NAV}" title="{esc(site['title'])}" href="{base}opds/index.xml">
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
  .feed {{ background:var(--card); border:1px solid var(--line); border-radius:10px; padding:18px 20px; margin: 28px 0 24px; }}
  .feed code {{ display:block; overflow-x:auto; white-space:nowrap; font: 15px/1.4 ui-monospace, SFMono-Regular, Menlo, monospace; padding:10px 12px; background:var(--bg); border-radius:6px; margin:8px 0; }}
  nav.langs {{ display:flex; flex-wrap:wrap; gap:8px; margin: 8px 0; }}
  nav.langs a {{ text-decoration:none; border:1px solid var(--line); background:var(--card); border-radius:999px; padding:4px 14px; }}
  nav.langs a span, h2 .count {{ color:var(--muted); font-size:.85em; margin-left:2px; font-weight:400; }}
  h2 {{ font-size:1.6rem; margin: 48px 0 4px; padding-bottom:6px; border-bottom:2px solid var(--ink); }}
  .authors {{ font-size:.92rem; color:var(--muted); margin: 10px 0 0; line-height:1.8; }}
  .authors a {{ color:var(--ink); }}
  h3 {{ font-size:.95rem; letter-spacing:.06em; text-transform:uppercase; margin: 28px 0 10px; font-weight:600; }}
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
      <p class="lede">Безкоштовні EPUB класики в суспільному надбанні — українською та англійською — для читалки «Читанка» та будь-якого OPDS-клієнта.</p>
    </div>
  </header>

  <section class="feed">
    <strong>OPDS-каталог</strong> для «Читанки», KOReader, Thorium, Moon+ та інших OPDS-клієнтів:
    <code>{base}opds/index.xml</code>
    <span class="lede">Мова → автор → книжки. English: OPDS 1.2 catalogue — language → author → books.</span>
  </section>

  <p class="lede">{len(uk) + len(en)} книжок, {human_size(total)} разом.</p>
  <nav class="langs"><a href="#uk">Українська <span>{len(uk)}</span></a> <a href="#en" lang="en">English <span>{len(en)}</span></a></nav>

{uk_sec}

{en_sec}

  <footer>
    <p><strong>Ліцензія.</strong> Українські тексти — суспільне надбання; оцифрування й вичитка — волонтери
    <a href="https://uk.wikisource.org/">Вікіджерел</a> (CC BY-SA); EPUB-видання «Читанки» — <a href="https://creativecommons.org/licenses/by-sa/4.0/deed.uk">CC BY-SA 4.0</a>.
    Англійські книжки — суспільне надбання; похідні від видань <a href="https://standardebooks.org/">Standard Ebooks</a> (CC0)
    і <a href="https://www.gutenberg.org/">Project Gutenberg</a> (з повною ліцензією PG усередині файлу), не є їхніми офіційними випусками.
    Подробиці — на сторінці «Про це видання» / «About this edition» в кожному файлі та в <a href="LICENSE-BOOKS.txt">LICENSE-BOOKS.txt</a>.
    Скрипти збирання — MIT.</p>
  </footer>
</main>
</body>
</html>
"""
