"""Typographic grayscale covers in the Chytanka brand style.

Layout is drawn on a 1200x1800 (2:3) master canvas and downscaled, so it
survives the 480x800 X4/X3 panel and small OPDS thumbnails. Only black,
white and a few greys: it has to look right on 16-level e-ink.
"""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 1200, 1800
MARGIN = 96
INK = 0
GREY = 90
LIGHT = 150


def _font(fonts_dir: Path, name: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(fonts_dir / name), size)


def _render_logo(mark_png: Path, size: int) -> Image.Image:
    """Brand mark (book + tryzub), pre-rasterized from chytanka-logo-transparent.svg.

    A committed PNG keeps the build free of a system cairo dependency; regenerate
    it with `python build.py --render-mark` if the SVG changes.
    """
    return Image.open(mark_png).convert("L").resize((size, size), Image.LANCZOS)


def _text_w(draw: ImageDraw.ImageDraw, text: str, font, tracking: int = 0) -> int:
    if not text:
        return 0
    w = draw.textlength(text, font=font)
    return int(w + tracking * (len(text) - 1))


def _draw_tracked(draw, cx: int, y: int, text: str, font, fill, tracking: int = 0) -> None:
    """Centered text with letter spacing (Pillow has no native tracking)."""
    x = cx - _text_w(draw, text, font, tracking) / 2
    for ch in text:
        draw.text((x, y), ch, font=font, fill=fill)
        x += draw.textlength(ch, font=font) + tracking


def _wrap(draw, text: str, font, max_w: int) -> list[str]:
    """Greedy word wrap; hyphenated compounds («Наталка-Полтавка») may break after the hyphen."""
    import re

    tokens = re.findall(r"\S+?-(?=\S)|\S+", text)
    lines: list[str] = []
    cur = ""
    for w in tokens:
        sep = "" if cur.endswith("-") else " "
        trial = f"{cur}{sep}{w}".strip() if cur else w
        if draw.textlength(trial, font=font) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def _fit_title(draw, text: str, fonts_dir: Path, max_w: int, max_lines: int = 4):
    for size in range(168, 70, -6):
        font = _font(fonts_dir, "Literata-SemiBold.ttf", size)
        lines = _wrap(draw, text, font, max_w)
        # keep the title block clear of the subtitle and edition line below it
        if len(lines) <= max_lines and all(draw.textlength(l, font=font) <= max_w for l in lines) \
                and int(size * 1.18) * len(lines) <= 540:
            return font, lines, size
    font = _font(fonts_dir, "Literata-SemiBold.ttf", 72)
    return font, _wrap(draw, text, font, max_w), 72


def render_cover(
    *,
    title: str,
    author: str,
    subtitle: str | None,
    edition_line: str | None,
    fonts_dir: Path,
    logo_png: Path,
    brand_line: str = "Читанка · суспільне надбання",
) -> Image.Image:
    img = Image.new("L", (W, H), 255)
    d = ImageDraw.Draw(img)
    cx = W // 2

    # Thin double frame — reads as "book" on e-ink without any fill.
    d.rectangle([MARGIN - 40, MARGIN - 40, W - MARGIN + 40, H - MARGIN + 40], outline=INK, width=4)
    d.rectangle([MARGIN - 26, MARGIN - 26, W - MARGIN + 26, H - MARGIN + 26], outline=INK, width=1)

    # Author — tracked caps at the top.
    f_author = _font(fonts_dir, "Literata-Regular.ttf", 50)
    author_up = author.upper()
    tracking = 6
    while _text_w(d, author_up, f_author, tracking) > W - 2 * MARGIN - 40 and f_author.size > 30:
        f_author = _font(fonts_dir, "Literata-Regular.ttf", f_author.size - 2)
    _draw_tracked(d, cx, 250, author_up, f_author, INK, tracking)
    d.line([cx - 60, 350, cx + 60, 350], fill=INK, width=3)

    # Title — the hero, optically centred a bit above the middle.
    f_title, lines, size = _fit_title(d, title, fonts_dir, W - 2 * MARGIN - 80)
    lh = int(size * 1.18)
    block_h = lh * len(lines)
    y = 800 - block_h // 2
    for line in lines:
        tw = d.textlength(line, font=f_title)
        d.text((cx - tw / 2, y), line, font=f_title, fill=INK)
        y += lh

    # Subtitle / genre in italic grey.
    y += 56
    if subtitle:
        f_sub = _font(fonts_dir, "Literata-Italic.ttf", 50 if len(subtitle) < 30 else 44)
        for sl in _wrap(d, subtitle, f_sub, W - 2 * MARGIN - 160)[:3]:
            tw = d.textlength(sl, font=f_sub)
            d.text((cx - tw / 2, y), sl, font=f_sub, fill=GREY)
            y += 64

    # Edition line.
    if edition_line:
        f_ed = _font(fonts_dir, "Literata-Regular.ttf", 40)
        _draw_tracked(d, cx, 1300, edition_line, f_ed, GREY, 2)

    # Brand block: mark + «Читанка · суспільне надбання».
    logo = _render_logo(logo_png, 120)
    img.paste(logo, (cx - 60, 1440))
    f_brand = _font(fonts_dir, "Literata-Regular.ttf", 36)
    _draw_tracked(d, cx, 1582, brand_line, f_brand, INK, 3)
    return img


def save_jpeg(img: Image.Image, path: Path, size: tuple[int, int], quality: int = 88) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    img.resize(size, Image.LANCZOS).save(path, "JPEG", quality=quality, optimize=True, progressive=False)


def jpeg_bytes(img: Image.Image, size: tuple[int, int], quality: int = 88) -> bytes:
    buf = io.BytesIO()
    # Baseline (non-progressive) JPEG: the firmware's JPEG decoder is happiest with it.
    img.resize(size, Image.LANCZOS).save(buf, "JPEG", quality=quality, optimize=True, progressive=False)
    return buf.getvalue()


def contact_sheet(images: list[Image.Image], path: Path, cols: int = 3, cell=(400, 600), gap=40) -> None:
    rows = (len(images) + cols - 1) // cols
    sw = cols * cell[0] + (cols + 1) * gap
    sh = rows * cell[1] + (rows + 1) * gap
    sheet = Image.new("L", (sw, sh), 232)
    for i, im in enumerate(images):
        r, c = divmod(i, cols)
        x = gap + c * (cell[0] + gap)
        y = gap + r * (cell[1] + gap)
        sheet.paste(im.resize(cell, Image.LANCZOS), (x, y))
    path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(path, "PNG", optimize=True)
