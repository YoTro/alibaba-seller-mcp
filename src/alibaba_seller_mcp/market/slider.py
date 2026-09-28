"""Clear the alibaba.com anti-bot slider, cheapest route first.

alibaba.com puts a NoCaptcha ("nc") slider — the ``_____tmd_____`` check — in front of
the search page after a few requests. Its submit token ``slidedata.n`` (``234!…``) and
``umidToken`` come from Alibaba's FireEye VM (``o.alicdn.com/seclab/f/index1.js``, a
~1100-opcode JSVMP) at runtime. Passing the check means producing that token and posting
it to ``/_____tmd_____/slide`` until the site returns ``result.code:0`` and sets the
``x5sec`` trust cookie — which, being domain-wide, then clears the HTTP client too.

Solving is ordered by the project's captcha policy — most automated and lightest first,
a real browser only as a last resort. Status tracks the reverse-engineering archive in
``camoufox-reverse-mcp/site_alibaba`` (truth source: ``docs/CONCLUSIONS.md``):

  ① **template replay (no browser)** — the signing chain is fully recovered: the token is
     ``"234!" + custom_base64(body)``, and the body is four plaintext blocks (A: navigator +
     drag trajectory, B: fingerprint, C: config/call stack, P: ``d[L]`` with the umid) run
     through fixed rotations and an adaptive arithmetic coder. What cannot be synthesised
     yet is the four blocks themselves: they still come from one real drag. But a captured
     set is *reusable* (archive B.83 r1: HTTP challenge + ``requests`` submit + forged umid,
     each template replayed ≤5 times at hour-scale gaps — 12/12 bar one bad template), so
     :class:`TemplateReplaySolver` fetches the challenge over HTTP, re-signs a stored
     template with a forged umid and submits — no browser, no JS VM. Templates are harvested
     by tier ④: every token that passes there is decoded back into its four blocks
     (:func:`template_from_slide_url`, verified to re-encode byte-for-byte), so the pool
     refills itself whenever replay falls through to the browser.
  ② **headless env-patching (hybrid)** — run the real FireEye VM in Node+jsdom and sign
     offline, fed a browser-captured umid + fingerprint, then submit. It passes (archive
     3/3 ``code:0``, a local run 2/3) but still needs a browser to acquire the challenge and
     to submit, and runs the 583 KB VM in jsdom beside it — heavier and less reliable than
     ④, so it stays a gated scaffold.
  ③ **cookie.txt** — reuse cookies from an already-cleared session. Handled outside this
     module by :class:`~.sources.HttpSearchSource` (it reads the cookie file, and every
     solved result is written back to it), so it is not a solver here.
  ④ **browser (inject)** — drive a Camoufox (anti-fingerprint Firefox) session and let the
     real nc component sign with the real umid + real environment. :class:`CamoufoxInjectSolver`
     dispatches a synthetic drag to the component (no real mouse, no offline signing) and
     harvests the ``x5sec`` cookies once it clears — the archive's ``--mode inject`` (9/10;
     ``--mode live`` 10/10). The page's own VM is left unpatched; the template for ① is
     recovered from the submitted token afterwards. Needs Camoufox + Playwright (imported
     lazily): ``pip install 'alibaba-seller-mcp[browser]'`` and ``python -m camoufox fetch``.

:class:`SliderSolverChain` runs the wired tiers in this order and returns the first cookies
that clear the check; the whole feature is off unless ``ALIBABA_MARKET_SOLVE_SLIDER`` is
set. ① is on by default within it (``ALIBABA_MARKET_SLIDER_ALGO``) — with an empty pool it
reports unavailable and costs nothing; ② stays off behind its own flag.

The recovered constants are pinned to FireEye ``index1.js`` ver 234 (``fireyeVersion``
1.234.37), nc.js 1.97.2, punish page 0.1.129. If Alibaba ships a new VM, replayed tokens
start failing, templates retire after :attr:`TemplateReplaySolver.max_fails` misses, and
the chain falls through to the browser — slower, not broken.

"""

from __future__ import annotations

import asyncio
import json
import random
import re
import string
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import parse_qs, urlencode, urlsplit

import requests

from .errors import MarketError

# ── FireEye 234! codec (route ①, recovered) ──────────────────────────────────
# Confirmed signing formula (the earlier three-key-XOR
# and sampling-hash write-ups in ALGO_RECOVERY.md were process-log hypotheses, superseded):
#     n     = "234!" + custom_base64( head10 + X2 + blocks A/B/C/P )
#     pos01 = Σ(body[12:] & 0xF)      ·   block = LEB128(len+2) + tag(2B) + data
#     data  = rotate(mode, plain)  or  rotate(mode, d8_encode(plain, Z))
# Custom base64 alphabet — NOT the standard one. Index 0-63 are the 6-bit map, index 64
# (``=``) is padding.
FIREYE_B64_ALPHABET = "e+A24Wak5tgchCGlPwisrUfpv/BuOQ8nVj1xybSLXDRqNmFIJT7MdH93YEKz6o0Z="
TOKEN_PREFIX = "234!"
FIREYE_VERSION = "1.234.37"
PAGE_VERSION = "0.1.129"
NC_APPKEY = "X82Y__c38a9560c3ecde3585e07474b36094cb"


