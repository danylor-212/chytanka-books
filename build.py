#!/usr/bin/env python3
"""Build the «Читанка — Книжки» OPDS catalogue.

    python build.py                     # fetch (cached), process, validate, write public/
    python build.py --refresh           # re-download from WS Export
    python build.py --only pidmohylnyi-misto
    python build.py --preview brand/books-preview.png

Pipeline per book (books.yaml):
  WS Export EPUB -> strip fonts -> clean MediaWiki markup -> (US-only editions:
  drop illustrations + editorial footnotes) -> fix image extensions -> Chytanka
  cover (cover-image + meta cover) -> credits page -> metadata -> validate ->
  public/books/<slug>.epub, public/covers/<slug>.jpg, public/opds/index.xml,
  public/index.html.
A book that fails validation or is flagged not release-ready is NOT published.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import yaml

from chytanka_books import cover as cov
from chytanka_books import english
from chytanka_books.catalogue import build_catalogue, landing_html
from chytanka_books.epub import process_epub, ws_url
from chytanka_books.site import human_size, licence_txt, normalize_book, sort_books, typographic_apostrophes
from chytanka_books.validate import validate_epub

ROOT = Path(__file__).resolve().parent
UA = "ChytankaBooks/1.0 (+https://github.com/danylor-212/chytanka-books)"
WS_EXPORT = "https://ws-export.wmcloud.org/book.php"
WS_API = "https://uk.wikisource.org/w/api.php"
FONTS = ROOT / "assets" / "fonts"
MARK = ROOT / "assets" / "logo" / "chytanka-mark-480.png"


def http_get(url: str, params: dict, timeout: int = 240, retries: int = 4) -> bytes:
    full = url + "?" + urllib.parse.urlencode(params)
    delay = 5
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(full, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as e:  # noqa: BLE001
            if attempt == retries:
                raise
            print(f"    retry {attempt} after error: {e}", file=sys.stderr)
            time.sleep(delay)
            delay *= 2
    raise RuntimeError("unreachable")


def fetch_raw(book: dict, cache: Path, refresh: bool) -> bytes:
    p = cache / f"{book['slug']}.ws.epub"
    if p.exists() and not refresh:
        return p.read_bytes()
    print(f"  fetching WS Export: {book['page']}")
    data = http_get(WS_EXPORT, {"lang": "uk", "format": "epub-3", "page": book["page"]})
    if not data.startswith(b"PK"):
        raise RuntimeError(f"WS Export did not return a zip for {book['page']}: {data[:200]!r}")
    cache.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    time.sleep(2)  # be polite to Toolforge / WMCloud
    return data


def fetch_revision(book: dict, cache: Path, refresh: bool) -> tuple[int | None, str | None]:
    p = cache / f"{book['slug']}.rev.json"
    if p.exists() and not refresh:
        d = json.loads(p.read_text())
        return d.get("revid"), d.get("timestamp")
    try:
        raw = http_get(WS_API, {"action": "query", "prop": "revisions", "rvprop": "ids|timestamp",
                                "titles": book["page"], "format": "json", "maxlag": "5"}, timeout=60)
        page = next(iter(json.loads(raw)["query"]["pages"].values()))
        rev = page["revisions"][0]
        d = {"revid": rev["revid"], "timestamp": rev["timestamp"]}
    except Exception as e:  # noqa: BLE001
        print(f"    WARN: revision lookup failed: {e}", file=sys.stderr)
        d = {"revid": None, "timestamp": None}
    cache.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(d))
    time.sleep(1)
    return d["revid"], d["timestamp"]


def edition_line(book: dict) -> str | None:
    ed = book.get("edition") or {}
    bits = [x for x in (ed.get("city"), str(ed["year"]) if ed.get("year") else None) if x]
    return " · ".join(bits) or None


def make_cover(book: dict):
    return cov.render_cover(title=book.get("cover_title") or book["title"], author=book["author"], subtitle=book.get("subtitle"),
                            edition_line=edition_line(book), fonts_dir=FONTS, logo_png=MARK)


def make_cover_en(book: dict):
    y = book.get("published")
    tr = book.get("translator")
    if tr and not str(tr).startswith("anonymous"):
        line = f"Translated by {tr}"
    elif y is not None:
        line = f"{-y} BC" if y < 0 else str(y)
    else:
        line = None
    return cov.render_cover(title=book.get("cover_title") or book["title"], author=book["author"],
                            subtitle=book.get("subject") if book.get("category") == "non-fiction" else None,
                            edition_line=line, fonts_dir=FONTS, logo_png=MARK, brand_line="Chytanka · public domain")


def write_site_assets(out: Path) -> None:
    from PIL import Image

    (out / "assets").mkdir(parents=True, exist_ok=True)
    Image.open(ROOT / "assets/logo/chytanka-mark-480-transparent.png").resize((112, 112), Image.LANCZOS) \
        .save(out / "assets/mark.png", optimize=True)
    Image.open(MARK).resize((192, 192), Image.LANCZOS).save(out / "assets/icon.png", optimize=True)
    (out / ".nojekyll").write_text("")


def catalogue_md(site: dict, rows: list[dict]) -> str:
    from chytanka_books.site import GENRE_ORDER, surname, uk_key

    def key(r):
        g = r.get("genre") or "Інше"
        return (GENRE_ORDER.index(g) if g in GENRE_ORDER else 99, uk_key(surname(r["author"])), uk_key(r["title"]))

    all_rows = rows
    rows = sorted([r for r in rows if r.get("lang") != "en"], key=key)
    en_rows = sorted([r for r in all_rows if r.get("lang") == "en"],
                     key=lambda r: (r.get("category") != "fiction", (r.get("author_sort") or r["author"]).casefold(), r["title"]))
    pub = [r for r in rows if r["release_ready"]]
    pub_en = [r for r in en_rows if r["release_ready"]]
    n_fic = sum(1 for r in pub_en if r.get("category") == "fiction")
    size_uk = sum(r["size_out"] for r in pub)
    size_en = sum(r["size_out"] for r in pub_en)
    lines = [f"# {site['title']} — каталог", "",
             f"Згенеровано `build.py` {datetime.now(timezone.utc):%Y-%m-%d}.", "",
             f"- **Українська:** опубліковано **{len(pub)}**, відкладено **{len(rows) - len(pub)}**, {human_size(size_uk)}.",
             f"- **English:** опубліковано **{len(pub_en)}** ({n_fic} fiction, {len(pub_en) - n_fic} non-fiction), "
             f"відкладено **{len(en_rows) - len(pub_en)}**, {human_size(size_en)}.",
             f"- **Разом:** {len(pub) + len(pub_en)} книжок, **{human_size(size_uk + size_en)}**.", "",
             "OPDS: `opds/index.xml` → Українська / English / Усі книжки → жанр або автор → книжки.", "",
             "Виноски: «прибрано» — редакторські (US-only або strip_editorial), «авт.» — збережені авторські, "
             "«у тексті» — виноски видань поза NY, що лишилися без змін (юридично PD).", "",
             "# Українська"]
    genre = None
    for r in rows:
        g = r.get("genre") or "Інше"
        if g != genre:
            genre = g
            lines += ["", f"## {g}", "", "| Автор | Твір | Жанр | Видання | Розмір | Виноски | Ілюстрації | Статус |",
                      "|---|---|---|---|---|---|---|---|"]
        ed = r.get("edition") or {}
        ed_s = r.get("source_note") or ", ".join(str(x) for x in (ed.get("city"), ed.get("publisher"), ed.get("year")) if x)
        flags = []
        if r.get("us_only_edition"):
            flags.append("US-only")
        if r.get("strip_editorial"):
            flags.append("strip_editorial")
        notes = []
        if r["notes_removed"]:
            notes.append(f"прибрано {len(r['notes_removed'])}")
        if r["notes_kept"]:
            notes.append(f"авт. {len(r['notes_kept'])}")
        if r["notes_in_text"]:
            notes.append(f"у тексті {r['notes_in_text']}")
        imgs = f"прибрано {len(r['images_removed'])}" if r["images_removed"] else "—"
        extra = []
        if r["chapters_dropped"]:
            extra.append(f"вилучено сторінок: {len(r['chapters_dropped'])}")
        if r["chapters_split"]:
            extra.append(f"поділено розділів: {len(r['chapters_split'])}")
        status = "✅ опубліковано" if r["release_ready"] else "⏸ " + "; ".join(r["not_ready_reasons"])[:160]
        if extra:
            status += " (" + ", ".join(extra) + ")"
        lines.append(f"| {r['author']} | {r['title']} | {r.get('subtitle') or ''} | {ed_s}{' · ' + ', '.join(flags) if flags else ''} "
                     f"| {human_size(r['size_out'])} | {', '.join(notes) or '—'} | {imgs} | {status} |")
    dropped = [(r["title"], d) for r in rows for d in r["chapters_dropped"]]
    if dropped:
        lines += ["", "## Вилучені сторінки (не належать до твору)", ""] + [f"- «{t}»: {d}" for t, d in dropped]
    if en_rows:
        lines += ["", "# English", "",
                  "Джерело: Standard Ebooks (CC0; зібрано з їхніх GitHub-репозиторіїв інструментом `se build`) або, "
                  "де SE немає ключового твору, Project Gutenberg (ліцензію PG збережено у файлі). Зміни «Читанки»: "
                  "обкладинка «Читанки», сторінка «About this edition», власний dc:identifier. Автори й перекладачі — †<1954."]
        cat_ = None
        for r in en_rows:
            if r.get("category") != cat_:
                cat_ = r.get("category")
                lines += ["", f"## {'Fiction' if cat_ == 'fiction' else 'Non-fiction'}", "",
                          "| Author | Title | Subject | Translator | Source | Size | Status |", "|---|---|---|---|---|---|---|"]
            tr = r.get("translator") or ""
            if tr and r.get("translator_died"):
                tr += f" (†{r['translator_died']})"
            status = "✅" if r["release_ready"] else "⏸ " + "; ".join(r.get("not_ready_reasons") or [])[:160]
            lines.append(f"| {r['author']} (†{r.get('author_died')}) | {r['title']} | {r.get('subject') or ''} | {tr} | "
                         f"{r.get('origin') or ''} | {human_size(r.get('size_out') or 0)} | {status} |")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=str(ROOT / "books.yaml"))
    ap.add_argument("--config-en", default=str(ROOT / "books-en.yaml"))
    ap.add_argument("--refresh-en", action="store_true",
                    help="check Standard Ebooks repos for new commits (git ls-remote) and rebuild changed books")
    ap.add_argument("--no-en", action="store_true", help="skip English books")
    ap.add_argument("--preview-en", help="contact sheet of the English covers")
    ap.add_argument("--out", default=str(ROOT / "public"))
    ap.add_argument("--cache", default=os.environ.get("CHYTANKA_CACHE", str(ROOT / ".cache")))
    ap.add_argument("--refresh", action="store_true", help="re-download from WS Export / API")
    ap.add_argument("--only", action="append", help="build only these slugs (feed still lists all built)")
    ap.add_argument("--base-url", help="override site.base_url (must end with /)")
    ap.add_argument("--page-size", type=int, help="override entries per OPDS page (firmware max 50)")
    ap.add_argument("--preview", help="also write a contact sheet PNG of all covers here")
    ap.add_argument("--summary", help="also write a Markdown catalogue summary here")
    ap.add_argument("--report", default=None, help="write JSON build report here (default: <out>/../build-report.json)")
    args = ap.parse_args()

    cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    site = cfg["site"]
    for k in ("title", "subtitle", "author"):
        site[k] = typographic_apostrophes(site.get(k))
    cfg["books"] = [normalize_book(b) for b in cfg["books"]]
    if args.base_url:
        site["base_url"] = args.base_url
    if not site["base_url"].endswith("/"):
        site["base_url"] += "/"
    page_size = args.page_size or int(site.get("page_size", 50))
    if page_size > 50:
        print("page_size > 50 exceeds firmware MAX_OPDS_FEED_ENTRIES; clamping to 50", file=sys.stderr)
        page_size = 50

    out = Path(args.out)
    cache = Path(args.cache)
    if out.exists():
        shutil.rmtree(out)
    (out / "books").mkdir(parents=True)
    (out / "covers").mkdir(parents=True)
    (out / "opds").mkdir(parents=True)

    published, report_rows, cover_imgs = [], [], []
    failed = False
    for book in cfg["books"]:
        if args.only and book["slug"] not in args.only:
            continue
        print(f"== {book['slug']}")
        raw = fetch_raw(book, cache, args.refresh)
        revid, rev_ts = fetch_revision(book, cache, args.refresh)
        src_url = ws_url(book["page"])
        fmt = "%Y-%m-%dT%H:%M:%SZ"
        pipeline = datetime.strptime(site.get("pipeline_date", "2026-01-01T00:00:00Z"), fmt).replace(tzinfo=timezone.utc)
        updated = max(pipeline, datetime.strptime(rev_ts, fmt).replace(tzinfo=timezone.utc)) if rev_ts \
            else datetime.now(timezone.utc).replace(microsecond=0)

        img = make_cover(book)
        cover_imgs.append((book["slug"], img))
        epub, rep = process_epub(raw, book, cover_jpeg=cov.jpeg_bytes(img, (600, 900)), source_url=src_url,
                                 revid=revid, rev_ts=rev_ts, site_url=site["base_url"], modified=updated)
        errs = validate_epub(epub)
        if errs:
            rep.block("validation: " + "; ".join(errs[:10]))
        if book.get("hold"):
            rep.block(f"held in books.yaml: {book['hold']}")

        print(f"   {human_size(rep.size_raw)} -> {human_size(rep.size_out)}; largest xhtml {human_size(rep.largest_xhtml)}; "
              f"images -{len(rep.images_removed)}; notes -{len(rep.notes_removed)} kept(author) {len(rep.notes_kept)} "
              f"in-text {rep.notes_in_text}; toc tables {rep.toc_tables_collapsed}; entity spans {rep.entity_spans_unwrapped}; "
              f"split {len(rep.chapters_split)}; dropped chapters {len(rep.chapters_dropped)}; "
              f"CSS decls -{rep.css_dropped}; release-ready: {'YES' if rep.release_ready else 'NO'}")
        for d in rep.chapters_dropped:
            print(f"   dropped (not part of the work): {d}")
        for r in rep.not_ready_reasons:
            print(f"   NOT READY: {r}")

        row = {k: v for k, v in rep.__dict__.items()}
        row.update(title=book["title"], author=book["author"], revid=revid, rev_ts=rev_ts, source=src_url,
                   genre=book.get("genre"), edition=book.get("edition"), source_note=book.get("source_note"),
                   subtitle=book.get("subtitle"), us_only_edition=bool(book.get("us_only_edition")),
                   strip_editorial=bool(book.get("strip_editorial")), strip_images=bool(book.get("strip_images")),
                   hold=book.get("hold"))
        report_rows.append(row)
        if not rep.release_ready:
            failed = failed or bool(errs)
            continue
        (out / "books" / f"{book['slug']}.epub").write_bytes(epub)
        cov.save_jpeg(img, out / "covers" / f"{book['slug']}.jpg", (200, 300), quality=85)
        cov.save_jpeg(img, out / "covers" / f"{book['slug']}-600.jpg", (600, 900), quality=85)
        published.append({**book, "_size": len(epub), "_updated": updated, "_source_url": src_url, "_revid": revid})

    published = sort_books(published)
    for b in published:
        b["_lang"] = "uk"

    # ---------------- English
    published_en, cover_imgs_en = [], []
    if not args.no_en and Path(args.config_en).exists():
        cfg_en = yaml.safe_load(Path(args.config_en).read_text(encoding="utf-8"))
        en_cache = cache / "en"
        fmt = "%Y-%m-%dT%H:%M:%SZ"
        pipeline = datetime.strptime(site.get("pipeline_date", "2026-01-01T00:00:00Z"), fmt).replace(tzinfo=timezone.utc)
        from concurrent.futures import ThreadPoolExecutor

        todo = [normalize_book(b) for b in cfg_en["books"] if not args.only or b["slug"] in args.only]
        # clone + `se build` is slow (~10–20 s per book): run up to 4 in parallel, then process in order
        pool = ThreadPoolExecutor(max_workers=int(os.environ.get("SE_JOBS", "4")))
        budget = english.RebuildBudget()
        se_jobs = {b["slug"]: pool.submit(english.fetch_se, b["se"], en_cache, args.refresh_en, None, budget)
                   for b in todo if b.get("se")}
        for book in todo:
            print(f"== en:{book['slug']}")
            commit = None
            try:
                if book.get("se"):
                    raw, commit = se_jobs[book["slug"]].result()
                    src_url = "https://standardebooks.org/ebooks/" + book["se"].replace("_", "/")
                else:
                    raw = english.fetch_pg(int(book["gutenberg"]), en_cache, UA, refresh=False)
                    src_url = f"https://www.gutenberg.org/ebooks/{book['gutenberg']}"
            except Exception as e:  # noqa: BLE001
                print(f"   NOT READY: fetch/build failed: {e}")
                report_rows.append({"slug": book["slug"], "title": book["title"], "author": book["author"], "lang": "en",
                                    "release_ready": False, "not_ready_reasons": [f"fetch failed: {e}"]})
                failed = True
                continue
            img = make_cover_en(book)
            cover_imgs_en.append((book["slug"], img))
            epub, rep = english.process_en(raw, book, cover_jpeg=cov.jpeg_bytes(img, (600, 900)), source_url=src_url,
                                           commit=commit, site_url=site["base_url"], modified=pipeline)
            errs = validate_epub(epub, lang="en")
            if errs:
                rep.block("validation: " + "; ".join(errs[:10]))
            if book.get("hold"):
                rep.block(f"held in books-en.yaml: {book['hold']}")
            print(f"   {human_size(rep.size_raw)} -> {human_size(rep.size_out)}; largest xhtml {human_size(rep.largest_xhtml)}; "
                  f"fonts -{len(rep.fonts_removed)}; old cover removed: {bool(rep.images_removed)}; "
                  f"release-ready: {'YES' if rep.release_ready else 'NO'}")
            for r in rep.not_ready_reasons:
                print(f"   NOT READY: {r}")
            row = {k: v for k, v in rep.__dict__.items()}
            row.update(lang="en", title=book["title"], author=book["author"], source=src_url, commit=commit,
                       category=book["category"], subject=book.get("subject"), translator=book.get("translator"),
                       author_died=book.get("author_died"), translator_died=book.get("translator_died"),
                       origin="Standard Ebooks" if book.get("se") else "Project Gutenberg", published=book.get("published"),
                       author_sort=book.get("author_sort"))
            report_rows.append(row)
            if not rep.release_ready:
                failed = failed or bool(errs)
                continue
            (out / "books" / f"{book['slug']}.epub").write_bytes(epub)
            cov.save_jpeg(img, out / "covers" / f"{book['slug']}.jpg", (200, 300), quality=85)
            cov.save_jpeg(img, out / "covers" / f"{book['slug']}-600.jpg", (600, 900), quality=85)
            published_en.append({**book, "_lang": "en", "_size": len(epub), "_updated": pipeline, "_source_url": src_url,
                                 "_commit": commit})
        pool.shutdown()
        print(f"Standard Ebooks: {budget.used} (re)built this run (limit {budget.limit}); "
              f"deferred to a later run: {len(budget.deferred)} {budget.deferred[:10]}")

    for rel, xml in build_catalogue(site, published, published_en).items():
        (out / rel).parent.mkdir(parents=True, exist_ok=True)
        (out / rel).write_text(xml, encoding="utf-8")
    (out / "index.html").write_text(landing_html(site, published, published_en), encoding="utf-8")
    (out / "LICENSE-BOOKS.txt").write_text(licence_txt(site, published, published_en), encoding="utf-8")
    write_site_assets(out)

    rp = Path(args.report) if args.report else out.parent / "build-report.json"
    rp.write_text(json.dumps(report_rows, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    if args.summary:
        Path(args.summary).write_text(catalogue_md(site, report_rows), encoding="utf-8")
        print(f"summary: {args.summary}")
    if args.preview:
        order = {b["slug"]: i for i, b in enumerate(sort_books([{**b, "_size": 0} for b in cfg["books"]]))}
        imgs = [im for _, im in sorted(cover_imgs, key=lambda t: order.get(t[0], 0))]
        big = len(imgs) > 12
        cov.contact_sheet(imgs, Path(args.preview), cols=10 if big else 3, cell=(240, 360) if big else (400, 600),
                          gap=24 if big else 40)
        print(f"preview: {args.preview}")
    if args.preview_en and cover_imgs_en:
        from chytanka_books.catalogue import en_key
        order = sorted(cover_imgs_en, key=lambda t: next((en_key(b["author_sort"]) + en_key(b["title"]) for b in published_en
                                                            if b["slug"] == t[0]), ""))
        cov.contact_sheet([im for _, im in order], Path(args.preview_en), cols=12, cell=(200, 300), gap=20)
        print(f"preview-en: {args.preview_en}")
    print(f"published {len(published)} uk + {len(published_en)} en book(s) -> {out}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
