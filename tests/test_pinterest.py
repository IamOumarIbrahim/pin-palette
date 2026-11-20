import json
import unittest

import helpers  # noqa: F401  (sets sys.path)
from pinterest import Pinterest


def pin(i, kind="pin"):
    return {"id": str(i), "type": kind, "images": {
        "236x": {"url": "https://i.pinimg.com/236x/%d.jpg" % i},
        "orig": {"url": "https://i.pinimg.com/originals/%d.jpg" % i}}}


class FakePinterest(Pinterest):
    """Serves canned search pages instead of calling Pinterest."""

    def __init__(self, pages, **kw):
        super().__init__(**kw)
        self.pages, self.calls = pages, []
        self._warm = True

    def _get(self, url, headers=None, timeout=20):
        self.calls.append(url)
        page = self.pages[len(self.calls) - 1]
        return json.dumps({"resource_response": page}).encode()


class Search(unittest.TestCase):
    def test_paginates_with_bookmarks_and_skips_non_pins(self):
        pages = [
            {"data": {"results": [pin(1), pin(2, "story"), {"id": "3", "type": "pin"}]}, "bookmark": "B1"},
            {"data": {"results": [pin(4), pin(5)]}, "bookmark": "-end-"},
        ]
        api = FakePinterest(pages)
        got = list(api.search("sage green", max_pins=50))
        self.assertEqual([p["id"] for p in got], ["1", "4", "5"])
        self.assertEqual(got[0]["thumb"], "https://i.pinimg.com/236x/1.jpg")
        self.assertEqual(got[0]["orig"], "https://i.pinimg.com/originals/1.jpg")
        self.assertEqual(got[0]["link"], "https://www.pinterest.com/pin/1/")
        self.assertIn("B1", api.calls[1])            # second request carries the bookmark
        self.assertEqual(len(api.calls), 2)          # stopped at -end-

    def test_respects_max_pins(self):
        pages = [{"data": {"results": [pin(i) for i in range(25)]}, "bookmark": "B"}] * 5
        api = FakePinterest(pages)
        self.assertEqual(len(list(api.search("x", max_pins=30))), 30)
        self.assertEqual(len(api.calls), 2)

    def test_empty_results_stop(self):
        self.assertEqual(list(FakePinterest([{"data": {"results": []}}]).search("x")), [])

    def test_cookie_prefix_is_stripped(self):
        self.assertEqual(Pinterest("_pinterest_sess=abc").session_cookie, "abc")
        self.assertEqual(Pinterest("  abc ").session_cookie, "abc")


if __name__ == "__main__":
    unittest.main()