def fireye_b64_encode(data: bytes, alphabet: str = FIREYE_B64_ALPHABET) -> str:
    """3-byte→4-char base64 with FireEye's custom alphabet (docs/CONCLUSIONS.md)."""
    out: list[str] = []
    pad = alphabet[64]
    for i in range(0, len(data), 3):
        chunk = data[i:i + 3]
        b0 = chunk[0]
        b1 = chunk[1] if len(chunk) > 1 else 0
        b2 = chunk[2] if len(chunk) > 2 else 0
        out.append(alphabet[b0 >> 2])
        out.append(alphabet[((b0 & 3) << 4) | (b1 >> 4)])
        out.append(alphabet[((b1 & 15) << 2) | (b2 >> 6)] if len(chunk) > 1 else pad)
        out.append(alphabet[b2 & 63] if len(chunk) > 2 else pad)
    return "".join(out)


def fireye_b64_decode(token: str, alphabet: str = FIREYE_B64_ALPHABET) -> bytes:
    """Inverse of :func:`fireye_b64_encode` — the real ciphertext bytes behind ``n[4:]``."""
    rev = {c: i for i, c in enumerate(alphabet[:64])}
    pad = alphabet[64]
    out = bytearray()
    clean = [c for c in token if c != pad]
    for i in range(0, len(clean), 4):
        quad = clean[i:i + 4]
        vals = [rev[c] for c in quad]
        if len(vals) >= 2:
            out.append(((vals[0] << 2) | (vals[1] >> 4)) & 0xFF)
        if len(vals) >= 3:
            out.append(((vals[1] << 4) | (vals[2] >> 2)) & 0xFF)
        if len(vals) >= 4:
            out.append(((vals[2] << 6) | vals[3]) & 0xFF)
    return bytes(out)


def pos01_checksum(body: bytes) -> tuple[int, int]:
    """Header pos0/pos1 = ``Σ(body[12:] & 0xF)`` as a 16-bit value (H1).

    pos0 is the high byte, pos1 the low byte; the server strong-checks both
    (docs/CONCLUSIONS.md)."""
    s = sum(b & 0xF for b in body[12:])
    return (s >> 8) & 0xFF, s & 0xFF


def java31_hash(text: str, positions: list[int] | None = None) -> int:
    """Java ``String.hashCode`` (base 31): ``h = (h * 31 + char) | 0``.

    A hashing primitive recovered from the FireEye VM during analysis (over sampled
    ``positions`` when given). Returns the 32-bit signed result ``| 0`` produces."""
    chars = [text[p] for p in positions] if positions is not None else list(text)
    h = 0
    for ch in chars:
        h = (h * 31 + ord(ch)) & 0xFFFFFFFF
    return h - 0x100000000 if h >= 0x80000000 else h


def repeat_xor(data: bytes, key: str | bytes, key_offset: int = 0) -> bytes:
    """``out[i] = data[i] ^ key[(i + key_offset) % len(key)]``.

    The generic repeated-XOR primitive; block P's second transform is XOR with the 2-byte
    key ``[99, 72]`` (``'c','H'``) — see docs/CONCLUSIONS.md."""
    kb = key.encode() if isinstance(key, str) else key
    return bytes(b ^ kb[(i + key_offset) % len(kb)] for i, b in enumerate(data))


def _leb128(n: int) -> list[int]:
    out = []
    while True:
        b = n & 127
        n >>= 7
        out.append(b | 128 if n else b)
        if not n:
            return out


def _read_leb128(buf: list[int], i: int) -> tuple[int, int]:
    """``(value, index after it)``."""
    n = shift = 0
    while True:
        b = buf[i]
        i += 1
        n |= (b & 127) << shift
        shift += 7
        if not b & 128:
            return n, i


def _to_int32(x: float) -> int:
    """JavaScript ``x | 0``."""
    v = int(x) & 0xFFFFFFFF
    return v - 0x100000000 if v >= 0x80000000 else v


def _js_binary(x: float) -> str:
    """``Number.prototype.toString(2)`` for a finite double.

    V8 prints the exact binary expansion (every double is a dyadic rational, and its
    radix-2 digit loop stops only at the last set bit), so this is exact, not rounded."""
    if x == 0:
        return "0"
    sign = "-" if x < 0 else ""
    x = abs(x)
    whole = int(x)
    frac = x - whole
    out = sign + format(whole, "b")
    if frac:
        num, den = frac.as_integer_ratio()
        out += "." + format(num, "b").zfill(den.bit_length() - 1).rstrip("0")
    return out


def _renormalise(hi: float, lo: float) -> tuple[float, float, int, str]:
    """Shift out the leading bits ``hi`` and ``lo`` share: ``(hi, lo, n, bits)``.

    Mirrors the VM step exactly — including JS string slicing on ``toString(2)`` and the
    ``| 0`` truncation — since the coder is defined by that float behaviour."""
    hb = _js_binary(hi)[2:]
    lb = _js_binary(lo)[2:]
    n = next((j for j, c in enumerate(hb) if j >= len(lb) or lb[j] != c), -1)
    p = 2.0 ** n
    hs = hi * p
    whole = _to_int32(hs)
    return hs - whole, lo * p - whole, n, hb[:n] if n > 0 else ""


