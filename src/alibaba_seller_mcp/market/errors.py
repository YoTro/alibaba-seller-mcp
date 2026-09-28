"""Exceptions for the buyer-site (alibaba.com search) context."""

from __future__ import annotations

from collections.abc import Sequence


class MarketError(Exception):
    """Base class for buyer-site errors."""


class MarketBlockedError(MarketError):
    """alibaba.com answered with its anti-bot slider page instead of results.

    Reached only when the check was not cleared: either no browser solver is wired in
    (``ALIBABA_MARKET_SOLVE_SLIDER``), or it ran and the site kept blocking. The seller
    then opens ``url`` in a browser (and refreshes the cookie file) or saves the page.
    ``notes`` are the solver's own account of what it tried, when one ran.
    """

    def __init__(self, url: str, notes: Sequence[str] = ()):
        self.url = url
        self.notes = list(notes)
        if self.notes:
            super().__init__(
                f"alibaba.com kept showing its slider check for {url} after the browser solver "
                f"ran ({'; '.join(self.notes)}). Open that URL in a browser and pass the check, "
                "then copy the request's Cookie header into the market cookie file "
                "(ALIBABA_MARKET_COOKIE_FILE) — or save the page (Save As… → HTML) and pass it "
                "as html_path."
            )
            return
        super().__init__(
            f"alibaba.com showed its slider check for {url}. Either enable the browser solver "
            "(ALIBABA_MARKET_SOLVE_SLIDER=1, needs Camoufox + Playwright), or open that URL in a "
            "browser and pass the check, then copy the request's Cookie header into the market "
            "cookie file (ALIBABA_MARKET_COOKIE_FILE) — or save the page (Save As… → HTML) and "
            "pass it as html_path."
        )


class MarketParseError(MarketError):
    """The page has no search data we recognise (layout change, or not a search page)."""
