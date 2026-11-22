#!/usr/bin/env python3
"""Pin Palette: find Pinterest images with a color palette similar to yours,
or images that look like a photo of yours (Similar image mode).

Run:  python app.py [image-or-folder]
      python app.py --similar [image]     (opens in Similar image mode)
The browser opens automatically at http://127.0.0.1:8765
"""
import json
import os
import re
import socket
import sys
import threading
import urllib.request
import uuid
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

import palette
import similar
from pinterest import Pinterest

HERE = Path(__file__).resolve().parent
IMG_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}
CONFIG = Path.home() / ".pin-palette" / "config.json"
DEFAULTS = {
    "cookie": os.environ.get("PINTEREST_COOKIE", ""),
    "outdir": str(Path.home() / "Pictures" / "Pin Palette"),
    "max": 150,
}

state = {
    "refs": [],      # [{"name", "palette"}]  palettes read from the user's images
    "target": [],    # the palette pins are matched against (editable by the user)
    "edited": False, # True once the user changed the target by hand
    "job": None,
}
lock = threading.Lock()
# Similar image mode: the source image and the current search.
sim = {"name": None, "path": None, "img": None, "job": None}


def score(cand, target):
    m = palette.compare(target, cand)
    m["score"] = round(m["emd"] + 0.5 * m["pct_diff"] + 3 * m["cx_diff"], 2)
    return m


def set_target(colors, edited):
    """Replace the target palette and re-score every result already found."""
    with lock:
        state["target"] = colors
        state["edited"] = edited
        job = state["job"]
        if job and colors:
            for r in job["results"]:
                r.update(score(r["palette"], colors))


def refs_changed():
    set_target(palette.combine([r["palette"] for r in state["refs"]]), False)


def load_config():
    cfg = dict(DEFAULTS)
    try:
        cfg.update(json.loads(CONFIG.read_text(encoding="utf-8")))
    except Exception:
        pass
    return cfg


def save_config(cfg):
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


config = load_config()


def load_path(p):
    p = Path(os.path.expanduser(p.strip().strip('"').strip("'")))
    if p.is_file():
        files = [p]
    elif p.is_dir():
        files = sorted(f for f in p.rglob("*") if f.suffix.lower() in IMG_EXT)
    else:
        files = []
    refs = []
    for f in files[:200]:
        try:
            refs.append({"name": f.name, "palette": palette.extract(f.read_bytes())})
        except Exception:
            pass
    return refs


def run_job(job, query, max_pins):
    pin = Pinterest(config["cookie"])

    def analyse(p):
        if job["cancel"]:
            return
        try:
            cand = palette.extract(pin.fetch(p["thumb"]))
        except Exception:
            return
        if not cand:
            return
        with lock:  # read the target under the lock: the user may be editing it
            job["results"].append(dict(p, palette=cand, **score(cand, state["target"])))
            job["done"] += 1

    try:
        with ThreadPoolExecutor(8) as ex:
            for p in pin.search(query, max_pins):
                if job["cancel"]:
                    break
                with lock:
                    job["found"] += 1
                ex.submit(analyse, p)
    except Exception as e:
        job["error"] = "Could not reach Pinterest (%s)." % e
    job["running"] = False


def download(items, outdir):
    out = Path(os.path.expanduser(outdir))
    out.mkdir(parents=True, exist_ok=True)
    pin = Pinterest(config["cookie"])
    saved = 0
    for it in items:
        url = it["orig"]
        ext = os.path.splitext(urlparse(url).path)[1] or ".jpg"
        dest = out / ("pin_" + re.sub(r"\D", "", it["id"]) + ext)
        if dest.exists():
            saved += 1
            continue
        try:
            dest.write_bytes(pin.fetch(url))
            saved += 1
        except Exception:
            pass
    return {"saved": saved, "dir": str(out)}


def sim_set_source(data, name, path=None):
    similar.describe(data)  # validate: raises if it is not a readable image
    if sim["job"]:
        sim["job"].cancel = True
    sim.update(name=name, path=path, img=data, job=None)


def sim_outdir():
    stem = os.path.splitext(sim["name"] or "image")[0]
    if sim["path"]:
        return str(Path(sim["path"]).parent / (stem + "_similar"))
    return str(Path(os.path.expanduser(config["outdir"])) / (stem + "_similar"))