def d8_encode(plain: list[int], z: int) -> list[int]:
    """Qu mode 15 (B.23): ``LEB128(len)`` + adaptive floating-point arithmetic coding.

    The model is 258 slots (a sentinel either side of bytes 0-255); symbol ``b`` owns
    ``[1 + b + z·(#earlier symbols < b), +1 + z·(#earlier b))`` and each use adds ``z`` to
    its count. Output bits are the shared prefix of ``hi``/``lo`` after every symbol, then
    ``lo``'s remaining bits, byte-packed with the last byte zero-padded."""
    out = _leb128(len(plain))
    counts = [0] * 256
    hi, lo, total, bits = 1.0, 0.0, 258, ""
    for raw in plain:
        b = raw & 255
        start = 1 + b + z * sum(counts[:b])
        end = start + 1 + z * counts[b]
        v = (hi - lo) / total
        hi, lo = lo + v * end, lo + v * start
        counts[b] += 1
        total += z
        hi, lo, _, emitted = _renormalise(hi, lo)
        bits += emitted
        while len(bits) >= 8:
            out.append(int(bits[:8], 2))
            bits = bits[8:]
    bits += _js_binary(lo)[2:].rstrip("0")
    while len(bits) >= 8:
        out.append(int(bits[:8], 2))
        bits = bits[8:]
    out.append(int((bits + "00000000")[:8], 2))
    return out


def d8_decode(encoded: list[int], z: int) -> list[int]:
    """Inverse of :func:`d8_encode`: replay the coder, picking at each step the symbol
    whose interval holds the code point. Exact comparisons (``Fraction``) against the same
    float boundaries the encoder computed keep it bit-faithful; callers still re-encode to
    verify."""
    count, i = _read_leb128(encoded, 0)
    stream = "".join(format(b, "08b") for b in encoded[i:])
    window = 400                                   # far past a double's 53-bit precision
    counts = [0] * 256
    hi, lo, total, offset, out = 1.0, 0.0, 258, 0, []
    for _ in range(count):
        x = Fraction(int(stream[offset:offset + window].ljust(window, "0"), 2), 1 << window)
        v = (hi - lo) / total
        below = [0]
        for c in counts:
            below.append(below[-1] + c)
        low, high = 0, 255
        while low < high:                          # largest symbol whose lower bound ≤ x
            mid = (low + high + 1) // 2
            if Fraction(lo + v * (1 + mid + z * below[mid])) <= x:
                low = mid
            else:
                high = mid - 1
        b = low
        start = 1 + b + z * below[b]
        end = start + 1 + z * counts[b]
        hi, lo = lo + v * end, lo + v * start
        counts[b] += 1
        total += z
        out.append(b)
        hi, lo, n, _ = _renormalise(hi, lo)
        offset += max(n, 0)
    return out


def _r2(seed: int) -> Callable[[list[int]], list[int]]:
    def f(chunk: list[int]) -> list[int]:
        state, out = seed, []
        for b in chunk:
            state ^= b
            out.append(state & 255)
        return out
    return f


def _r2_inv(seed: int) -> Callable[[list[int]], list[int]]:
    def f(chunk: list[int]) -> list[int]:
        prev, out = seed, []
        for b in chunk:
            out.append((b ^ prev) & 255)
            prev = b
        return out
    return f


def _xor2(key: list[int]) -> Callable[[list[int]], list[int]]:
    return lambda chunk: [b ^ key[i] for i, b in enumerate(chunk)]


def _sub_ror(sub: int, n: int) -> Callable[[list[int]], list[int]]:
    return lambda chunk: [((((b - sub) & 255) >> n) | (((b - sub) & 255) << (8 - n))) & 255 for b in chunk]


def _sub_ror_inv(sub: int, n: int) -> Callable[[list[int]], list[int]]:
    return lambda chunk: [(((b << n) | (b >> (8 - n))) + sub) & 255 for b in chunk]


def _add(a: int) -> Callable[[list[int]], list[int]]:
    return lambda chunk: [(b + a) & 255 for b in chunk]


# Qu rotation modes (B.27): 2-byte chunks cycle through four per-chunk transforms by k % 4.
_ROTATIONS = {
    4: ([_r2(216), _xor2([99, 72]), _sub_ror(0, 3), _add(225)],
        [_r2_inv(216), _xor2([99, 72]), _sub_ror_inv(0, 3), _add(-225)]),
    5: ([_sub_ror(2, 1), _xor2([116, 117]), _xor2([217, 45]), _add(49)],
        [_sub_ror_inv(2, 1), _xor2([116, 117]), _xor2([217, 45]), _add(-49)]),
    7: ([_r2(245), _r2(16), _xor2([57, 56]), _add(196)],
        [_r2_inv(245), _r2_inv(16), _xor2([57, 56]), _add(-196)]),
    8: ([_xor2([109, 117]), _sub_ror(0, 5), _sub_ror(3, 4), _add(183)],
        [_xor2([109, 117]), _sub_ror_inv(0, 5), _sub_ror_inv(3, 4), _add(-183)]),
}

