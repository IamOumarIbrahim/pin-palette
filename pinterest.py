"""Minimal Pinterest search client (public web endpoint, no login)."""
import json
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")
BASE = "https://www.pinterest.com"


class Pinterest:
    def __init__(self, session_cookie=None):
        """session_cookie: optional value of the `_pinterest_sess` cookie from a
        logged-in browser. Logged-in searches return more, personalised results."""
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))
        self.session_cookie = (session_cookie or "").strip()
        if self.session_cookie.startswith("_pinterest_sess="):
            self.session_cookie = self.session_cookie.split("=", 1)[1]
        self._warm = False

    def _get(self, url, headers=None, timeout=20):
        h = {"User-Agent": UA, **(headers or {})}
        if self.session_cookie and "pinterest.com" in url:
            h["Cookie"] = "_pinterest_sess=" + self.session_cookie
        req = urllib.request.Request(url, headers=h)
        with self.opener.open(req, timeout=timeout) as r:
            return r.read()

    def _warmup(self):
        if not self._warm:
            try:
                self._get(BASE + "/")  # sets csrftoken cookie
            except Exception:
                pass
            self._warm = True

    def search(self, query, max_pins=100):
        """Yield pins: {"id", "thumb", "orig", "link"}."""
        self._warmup()
        bookmark = None
        seen = 0
        while seen < max_pins:
            options = {"query": query, "scope": "pins", "page_size": 25, "rs": "typed"}
            if bookmark:
                options["bookmarks"] = [bookmark]
            source = "/search/pins/?q=" + urllib.parse.quote(query)
            params = urllib.parse.urlencode({
                "source_url": source,
                "data": json.dumps({"options": options, "context": {}}),
            })
            raw = self._get(
                BASE + "/resource/BaseSearchResource/get/?" + params,
                headers={
                    "Accept": "application/json, text/javascript, */*; q=0.01",
                    "X-Requested-With": "XMLHttpRequest",
                    "X-Pinterest-AppState": "active",
                    "X-Pinterest-PWS-Handler": "www/search/[scope].js",
                    "X-Pinterest-Source-Url": source,
                    "Referer": BASE + source,
                },
            )
            rr = json.loads(raw).get("resource_response", {})
            data = rr.get("data") or {}
            results = data.get("results", []) if isinstance(data, dict) else data
            if not results:
                return
            for item in results:
                imgs = item.get("images") or {}
                if not imgs or item.get("type", "pin") != "pin":
                    continue
                thumb = (imgs.get("236x") or imgs.get("474x") or {}).get("url")
                orig = (imgs.get("orig") or imgs.get("736x") or {}).get("url")
                if not thumb or not orig:
                    continue
                yield {"id": str(item["id"]), "thumb": thumb, "orig": orig,
                       "link": f"{BASE}/pin/{item['id']}/"}
                seen += 1
                if seen >= max_pins:
                    return
            bookmark = rr.get("bookmark")
            if not bookmark and isinstance(data, dict):
                bookmark = (data.get("bookmarks") or [None])[0]
            if not bookmark or bookmark == "-end-":
                return

    def fetch(self, url):
        return self._get(url, headers={"Referer": BASE + "/"})
