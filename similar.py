"""Find images that look like a given image.

1. Search: upload the image to Bing's visual search (the same request bing.com makes
   when you drop an image on it). Bing guesses a caption, e.g. "Misa Amane Cosplay";
   its image results for that caption are the candidate pool. Several uploads in
   separate sessions give several guesses, for variety.
2. Download every candidate in parallel.
3. Re-rank locally by how close each one looks to your image:
   - CLIP (an image-understanding AI model, run locally via onnxruntime): subject,
     pose, framing, style
   - color palette (HSV histogram) and color layout (composition)
   - zero-shot CLIP classes: penalise drawings / shop listings when the source is a
     photo, full-body / from-behind shots when the source is a close-up, etc.
   - near-duplicates and copies of the source are dropped.
All ranking settings re-rank instantly, without downloading again.
"""
import base64
import hashlib
import html
import io
import json
import math
import os
import re
import threading
import time
import urllib.parse
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from http.cookiejar import CookieJar
from pathlib import Path

import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0 Safari/537.36")
BING = "https://www.bing.com"
UPLOAD_URL = BING + "/images/search?view=detailv2&iss=sbiupload&FORM=SBIIDP&sbisrc=ImageDropper&idpbck=1"
PAGE = 35
MURL = re.compile(r'"murl":"(https?:(?:\\?/){2}[^"]+)"')  # slashes may be JSON-escaped

# Search settings (apply to the next search).
SEARCH_DEFAULTS = {
    "count": 10,        # images to keep
    "pool": 12,         # candidates fetched per kept image
    "uploads": 8,       # parallel Bing uploads (= caption guesses)
    "pages": 6,         # max result pages per query (35 images each)
    "threads": 32,      # parallel downloads
    "timeout": 12,      # seconds before slow downloads are abandoned
    "wait": 15,         # max seconds to wait for Bing's slower guesses
    "min_kb": 8,        # skip files smaller than this (thumbnails, junk)
    "photo": True,      # Bing filter: photos only (no clipart / drawings)
    "people": "any",    # any | faces | portrait (head & shoulders)
    "size": "any",      # any | medium | large | wallpaper
    "aspect": "any",    # any | tall | wide | square
    "color": "any",     # any | color | bw
    "safe": "moderate", # strict | moderate | off
    "extra": "",        # words added to every Bing guess
    "queries": "",      # your own searches, one per line
    "guesses": True,    # use Bing's caption guesses
    "clip": True,       # use the CLIP model
    "dino": True,       # use the DINOv2 model
}
# Ranking settings (re-rank instantly).
RANK_DEFAULTS = {
    "look": 0.8,        # weight: visual look - lighting, pose, composition (DINOv2)
    "clip": 0.4,        # weight: content / subject / style (CLIP)
    "color": 0.3,       # weight: color palette
    "layout": 0.05,     # weight: color layout / composition
    "style": 3.0,       # penalty: photo vs drawing vs shop-listing mismatch
    "frame": 1.5,       # penalty: close-up vs full body vs from behind mismatch
    "details": 3.0,     # penalty: hair color, glasses, dark vs bright lighting mismatch
    "dup_cos": 0.92,    # CLIP similarity >= this = near-duplicate shot (1 = off)
    "dup_bits": 6,      # perceptual-hash distance <= this = same picture (-1 = off)
    "keep_source": False,
    "min_side": 0,      # drop images whose short side is below this (px)
    "orient": "any",    # any | same | portrait | landscape | square
}


def clean_search(d):
    o = dict(SEARCH_DEFAULTS)
    for k, v in (d or {}).items():
        if k in o:
            o[k] = type(o[k])(v) if not isinstance(o[k], bool) else bool(v)
    o["count"] = max(1, min(500, o["count"]))
    o["pool"] = max(1, min(50, o["pool"]))
    o["uploads"] = max(0, min(10, o["uploads"]))
    o["pages"] = max(1, min(20, o["pages"]))
    o["threads"] = max(1, min(256, o["threads"]))
    o["timeout"] = max(2, min(120, o["timeout"]))
    o["wait"] = max(1, min(60, o["wait"]))
    return o


def clean_rank(d):
    o = dict(RANK_DEFAULTS)
    for k, v in (d or {}).items():
        if k in o:
            o[k] = type(o[k])(v) if not isinstance(o[k], bool) else bool(v)
    return o