# Block → (tag mask, rotation mode, d8 coder Z or None). Order in the body is A, B, C, P.
#   A navigator TLV incl. the drag trajectory   Qu7(Qu15_Z16(plain))
#   B fingerprint TLV, computed once per session Qu5(plain)
#   C config / call-stack string                 Qu8(plain)
#   P d[L], carries the umid                     Qu4(Qu15_Z8(plain))
BLOCKS: dict[str, tuple[int, int, int | None]] = {
    "A": (171, 7, 16), "B": (213, 5, None), "C": (65, 8, None), "P": (168, 4, 8),
}


def _rotate(mode: int, data: list[int], *, inverse: bool = False) -> list[int]:
    table = _ROTATIONS[mode][1 if inverse else 0]
    out: list[int] = []
    for k in range(0, len(data), 2):
        out += table[(k // 2) % 4](data[k:k + 2])
    return out


def build_body(plains: dict[str, list[int]], ts: int) -> list[int]:
    """Four plaintext blocks → token body (B.27). ``ts`` only fills header bytes the
    server does not check (pos4/5 and the X2 pair)."""
    p4, p5 = (ts >> 8) & 255, ts & 255
    blocks: list[int] = []
    for key, (mask, mode, z) in BLOCKS.items():
        plain = [b & 255 for b in plains[key]]
        data = _rotate(mode, d8_encode(plain, z) if z else plain)
        tag = sum(b & mask for b in data) & 0xFFFF
        blocks += _leb128(len(data) + 2) + [tag >> 8, tag & 255] + data
    pos01 = sum(b & 15 for b in blocks) & 0xFFFF
    size = len(blocks)
    return [pos01 >> 8, pos01 & 255, 0, 234, p4, p5, 0, 4, (size >> 8) & 255, size & 255,
            (p4 + 15) & 255, (p5 + 15) & 255] + blocks


def sign_token(plains: dict[str, list[int]], ts: int | None = None) -> str:
    """The ``slidedata.n`` token for four plaintext blocks (jsdomfree/fy_sign.js)."""
    ts = int(time.time() * 1000) if ts is None else ts
    return TOKEN_PREFIX + fireye_b64_encode(bytes(build_body(plains, ts)))


def plains_from_token(token: str) -> dict[str, list[int]]:
    """Decode a ``234!`` token back into its four plaintext blocks, verified.

    Raises :class:`MarketError` unless re-encoding the result reproduces the token's body
    byte-for-byte — so a returned template is exactly what the VM signed."""
    if not token.startswith(TOKEN_PREFIX):
        raise MarketError("not a FireEye 234! token")
    body = list(fireye_b64_decode(token[len(TOKEN_PREFIX):]))
    if len(body) < 12 or body[2:4] != [0, 234]:
        raise MarketError("token body has no FireEye 234 header")
    segments, i = [], 12
    while i < len(body):
        n, j = _read_leb128(body, i)
        segments.append(body[j + 2:j + n])
        i = j + n
    if len(segments) != len(BLOCKS):
        raise MarketError(f"token has {len(segments)} blocks, expected {len(BLOCKS)}")
    plains = {}
    for (key, (_, mode, z)), data in zip(BLOCKS.items(), segments, strict=True):
        raw = _rotate(mode, data, inverse=True)
        plains[key] = d8_decode(raw, z) if z else raw
    if build_body(plains, (body[4] << 8) | body[5]) != body:
        raise MarketError("decoded blocks do not re-encode to the token (unknown FireEye version?)")
    return plains


def forge_umid() -> str:
    """A umid the server accepts (B.80, 10/10): ``T2gA`` prefix, 68 characters."""
    return "T2gA" + "".join(random.choices(string.ascii_letters + string.digits + "-_", k=64))


def swap_umid(block_p: list[int], old: str, new: str) -> list[int]:
    """Replace the umid inside block P (F21 must match ``p.umidToken``)."""
    if len(old) != len(new):
        raise MarketError("a replacement umid must keep the original length")
    at = bytes(block_p).find(old.encode("latin1"))
    if at < 0:
        raise MarketError("the template's umid is not in its block P")
    return block_p[:at] + list(new.encode("latin1")) + block_p[at + len(old):]


# ── challenge + submit over plain HTTP ───────────────────────────────────────
_CONFIG_RE = re.compile(r"window\._config_\s*=\s*(\{.*?\})\s*;", re.S)


@dataclass
class SliderChallenge:
    """What the punish page hands out: the nc token, the ``x5secdata``, where to post."""

    t: str
    x5secdata: str
    appkey: str = NC_APPKEY
    host_path: str = "www.alibaba.com/trade/search"


def parse_challenge(html: str) -> SliderChallenge | None:
    """The slider challenge in a punish page's ``window._config_``, or None if absent."""
    m = _CONFIG_RE.search(html)
    if not m:
        return None
    try:
        cfg = json.loads(m.group(1))
    except ValueError:
        return None
    if not (cfg.get("NCTOKENSTR") and cfg.get("SECDATA")):
        return None
    host = str(cfg.get("HOST") or "www.alibaba.com").split(":")[0]
    return SliderChallenge(t=cfg["NCTOKENSTR"], x5secdata=cfg["SECDATA"],
                           appkey=cfg.get("NCAPPKEY") or NC_APPKEY,
                           host_path=host + str(cfg.get("PATH") or "/trade/search"))


def _rand_str(k: int) -> str:
    return "".join(random.choices(string.ascii_letters + string.digits + "-_", k=k))


def slide_url(challenge: SliderChallenge, token: str, umid: str, *, ncbtn: str = "",
              fireye_version: str = FIREYE_VERSION, page_version: str = PAGE_VERSION,
              lang: str = "en") -> str:
    """The ``/_____tmd_____/slide`` GET the nc component sends (core/fy_session.js)."""
    slidedata = {
        "a": challenge.appkey, "t": challenge.t, "n": token,
        "p": json.dumps({"ncbtn": ncbtn or "515.5|813|42|30|813|843|515.5|557.5",
                         "umidToken": umid}, separators=(",", ":")),
        "scene": "register", "asyn": 0, "lang": lang, "v": 1,
    }
    query = urlencode({
        "slidedata": json.dumps(slidedata, separators=(",", ":")),
        "x5secdata": challenge.x5secdata, "ppt": "0", "landscape": "1",
        "ts": str(int(time.time() * 1000)), "fireyeVersion": fireye_version,
        "pageVersion": page_version, "_rand": _rand_str(101),
        "v": "".join(random.choices(string.digits, k=18)),
    })
    return f"https://{challenge.host_path}/_____tmd_____/slide?{query}"


def _slide_success(body: str) -> bool:
    """True only when /slide explicitly passed (result.code===0). A ``bx-x5sec`` header or
    a refreshed challenge is not a pass (envpatch/solve_captcha.py ``_parse_slide_success``)."""
    try:
        data = json.loads(body)
    except (ValueError, TypeError):
        return False
    if not isinstance(data, dict) or data.get("rgv587_flag"):
        return False
    result = data.get("result")
    return result == 0 or (isinstance(result, dict) and result.get("code") == 0)


def _slide_code(body: str) -> Any:
    try:
        result = json.loads(body).get("result")
    except (ValueError, TypeError, AttributeError):
        return "unparsable"
    return result.get("code") if isinstance(result, dict) else result


def _challenge_trigger(url: str) -> str:
    """The archive's reliable challenge trigger: page 2 of the same search."""
    if "page=" in url:
        return url
    return url + ("&" if "?" in url else "?") + "page=2"


# ── template pool ────────────────────────────────────────────────────────────
@dataclass
class SliderTemplate:
    """Four plaintext blocks from one real drag, plus what replaying them needs.

    ``uses`` counts real submissions of these blocks (the harvesting drag is the first);
    ``fails`` counts consecutive rejected ones."""

    umid: str
    blocks: dict[str, list[int]]
    user_agent: str
    captured_at: float
    ncbtn: str = ""
    fireye_version: str = FIREYE_VERSION
    page_version: str = PAGE_VERSION
    uses: int = 1
    fails: int = 0
    last_used: float | None = None
    id: str = field(default="")

    def __post_init__(self) -> None:
        if not self.id:
            self.id = f"{int(self.captured_at * 1000)}-{self.umid[-8:]}"


def template_from_slide_url(url: str, user_agent: str, *, now: float | None = None) -> SliderTemplate:
    """Recover a reusable template from a ``/slide`` request the page itself sent.

    The token decodes into the four blocks (verified to re-encode exactly) and ``p`` names
    the umid, which must sit inside block P for a forged one to be swapped in later."""
    query = parse_qs(urlsplit(url).query)
    try:
        slidedata = json.loads(query["slidedata"][0])
        p = json.loads(slidedata.get("p") or "{}")
    except (KeyError, IndexError, ValueError) as exc:
        raise MarketError(f"not a /slide request: {exc}") from exc
    token, umid = str(slidedata.get("n") or ""), str(p.get("umidToken") or "")
    plains = plains_from_token(token)
    if not umid or umid.encode("latin1") not in bytes(plains["P"]):
        raise MarketError("the submitted umid is not in block P; the template could not be replayed")
    return SliderTemplate(
        umid=umid, blocks=plains, user_agent=user_agent,
        captured_at=time.time() if now is None else now, ncbtn=str(p.get("ncbtn") or ""),
        fireye_version=(query.get("fireyeVersion") or [FIREYE_VERSION])[0],
        page_version=(query.get("pageVersion") or [PAGE_VERSION])[0],
    )


class TemplatePool:
    """Templates on disk, one JSON file each; retired ones are deleted."""

    def __init__(self, directory: Path):
        self.directory = directory

    def _path(self, tpl: SliderTemplate) -> Path:
        return self.directory / f"{tpl.id}.json"

    def save(self, tpl: SliderTemplate) -> Path:
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self._path(tpl)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(tpl)), encoding="utf-8")
        tmp.replace(path)
        return path

    def all(self) -> list[SliderTemplate]:
        if not self.directory.is_dir():
            return []
        out = []
        for path in sorted(self.directory.glob("*.json")):
            try:
                out.append(SliderTemplate(**json.loads(path.read_text(encoding="utf-8"))))
            except (ValueError, TypeError):
                continue                               # a torn or foreign file is not a template
        return out

    def pick(self) -> SliderTemplate | None:
        """The freshest template — block A's timing may age (B.82), so newest first."""
        pool = self.all()
        return max(pool, key=lambda t: t.captured_at) if pool else None

    def retire(self, tpl: SliderTemplate) -> None:
        self._path(tpl).unlink(missing_ok=True)


