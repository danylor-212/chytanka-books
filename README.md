# Читанка — Книжки

Статичний OPDS 1.2 (Atom) каталог безкоштовних EPUB української класики в суспільному надбанні.
Книжки генеруються з [uk.wikisource.org](https://uk.wikisource.org) через
[WS Export](https://ws-export.wmcloud.org), чистяться і публікуються на GitHub Pages.
Каталог призначений для вшивання в прошивку читалки «Читанка» (форк CrossInk для Xteink X4/X3) як сервера за замовчуванням.

- Сайт: `https://danylor-212.github.io/chytanka-books/`
- OPDS: `https://danylor-212.github.io/chytanka-books/opds/index.xml`

## Що всередині

| Файл | Призначення |
|---|---|
| `books.yaml` | кураторський список: сторінка Вікіджерел, видання, рік смерті автора, прапорці |
| `build.py` | збирання: завантаження → чистка → обкладинка → перевірка → `public/` |
| `chytanka_books/epub.py` | постобробка EPUB (шрифти, розмітка, виноски, ілюстрації, OPF/nav/NCX) |
| `chytanka_books/cover.py` | типографічні обкладинки (Literata, ґрейскейл, 2:3) |
| `chytanka_books/validate.py` | структурна перевірка EPUB на Python (замість epubcheck) |
| `chytanka_books/site.py` | OPDS-фід і лендинг |
| `tools/opds_harness/` | прогін фіду через **парсер самої прошивки** (`lib/OpdsParser`) на хості |
| `.github/workflows/build.yml` | CI: збирання на push / щотижня / вручну і деплой на Pages |

Результат збирання (`public/`, у git не комітиться — його робить CI):

```
public/
  index.html               лендинг українською
  LICENSE-BOOKS.txt        атрибуції по кожній книжці
  opds/index.xml           acquisition-фід (≤50 записів на сторінку, далі rel="next" → all-2.xml …)
  books/<slug>.epub
  covers/<slug>.jpg        мініатюра 200×300
  covers/<slug>-600.jpg    обкладинка 600×900
  assets/icon.png, mark.png
```

## Збирання локально

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python build.py                      # кеш WS Export у .cache/
.venv/bin/python build.py --refresh            # перезавантажити з Вікіджерел
.venv/bin/python build.py --only pidmohylnyi-misto
.venv/bin/python build.py --preview covers.png # контактний аркуш обкладинок
tools/opds_harness/run.sh                      # перевірити фід парсером прошивки
```

`build-report.json` — що саме прибрано з кожної книжки, розміри до/після, `release_ready`.

## Що робить конвеєр

1. **WS Export** → EPUB3 (`lang=uk&format=epub-3`), ревізія сторінки (`oldid`) — з MediaWiki API.
2. **Шрифти**: видаляє 4 вбудовані FreeSerif (~4 МБ на книжку; параметр `fonts=` для uk не працює), `@font-face`, iBooks-прапорець.
3. **Розмітка MediaWiki**: атрибути Parsoid, номери сторінок сканів, ліцензійні плашки `{{PD-…}}`, зовнішні посилання на вікі (пристрій їх не відкриє), дублікати виносок `mw-ref-follow`.
4. **Видання «PD лише в США»** (`us_only_edition: true`, Нью-Йорк 1955/1960): прибирає **всі ілюстрації** і **всі виноски**, крім явно авторських (`keep_note_markers`, напр. «Прим. авт.», «Ів Фр.»); решту перенумеровує.
5. **Зображення**: незадіяні видаляє, решті дає справжні розширення (`images/img01.jpg`).
6. **Обкладинка**: власна типографічна (`images/cover.jpg`, `properties="cover-image"` + `<meta name="cover">` для EPUB2 + `cover.xhtml` першою в spine); титулка WS Export з логотипом Вікіджерел видаляється.
7. **Сторінка «Про це видання»**: видання-джерело, статус PD, посилання на Вікіджерела з `oldid`, CC BY-SA 4.0, перелік змін. Сторінка подяк WS Export (`about.xhtml`, список вичитувачів) лишається.
8. **Метадані**: `dc:language=uk`, чисті `dc:title`/`dc:creator` з `books.yaml`, `dc:identifier=urn:chytanka:book:<slug>`, `dcterms:modified` = max(`site.pipeline_date`, час ревізії на Вікіджерелах) — збирання відтворювані байт у байт. Nav і NCX генеруються наново.
9. **Перевірка**: zip (`mimetype` перший і STORED), well-formed XML, маніфест ↔ файли, spine, внутрішні посилання, обкладинка, відсутність шрифтів. Книжка з помилками **не публікується**, а збирання падає.

Повний `epubcheck` потребує Java; у CI він запускається інформаційно (`continue-on-error`).

## Обмеження прошивки, під які зроблено фід

- Тільки Atom; книжка = `link rel="…opds-spec.org/acquisition…" type="application/epub+zip"` з `.epub` в href.
- `MAX_OPDS_FEED_ENTRIES = 50` у chytanka-main → не більше 50 записів на сторінку, далі `rel="next"`.
- `UrlUtils::buildUrl` не розв'язує відносні URL за RFC 3986 → **у фіді лише абсолютні URL**. Тому `site.base_url` у `books.yaml` треба змінити, якщо каталог переїде (напр. на власний домен).
- Пошук у прошивці — лише шаблон `{searchTerms}`; статичний сайт шукати не вміє, тож `rel="search"` немає.
- Парсер шукає імена елементів через `strstr(name, ":id")` тощо, тому у фіді немає `dc:identifier`.

## Як додати книжку

1. Знайдіть твір на uk.wikisource, бажано з вичитаним сканом і правописом, близьким до сучасного.
2. Перевірте статус: автор помер понад 70 років тому; видання-джерело — PD в Україні й США. Для нью-йоркських видань 1950–60-х ставте `us_only_edition: true`.
3. Додайте запис у `books.yaml`, зберіть локально, прочитайте `build-report.json` (особливо `notes_removed` / `notes_kept`) і відкрийте EPUB.
4. Якщо редакторські примітки вплетені в сам текст і їх не можна чисто прибрати — не публікуйте: поставте `hold: "<причина>"`.

## Ліцензії

Скрипти — MIT. Тексти — суспільне надбання; шар Вікіджерел — CC BY-SA; EPUB-видання «Читанки» — CC BY-SA 4.0. Шрифт Literata — SIL OFL 1.1. Деталі — у [LICENSE](LICENSE).

---

## English

**Chytanka Books** is a static OPDS 1.2 (Atom) catalogue of public-domain Ukrainian classics, built from
uk.wikisource via WS Export and served from GitHub Pages. It is meant to be the default catalogue of the Chytanka
e-reader firmware (a CrossInk fork for Xteink X4/X3).

`build.py` downloads each title listed in `books.yaml`. It strips the ~4 MB of embedded FreeSerif fonts and cleans the
MediaWiki markup. For editions that are public domain only in the US, it removes illustrations and
editorial footnotes. It then gives images real extensions, adds a typographic grayscale cover (`cover-image` + EPUB2
`meta cover`) and a Ukrainian credits page, fixes the metadata, and validates the zip/OPF/NCX/nav structure in Python.
Finally it writes `public/` (EPUBs, covers, `opds/index.xml`, landing page). The feed uses absolute URLs only and
at most 50 entries per page. Both limits come from the firmware's `OpdsParser` and `UrlUtils::buildUrl`.
`tools/opds_harness/run.sh` compiles the firmware's own parser on the host and runs it against the feed.

Licences: scripts MIT; texts public domain; Wikisource transcription CC BY-SA; the Chytanka EPUB editions
CC BY-SA 4.0; Literata font OFL 1.1.