def sim_rank(d):
    job = sim["job"]
    if not job or not job.src:
        return []
    with job.lock:
        cands = list(job.cands)
    return similar.rank(job.src, cands, similar.clean_rank(d.get("weights")), int(d.get("count", 10)),
                        d.get("excluded", ()), d.get("included", ()))


def open_folder(path):
    path = os.path.expanduser(path)
    os.makedirs(path, exist_ok=True)
    if sys.platform.startswith("win"):
        os.startfile(path)  # noqa: only exists on Windows
    elif sys.platform == "darwin":
        os.system('open "%s"' % path)
    else:
        os.system('xdg-open "%s" >/dev/null 2>&1 &' % path)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _bytes(self, data, cache="no-store"):
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Cache-Control", cache)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        n = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(n) if n else b""

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/":
            body = (HERE / "index.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")  # always serve the latest UI after an update
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/api/sim/state":
            job = sim["job"]
            src = None
            if sim["img"] is not None:
                src = {"name": sim["name"], "path": sim["path"]}
                if job and job.src is not None and job.src.cls:
                    src["kind"] = [similar.top_class(job.src.cls[k]) for k in similar.DIMS]
            self._json({"source": src, "job": job.info() if job else None,
                        "outdir": sim_outdir() if sim["img"] is not None else "",
                        "model_errors": similar.Model.errors(),
                        "defaults": {"search": similar.SEARCH_DEFAULTS, "rank": similar.RANK_DEFAULTS}})
        elif path == "/api/sim/src":
            if sim["img"] is None:
                return self.send_error(404)
            self._bytes(sim["img"])
        elif path.startswith("/api/sim/img/"):
            job = sim["job"]
            try:
                c = job.cands[int(path.rsplit("/", 1)[1])]
            except (AttributeError, ValueError, IndexError):
                return self.send_error(404)
            self._bytes(c["data"], "max-age=3600")
        elif path == "/api/state":
            job = state["job"]
            with lock:
                j = None if not job else {
                    "running": job["running"], "found": job["found"], "done": job["done"],
                    "error": job["error"], "results": list(job["results"]),
                }
            target = state["target"]
            self._json({
                "refs": [{"name": r["name"], "palette": r["palette"]} for r in state["refs"]],
                "target": target,
                "edited": state["edited"],
                "job": j,
                "query": palette.query_words([target]) if target else "",
                "settings": {"outdir": config["outdir"], "max": config["max"],
                             "logged_in": bool(config["cookie"])},
            })
        else:
            self.send_error(404)

    def do_POST(self):
        path = urlparse(self.path).path
        # Only this app's own page may call the API: refuse requests from other websites.
        origin = self.headers.get("Origin")
        if origin and urlparse(origin).netloc != self.headers.get("Host"):
            return self._json({"error": "forbidden"}, 403)
        try:
            if path.startswith("/api/sim/"):
                return self.sim_post(path)
            if path == "/api/refs/path":
                refs = load_path(json.loads(self._body())["path"])
                if not refs:
                    return self._json({"error": "No images found at that path."}, 400)
                state["refs"] += refs
                refs_changed()
                return self._json({"added": len(refs)})
            if path == "/api/refs/upload":
                name = unquote(self.headers.get("X-Filename", "image"))
                pal = palette.extract(self._body())
                state["refs"].append({"name": name, "palette": pal})
                refs_changed()
                return self._json({"added": 1})
            if path == "/api/refs/remove":
                i = int(json.loads(self._body())["index"])
                if 0 <= i < len(state["refs"]):
                    state["refs"].pop(i)
                refs_changed()
                return self._json({})
            if path == "/api/refs/clear":
                state["refs"] = []
                refs_changed()
                return self._json({})
            if path == "/api/target":
                colors = palette.normalize(json.loads(self._body())["colors"])
                set_target(colors, True)
                return self._json({"target": colors})
            if path == "/api/target/reset":
                refs_changed()
                return self._json({"target": state["target"]})
            if path == "/api/settings":
                d = json.loads(self._body())
                if "cookie" in d:
                    c = (d["cookie"] or "").strip()
                    config["cookie"] = c.split("=", 1)[1] if c.startswith("_pinterest_sess=") else c
                if d.get("outdir"):
                    config["outdir"] = d["outdir"]
                if d.get("max"):
                    config["max"] = max(25, min(1000, int(d["max"])))
                save_config(config)
                return self._json({})
            if path == "/api/search":
                if not state["target"]:
                    return self._json({"error": "Add an image or a color first."}, 400)
                d = json.loads(self._body())
                if state["job"]:
                    state["job"]["cancel"] = True
                job = {"id": uuid.uuid4().hex, "running": True, "cancel": False,
                       "found": 0, "done": 0, "error": None, "results": []}
                state["job"] = job
                threading.Thread(target=run_job, daemon=True,
                                 args=(job, d["query"], int(config["max"]))).start()
                return self._json({"ok": True})
            if path == "/api/stop":
                if state["job"]:
                    state["job"]["cancel"] = True
                return self._json({})
            if path == "/api/download":
                d = json.loads(self._body())
                return self._json(download(d["items"], config["outdir"]))
            if path == "/api/open":
                open_folder(config["outdir"])
                return self._json({})
        except (ValueError, KeyError) as e:  # bad input from the page
            return self._json({"error": str(e)}, 400)
        except Exception as e:
            return self._json({"error": str(e)}, 500)
        self.send_error(404)


    def sim_post(self, path):
        if path == "/api/sim/source/upload":
            name = unquote(self.headers.get("X-Filename", "image"))
            sim_set_source(self._body(), name)
            return self._json({"ok": True})
        if path == "/api/sim/source/path":
            p = Path(os.path.expanduser(json.loads(self._body())["path"].strip().strip('"').strip("'")))
            if not p.is_file():
                return self._json({"error": "No image at that path."}, 400)
            sim_set_source(p.read_bytes(), p.name, str(p.resolve()))
            return self._json({"ok": True})
        if path == "/api/sim/search":
            if sim["img"] is None:
                return self._json({"error": "Choose an image first."}, 400)
            if sim["job"]:
                sim["job"].cancel = True
            job = similar.Job(sim["img"], similar.clean_search(json.loads(self._body() or b"{}")))
            sim["job"] = job
            threading.Thread(target=job.run, daemon=True).start()
            return self._json({"ok": True})
        if path == "/api/sim/stop":
            if sim["job"]:
                sim["job"].cancel = True
            return self._json({})
        if path == "/api/sim/rank":
            return self._json({"items": [i for _, i in sim_rank(json.loads(self._body()))]})
        if path == "/api/sim/save":
            d = json.loads(self._body())
            items = [c for c, i in sim_rank(d) if i["status"] == "picked"]
            if not items:
                return self._json({"error": "Nothing to save."}, 400)
            r = similar.save(items, d.get("outdir") or sim_outdir())
            sim["saved"] = r["dir"]
            return self._json(r)
        if path == "/api/sim/open":
            open_folder(sim.get("saved") or sim_outdir())
            return self._json({})
        self.send_error(404)