# ── solver protocol + chain ──────────────────────────────────────────────────
class SolverUnavailable(MarketError):
    """A solver tier cannot run (dependency missing, not implemented, or not configured).

    Distinct from a tier that runs and fails to clear the check: this means "skip me",
    so :class:`SliderSolverChain` moves straight on to the next tier."""


class SliderSolver(Protocol):
    name: str

    def solve(self, url: str, country: str) -> dict[str, str]: ...


class SliderSolverChain:
    """Run solver tiers in priority order; return the first cookies that clear the check.

    A tier raising :class:`SolverUnavailable` (cannot run) or returning no cookies (ran,
    did not clear) is skipped, and the chain tries the next. Empty result means nothing
    cleared it — the caller then raises :class:`~.errors.MarketBlockedError`. The reasons
    tiers gave, and any ``notes`` a tier left about its own run, are recorded on
    :attr:`notes` for diagnosis."""

    name = "chain"

    def __init__(self, solvers: list[SliderSolver]):
        self.solvers = list(solvers)
        self.notes: list[str] = []

    def solve(self, url: str, country: str) -> dict[str, str]:
        self.notes = []
        for solver in self.solvers:
            try:
                cookies = solver.solve(url, country)
            except SolverUnavailable as exc:
                self.notes.append(f"{solver.name}: unavailable ({exc})")
                continue
            except MarketError as exc:
                self.notes.append(f"{solver.name}: failed ({exc})")
                continue
            finally:
                self.notes += [f"{solver.name}: {n}" for n in getattr(solver, "notes", ())]
            if cookies:
                self.notes.append(f"{solver.name}: cleared the check")
                return cookies
            self.notes.append(f"{solver.name}: did not clear the check")
        return {}


