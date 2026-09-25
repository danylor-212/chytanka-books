"""Tests for chytanka_books.catalogue (feed tree + grouping)."""

import unittest
from datetime import datetime

from lxml import etree

from chytanka_books import catalogue as cat

SITE = {"base_url": "https://x.test/cb/", "title": "Читанка — Книжки", "subtitle": "s"}
D = datetime(2026, 9, 25)


def uk(slug, author, genre="Проза", died=1916):
    return {"slug": slug, "title": slug, "author": author, "author_died": died, "genre": genre, "summary": "s",
            "edition": {"city": "Київ", "year": 1920}, "_lang": "uk", "_size": 1, "_updated": D, "_source_url": "https://w/"}


def en(slug, author_sort, category="fiction"):
    return {"slug": slug, "title": slug, "author": author_sort, "author_sort": author_sort, "author_died": 1900,
            "category": category, "subject": "Novel", "summary": "s", "published": 1850, "se": "a_b",
            "_lang": "en", "_size": 1, "_updated": D, "_source_url": "https://se/"}


class Helpers(unittest.TestCase):
    def test_slugify_and_plural(self):
        self.assertEqual(cat.slugify("Франко Іван"), "franko-ivan")
        self.assertEqual(cat.slugify("Підмогильний Валер’ян"), "pidmohylnyi-valerian")
        self.assertEqual(cat.slugify("Brontë, Charlotte"), "bronte-charlotte")
        self.assertEqual([cat.uk_books_word(n) for n in (1, 2, 5, 11, 21, 22, 25)],
                         ["книжка", "книжки", "книжок", "книжок", "книжка", "книжки", "книжок"])

    def test_uk_author_order_and_pseudonyms(self):
        g = cat.author_groups([uk("a", "Іван Франко"), uk("b", "Леся Українка"), uk("c", "Ольга Кобилянська"),
                               uk("d", "Марко Вовчок"), uk("e", "Михайло Коцюбинський")], "uk")
        self.assertEqual([x["display"] for x in g],
                         ["Кобилянська Ольга", "Коцюбинський Михайло", "Леся Українка", "Марко Вовчок", "Франко Іван"])


class Tree(unittest.TestCase):
    def test_tree_and_paging(self):
        uks = [uk(f"u{i}", f"Автор{i:02d} Прізвище{i:02d}") for i in range(60)]
        ens = [en("e1", "Austen, Jane"), en("e2", "Brontë, Emily"), en("e3", "Darwin, Charles", "non-fiction")]
        files = cat.build_catalogue(SITE, uks, ens)
        for path in ("opds/index.xml", "opds/uk/index.xml", "opds/uk/index-2.xml", "opds/en/index.xml",
                     "opds/en/fiction.xml", "opds/en/nonfiction.xml", "opds/en/austen-jane.xml", "opds/all.xml",
                     "opds/all-2.xml", "opds/uk/genre-proza.xml", "opds/uk/genre-proza-2.xml"):
            self.assertIn(path, files)
            etree.fromstring(files[path].encode())  # well-formed
        root = files["opds/index.xml"]
        self.assertIn("https://x.test/cb/opds/uk/index.xml", root)
        self.assertIn("Українська (60)", root)
        self.assertIn("English (3)", root)
        # every href is absolute
        for path, xml in files.items():
            for h in etree.fromstring(xml.encode()).iter("{http://www.w3.org/2005/Atom}link"):
                self.assertTrue(h.get("href").startswith("https://"), (path, h.get("href")))
        # each navigation entry has exactly one atom link (firmware keeps the LAST one)
        for path, xml in files.items():
            for e in etree.fromstring(xml.encode()).iter("{http://www.w3.org/2005/Atom}entry"):
                atom = [l for l in e.iter("{http://www.w3.org/2005/Atom}link") if "atom+xml" in (l.get("type") or "")]
                self.assertLessEqual(len(atom), 1, path)
        # 1 + 60 authors + 1 genre = 61 rows -> 2 pages of <= 50
        p1 = etree.fromstring(files["opds/uk/index.xml"].encode())
        self.assertEqual(len(p1.findall("{http://www.w3.org/2005/Atom}entry")), 50)
        self.assertTrue(any(l.get("rel") == "next" for l in p1.findall("{http://www.w3.org/2005/Atom}link")))


if __name__ == "__main__":
    unittest.main()
