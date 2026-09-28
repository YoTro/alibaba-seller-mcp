"""The buyer-site context: who advertises on an alibaba.com keyword, and how locked it is.

A separate bounded context from ``alibaba`` (the open-platform client): it reads the
public search page, not a signed API, so it has its own transport, auth (none, or
a browser cookie) and failure modes (the anti-bot slider).

    page_data.py   HTML → the page's JSON data blocks
    ads.py         AdKind / AdPlacement / KeywordAdSnapshot — the domain model
    parsers.py     data blocks → placements (one strategy per ad source)
    monopoly.py    snapshot → MonopolyReport (pure scoring)
    sources.py     live HTTP page source + saved-page source
    slider.py      pass the anti-bot slider, cheapest route first (template replay → headless → browser)
    snapshots.py   JSONL cache + history
    analysis.py    KeywordAdsAnalysis — the application service tying them together
"""

from .ads import AdKind, AdPlacement, KeywordAdSnapshot
from .analysis import KeywordAdsAnalysis, MonopolyBatch, SnapshotOutcome
from .errors import MarketBlockedError, MarketError, MarketParseError
from .monopoly import ExposureWeights, HolderShare, MonopolyReport, MonopolyScorer, Thresholds
from .slider import (
    CamoufoxInjectSolver,
    HeadlessEnvSolver,
    SliderSolver,
    SliderSolverChain,
    SliderTemplate,
    SolverUnavailable,
    TemplatePool,
    TemplateReplaySolver,
)
from .snapshots import SnapshotStore
from .sources import HttpSearchSource, SavedPageSource

__all__ = [
    "AdKind",
    "AdPlacement",
    "CamoufoxInjectSolver",
    "ExposureWeights",
    "HeadlessEnvSolver",
    "HolderShare",
    "HttpSearchSource",
    "KeywordAdSnapshot",
    "KeywordAdsAnalysis",
    "MarketBlockedError",
    "MarketError",
    "MarketParseError",
    "MonopolyBatch",
    "MonopolyReport",
    "MonopolyScorer",
    "SavedPageSource",
    "SliderSolver",
    "SliderSolverChain",
    "SliderTemplate",
    "SolverUnavailable",
    "SnapshotOutcome",
    "SnapshotStore",
    "TemplatePool",
    "TemplateReplaySolver",
    "Thresholds",
]