# ── ① template replay (no browser) ───────────────────────────────────────────
class TemplateReplaySolver:
    """Pass the slider with plain HTTP by re-signing a captured template (route ①).

    Challenge from a fresh ``requests`` session (B.78), the stored four blocks with a forged
    umid swapped into block P (B.80), signed in pure Python, submitted over the same session
    (B.75). One template per attempt: a rejected submit burns the challenge, and the chain's
    browser tier then passes and harvests a fresh template. A template retires after
    ``max_uses`` submissions (B.83 r1 validated ≤5) or ``max_fails`` consecutive rejections."""

    name = "template-replay"

    def __init__(self, pool: TemplatePool, *, max_uses: int = 5, max_fails: int = 2,
                 timeout: float = 20.0,
                 session_factory: Callable[[], requests.Session] = requests.Session):
        self.pool = pool
        self.max_uses = max_uses
        self.max_fails = max_fails
        self.timeout = timeout
        self._session_factory = session_factory

    def solve(self, url: str, country: str) -> dict[str, str]:
        tpl = self.pool.pick()
        if tpl is None:
            raise SolverUnavailable("no captured template yet — the browser tier saves one each "
                                    "time it passes the check")
        session = self._session_factory()
        session.headers.update({"User-Agent": tpl.user_agent,
                                "Accept-Language": "en-US,en;q=0.9",
                                "Referer": "https://www.alibaba.com/"})
        session.cookies.set("buyer_ship_to_info", f"local_country={country}",
                            domain=".alibaba.com", path="/")
        challenge = None
        for target in (url, _challenge_trigger(url)):
            challenge = parse_challenge(self._get(session, target).text)
            if challenge:
                break
        if challenge is None:
            raise MarketError("alibaba.com served no slider challenge to a fresh HTTP session "
                              "(it does this in stretches)")

        umid = forge_umid()
        blocks = {**tpl.blocks, "P": swap_umid(tpl.blocks["P"], tpl.umid, umid)}
        submit = slide_url(challenge, sign_token(blocks), umid, ncbtn=tpl.ncbtn,
                           fireye_version=tpl.fireye_version, page_version=tpl.page_version)
        resp = self._get(session, submit, headers={"Referer": f"https://{challenge.host_path}"})
        passed = _slide_success(resp.text)
        self._record(tpl, passed)
        if not passed:
            raise MarketError(f"/slide rejected the replayed template {tpl.id} "
                              f"(code {_slide_code(resp.text)})")
        return session.cookies.get_dict()

    def _get(self, session: requests.Session, url: str, **kw: Any) -> requests.Response:
        try:
            return session.get(url, timeout=self.timeout, **kw)
        except requests.RequestException as exc:
            raise MarketError(f"template replay request failed: {exc}") from exc

    def _record(self, tpl: SliderTemplate, passed: bool) -> None:
        tpl.uses += 1
        tpl.fails = 0 if passed else tpl.fails + 1
        tpl.last_used = time.time()
        if tpl.uses >= self.max_uses or tpl.fails >= self.max_fails:
            self.pool.retire(tpl)
        else:
            self.pool.save(tpl)


