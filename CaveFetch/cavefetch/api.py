"""e621 API access. Credentials are only ever sent to e621.net, never to the CDN."""
import os
import threading
import time

import requests

BASE = "https://e621.net"
PAGE_SIZE = 75        # e621 allows up to 320 per request
MIN_API_GAP = 0.6     # seconds between API calls (the site asks for <= ~2/sec)


class Api:
    def __init__(self):
        self.api = requests.Session()      # carries credentials, e621.net only
        self.cdn = requests.Session()      # no credentials, image servers
        self.user = None
        self._lock = threading.Lock()
        self._last = 0.0
        self._set_ua()

    def _set_ua(self):
        who = f"by {self.user} on e621" if self.user else "personal client"
        ua = f"CaveFetch/1.1 ({who})"
        self.api.headers["User-Agent"] = ua
        self.cdn.headers["User-Agent"] = ua

    def _throttle(self):
        with self._lock:
            wait = MIN_API_GAP - (time.time() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.time()

    def get(self, path, **params):
        self._throttle()
        r = self.api.get(BASE + path, params=params, timeout=30)
        r.raise_for_status()
        return r

    # ---- account
    def login(self, user, key):
        self.api.auth = (user, key)
        self.user = user
        self._set_ua()
        try:
            return self.get("/profile.json").json()
        except Exception:
            self.logout()
            raise

    def logout(self):
        self.api.auth = None
        self.user = None
        self._set_ua()

    # ---- data
    def posts(self, tags, before_id=None):
        p = {"tags": tags, "limit": PAGE_SIZE}
        if before_id:
            p["page"] = f"b{before_id}"
        return self.get("/posts.json", **p).json().get("posts", [])

    def autocomplete(self, term):
        r = self.get("/tags/autocomplete.json",
                     **{"search[name_matches]": term, "expiry": 7})
        data = r.json()
        return data if isinstance(data, list) else []

    # ---- files (CDN)
    def fetch_bytes(self, url):
        r = self.cdn.get(url, timeout=60)
        r.raise_for_status()
        return r.content

    def download_to(self, url, path):
        tmp = path + ".part"
        with self.cdn.get(url, stream=True, timeout=60) as r:
            r.raise_for_status()
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(64 * 1024):
                    f.write(chunk)
        os.replace(tmp, path)
