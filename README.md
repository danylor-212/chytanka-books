# Читанка — Книжки

Статичний OPDS 1.2 (Atom) каталог безкоштовних EPUB класики в суспільному надбанні — української та англійської.
Книжки генеруються з [uk.wikisource.org](https://uk.wikisource.org) через
[WS Export](https://ws-export.wmcloud.org), чистяться і публікуються на GitHub Pages.
Каталог призначений для вшивання в прошивку читалки «Читанка» (форк CrossInk для Xteink X4/X3) як сервера за замовчуванням.

- Сайт: `https://danylor-212.github.io/chytanka-books/`
- OPDS: `https://danylor-212.github.io/chytanka-books/opds/index.xml`

## Що всередині

| Файл | Призначення |
|---|---|
| `books-en.yaml` | англійські книжки: репозиторій Standard Ebooks або номер Project Gutenberg, автор/перекладач і роки смерті, fiction / non-fiction |
| `chytanka_books/english.py` | збирання англійських книжок (`se build` із GitHub SE / PG) і мінімальні зміни |
| `chytanka_books/catalogue.py` | дерево OPDS (мова → автор → книжки) і лендинг |
| `books.yaml` | кураторський список: сторінка Вікіджерел, видання, рік смерті автора, прапорці |
| `build.py` | збирання: завантаження → чистка → обкладинка → перевірка → `public/` |
| `chytanka_books/epub.py` | постобробка EPUB (шрифти, розмітка, виноски, ілюстрації, OPF/nav/NCX) |
| `chytanka_books/transforms.py` | структурні перетворення: таблиці змісту → списки, розгортання `mw:Entity`, ділення завеликих розділів, захист від чужих сторінок, виправлення моделі вмісту XHTML5 |
| `tests/` | модульні тести (`python -m unittest discover -s tests`) |
| `chytanka_books/cover.py` | типографічні обкладинки (Literata, ґрейскейл, 2:3) |
| `chytanka_books/validate.py` | структурна перевірка EPUB на Python (швидка, без Java; доповнює epubcheck) |
| `chytanka_books/site.py` | OPDS-фід і лендинг |
| `tools/opds_harness/` | прогін фіду через **парсер самої прошивки** (`lib/OpdsParser`) на хості |
| `.github/workflows/build.yml` | CI: збирання на push / щотижня / вручну і деплой на Pages |

Результат збирання (`public/`, у git не комітиться — його робить CI):

```
public/
  index.html               лендинг українською
  LICENSE-BOOKS.txt        атрибуції по кожній книжці
  opds/index.xml           навігаційний корінь: Українська · English · Усі книжки
  opds/uk/index.xml        жанри + автори (українська абетка), ≤50 рядків на сторінку, далі rel="next"
  opds/uk/<автор>.xml      книжки автора;  opds/uk/genre-<жанр>.xml — книжки жанру
  opds/en/index.xml        Fiction, Non-fiction + автори (за прізвищем);  opds/en/<author>.xml, fiction.xml, nonfiction.xml
  opds/all.xml, all-2.xml… плаский список усіх книжок (старий формат, для наявних посилань)
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
4a. **`strip_editorial: true`** — та сама чистка (ілюстрації + неавторські виноски) для видань поза NY (напр. радянське шкільне 1937 р.); сторінка подяк не пише про «PD лише в США». **`strip_images: true`** — лише ілюстрації/фото (розмір або невідоме авторство). **`exclude_pages`** — підрядки назв підсторінок із чужими текстами (передмови редакторів, що померли після 1953 р., тощо).
4b. **Захист від забруднення**: WS Export тягне всі сторінки, на які посилається корінь (оголошення видавництв, інші твори). Лишаються тільки корінь і його підсторінки; вилучене пишеться в журнал і в `build-report.json`.
4c. **Таблиці змісту** `ws-summary` (крапкові заповнювачі, до 1,5 МБ розмітки) → простий список посилань; обгортки `<span typeof="mw:Entity">` навколо кожного нерозривного пробілу розгортаються в текст.
4d. **Ділення розділів**: XHTML понад 280 КБ ділиться на частини, переважно перед заголовками; посилання на виноски між частинами переписуються. Жорстка межа перевірки — 462 КБ (найбільший розділ, що точно працює на пристрої).
5. **Зображення**: незадіяні видаляє, решті дає справжні розширення (`images/img01.jpg`).
6. **Обкладинка**: власна типографічна (`images/cover.jpg`, `properties="cover-image"` + `<meta name="cover">` для EPUB2 + `cover.xhtml` першою в spine); титулка WS Export з логотипом Вікіджерел видаляється.
7. **Сторінка «Про це видання»**: видання-джерело, статус PD, посилання на Вікіджерела з `oldid`, CC BY-SA 4.0, перелік змін. Сторінка подяк WS Export (`about.xhtml`, список вичитувачів) лишається.
8. **Метадані**: `dc:language=uk`, чисті `dc:title`/`dc:creator` з `books.yaml`, `dc:identifier=urn:chytanka:book:<slug>`, `dcterms:modified` = max(`site.pipeline_date`, час ревізії на Вікіджерелах) — збирання відтворювані байт у байт. Nav і NCX генеруються наново.
9. **Перевірка**: zip (`mimetype` перший і STORED), well-formed XML, маніфест ↔ файли, spine, внутрішні посилання, обкладинка, відсутність шрифтів. Книжка з помилками **не публікується**, а збирання падає.

Повний `epubcheck` (W3C, потребує Java) — обов'язковий крок CI: `tools/epubcheck_all.py` перевіряє всі книжки, друкує підсумок і падає на будь-якій помилці чи попередженні. Локально: `python tools/epubcheck_all.py --jar <epubcheck.jar> public/books/*.epub`.

Некоректний inline-CSS із шаблонів Вікіджерел (`width:;`, незбалансовані лапки тощо) і вікі-хаки верстки (`position`, `z-index`, `font-family`) вичищаються генерично в `sanitize_inline_styles`.

## Англійські книжки (English books)

- **Відбір** (`books-en.yaml`): 41 художня + 88 нехудожніх книжок (філософія, політика, економіка, історія,
  мемуари, подорожі, наука, есеї, саморозвиток). Автори **і перекладачі** померли до 1954 р.; тексти — PD у США.
  Виключено, наприклад, «Мистецтво війни» (перекладач Лайонел Джайлз †1958), «Суспільний договір» (Коул †1959),
  «Трактат» Вітгенштайна (Оґден †1957), «Нікомахову етику» (рік смерті Ф. Г. Пітерса не встановлено).
- **Джерело — Standard Ebooks** (CC0). У `robots.txt` сайту standardebooks.org завантаження `/ebooks/*/downloads/*`
  заборонені для AI-агентів, тому конвеєр **не звертається до сайту**: клонує публічний репозиторій
  `github.com/standardebooks/<книжка>` і збирає EPUB інструментом SE `se build` (пакет `standardebooks`) — це та сама
  «compatible» збірка, яку роздає сайт. Кеш — за комітом (`--refresh-en` перевіряє `git ls-remote`).
- **Запасне джерело — Project Gutenberg** (лише де в SE немає ключового твору: Монтень, Спіноза, «Політика»
  Аристотеля, «Походження людини», Фарадей, Фройд, Смайлс, Аллен, Беннетт). Повну ліцензію PG залишено у файлі.
- **Обкладинки — «Читанки»** для всіх мов: картини на обкладинках SE лише «вважаються PD у США» (роки смерті художників
  не перевірені), важать ~0,6 МБ кожна й погано виглядають на e-ink; типографічна обкладинка однакова для каталогу.
- **Мінімальні зміни**: обкладинка, сторінка «About this edition» («похідне від, не офіційний випуск Standard Ebooks /
  Project Gutenberg»), `dc:identifier = urn:chytanka:book:<slug>` (оригінальний — у `dc:source`), вбудовані шрифти
  (якщо є). Текст, розмітка, CSS, сторінки Imprint / Colophon / Uncopyright SE і шапка та ліцензія PG — без змін.
  `dc:language` лишається як у джерелі (`en`, `en-US`, `en-GB`); прошивка бере первинний підтег (`en`).

## Обмеження прошивки, під які зроблено фід

- Тільки Atom; книжка = `link rel="…opds-spec.org/acquisition…" type="application/epub+zip"` з `.epub` в href.
- `MAX_OPDS_FEED_ENTRIES = 50` у chytanka-main → не більше 50 записів на сторінку, далі `rel="next"`.
- `UrlUtils::buildUrl` не розв'язує відносні URL за RFC 3986 → **у фіді лише абсолютні URL**. Тому `site.base_url` у `books.yaml` треба змінити, якщо каталог переїде (напр. на власний домен).
- Навігація: рядок фіду — «NAV», якщо в ньому є посилання з типом `application/atom+xml` (rel не важливий; якщо таких
  кілька, береться **останнє**), і «BOOK», якщо є acquisition-посилання `application/epub+zip`. Глибина необмежена
  (стек історії), «Назад» повертає на попередній фід. Навігаційні рядки показують лише назву, тож кількість книжок — у назві.
  Перевірка: `tools/opds_harness/run.sh --crawl --walk --expect-books N` проходить усе дерево парсером і `UrlUtils` прошивки.
- Пошук у прошивці — лише шаблон `{searchTerms}`; статичний сайт шукати не вміє, тож `rel="search"` немає.
- `MAX_TITLE_CHARS = 160` рахує **байти** й може розрізати UTF-8 посеред літери → назви у фіді скорочуються до 160 байтів по межі слова з «…» (поле `feed_title` для ручного варіанта).
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

**Chytanka Books** is a static OPDS 1.2 (Atom) catalogue of public-domain classics — Ukrainian (from uk.wikisource
via WS Export) and English (41 fiction + 88 non-fiction, from Standard Ebooks' CC0 GitHub sources built with their own
`se build`, with Project Gutenberg as a fallback) — served from GitHub Pages. The feed is a tree:
`opds/index.xml` → language → author (or genre / fiction / non-fiction) → books; `opds/all.xml` keeps the flat list. It is meant to be the default catalogue of the Chytanka
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