def running_instance(port):
    """URL of a Pin Palette already running on `port`, or None."""
    url = "http://127.0.0.1:%d" % port
    try:
        with urllib.request.urlopen(url + "/api/sim/state", timeout=0.6) as r:
            return url if "defaults" in json.loads(r.read()) else None
    except Exception:
        return None


def hand_off(url, sim_mode, args):
    """Reuse a running Pin Palette: pass the image to it and open the page."""
    if args:
        api = "/api/sim/source/path" if sim_mode else "/api/refs/path"
        req = urllib.request.Request(url + api, data=json.dumps({"path": str(Path(args[0]).resolve())}).encode())
        urllib.request.urlopen(req, timeout=10).read()
    webbrowser.open(url + ("/#similar" if sim_mode else "/"))
    print("Pin Palette is already running at " + url)


def free_port(start):
    for port in range(start, start + 20):
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    return start


if __name__ == "__main__":
    args = sys.argv[1:]
    sim_mode = "--similar" in args
    args = [a for a in args if a != "--similar"]
    existing = running_instance(int(os.environ.get("PORT", 8765)))
    if existing and not os.environ.get("NO_BROWSER"):
        hand_off(existing, sim_mode, args)
        sys.exit(0)
    if args and sim_mode:
        p = Path(os.path.expanduser(args[0]))
        sim_set_source(p.read_bytes(), p.name, str(p.resolve()))
        print("Similar image mode: " + p.name)
    elif args:
        state["refs"] = load_path(args[0])
        refs_changed()
        print("Loaded %d reference image(s)." % len(state["refs"]))
    port = free_port(int(os.environ.get("PORT", 8765)))
    url = "http://127.0.0.1:%d" % port + ("/#similar" if sim_mode else "")
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    print("Pin Palette is running at " + url)
    print("Keep this window open. Press Ctrl+C to quit.")
    if not os.environ.get("NO_BROWSER"):
        threading.Timer(0.6, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
