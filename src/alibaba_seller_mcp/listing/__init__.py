"""The application layer: turning a seller's brief into a published listing.

    publishing.py   PublishFromBrief — owns the whole flow (media, schema, AI
                    copy, attributes, commercial fields, publish)
    media.py        a brief's `photos` → main images + code-rendered detail pages

Everything above this layer is an adapter (``server.py``, ``authserver.py``);
everything below is an integration (``alibaba``, ``ai``) or the rendering engine.
"""

from .media import MediaPrep, prepare_brief_media
from .publishing import BriefOutcome, PublishFromBrief

__all__ = ["BriefOutcome", "MediaPrep", "PublishFromBrief", "prepare_brief_media"]