# ── ② headless env-patching ──────────────────────────────────────────────────
class HeadlessEnvSolver:
    """Sign ``n`` by running the real FireEye VM headless (Node + jsdom), then submit (hybrid).

    This passes: fed a browser-captured umid + fingerprint, the Node harness
    (``envpatch/build_slide_request.js``) signs a valid ``n`` and the same-origin submit
    clears the check (the archive reports 3/3 ``code:0``; a local run measured 2/3 —
    intermittently ``code:300`` from risk control). But it still needs a browser to acquire
    the challenge and to submit, and runs the 583 KB VM in jsdom beside that browser, so it
    is heavier and less reliable than the inject tier (measured ~1.6 GiB vs ~1.4 GiB peak,
    2/3 vs 3/3), and the template-replay tier now covers the no-browser case. It stays a
    gated scaffold naming the Node harness (``ALIBABA_MARKET_HEADLESS_SIGNER_DIR``). Off
    unless ``ALIBABA_MARKET_SLIDER_HEADLESS_SIGN=1``."""

    name = "headless-env"

    def __init__(self, signer_dir: Path | None = None):
        self.signer_dir = signer_dir

    def solve(self, url: str, country: str) -> dict[str, str]:
        if self.signer_dir is None:
            raise SolverUnavailable(
                "headless env-patching signer not configured (set ALIBABA_MARKET_HEADLESS_SIGNER_DIR "
                "to the site_alibaba Node harness, e.g. its envpatch/ directory)"
            )
        raise SolverUnavailable(
            "headless env-patching (hybrid) signing works, but it still needs a browser to acquire "
            "the challenge and submit, and is heavier and less reliable than the inject tier "
            "(~1.6 GiB vs ~1.4 GiB, 2/3 vs 3/3 code:0) — use the browser tier"
        )


# ── ④ browser (Camoufox inject) ──────────────────────────────────────────────
# Page globals live in Firefox's main world; Playwright's evaluate runs in an isolated
# world, so reach them via window.wrappedJSObject (envpatch/acquire_challenge.py).
_MAIN_WIN = (
    "(window.wrappedJSObject && (window.wrappedJSObject._config_ || "
    "window.wrappedJSObject.__fyModule)) ? window.wrappedJSObject : window"
)
_SECDATA_READY = f"() => {{ const W = {_MAIN_WIN}; return !!(W._config_ && W._config_.SECDATA); }}"
_SLIDER_READY = "() => !!document.querySelector('#nc_1_n1z')"
_FYMODULE_READY = f"() => {{ const W = {_MAIN_WIN}; return !!(W.__fyModule && W.__fyModule.getFYToken); }}"

# Dispatch a synthetic ease-out drag to the real nc component (no real mouse). The real
# component then signs with the real umid + environment and submits /slide itself
# (envpatch/solve_captcha.py, --mode inject; verified to return result.code:0).
_DISPATCH_EVENTS = """() => {
  const W = """ + _MAIN_WIN + """;
  const doc = document;
  const btn = doc.querySelector('#nc_1_n1z');
  const track = doc.querySelector('.nc_scale');
  if (!btn) return { ok:false, err:'slider #nc_1_n1z not found' };
  const br = btn.getBoundingClientRect();
  const tr = track ? track.getBoundingClientRect() : {width:300};
  const x0 = br.x + br.width/2, y0 = br.y + br.height/2, dist = Math.max(0, tr.width - br.width);
  const targets = [doc, window, doc.body, doc.documentElement, btn].filter(Boolean);
  let t = (W.performance && W.performance.now) ? W.performance.now() : Date.now();
  const fire = (type, x, y) => {
    t += type==='mousemove' ? (8+Math.random()*22) : 60;
    const ev = new MouseEvent(type, {bubbles:true,cancelable:true,
      clientX:Math.round(x),clientY:Math.round(y),screenX:Math.round(x),screenY:Math.round(y+100),
      button:0,buttons:type==='mouseup'?0:1});
    try{Object.defineProperty(ev,'isTrusted',{configurable:true,get:()=>true});}catch(e){}
    for (const tg of targets){ try{ tg.dispatchEvent(ev); }catch(e){} }
  };
  fire('mousedown', x0, y0);
  for (let i=1;i<=42;i++){ const p=i/42, e=1-Math.pow(1-p,2.3);
    fire('mousemove', x0+dist*e+(Math.random()-0.5)*1.6, y0+Math.sin(p*Math.PI)*2.5+(Math.random()-0.5)*1.2); }
  fire('mouseup', x0+dist, y0);
  return { ok:true, dist:dist };
}"""