# ======================= Bing =======================

class Bing:
    def __init__(self, opts, log=lambda *a: None):
        self.o = opts
        self.log = log

    @staticmethod
    def _opener():
        return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))

    @staticmethod
    def _req(opener, url, data=None, ctype=None, timeout=30):
        h = {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9", "Referer": BING + "/images"}
        if ctype:
            h["Content-Type"] = ctype
            h["Origin"] = BING
        with opener.open(urllib.request.Request(url, data=data, headers=h), timeout=timeout) as r:
            return r.read(20 << 20), r.geturl()

    def _session(self):
        return self._opener()  # Bing needs no warm-up request; cookies come with the upload

    def upload(self, img):
        """Upload the image; returns (caption guess, bcid, session)."""
        op = self._session()
        b = uuid.uuid4().hex
        body = b"".join(
            b"--%s\r\nContent-Disposition: form-data; name=\"%s\"\r\n\r\n%s\r\n" % (b.encode(), k, v)
            for k, v in ((b"cbir", b"sbi"), (b"imageBin", base64.b64encode(img)))
        ) + b"--%s--\r\n" % b.encode()
        _, final = self._req(op, UPLOAD_URL, body, "multipart/form-data; boundary=" + b)
        q = urllib.parse.parse_qs(urllib.parse.urlparse(final).query)
        guess = (q.get("q") or [""])[0]
        if not guess:
            raise RuntimeError("Bing returned no caption")
        if "bing" in guess.lower():  # Bing failed to caption; falls back to its own name
            raise RuntimeError("useless caption %r" % guess)
        return guess, (q.get("bcid") or [""])[0], op

    def _qft(self):
        o, f = self.o, []
        if o["photo"]:
            f.append("+filterui:photo-photo")
        f += {"faces": ["+filterui:face-face"], "portrait": ["+filterui:face-portrait"]}.get(o["people"], [])
        if o["size"] in ("medium", "large", "wallpaper"):
            f.append("+filterui:imagesize-" + o["size"])
        if o["aspect"] in ("tall", "wide", "square"):
            f.append("+filterui:aspect-" + o["aspect"])
        if o["color"] in ("color", "bw"):
            f.append("+filterui:color2-" + o["color"])
        return "".join(f)

    def page(self, query, bcid, op, p):
        v = {"q": query, "form": "SBIIDP"}
        qft = self._qft()
        if qft:
            v["qft"] = qft
        if bcid:
            v["bcid"] = bcid
        if self.o["safe"] in ("strict", "off"):
            v["adlt"] = self.o["safe"]
        enc = urllib.parse.urlencode(v)
        url = (BING + "/images/search?" + enc + "&first=1" if p == 0 else
               BING + "/images/async?%s&first=%d&count=%d&mmasync=1" % (enc, 1 + p * PAGE, PAGE))
        try:
            body, _ = self._req(op, url)
        except Exception:
            return []
        return parse_murls(body.decode("utf-8", "replace"))

    def find(self, img, want, cancelled=lambda: False):
        """Returns (queries used, candidate URLs best-first).

        Each upload fetches its own result pages as soon as Bing answers. All guesses
        are waited for (the slow ones are often the most descriptive, e.g. "selfie of
        young woman with long blonde hair"), up to the "wait" setting."""
        o = self.o
        n_up = o["uploads"] if o["guesses"] else 0
        custom = [l.strip() for l in o["queries"].splitlines() if l.strip()]
        n_q = n_up + len(custom)
        if not n_q:
            raise RuntimeError("Nothing to search: turn on Bing's guesses or add your own searches")
        pages = max(1, min(o["pages"], math.ceil(want / n_q / PAGE)))
        lock = threading.Lock()
        queries, lists, errors = [], {}, []

        def run(query, bcid, op):
            with lock:
                if query.lower() in (q.lower() for q in queries):
                    return  # another upload got the same guess
                queries.append(query)
            self.log("guess", query)
            with ThreadPoolExecutor(pages) as ex:
                got = list(ex.map(lambda p: self.page(query, bcid, op, p), range(pages)))
            with lock:
                lists[query] = [u for g in got for u in g]

        def guess(_):
            try:
                g, bcid, op = self.upload(img)
            except Exception as e:
                errors.append(str(e))
                return
            run((g + " " + o["extra"].strip()).strip(), bcid, op)

        ex = ThreadPoolExecutor(n_q)
        pending = {ex.submit(guess, i) for i in range(n_up)}
        pending |= {ex.submit(run, q, "", self._session()) for q in custom}
        stop = time.time() + o["wait"]
        while pending and not cancelled() and time.time() < stop:
            _, pending = wait(pending, timeout=0.1, return_when=FIRST_COMPLETED)
        ex.shutdown(wait=False, cancel_futures=True)
        with lock:
            order = [q for q in queries if q in lists]
            lst = [lists[q] for q in order]
        if not order:
            raise RuntimeError("Bing gave no results (%s)" % (errors[-1] if errors else "timed out"))
        # Interleave queries so every guess is represented near the top.
        out, seen = [], set()
        for i in range(max(map(len, lst), default=0)):
            for l in lst:
                if i < len(l) and l[i] not in seen:
                    seen.add(l[i])
                    out.append(l[i])
        return order, out[:want]


def parse_murls(page_html):
    """Full-size image URLs ("murl") from a Bing images results page."""
    return [m.replace("\\/", "/") for m in MURL.findall(html.unescape(page_html))]


# ======================= AI models =======================
# Both run locally on the CPU via onnxruntime; downloaded once, pinned by commit + SHA-256.
#   CLIP (ViT-B/32): what is in the picture - subject, pose, style. Also powers the
#     zero-shot photo/drawing and close-up/full-body classes.
#   DINOv2 (ViT-B/14): how the picture looks - lighting, pose, composition, texture.

MODEL_DIR = Path.home() / ".pin-palette" / "models"
MODELS = {
    "clip": {
        "url": "https://huggingface.co/Xenova/clip-vit-base-patch32/resolve/"
               "6b7c523703ae26e39be4b03774966f8571c7f523/onnx/vision_model_quantized.onnx",
        "sha": "583fd1110a514667812fee7d684952aaf82a99b959760c8d7dca7e0ab9839299",
        "file": "clip-vit-b32-vision-q.onnx", "mb": 89, "resize": 224,
        "mean": (0.48145466, 0.4578275, 0.40821073), "std": (0.26862954, 0.26130258, 0.27577711),
    },
    "dino": {
        "url": "https://huggingface.co/onnx-community/dinov2-base/resolve/"
               "31ef06cac16d5d301c5930d147002a058c85a5e4/onnx/model.onnx",
        "sha": "320d1012a6fc65b101fc85ca30ee7a47b2e4f6a2e8bd78fb9d7036def0e30cb0",
        "file": "dinov2-base.onnx", "mb": 347, "resize": 256,
        "mean": (0.485, 0.456, 0.406), "std": (0.229, 0.224, 0.225),
    },
}


class Model:
    _inst, _err, _lock = {}, {}, threading.Lock()

    @classmethod
    def get(cls, name, log=lambda *a: None):
        """The shared model, downloading it on first use. None if unavailable."""
        with cls._lock:
            if name not in cls._inst and name not in cls._err:
                try:
                    cls._inst[name] = cls(name, log)
                except Exception as e:
                    cls._err[name] = str(e)
            return cls._inst.get(name)

    @classmethod
    def errors(cls):
        return dict(cls._err)

    def __init__(self, name, log):
        import onnxruntime as ort  # optional dependency
        m = self.cfg = MODELS[name]
        self.name = name
        path = MODEL_DIR / m["file"]
        if not path.exists():
            log("stage", "Downloading the %s model (%d MB, first time only)" % (name.upper(), m["mb"]))
            download_verified(m["url"], m["sha"], path)
        so = ort.SessionOptions()
        so.intra_op_num_threads = 3  # several images at once, a few threads each
        so.log_severity_level = 3
        self.sess = ort.InferenceSession(str(path), so, providers=["CPUExecutionProvider"])
        self.inp = self.sess.get_inputs()[0].name
        outs = [o.name for o in self.sess.get_outputs()]
        self.out = "image_embeds" if "image_embeds" in outs else "last_hidden_state"
        self.mean = np.array(m["mean"], np.float32)
        self.std = np.array(m["std"], np.float32)
        self.sem = threading.Semaphore(max(1, (os.cpu_count() or 4) // 3))

    def embed(self, im):
        x = preprocess(im, self.cfg["resize"], 224, self.mean, self.std)[None]
        with self.sem:
            e = self.sess.run([self.out], {self.inp: x})[0][0]
        if e.ndim == 2:  # DINOv2 tokens: class token + mean of patch tokens
            e = np.concatenate([e[0], e[1:].mean(0)])
        return e / np.linalg.norm(e)


def preprocess(im, resize, crop, mean, std):
    """Resize short side to `resize` (bicubic), centre-crop `crop`, normalise, CHW."""
    w, h = im.size
    s = resize / min(w, h)
    nw, nh = max(resize, round(w * s)), max(resize, round(h * s))
    im = im.resize((nw, nh), Image.BICUBIC)
    l, t = (nw - crop) // 2, (nh - crop) // 2
    a = np.asarray(im.crop((l, t, l + crop, t + crop)), np.float32) / 255
    return ((a - mean) / std).transpose(2, 0, 1).copy()


def download_verified(url, sha, dest):
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part")
    h = hashlib.sha256()
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            h.update(chunk)
            f.write(chunk)
    if h.hexdigest() != sha:
        tmp.unlink(missing_ok=True)
        raise RuntimeError("model download corrupted (sha256 mismatch)")
    os.replace(tmp, dest)


# Zero-shot classes: CLIP text embeddings of prompts, precomputed by tools/gen_clip_prompts.py.
_P = json.loads((HERE / "clip_prompts.json").read_text(encoding="utf-8"))
P_EMB = np.array([p["emb"] for p in _P], np.float32)
P_DIM = [p["dim"] for p in _P]
P_CLS = [p["class"] for p in _P]
DIMS = tuple(dict.fromkeys(P_DIM))  # style, framing, hair, eyewear, light
# Which ranking setting weighs a mismatch in each dimension.
DIM_WEIGHT = {"style": "style", "framing": "frame", "hair": "details", "eyewear": "details", "light": "details"}


def class_probs(emb):
    """{dim: {class: probability}}: softmax (CLIP logit scale 100) over each class's best prompt."""
    sims = P_EMB @ emb
    out = {}
    for dim in DIMS:
        best = {}
        for s, d, c in zip(sims, P_DIM, P_CLS):
            if d == dim:
                best[c] = max(best.get(c, -1.0), float(s))
        mx = max(best.values())
        ex = {c: math.exp(100 * (v - mx)) for c, v in best.items()}
        tot = sum(ex.values())
        out[dim] = {c: v / tot for c, v in ex.items()}
    return out


def top_class(p):
    return max(sorted(p), key=lambda k: p[k])


# ======================= image descriptors =======================

class Desc:
    __slots__ = ("hist", "layout", "dhash", "w", "h", "emb", "dino", "cls")


def describe(data, clip=None, dino=None):
    im = Image.open(io.BytesIO(data))
    w, h = im.size
    if w < 8 or h < 8:
        raise ValueError("image too small")
    im.draft("RGB", (448, 448))  # JPEG: decode at reduced size, much faster
    im = im.convert("RGB")
    d = Desc()
    d.w, d.h = w, h
    hsv = np.asarray(im.resize((64, 64), Image.BILINEAR).convert("HSV"), np.int32).reshape(-1, 3)
    idx = (np.minimum(hsv[:, 0] * 12 // 256, 11) * 4 + hsv[:, 1] * 4 // 256) * 4 + hsv[:, 2] * 4 // 256
    d.hist = np.bincount(idx, minlength=192).astype(np.float32) / len(idx)
    d.layout = np.asarray(im.resize((8, 8), Image.BOX), np.float32) / 255
    g = np.asarray(im.convert("L").resize((9, 8), Image.BOX), np.int16)
    d.dhash = int("".join("1" if b else "0" for b in (g[:, :-1] > g[:, 1:]).ravel()), 2)
    d.emb = clip.embed(im) if clip else None
    d.dino = dino.embed(im) if dino else None
    d.cls = class_probs(d.emb) if d.emb is not None else None
    return d


def orientation(w, h):
    r = w / h
    return "landscape" if r > 1.15 else "portrait" if r < 0.87 else "square"


def hamming(a, b):
    return bin(a ^ b).count("1")


def _z(v):
    sd = v.std()
    return (v - v.mean()) / sd if sd > 0 else np.zeros_like(v)


def rank(src, cands, w, want, excluded=(), included=()):
    """Score and sort all candidates; returns [(cand, info)] best-first. info has the
    score breakdown and a status: picked | ok | dup | source | small | orient | excluded.
    The first `want` candidates that pass every filter are "picked"; candidates in
    `included` are always picked first."""
    if not cands:
        return []
    excluded, included = set(excluded), set(included)
    H = np.stack([c["d"].hist for c in cands])
    col_d = 1 - np.minimum(H, src.hist).sum(1)
    L = np.stack([c["d"].layout for c in cands])
    rms = lambda a: np.sqrt((a ** 2).mean(axis=(1, 2, 3)))
    lay_d = np.minimum(rms(L - src.layout), rms(L[:, :, ::-1] - src.layout))  # mirror-invariant
    use_clip = src.emb is not None and all(c["d"].emb is not None for c in cands)
    clip_d = 1 - np.stack([c["d"].emb for c in cands]) @ src.emb if use_clip else np.zeros(len(cands))
    use_dino = src.dino is not None and all(c["d"].dino is not None for c in cands)
    look_d = 1 - np.stack([c["d"].dino for c in cands]) @ src.dino if use_dino else np.zeros(len(cands))
    pen = np.zeros(len(cands))
    if use_clip and src.cls:
        for dim in DIMS:
            wt = w[DIM_WEIGHT.get(dim, "details")]
            if wt and all(dim in c["d"].cls for c in cands):
                want_cls = top_class(src.cls[dim])
                if DIM_WEIGHT.get(dim) == "details":
                    # Scale by how sure we are about the source (0 = coin flip, 1 = certain).
                    k = len(src.cls[dim])
                    wt *= max(0.0, (src.cls[dim][want_cls] - 1 / k) / (1 - 1 / k))
                pen += wt * (1 - np.array([c["d"].cls[dim][want_cls] for c in cands]))
    score = ((w["clip"] if use_clip else 0) * _z(clip_d) + (w["look"] if use_dino else 0) * _z(look_d)
             + w["color"] * _z(col_d) + w["layout"] * _z(lay_d) + pen)

    order = sorted(range(len(cands)), key=lambda i: (cands[i]["id"] not in included, score[i]))
    src_or = orientation(src.w, src.h)
    picked, out = [], []
    for i in order:
        c, d = cands[i], cands[i]["d"]
        if c["id"] in excluded:
            st = "excluded"
        elif c["id"] in included:
            st = "picked"
        elif not w["keep_source"] and w["dup_bits"] >= 0 and hamming(d.dhash, src.dhash) <= w["dup_bits"]:
            st = "source"
        elif min(d.w, d.h) < w["min_side"]:
            st = "small"
        elif (w["orient"] == "same" and orientation(d.w, d.h) != src_or) or \
                (w["orient"] in ("portrait", "landscape", "square") and orientation(d.w, d.h) != w["orient"]):
            st = "orient"
        else:
            st = "ok"
            for o in picked:
                if (w["dup_bits"] >= 0 and hamming(d.dhash, o.dhash) <= w["dup_bits"]) or \
                        (w["dup_cos"] < 1 and use_clip and float(d.emb @ o.emb) >= w["dup_cos"]):
                    st = "dup"
                    break
            if st == "ok" and len(picked) < want:
                st = "picked"
        if st == "picked":
            picked.append(d)
        out.append((c, {
            "id": c["id"], "status": st, "score": round(float(score[i]), 3),
            "clip": round(1 - float(clip_d[i]), 3) if use_clip else None,
            "look": round(1 - float(look_d[i]), 3) if use_dino else None,
            "color": round(1 - float(col_d[i]), 3), "layout": round(1 - float(lay_d[i]), 3),
            "style": top_class(d.cls["style"]) if d.cls else None,
            "framing": top_class(d.cls["framing"]) if d.cls else None,
            "details": [top_class(d.cls[k]) for k in ("hair", "eyewear", "light") if k in d.cls] if d.cls else [],
            "w": d.w, "h": d.h, "url": c["url"],
        }))
    return out


# ======================= search job =======================

SIG = [(b"\xff\xd8\xff", ".jpg"), (b"\x89PNG", ".png"), (b"GIF8", ".gif"), (b"BM", ".bmp")]


def sniff(data):
    for sig, ext in SIG:
        if data.startswith(sig):
            return ext
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    if data[4:12] in (b"ftypavif", b"ftypavis"):
        return ".avif"
    return None


def fetch(url, timeout, min_bytes):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept": "image/avif,image/webp,image/png,image/jpeg,image/*;q=0.8"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read(50 << 20)
    if len(data) < min_bytes:
        raise ValueError("too small")
    ext = sniff(data)
    if not ext:
        raise ValueError("not an image")
    return data, ext


class Job:
    def __init__(self, img, opts):
        self.id = uuid.uuid4().hex
        self.img, self.o = img, opts
        self.running, self.cancel = True, False
        self.stage, self.error = "Starting", None
        self.guesses, self.cands = [], []
        self.found = self.done = 0
        self.src = None
        self.clip_used = self.dino_used = False
        self.t0, self.elapsed = time.time(), 0.0
        self.lock = threading.Lock()
        self.closed = False  # set when the download phase ends: no late additions

    def log(self, kind, msg):
        if kind == "guess":
            self.guesses.append(msg)
        else:
            self.stage = msg

    def info(self):
        with self.lock:
            return {"id": self.id, "running": self.running, "stage": self.stage, "error": self.error,
                    "guesses": list(self.guesses), "found": self.found, "done": self.done,
                    "ok": len(self.cands), "clip": self.clip_used, "dino": self.dino_used,
                    "elapsed": round((time.time() - self.t0) if self.running else self.elapsed, 1)}

    def run(self):
        try:
            self._run()
        except Exception as e:
            self.error = str(e)
        self.elapsed = time.time() - self.t0
        self.running = False

    def _run(self):
        o = self.o
        clip = dino = None
        if o["clip"]:
            self.stage = "Loading the CLIP model"
            clip = Model.get("clip", self.log)
        if o["dino"]:
            self.stage = "Loading the DINOv2 model"
            dino = Model.get("dino", self.log)
        self.clip_used, self.dino_used = clip is not None, dino is not None
        self.src = describe(self.img, clip, dino)
        if self.cancel:
            return
        self.stage = "Searching Bing"
        want = o["count"] * o["pool"]
        used, urls = Bing(o, self.log).find(self.img, want, lambda: self.cancel)
        self.guesses = used
        self.found = len(urls)
        if not urls:
            raise RuntimeError("Bing returned no images. Try other search settings.")
        self.stage = "Downloading and analysing"
        deadline = time.time() + o["timeout"]
        seen = set()

        def work(u):
            if self.cancel or time.time() > deadline:
                return
            try:
                data, ext = fetch(u, max(1.0, deadline - time.time()), o["min_kb"] * 1024)
                d = describe(data, clip, dino)
            except Exception:
                return
            h = hashlib.sha1(data).digest()
            with self.lock:
                if h in seen or self.closed:
                    return
                seen.add(h)
                self.cands.append({"id": len(self.cands), "url": u, "data": data, "ext": ext, "d": d})

        ex = ThreadPoolExecutor(o["threads"])
        pending = {ex.submit(work, u) for u in urls}
        while pending and not self.cancel:
            left = deadline - time.time()
            if left <= 0:
                break
            _, pending = wait(pending, timeout=min(left, 0.25), return_when=FIRST_COMPLETED)
            self.done = len(urls) - len(pending)
        with self.lock:
            self.closed = True
        ex.shutdown(wait=False, cancel_futures=True)
        self.done = len(urls)
        self.stage = "Done"


def next_index(folder):
    """Highest NN in files named NN.ext in folder, so new saves never overwrite."""
    n = 0
    for f in Path(folder).glob("*"):
        m = re.fullmatch(r"(\d+)\.\w+", f.name)
        if m:
            n = max(n, int(m.group(1)))
    return n


def save(items, folder):
    folder = Path(os.path.expanduser(folder))
    folder.mkdir(parents=True, exist_ok=True)
    start = next_index(folder)
    files = []
    for k, c in enumerate(items, start + 1):
        p = folder / ("%02d%s" % (k, c["ext"]))
        p.write_bytes(c["data"])
        files.append(p.name)
    return {"dir": str(folder), "files": files}
