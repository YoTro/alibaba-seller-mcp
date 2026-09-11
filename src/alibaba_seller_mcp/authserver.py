"""Local authorization helper: run the seller OAuth flow and capture the code.

Usage:
    python -m alibaba_seller_mcp.authserver          # or: alibaba-seller-auth

Behaviour depends on the configured ``ALIBABA_REDIRECT_URI``:

* **localhost/127.0.0.1 callback** — starts a tiny HTTP server on that host/port,
  opens the browser to the authorize URL, and auto-captures the ``?code=...`` the
  platform redirects back with. Fully hands-off.
  (Register that exact localhost URL in the app console first — the sent
  ``redirect_uri`` must byte-match the registered one.)

* **any other callback** (e.g. https://www.alibaba.com for a test app) — the local
  server can't receive that redirect, so it opens the browser and then asks you to
  paste the full URL you landed on; it extracts the code from it.

Either way it exchanges the code for a token and stores it.
"""

from __future__ import annotations

import argparse
import os
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

from .alibaba.auth import SellerAuth
from .alibaba.client import AlibabaClient
from .alibaba.errors import AlibabaError
from .config import Config, load_config
from .server import _load_dotenv
from .storage import TokenStore

_SUCCESS_HTML = (
    "<html><body style='font-family:sans-serif;padding:2rem'>"
    "<h2>✅ Authorization received</h2>"
    "<p>You can close this tab and return to the terminal.</p></body></html>"
)
_ERROR_HTML = (
    "<html><body style='font-family:sans-serif;padding:2rem'>"
    "<h2>⚠️ No authorization code in the callback</h2>"
    "<p>Check the terminal for details.</p></body></html>"
)


def _is_local(host: str) -> bool:
    return host in {"localhost", "127.0.0.1", "0.0.0.0", "[::1]", "::1"}


def _build_auth(config: Config) -> SellerAuth:
    return SellerAuth(config, AlibabaClient(config), TokenStore(config.token_store_path))


def _capture_via_server(
    config: Config,
    auth: SellerAuth,
    state: str,
    timeout: float,
    *,
    bind_host: str,
    bind_port: int,
) -> str:
    """Run a local HTTP listener on ``bind_host:bind_port`` and capture the code.

    The listener matches the *path* of the configured redirect_uri, so it works
    both for a localhost redirect and for a public https tunnel that forwards to
    this port (register the tunnel URL as the console callback).
    """
    expected_path = urlparse(config.redirect_uri).path or "/"

    captured: dict[str, str] = {}
    done = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 (stdlib naming)
            req = urlparse(self.path)
            if req.path.rstrip("/") != expected_path.rstrip("/"):
                self.send_response(404)
                self.end_headers()
                return
            try:
                captured["code"] = SellerAuth.parse_code_from_url(self.path)
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(_SUCCESS_HTML.encode("utf-8"))
            except AlibabaError:
                self.send_response(400)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(_ERROR_HTML.encode("utf-8"))
            finally:
                done.set()

        def log_message(self, *args):  # silence default request logging
            pass

    server = HTTPServer((bind_host, bind_port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    url = auth.build_authorize_url(state=state)
    print(f"Listening on {bind_host}:{bind_port} (path {expected_path}) …")
    print(f"Callback the platform will redirect to: {config.redirect_uri}")
    print(f"Opening browser to:\n  {url}\n")
    webbrowser.open(url)

    got = done.wait(timeout=timeout)
    server.shutdown()
    if not got or "code" not in captured:
        raise SystemExit("Timed out waiting for the callback. Try again.")
    return captured["code"]


def _capture_via_paste(config: Config, auth: SellerAuth, state: str) -> str:
    url = auth.build_authorize_url(state=state)
    print(
        "The configured redirect is not a localhost URL, so this helper can't\n"
        "receive it directly. Approve access in the browser, then paste the FULL\n"
        "URL you were redirected to (it contains ?code=...).\n"
    )
    print(f"Opening browser to:\n  {url}\n")
    webbrowser.open(url)
    pasted = input("Paste the redirected URL here: ").strip()
    return SellerAuth.parse_code_from_url(pasted)


def main() -> None:
    parser = argparse.ArgumentParser(description="Alibaba seller OAuth helper.")
    parser.add_argument("--state", default="mcp", help="OAuth state value.")
    parser.add_argument("--timeout", type=float, default=300.0, help="Seconds to wait for the callback.")
    parser.add_argument(
        "--paste",
        action="store_true",
        help="Force paste mode even if the redirect URI is localhost.",
    )
    parser.add_argument(
        "--bind",
        default="",
        help=(
            "HOST:PORT for the local listener when the public callback is a tunnel "
            "(e.g. 127.0.0.1:8721). Also read from ALIBABA_LOCAL_BIND. Use this when "
            "the console requires a public https callback that forwards here."
        ),
    )
    args = parser.parse_args()

    _load_dotenv()
    config = load_config()
    config.require_alibaba()
    auth = _build_auth(config)

    host = urlparse(config.redirect_uri).hostname or ""
    port = urlparse(config.redirect_uri).port
    bind = args.bind or os.environ.get("ALIBABA_LOCAL_BIND", "")

    if args.paste:
        code = _capture_via_paste(config, auth, args.state)
    elif bind:
        bind_host, _, bind_port_s = bind.partition(":")
        code = _capture_via_server(
            config, auth, args.state, args.timeout,
            bind_host=bind_host or "127.0.0.1", bind_port=int(bind_port_s or "8721"),
        )
    elif _is_local(host):
        code = _capture_via_server(
            config, auth, args.state, args.timeout,
            bind_host="127.0.0.1", bind_port=port or 80,
        )
    else:
        code = _capture_via_paste(config, auth, args.state)

    print("\nExchanging code for a token …")
    token = auth.exchange_code(code)
    print("✅ Authorized.")
    print(f"   account_key      : {token['account_key']}")
    print(f"   expires_at       : {token['expires_at']}")
    print(f"   has_refresh_token: {bool(token.get('refresh_token'))}")
    print(f"   token stored at  : {config.token_store_path}")


if __name__ == "__main__":
    main()
