import json
import os
import shutil
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from helpers import BEIGE, BROWN, RED, SAGE, stripes

import app
import palette


class Server(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        app.CONFIG = Path(cls.tmp) / "config.json"  # never touch the user's real config
        app.config["outdir"] = os.path.join(cls.tmp, "out")
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        cls.base = "http://127.0.0.1:%d" % cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        app.state.update(refs=[], target=[], edited=False, job=None)

    def call(self, path, body=None, headers=None):
        data = body if isinstance(body, bytes) or body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=data, headers=headers or {},
                                     method="GET" if data is None else "POST")
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            with e:
                return e.code, json.loads(e.read() or b"{}")

    def upload(self, img, name="a.png"):
        return self.call("/api/refs/upload", img, {"X-Filename": name})

    def test_page_is_served(self):
        with urllib.request.urlopen(self.base + "/") as r:
            self.assertIn(b"Pin Palette", r.read())

    def test_upload_builds_target_and_query(self):
        self.upload(stripes([(SAGE, 140), (BEIGE, 60)]), "my%20room.png")
        _, s = self.call("/api/state")
        self.assertEqual(s["refs"][0]["name"], "my room.png")
        self.assertEqual(len(s["target"]), 2)
        self.assertFalse(s["edited"])
        self.assertTrue(s["query"].startswith("sage green"))

    def test_folder_path(self):
        d = Path(self.tmp) / "imgs"
        d.mkdir(exist_ok=True)
        (d / "1.png").write_bytes(stripes([(SAGE, 200)]))
        (d / "2.png").write_bytes(stripes([(RED, 200)]))
        (d / "notes.txt").write_text("x")
        code, r = self.call("/api/refs/path", {"path": str(d)})
        self.assertEqual((code, r["added"]), (200, 2))
        self.assertEqual(self.call("/api/refs/path", {"path": str(d / "missing")})[0], 400)

    def test_edit_remove_and_reset_palette(self):
        self.upload(stripes([(SAGE, 120), (BEIGE, 60), (BROWN, 20)]))
        _, s = self.call("/api/state")
        edited = [{"hex": c["hex"], "pct": c["pct"]} for c in s["target"][:2]]  # remove the third color
        edited[0]["pct"], edited[1]["pct"] = 80, 40                         # and change the amounts
        code, r = self.call("/api/target", {"colors": edited})
        self.assertEqual(code, 200)
        self.assertEqual([c["pct"] for c in r["target"]], [66.67, 33.33])
        _, s = self.call("/api/state")
        self.assertTrue(s["edited"])
        self.assertEqual(len(s["target"]), 2)

        self.call("/api/target/reset", b"")
        _, s = self.call("/api/state")
        self.assertFalse(s["edited"])
        self.assertEqual(len(s["target"]), 3)

    def test_bad_color_is_rejected(self):
        code, r = self.call("/api/target", {"colors": [{"hex": "#12", "pct": 50}]})
        self.assertEqual(code, 400)
        self.assertIn("Bad color", r["error"])

    def test_editing_palette_rescores_results(self):
        cand = palette.extract(stripes([(SAGE, 200)]))
        app.state["job"] = {"running": False, "cancel": False, "found": 1, "done": 1, "error": None,
                            "results": [{"id": "1", "thumb": "", "orig": "", "link": "", "palette": cand,
                                         "emd": 99, "pct_diff": 99, "cx_diff": 9, "score": 999}]}
        self.call("/api/target", {"colors": [{"hex": "#a0af8c", "pct": 100}]})
        _, s = self.call("/api/state")
        r = s["job"]["results"][0]
        self.assertLess(r["emd"], 1)
        self.assertEqual((r["pct_diff"], r["cx_diff"]), (0, 0))

    def test_search_needs_a_palette(self):
        self.assertEqual(self.call("/api/search", {"query": "x"})[0], 400)

    def test_remove_and_clear_refs(self):
        self.upload(stripes([(SAGE, 200)]))
        self.upload(stripes([(RED, 200)]))
        self.call("/api/refs/remove", {"index": 0})
        _, s = self.call("/api/state")
        self.assertEqual(len(s["refs"]), 1)
        self.assertEqual(len(s["target"]), 1)
        self.call("/api/refs/clear", b"")
        _, s = self.call("/api/state")
        self.assertEqual((s["refs"], s["target"]), ([], []))

    def test_settings_saved(self):
        self.call("/api/settings", {"max": 5000, "cookie": "_pinterest_sess=xyz"})
        saved = json.loads(app.CONFIG.read_text())
        self.assertEqual(saved["max"], 1000)  # clamped
        self.assertEqual(saved["cookie"], "xyz")
        _, s = self.call("/api/state")
        self.assertTrue(s["settings"]["logged_in"])
        self.call("/api/settings", {"cookie": ""})

    def test_download(self):
        orig = app.Pinterest.fetch
        app.Pinterest.fetch = lambda self, url: b"IMG:" + url.encode()
        try:
            r = app.download([{"id": "123", "orig": "https://i.pinimg.com/originals/a/b.png"},
                              {"id": "45x6", "orig": "https://i.pinimg.com/originals/c"}],
                             os.path.join(self.tmp, "dl"))
        finally:
            app.Pinterest.fetch = orig
        self.assertEqual(r["saved"], 2)
        self.assertEqual(sorted(os.listdir(r["dir"])), ["pin_123.png", "pin_456.jpg"])


if __name__ == "__main__":
    unittest.main()
