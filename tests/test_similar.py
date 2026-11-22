"""Similar image mode: parsing, descriptors, ranking, saving and the server API.
No network and no model download: CLIP is not used here."""
import io
import json
import os
import random
import shutil
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from PIL import Image, ImageDraw

from helpers import BLUE, RED, stripes

import app
import similar

WARM = (230, 200, 140)


def photo(color, seed=0, size=(200, 300), fmt="JPEG"):
    """Image in shades of `color` with a seeded block pattern: same palette, different structure."""
    rnd = random.Random(seed)
    im = Image.new("RGB", size, color)
    d = ImageDraw.Draw(im)
    for _ in range(12):
        x, y = rnd.randrange(size[0]), rnd.randrange(size[1])
        k = rnd.uniform(0.55, 1.0)
        d.rectangle([x, y, x + rnd.randrange(20, 90), y + rnd.randrange(20, 120)],
                    fill=tuple(int(c * k) for c in color))
    buf = io.BytesIO()
    im.save(buf, fmt)
    return buf.getvalue()


def cands(images):
    return [{"id": i, "url": "u%d" % i, "data": b, "ext": ".jpg", "d": similar.describe(b)}
            for i, b in enumerate(images)]


W = similar.clean_rank({})


class Parsing(unittest.TestCase):
    def test_murls_from_bing_page(self):
        page = ('<a class="iusc" m="{&quot;murl&quot;:&quot;https://i.pinimg.com/736x/a.jpg&quot;,'
                '&quot;purl&quot;:&quot;https://x&quot;}"></a>'
                '<a m="{&quot;murl&quot;:&quot;https:\\/\\/b.example\\/b.png&quot;}"></a>')
        self.assertEqual(similar.parse_murls(page), ["https://i.pinimg.com/736x/a.jpg", "https://b.example/b.png"])

    def test_sniff(self):
        self.assertEqual(similar.sniff(photo(RED)), ".jpg")
        self.assertEqual(similar.sniff(stripes([(RED, 200)])), ".png")
        self.assertEqual(similar.sniff(b"RIFF\0\0\0\0WEBPVP8 "), ".webp")
        self.assertIsNone(similar.sniff(b"<html>hello</html>"))

    def test_clean_search_clamps_and_types(self):
        o = similar.clean_search({"count": "9999", "threads": 0, "photo": 0, "bogus": 1})
        self.assertEqual(o["count"], 500)
        self.assertEqual(o["threads"], 1)
        self.assertIs(o["photo"], False)
        self.assertNotIn("bogus", o)

    def test_qft_filters(self):
        b = similar.Bing(similar.clean_search({"people": "portrait", "aspect": "tall", "color": "bw"}))
        q = b._qft()
        for f in ("photo-photo", "face-portrait", "aspect-tall", "color2-bw"):
            self.assertIn("filterui:" + f, q)