class CamoufoxInjectSolver:
    """Pass the slider in a Camoufox browser by injecting a synthetic drag (route ④).

    With a ``pool``, the token that passed is decoded into a template for route ①; a
    harvest problem is noted on :attr:`notes`, never allowed to fail the solve."""

    name = "camoufox-inject"

    def __init__(self, *, headless: bool = True, os_type: str = "macos",
                 locale: str = "en-US", timeout: float = 90.0, pool: TemplatePool | None = None):
        self.headless = headless
        self.os_type = os_type
        self.locale = locale
        self.timeout = timeout
        self.pool = pool
        self.notes: list[str] = []

    def solve(self, url: str, country: str) -> dict[str, str]:
        self.notes = []
        cookies, passed_url, user_agent = _run_async(self._solve_async(url, country))
        if self.pool is not None and passed_url:
            self._harvest(passed_url, user_agent)
        return cookies

    def _harvest(self, slide: str, user_agent: str) -> None:
        try:
            tpl = template_from_slide_url(slide, user_agent)
        except MarketError as exc:
            self.notes.append(f"no template saved ({exc})")
            return
        self.pool.save(tpl)
        self.notes.append(f"saved template {tpl.id}")

    async def _solve_async(self, url: str, country: str) -> tuple[dict[str, str], str, str]:
        """``(cookies, url of the /slide request that passed or "", browser user agent)``."""
        AsyncCamoufox = self._load()
        try:
            async with AsyncCamoufox(os=self.os_type, humanize=True,
                                     headless=self.headless, locale=self.locale) as browser:
                page = await browser.new_page()
                await self._prime_country(page, url, country)
                slide_responses: list[dict[str, Any]] = []

                async def on_response(resp: Any) -> None:
                    if "/_____tmd_____/slide" in resp.url:
                        try:
                            body = await resp.text()
                        except Exception:
                            body = ""
                        slide_responses.append({"status": resp.status, "body": body[:4000],
                                                "url": resp.url})

                page.on("response", on_response)

                if not await self._goto_challenge(page, url):
                    # The browser was not challenged — return whatever cookies it holds; the
                    # caller retries the HTTP request and re-checks the block.
                    return await self._cookies(page), "", ""

                await _poll(page, self.timeout, _SLIDER_READY)
                await _poll(page, min(self.timeout, 30.0), _FYMODULE_READY)
                r = await page.evaluate(_DISPATCH_EVENTS)
                if not r.get("ok"):
                    raise MarketError(f"inject dispatch failed: {r.get('err')}")
                passed = await _wait_for_pass(page, slide_responses, self.timeout)
                cookies = await self._cookies(page)
                if not passed and "x5sec" not in cookies:
                    return {}, "", ""
                passed_url = next((r["url"] for r in slide_responses
                                   if r["status"] in (200, 201, 204) and _slide_success(r["body"])), "")
                user_agent = await page.evaluate("() => navigator.userAgent") if passed_url else ""
                return cookies, passed_url, user_agent
        except MarketError:
            raise
        except Exception as exc:                      # camoufox/playwright runtime errors
            raise MarketError(f"browser slider solve failed for {url}: {exc}") from exc

    async def _goto_challenge(self, page: Any, url: str) -> bool:
        """Navigate until the punish page's ``_config_.SECDATA`` appears; True if it did."""
        for target in (url, _challenge_trigger(url)):
            try:
                await page.goto(target, wait_until="domcontentloaded", timeout=60_000)
            except Exception:
                pass                                  # navigation may be interrupted by the redirect
            if await _poll(page, min(self.timeout, 15.0), _SECDATA_READY):
                return True
        return False

    async def _prime_country(self, page: Any, url: str, country: str) -> None:
        host = urlsplit(url).hostname or "www.alibaba.com"
        domain = ".alibaba.com" if host.endswith("alibaba.com") else host
        try:
            await page.context.add_cookies(
                [{"name": "buyer_ship_to_info", "value": f"local_country={country}",
                  "domain": domain, "path": "/"}]
            )
        except Exception:
            pass

    @staticmethod
    async def _cookies(page: Any) -> dict[str, str]:
        try:
            jar = await page.context.cookies()
        except Exception:
            return {}
        return {c["name"]: c["value"] for c in jar if c.get("name") and c.get("value") is not None}

    @staticmethod
    def _load() -> Any:
        try:
            from camoufox.async_api import AsyncCamoufox
        except ImportError as exc:
            raise SolverUnavailable(
                "the browser slider solver needs Camoufox and Playwright, which are not installed. "
                "Install with `pip install 'alibaba-seller-mcp[browser]'` and `python -m camoufox fetch`, "
                "or disable ALIBABA_MARKET_SOLVE_SLIDER and pass blocked pages via html_path."
            ) from exc
        return AsyncCamoufox


# ── async plumbing ───────────────────────────────────────────────────────────
async def _poll(page: Any, timeout: float, js: str) -> Any:
    """Evaluate ``js`` once a second until it returns truthy or ``timeout`` elapses."""
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            last = await page.evaluate(js)
        except Exception:
            last = None
        if last:
            return last
        await asyncio.sleep(1.0)
    return last


async def _wait_for_pass(page: Any, slide_responses: list[dict[str, Any]], timeout: float) -> bool:
    """True once /slide returns result.code:0, the ``x5sec`` cookie is set, or the page
    leaves the captcha (envpatch/solve_captcha.py ``wait_for_pass``)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for r in slide_responses:
            if r["status"] in (200, 201, 204) and _slide_success(r["body"]):
                return True
        try:
            cur = await page.context.cookies()
            if any(c.get("name") == "x5sec" and c.get("value") for c in cur):
                return True
            title = (await page.title()).lower()
            if "_____tmd_____" not in page.url and title != "captcha verification" and slide_responses:
                return True
        except Exception:
            pass
        await asyncio.sleep(1.0)
    return False


def _run_async(coro: Any) -> Any:
    """Run ``coro`` to completion from sync code, even if a loop is already running here
    (the MCP tool call). A running loop means we spin a dedicated thread with its own."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    box: dict[str, Any] = {}

    def runner() -> None:
        try:
            box["value"] = asyncio.run(coro)
        except BaseException as exc:                  # re-raised on the calling thread
            box["error"] = exc

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    thread.join()
    if "error" in box:
        raise box["error"]
    return box.get("value")
