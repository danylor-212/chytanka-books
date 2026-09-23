"""Unit tests for chytanka_books.transforms (run: .venv/bin/python -m pytest -q  or  python -m unittest)."""

import unittest

from lxml import etree

from chytanka_books import transforms as tf
from chytanka_books.epub import sanitize_css_declarations

X = "http://www.w3.org/1999/xhtml"


def doc(body: str):
    return etree.fromstring(f'<html xmlns="{X}"><head><title>t</title></head><body>{body}</body></html>')


def ser(el) -> str:
    return etree.tostring(el, encoding="unicode")


class EntitySpans(unittest.TestCase):
    def test_unwrap_keeps_text_and_order(self):
        r = doc('<p>a<span typeof="mw:Entity"> </span>b<span typeof="mw:Entity"> </span><i>c</i></p>')
        self.assertEqual(tf.unwrap_entity_spans(r), 2)
        self.assertEqual("".join(r.find(f"{{{X}}}body").itertext()), "a b c")
        self.assertNotIn("mw:Entity", ser(r))

    def test_bare_spans(self):
        r = doc('<p><span>x</span><span class="k">y</span></p>')
        tf.unwrap_bare_spans(r)
        self.assertIn('<p>x<span class="k">y</span></p>', ser(r))


class TocTables(unittest.TestCase):
    ROW = ('<table class="ws-summary"><tr><td>{n}</td><td><div style="position:relative">'
           '<div style="display:inline">{label}</div></div>'
           '<div style="position:absolute; right:0">   . . . .</div></td>'
           '<td style="text-align:right">{page}</td></tr></table>\n')

    def test_linked_rows_become_list(self):
        body = "".join(self.ROW.format(n=f"{i}.", label=f'<a href="c{i}_x.xhtml" title="W/{i}">Розділ {i}</a>',
                                       page=str(10 * i)) for i in (1, 2, 3))
        r = doc(body)
        self.assertEqual(tf.collapse_toc_tables(r), 3)
        uls = r.findall(f".//{{{X}}}ul")
        self.assertEqual(len(uls), 1, "adjacent single-row tables are merged")
        items = uls[0].findall(f"{{{X}}}li")
        self.assertEqual([li.find(f"{{{X}}}a").get("href") for li in items], ["c1_x.xhtml", "c2_x.xhtml", "c3_x.xhtml"])
        self.assertEqual(items[0].find(f"{{{X}}}a").text, "Розділ 1")
        self.assertNotIn("10", ser(uls[0]), "page numbers dropped")
        self.assertIsNone(r.find(f".//{{{X}}}table"))

    def test_unlinked_row_keeps_value(self):
        r = doc(self.ROW.format(n="1.", label="<b>Вовчок М.:</b> т. 1, ціна", page="7.00"))
        tf.collapse_toc_tables(r)
        li = r.find(f".//{{{X}}}li")
        self.assertEqual(li.text, "1. Вовчок М.: т. 1, ціна — 7.00")

    def test_non_toc_table_untouched(self):
        r = doc("<table><tr><td>a</td><td>b</td></tr></table>")
        self.assertEqual(tf.collapse_toc_tables(r), 0)
        self.assertIsNotNone(r.find(f".//{{{X}}}table"))


class Contamination(unittest.TestCase):
    def test_title_based(self):
        self.assertTrue(tf.is_own_chapter("Твори (Франко, 1956–1962)/14/Мойсей/I", "c1.xhtml",
                                          "Твори (Франко, 1956–1962)/14/Мойсей", "c0.xhtml"))
        self.assertFalse(tf.is_own_chapter("Захар Беркут", "c5.xhtml", "Мойсей (1930)", "c0.xhtml"))
        self.assertFalse(tf.is_own_chapter("Мойсей (1930) інше", "c5.xhtml", "Мойсей (1930)", "c0.xhtml"))

    def test_filename_fallback(self):
        self.assertTrue(tf.is_own_chapter(None, "c3_Misto_1_III.xhtml", "Місто", "c0_Misto.xhtml"))
        self.assertFalse(tf.is_own_chapter(None, "c9_Zahar_Berkut.xhtml", "Місто", "c0_Misto.xhtml"))

    def test_title_map(self):
        r = doc('<p><a rel="mw:WikiLink" href="c1_a.xhtml" title="Root/I">I</a>'
                '<a href="https://x/y.xhtml" title="ext">e</a></p>')
        self.assertEqual(tf.chapter_title_map({"c0": r}), {"c1_a.xhtml": "Root/I"})


class Split(unittest.TestCase):
    def test_no_split_when_small(self):
        r = doc("<p>x</p>")
        self.assertEqual(len(tf.split_document(r, limit=10_000)), 1)

    def test_split_prefers_headings_and_preserves_text(self):
        para = "<p>" + "слово " * 400 + "</p>"
        body = '<section><div class="prp-pages-output">' + "".join(
            f"<h3>Розділ {i}</h3>" + para * 6 for i in range(1, 6)) + "</div></section>"
        r = doc(body)
        before = "".join(r.find(f"{{{X}}}body").itertext())
        parts = tf.split_document(r, limit=40_000, min_part=10_000)
        self.assertGreater(len(parts), 1)
        after = "".join("".join(p.find(f"{{{X}}}body").itertext()) for p in parts)
        self.assertEqual(before, after, "no text lost or duplicated")
        for p in parts:
            self.assertLessEqual(len(etree.tostring(p, encoding="utf-8")), 40_000 * 1.2)
            # wrapper chain rebuilt in every part
            self.assertIsNotNone(p.find(f".//{{{X}}}section/{{{X}}}div"))
        # every part after the first starts with a heading
        for p in parts[1:]:
            first = p.find(f".//{{{X}}}div")[0]
            self.assertEqual(first.tag, f"{{{X}}}h3")


class ContentModel(unittest.TestCase):
    def test_fixes(self):
        r = doc('<dl><dd>відступ</dd></dl><p><span class="reference-text">note<div style="text-align:right">Ред.</div></span></p>'
                '<p>a<math xmlns="http://www.w3.org/1998/Math/MathML"><mo>}</mo></math>b</p>')
        self.assertEqual(tf.fix_content_model(r), 3)
        out = ser(r)
        self.assertNotIn("<dl", out)
        self.assertIn('class="dd"', out)
        self.assertIn('<span style="display:block; text-align:right">Ред.</span>', out)
        self.assertIn('a<span class="math">}</span>b', out)

    def test_table_in_inline_and_obsolete_attrs(self):
        r = doc('<p><span class="reference-text">x<table><tr><td valign="top" width="3">y</td></tr></table></span></p>')
        tf.fix_content_model(r)
        out = ser(r)
        self.assertIn('<div><div class="reference-text">x<table>', out)
        self.assertIn('<td style="vertical-align:top">y</td>', out)


class FeedTitle(unittest.TestCase):
    def test_fit_bytes(self):
        from chytanka_books.site import fit_bytes
        t = "Подорож ученого доктора Леонардо і його майбутньої коханки прекрасної Альцести у Слобожанську Швайцарію"
        out = fit_bytes(t)
        self.assertLessEqual(len(out.encode("utf-8")), 160)
        self.assertTrue(out.endswith("…"))
        self.assertEqual(fit_bytes("Місто"), "Місто")


class Css(unittest.TestCase):
    def test_sanitize(self):
        clean, dropped = sanitize_css_declarations("float:right; width:; color:red !important; position:absolute")
        self.assertEqual(clean, "float:right; color:red !important")
        self.assertEqual(dropped, ["width:", "position:absolute"])


if __name__ == "__main__":
    unittest.main()
