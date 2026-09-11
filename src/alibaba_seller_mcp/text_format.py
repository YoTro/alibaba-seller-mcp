"""Alibaba.com product-title capitalization ("title case").

Rules:
  1. The first word is always capitalized.
  2. Major words (nouns, verbs, adjectives, adverbs, and any word >= 4 letters —
     including longer articles/conjunctions/prepositions) are capitalized.
  3. These short function words stay lowercase (unless they are the first word):
       articles      the a an
       conjunctions  and but for nor or so yet if
       prepositions  at by for in of on to up
     "with" may be either case; we keep it lowercase.
  4. Brand / model / acronym tokens are left untouched (not subject to the rules):
     anything containing a digit (C01B), an all-caps token (USB, ABS, LED),
     mixed-case tokens (iPhone), or a name passed in ``brands``.
"""

from __future__ import annotations

import re
import string

# Publishing title rules: length <= 128 chars (incl. spaces); avoid special
# characters (@ ! ！ ? ？ $ ^ { } ~ 、 etc.); the only punctuation kept is - / , & .
TITLE_MAX_LEN = 128
_ALLOWED_TITLE_PUNCT = "-/,&."
# Anything that is not a word char (unicode letters/digits/_), whitespace, or one
# of the allowed punctuation marks is treated as a disallowed special character.
_DISALLOWED_TITLE_RE = re.compile(r"[^\w\s" + re.escape(_ALLOWED_TITLE_PUNCT) + r"]", re.UNICODE)

SMALL_WORDS = {
    "the", "a", "an",
    "and", "but", "for", "nor", "or", "so", "yet", "if",
    "at", "by", "in", "of", "on", "to", "up",
    "with",
}


def _core(token: str) -> str:
    return token.strip(string.punctuation)


def _is_preserved(token: str) -> bool:
    """A brand/model/acronym token that must keep its original casing."""
    core = _core(token)
    if not core:
        return False
    if any(ch.isdigit() for ch in core):
        return True
    if len(core) > 1 and core.isupper():
        return True
    if any(ch.isupper() for ch in core[1:]):  # internal caps, e.g. iPhone
        return True
    return False


def _cap_first(token: str) -> str:
    """Uppercase the first alphabetic char, lowercase the rest."""
    for i, ch in enumerate(token):
        if ch.isalpha():
            return token[:i] + ch.upper() + token[i + 1 :].lower()
    return token


def title_case(title: str, brands: list[str] | None = None) -> str:
    brand_map = {b.lower(): b for b in (brands or [])}
    words = title.split()
    out: list[str] = []
    for idx, word in enumerate(words):
        key = _core(word).lower()
        if key in brand_map:
            out.append(brand_map[key])
        elif _is_preserved(word):
            out.append(word)
        elif idx == 0:
            out.append(_cap_first(word))
        elif key in SMALL_WORDS:
            out.append(word.lower())
        else:
            out.append(_cap_first(word))
    return " ".join(out)


def clean_title(title: str) -> str:
    """Strip disallowed special characters and collapse whitespace.

    Disallowed marks are replaced with a space (so tokens don't fuse), then runs
    of whitespace are collapsed. Only ``- / , & .`` punctuation is kept."""
    cleaned = _DISALLOWED_TITLE_RE.sub(" ", title)
    return re.sub(r"\s+", " ", cleaned).strip()


def enforce_length(title: str, max_len: int = TITLE_MAX_LEN) -> str:
    """Truncate to ``max_len`` characters, preferring a word boundary."""
    if len(title) <= max_len:
        return title
    head = title[:max_len]
    trimmed = head.rsplit(" ", 1)[0].strip()
    return trimmed or head.strip()


def normalize_title(title: str, brands: list[str] | None = None) -> str:
    """Full publishing-ready title: clean disallowed chars → title case → length cap."""
    return enforce_length(title_case(clean_title(title), brands=brands))


def validate_title(title: str) -> list[str]:
    """Return a list of rule violations (empty if the title is compliant)."""
    issues: list[str] = []
    if len(title) > TITLE_MAX_LEN:
        issues.append(f"length {len(title)} exceeds {TITLE_MAX_LEN} characters")
    bad = sorted(set(_DISALLOWED_TITLE_RE.findall(title)))
    if bad:
        issues.append("contains disallowed characters: " + " ".join(bad))
    return issues