class Ranking(unittest.TestCase):
    def test_describe(self):
        d = similar.describe(photo(WARM))
        self.assertEqual((d.w, d.h), (200, 300))
        self.assertAlmostEqual(float(d.hist.sum()), 1.0, places=4)
        self.assertEqual(d.layout.shape, (8, 8, 3))
        self.assertIsNone(d.emb)

    def test_similar_colors_win(self):
        src = similar.describe(photo(WARM, 99))
        cs = cands([photo(BLUE, i) if i % 2 else photo(WARM, i) for i in range(12)])  # even id = warm
        picked = [c for c, i in similar.rank(src, cs, W, 5) if i["status"] == "picked"]
        self.assertEqual(len(picked), 5)
        self.assertTrue(all(c["id"] % 2 == 0 for c in picked), [c["id"] for c in picked])

    def test_copies_of_source_and_duplicates_are_dropped(self):
        img = photo(WARM, 5)
        src = similar.describe(img)
        other = photo(RED, 3)
        cs = cands([img, other, other, photo(BLUE, 7)])
        st = {i["id"]: i["status"] for _, i in similar.rank(src, cs, W, 10)}
        self.assertEqual(st[0], "source")
        self.assertEqual(sorted([st[1], st[2]]), ["dup", "picked"])
        st = {i["id"]: i["status"] for _, i in similar.rank(src, cs, dict(W, keep_source=True), 10)}
        self.assertEqual(st[0], "picked")

    def test_size_and_orientation_filters(self):
        src = similar.describe(photo(WARM, 1, (200, 300)))
        cs = cands([photo(WARM, 2, (300, 200)), photo(WARM, 3, (60, 90)), photo(WARM, 4, (220, 330))])
        st = {i["id"]: i["status"] for _, i in similar.rank(src, cs, dict(W, orient="same", min_side=100), 10)}
        self.assertEqual(st, {0: "orient", 1: "small", 2: "picked"})

    def test_exclude_and_include(self):
        src = similar.describe(photo(WARM, 1))
        cs = cands([photo(WARM, 2), photo(WARM, 3), photo(BLUE, 4)])
        out = similar.rank(src, cs, W, 1, excluded=[0, 1], included=[2])
        st = {i["id"]: i["status"] for _, i in out}
        self.assertEqual(st, {2: "picked", 0: "excluded", 1: "excluded"})
        self.assertEqual(out[0][1]["id"], 2)  # forced-in images come first

    def test_look_signal_steers_ranking(self):
        src = similar.describe(photo(WARM, 1))
        cs = cands([photo(WARM, i) for i in range(2, 8)])
        rng = similar.np.random.default_rng(0)
        src.dino = similar.np.ones(8, similar.np.float32) / similar.np.sqrt(8)
        for c in cs:  # candidate 4 looks most like the source to the "model"
            v = rng.normal(size=8).astype(similar.np.float32)
            v = src.dino * 3 + v if c["id"] == 4 else v
            c["d"].dino = v / similar.np.linalg.norm(v)
        top = similar.rank(src, cs, dict(W, look=5, color=0, layout=0), 1)[0][1]
        self.assertEqual((top["id"], top["status"]), (4, "picked"))
        self.assertGreater(top["look"], 0.8)
        # without the model on every image, the signal is ignored instead of crashing
        cs[0]["d"].dino = None
        self.assertIsNone(similar.rank(src, cs, W, 1)[0][1]["look"])

    def test_zero_shot_prompts(self):
        for p in json.loads((Path(similar.__file__).parent / "clip_prompts.json").read_text()):
            probs = similar.class_probs(similar.np.array(p["emb"], similar.np.float32))
            self.assertEqual(similar.top_class(probs[p["dim"]]), p["class"], p["text"])

    def test_save_never_overwrites(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "03.jpg").write_bytes(b"old")
            r = similar.save([{"data": b"a", "ext": ".jpg"}, {"data": b"b", "ext": ".png"}], d)
            self.assertEqual(r["files"], ["04.jpg", "05.png"])
            self.assertEqual(Path(d, "03.jpg").read_bytes(), b"old")


class Server(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        app.CONFIG = Path(cls.tmp) / "config.json"
        app.config["outdir"] = os.path.join(cls.tmp, "out")
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        cls.base = "http://127.0.0.1:%d" % cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        app.sim.update(name=None, path=None, img=None, job=None)

    def call(self, path, body=None, headers=None):
        data = body if isinstance(body, bytes) or body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=data, headers=headers or {},
                                     method="GET" if data is None else "POST")
        try:
            with urllib.request.urlopen(req) as r:
                raw = r.read()
                return r.status, json.loads(raw) if r.headers["Content-Type"] == "application/json" else raw
        except urllib.error.HTTPError as e:
            with e:
                return e.code, json.loads(e.read() or b"{}")

    def test_source_upload_and_state(self):
        img = photo(WARM)
        self.assertEqual(self.call("/api/sim/source/upload", img, {"X-Filename": "me.jpg"})[0], 200)
        code, st = self.call("/api/sim/state")
        self.assertEqual(st["source"]["name"], "me.jpg")
        self.assertTrue(st["outdir"].endswith("me_similar"))
        self.assertEqual(st["defaults"]["rank"]["clip"], similar.RANK_DEFAULTS["clip"])
        self.assertEqual(self.call("/api/sim/src"), (200, img))

    def test_source_path_sets_outdir_next_to_image(self):
        p = Path(self.tmp, "pic.jpg")
        p.write_bytes(photo(RED))
        self.call("/api/sim/source/path", {"path": str(p)})
        self.assertEqual(self.call("/api/sim/state")[1]["outdir"], str(Path(self.tmp, "pic_similar")))

    def test_bad_inputs(self):
        self.assertEqual(self.call("/api/sim/source/upload", b"not an image")[0], 500)
        self.assertEqual(self.call("/api/sim/source/path", {"path": "/no/such/file.jpg"})[0], 400)
        self.assertEqual(self.call("/api/sim/search", {})[0], 400)  # no image yet
        self.assertEqual(self.call("/api/sim/rank", {"weights": {}})[1], {"items": []})

    def test_other_websites_are_refused(self):
        code, r = self.call("/api/sim/source/path", {"path": "/etc/hostname"}, {"Origin": "https://evil.example"})
        self.assertEqual(code, 403)
        self.assertIsNone(app.sim["img"])


if __name__ == "__main__":
    unittest.main()
