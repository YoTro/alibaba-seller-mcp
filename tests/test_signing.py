"""Verify the HMAC-SHA256 signing against the documented reference example.

Open-platform docs: with App Secret "helloworld" and the params below, the
SHA256 signature over base string
    /order/get + access_token test + app_key 123456 + order_id 1234
               + sign_method sha256 + timestamp 1517820392000
is 4190D32361CFB9581350222F345CB77F3B19F0E31D162316848A2C1FFD5FAB4A.
"""

from alibaba_seller_mcp.alibaba.client import AlibabaClient, is_path_style
from alibaba_seller_mcp.config import Config


def test_signature_matches_documented_example():
    client = AlibabaClient(Config(app_key="123456", app_secret="helloworld"))
    params = {
        "access_token": "test",
        "app_key": "123456",
        "order_id": "1234",
        "sign_method": "sha256",
        "timestamp": "1517820392000",
    }
    sig = client._sign("/order/get", params)
    assert sig == "4190D32361CFB9581350222F345CB77F3B19F0E31D162316848A2C1FFD5FAB4A"


def test_sign_excludes_sign_field():
    client = AlibabaClient(Config(app_key="123456", app_secret="helloworld"))
    params = {
        "access_token": "test",
        "app_key": "123456",
        "order_id": "1234",
        "sign_method": "sha256",
        "timestamp": "1517820392000",
        "sign": "SHOULD_BE_IGNORED",
    }
    assert client._sign("/order/get", params) == (
        "4190D32361CFB9581350222F345CB77F3B19F0E31D162316848A2C1FFD5FAB4A"
    )


def test_sync_sign_is_kv_only_no_prefix():
    # TOP /sync gateway (photobank.upload) signs sorted key+value with NO api-name
    # prefix (verified against a live console request); differs from /rest signing.
    import hashlib
    import hmac

    client = AlibabaClient(Config(app_key="123456", app_secret="helloworld"))
    params = {"app_key": "123456", "method": "alibaba.icbu.photobank.upload", "timestamp": "1"}
    base = "".join(f"{k}{params[k]}" for k in sorted(params))  # no prefix
    expected = hmac.new(b"helloworld", base.encode(), hashlib.sha256).hexdigest().upper()
    assert client._sign_sync(params) == expected
    # and it must differ from the /rest (method-prefixed) signature
    assert client._sign_sync(params) != client._sign("alibaba.icbu.photobank.upload", params)


def test_is_path_style():
    assert is_path_style("/auth/token/create") is True
    assert is_path_style("alibaba.icbu.product.list") is False


def test_method_style_sign_prefixes_method_name():
    # TOP/method style (confirmed live): base is the dotted method name followed
    # by the sorted key+value pairs (which include `method`).
    import hashlib
    import hmac

    client = AlibabaClient(Config(app_key="123456", app_secret="helloworld"))
    method = "alibaba.icbu.product.list"
    params = {"app_key": "123456", "method": method, "timestamp": "1"}
    base = method + "".join(f"{k}{params[k]}" for k in sorted(params))
    expected = hmac.new(b"helloworld", base.encode(), hashlib.sha256).hexdigest().upper()
    assert client._sign(method, params) == expected


def test_parse_code_from_url():
    from alibaba_seller_mcp.alibaba.auth import SellerAuth
    from alibaba_seller_mcp.alibaba.errors import AlibabaAuthError

    assert (
        SellerAuth.parse_code_from_url("https://www.alibaba.com/?code=ABC123&state=mcp")
        == "ABC123"
    )
    assert SellerAuth.parse_code_from_url("http://127.0.0.1:8721/callback?code=XY") == "XY"
    try:
        SellerAuth.parse_code_from_url("https://www.alibaba.com/?state=mcp")
    except AlibabaAuthError:
        pass
    else:  # pragma: no cover
        raise AssertionError("expected AlibabaAuthError for missing code")
