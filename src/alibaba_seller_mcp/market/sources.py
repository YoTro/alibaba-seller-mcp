"""Where a search page comes from: live over HTTP, or a page the seller saved.

The search page needs no login, but alibaba.com puts an anti-bot slider in front of
it after only a few requests from a bare client. So the HTTP source:

* keeps one ``requests.Session`` per server run, so cookies the site sets carry over
  from request to request, as they would in a browser;
* can start from a browser's cookies — the ``Cookie`` header copied from DevTools
  into ``ALIBABA_MARKET_COOKIE_FILE``, re-read whenever the file changes. After passing the slider once in a browser,
  its ``x5sec`` cookie clears it for this client too. Logged in or not both work;
* waits ``min_interval`` seconds between requests;
* when a :class:`~.slider.SliderSolver` is wired in, passes the slider once in a browser
  and retries with the cookies that clears; otherwise raises :class:`MarketBlockedError`.

The saved-page source is the fallback when there is no solver and the slider will not
clear: the seller opens the search in a browser, saves it (Save As… → HTML), passes it.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote_plus

import requests

from .errors import MarketBlockedError, MarketError
from .page_data import is_blocked
from .slider import SliderSolver

SEARCH_URL = "https://www.alibaba.com/trade/search?tab=all&SearchText={q}"

_BROWSER_HEADERS = {
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "accept-language": "en-US,en;q=0.9",
    "referer": "https://www.alibaba.com/",
    "sec-fetch-dest": "document",
    "sec-fetch-mode": "navigate",
    "sec-fetch-site": "same-origin",
    "upgrade-insecure-requests": "1",
    "user-agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"),
}

# Query parameters the page itself sends to its bottom-P4P endpoint.
_BOTTOM_P4P_PARAMS = ("keyword", "count", "offset", "pageIndex", "pid", "dl", "mt", "imgSizeRule",
                      "showIntelMatch")


def parse_cookie_text(text: str) -> dict[str, str]:
    """Cookies from what a seller will paste: a DevTools ``Cookie`` header (with or
    without the ``cookie:`` prefix), curl's ``-b '…'`` value, or a Netscape
    cookies.txt export."""
    cookies: dict[str, str] = {}
    lines = [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    netscape = [ln.split("\t") for ln in lines if ln.count("\t") >= 6]
    if netscape:
        return {f[5]: f[6] for f in netscape}
    raw = " ".join(lines).strip().strip("'\"")
    if raw.lower().startswith("cookie:"):
        raw = raw[len("cookie:"):]
    for part in raw.split(";"):
        name, sep, value = part.strip().partition("=")
        if sep and name:
            cookies[name] = value      # a repeated name (XSRF-TOKEN is sent twice) keeps the last
    return cookies


class SearchPageSource(Protocol):
    name: str

    def search_page(self, keyword: str, country: str) -> str: ...


class HttpSearchSource:
    name = "http"

    def __init__(
        self,
        cookie_file: Path | None = None,
        *,
        min_interval: float = 10.0,
        timeout: float = 20.0,
        session: requests.Session | None = None,
        solver: SliderSolver | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.cookie_file = cookie_file
        self.min_interval = min_interval
        self.timeout = timeout
        self.solver = solver
        self._session = session or requests.Session()
        self._session.headers.update(_BROWSER_HEADERS)
        self._clock = clock
        self._sleep = sleep
        self._last: float | None = None
        self._cookie_mtime: float | None = None

    def _load_cookies(self) -> None:
        """(Re)load the cookie file whenever it changes — a seller who refreshes it
        after a slider check should not have to restart the server."""
        if not (self.cookie_file and self.cookie_file.is_file()):
            return
        mtime = self.cookie_file.stat().st_mtime
        if mtime == self._cookie_mtime:
            return
        self._cookie_mtime = mtime
        for name, value in parse_cookie_text(self.cookie_file.read_text(encoding="utf-8")).items():
            self._session.cookies.set(name, value, domain=".alibaba.com", path="/")

    def _pace(self) -> None:
        if self._last is not None:
            wait = self.min_interval - (self._clock() - self._last)
            if wait > 0:
                self._sleep(wait)
        self._last = self._clock()

    def _get(self, url: str, **kw: Any) -> requests.Response:
        self._load_cookies()
        self._pace()
        try:
            resp = self._session.get(url, timeout=self.timeout, **kw)
        except requests.RequestException as exc:
            raise MarketError(f"request to {url} failed: {exc}") from exc
        if resp.status_code != 200:
            raise MarketError(f"{url} answered HTTP {resp.status_code}")
        return resp

    def search_page(self, keyword: str, country: str) -> str:
        # Ads are targeted by buyer country; the site reads it from this cookie.
        self._load_cookies()
        self._session.cookies.set("buyer_ship_to_info", f"local_country={country}",
                                  domain=".alibaba.com", path="/")
        url = SEARCH_URL.format(q=quote_plus(keyword))
        html = self._get(url).text
        if is_blocked(html):
            retried = self._solve_and_retry(url, country)
            if retried is None or is_blocked(retried):
                notes = list(getattr(self.solver, "notes", ()))
                if retried is not None:
                    notes.append("the retry with the solved cookies was blocked again")
                raise MarketBlockedError(url, notes)
            html = retried
        return html

    def _solve_and_retry(self, url: str, country: str) -> str | None:
        """On the slider page, pass it in a browser (if a solver is wired) and fetch once more.

        ``None`` when there is nothing to retry with (no solver, or the solver produced no
        cookies); otherwise the retried page's HTML, which the caller re-checks. Never loops:
        one browser solve per blocked request is enough — a fresh block means the check
        escalated, not that another drag would help."""
        if self.solver is None:
            return None
        cookies = self.solver.solve(url, country)
        if not cookies:
            return None
        self._install_cookies(cookies)
        return self._get(url).text

    def _install_cookies(self, cookies: dict[str, str]) -> None:
        """Adopt the browser's post-solve cookies for this session, and persist them to the
        cookie file so a later run (or a human) inherits the cleared session too."""
        for name, value in cookies.items():
            self._session.cookies.set(name, value, domain=".alibaba.com", path="/")
        if not self.cookie_file:
            return
        merged = {}
        if self.cookie_file.is_file():
            merged.update(parse_cookie_text(self.cookie_file.read_text(encoding="utf-8")))
        merged.update(cookies)
        header = "; ".join(f"{n}={v}" for n, v in merged.items())
        self.cookie_file.parent.mkdir(parents=True, exist_ok=True)
        self.cookie_file.write_text(header + "\n", encoding="utf-8")
        # We just wrote our own cookies in; don't let the next _load_cookies re-read and
        # treat them as an external change.
        self._cookie_mtime = self.cookie_file.stat().st_mtime

    def bottom_p4p(self, params: dict[str, Any]) -> dict[str, Any]:
        prefix = str(params.get("urlPrefix") or "")
        url = ("https:" + prefix) if prefix.startswith("//") else prefix
        query = {k: params[k] for k in _BOTTOM_P4P_PARAMS if params.get(k) not in (None, "")}
        resp = self._get(url, params=query)
        try:
            return resp.json()
        except ValueError as exc:
            raise MarketError(f"bottom P4P response from {url} is not JSON") from exc


class SavedPageSource:
    """A search page the seller saved from a browser."""

    name = "file"

    def __init__(self, path: Path):
        self.path = path

    def search_page(self, keyword: str, country: str) -> str:
        html = self.path.read_text(encoding="utf-8", errors="replace")
        if is_blocked(html):
            raise MarketBlockedError(f"(saved page {self.path.name})")
        return html
