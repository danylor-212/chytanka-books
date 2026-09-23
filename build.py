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
from chytanka_books.epub import process_epub, ws_url
from chytanka_books.site import build_feeds, human_size, landing_html, licence_txt
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
    return cov.render_cover(title=book["title"], author=book["author"], subtitle=book.get("subtitle"),
                            edition_line=edition_line(book), fonts_dir=FONTS, logo_png=MARK)


def write_site_assets(out: Path) -> None:
    from PIL import Image

    (out / "assets").mkdir(parents=True, exist_ok=True)
    Image.open(ROOT / "assets/logo/chytanka-mark-480-transparent.png").resize((112, 112), Image.LANCZOS) \
        .save(out / "assets/mark.png", optimize=True)
    Image.open(MARK).resize((192, 192), Image.LANCZOS).save(out / "assets/icon.png", optimize=True)
    (out / ".nojekyll").write_text("")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=str(ROOT / "books.yaml"))
    ap.add_argument("--out", default=str(ROOT / "public"))
    ap.add_argument("--cache", default=os.environ.get("CHYTANKA_CACHE", str(ROOT / ".cache")))
    ap.add_argument("--refresh", action="store_true", help="re-download from WS Export / API")
    ap.add_argument("--only", action="append", help="build only these slugs (feed still lists all built)")
    ap.add_argument("--base-url", help="override site.base_url (must end with /)")
    ap.add_argument("--page-size", type=int, help="override entries per OPDS page (firmware max 50)")
    ap.add_argument("--preview", help="also write a contact sheet PNG of all covers here")
    ap.add_argument("--report", default=None, help="write JSON build report here (default: <out>/../build-report.json)")
    args = ap.parse_args()

    cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    site = cfg["site"]
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
        cover_imgs.append(img)
        epub, rep = process_epub(raw, book, cover_jpeg=cov.jpeg_bytes(img, (600, 900)), source_url=src_url,
                                 revid=revid, rev_ts=rev_ts, site_url=site["base_url"], modified=updated)
        errs = validate_epub(epub)
        if errs:
            rep.block("validation: " + "; ".join(errs[:10]))
        if book.get("hold"):
            rep.block(f"held in books.yaml: {book['hold']}")

        print(f"   {human_size(rep.size_raw)} -> {human_size(rep.size_out)}; fonts -{len(rep.fonts_removed)} "
              f"({human_size(rep.fonts_bytes)}); images removed {len(rep.images_removed)}; "
              f"notes removed {len(rep.notes_removed)} kept {len(rep.notes_kept)}; licence boxes {rep.licence_boxes_removed}; "
              f"links unwrapped {rep.links_unwrapped}; release-ready: {'YES' if rep.release_ready else 'NO'}")
        for r in rep.not_ready_reasons:
            print(f"   NOT READY: {r}")

        row = {k: v for k, v in rep.__dict__.items()}
        row.update(title=book["title"], author=book["author"], revid=revid, rev_ts=rev_ts, source=src_url)
        report_rows.append(row)
        if not rep.release_ready:
            failed = failed or bool(errs)
            continue
        (out / "books" / f"{book['slug']}.epub").write_bytes(epub)
        cov.save_jpeg(img, out / "covers" / f"{book['slug']}.jpg", (200, 300), quality=85)
        cov.save_jpeg(img, out / "covers" / f"{book['slug']}-600.jpg", (600, 900), quality=85)
        published.append({**book, "_size": len(epub), "_updated": updated, "_source_url": src_url, "_revid": revid})

    for rel, xml in build_feeds(site, published, page_size).items():
        (out / rel).write_text(xml, encoding="utf-8")
    (out / "index.html").write_text(landing_html(site, published), encoding="utf-8")
    (out / "LICENSE-BOOKS.txt").write_text(licence_txt(site, published), encoding="utf-8")
    write_site_assets(out)

    rp = Path(args.report) if args.report else out.parent / "build-report.json"
    rp.write_text(json.dumps(report_rows, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    if args.preview:
        cov.contact_sheet(cover_imgs, Path(args.preview))
        print(f"preview: {args.preview}")
    print(f"published {len(published)} book(s) -> {out}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
