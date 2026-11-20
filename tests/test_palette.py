import unittest

from helpers import BEIGE, BLUE, BROWN, RED, SAGE, stripes

import palette


def total(pal):
    return sum(c["pct"] for c in pal)


class Extract(unittest.TestCase):
    def test_finds_colors_and_shares(self):
        pal = palette.extract(stripes([(SAGE, 120), (BEIGE, 60), (BROWN, 20)]))
        self.assertEqual(len(pal), 3)
        self.assertAlmostEqual(total(pal), 100, delta=0.1)
        self.assertEqual([round(c["pct"] / 10) for c in pal], [6, 3, 1])  # ~60/30/10, largest first
        self.assertEqual(pal[0]["hex"], "#a0af8c")

    def test_single_color(self):
        pal = palette.extract(stripes([(RED, 200)]))
        self.assertEqual(len(pal), 1)
        self.assertEqual(pal[0]["pct"], 100)

    def test_jpeg_and_values_are_json_safe(self):
        import json
        pal = palette.extract(stripes([(SAGE, 100), (BLUE, 100)], fmt="JPEG"))
        json.dumps(pal)  # no numpy types
        self.assertEqual(len(pal), 2)


class Compare(unittest.TestCase):
    ref = palette.extract(stripes([(SAGE, 120), (BEIGE, 60), (BROWN, 20)]))

    def test_identical_is_zero(self):
        m = palette.compare(self.ref, self.ref)
        self.assertEqual((m["emd"], m["pct_diff"], m["cx_diff"]), (0, 0, 0))

    def test_similar_beats_different(self):
        similar = palette.extract(stripes([((150, 170, 135), 110), ((225, 210, 180), 70), ((70, 45, 30), 20)]))
        different = palette.extract(stripes([(RED, 100), (BLUE, 100)]))
        a, b = palette.compare(self.ref, similar), palette.compare(self.ref, different)
        self.assertLess(a["emd"], 6)
        self.assertLess(a["pct_diff"], 8)
        self.assertGreater(b["emd"], 50)

    def test_same_colors_wrong_amounts_is_caught(self):
        swapped = palette.extract(stripes([(SAGE, 40), (BEIGE, 140), (BROWN, 20)]))
        m = palette.compare(self.ref, swapped)
        self.assertGreater(m["pct_diff"], 30)

    def test_color_count(self):
        two = palette.extract(stripes([(SAGE, 130), (BEIGE, 70)]))
        self.assertEqual(palette.compare(self.ref, two)["cx_diff"], 1)


class Editing(unittest.TestCase):
    def test_hex_to_lab(self):
        self.assertEqual(palette.hex_to_lab("#ffffff")[0], 100)
        self.assertEqual(palette.hex_to_lab("000"), [0, 0, 0])
        with self.assertRaises(ValueError):
            palette.hex_to_lab("#zzzzzz")

    def test_lab_hex_round_trip(self):
        for h in ["#a0af8c", "#3b271d", "#ff0000", "#00ff00", "#0000ff", "#808080"]:
            self.assertEqual(palette._lab_to_hex(palette.hex_to_lab(h)), h)

    def test_normalize_rescales_and_keeps_user_colors(self):
        out = palette.normalize([{"hex": "A0AF8C", "pct": 30}, {"hex": "#a5b391", "pct": 30}, {"hex": "#000", "pct": 0}])
        self.assertEqual([c["hex"] for c in out], ["#a0af8c", "#a5b391"])  # near-duplicates kept, 0% dropped
        self.assertEqual([c["pct"] for c in out], [50, 50])

    def test_normalize_rejects_bad_hex(self):
        with self.assertRaises(ValueError):
            palette.normalize([{"hex": "nope", "pct": 10}])

    def test_combine(self):
        a = palette.extract(stripes([(SAGE, 200)]))
        b = palette.extract(stripes([(RED, 200)]))
        out = palette.combine([a, b])
        self.assertEqual(len(out), 2)
        self.assertEqual([c["pct"] for c in out], [50, 50])
        self.assertEqual(palette.combine([]), [])
        self.assertEqual(palette.combine([a, a]), a)

    def test_query_words(self):
        pal = palette.normalize([{"hex": "#a0af8c", "pct": 70}, {"hex": "#decdaf", "pct": 30}])
        self.assertEqual(palette.query_words([pal]), "sage green beige aesthetic")


if __name__ == "__main__":
    unittest.main()
