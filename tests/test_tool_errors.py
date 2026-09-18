"""MCP error contract: an anticipated failure must reach the client as a tool
execution error (`isError: true`), not as a successful call carrying ok=False.

Spec: MCP 2025-11-25, Server/Tools § Error Handling — protocol errors are for
unknown tools and malformed requests; API failures, input-validation failures and
business-logic failures are tool execution errors.
"""

import asyncio

from mcp.types import CallToolResult

from alibaba_seller_mcp import server
from alibaba_seller_mcp.alibaba.errors import AlibabaError
from alibaba_seller_mcp.models import (
    AuthStatusResult,
    MediaInfoResult,
    PublishResult,
    Result,
    VideoRelateResult,
)
from alibaba_seller_mcp.pathsafe import PathNotAllowedError


def _call(name, args=None):
    return asyncio.run(server.mcp.call_tool(name, args or {}))


def test_path_outside_allowlist_is_a_tool_execution_error():
    res = _call("read_local_media", {"path": "/etc/passwd"})
    assert res.is_error is True
    # the body still carries the typed failure so a client can branch on it
    assert res.structured_content["ok"] is False
    assert res.structured_content["error_type"] == "PathNotAllowedError"
    assert "outside the allowed" in res.structured_content["error"]
    # and the message is readable in plain content for the model
    assert "PathNotAllowedError" in res.content[0].text


def test_success_is_not_flagged():
    res = _call("usage_stats")
    assert res.is_error is False
    assert res.structured_content["ok"] is True


def test_every_caught_exception_type_is_flagged():
    for exc in (AlibabaError("api down"), PathNotAllowedError("nope"),
                ValueError("bad input"), RuntimeError("missing env")):
        def boom(exc=exc) -> MediaInfoResult:   # bind the loop variable
            raise exc

        out = server.tool_errors(MediaInfoResult)(boom)()
        assert isinstance(out, CallToolResult), type(exc).__name__
        assert out.is_error is True, type(exc).__name__
        assert out.structured_content["error_type"] == type(exc).__name__


def test_business_failure_is_flagged_and_keeps_the_payload():
    """`code: "0"` with `biz_success: false` — the gateway accepted the call and
    the platform refused it. No exception is raised, so it needs its own check."""
    def rejected() -> PublishResult:
        return PublishResult(product_id="123", biz_success=False, filled_fields=["icbuCatProp"],
                             response={"code": "0", "message": "isv.missing-parameter:productTitle"})

    out = server.tool_errors(PublishResult)(rejected)()
    assert out.is_error is True
    assert out.structured_content["ok"] is False
    assert out.structured_content["error_type"] == "BusinessFailure"
    assert out.structured_content["error"] == "isv.missing-parameter:productTitle"
    # everything the caller needs to debug the rejection survives
    assert out.structured_content["product_id"] == "123"
    assert out.structured_content["filled_fields"] == ["icbuCatProp"]


def test_successful_publish_is_not_mistaken_for_a_business_failure():
    for biz in (True, "true", None):
        def good(biz=biz) -> PublishResult:
            return PublishResult(product_id="123", biz_success=biz)

        out = server.tool_errors(PublishResult)(good)()
        assert isinstance(out, PublishResult), biz
        assert out.ok is True


# ── the verdict field differs per API, so each model declares its own ───────
def test_a_refused_video_relation_is_a_business_failure():
    """The video APIs report their verdict in `success`, not `biz_success`; a
    checker that only knew about `biz_success` reported a refused relation as a
    successful call."""
    def refused() -> VideoRelateResult:
        return VideoRelateResult(success=False, video_id="v1", product_id="p1",
                                 msg_code="VIDEO_NOT_FOUND", msg_info="video does not exist")

    out = server.tool_errors(VideoRelateResult)(refused)()
    assert out.is_error is True
    assert out.structured_content["error_type"] == "BusinessFailure"
    assert out.structured_content["error"] == "video does not exist"
    assert out.structured_content["msg_code"] == "VIDEO_NOT_FOUND"   # payload preserved


def test_a_successful_video_relation_is_not_flagged():
    for verdict in (True, None):
        def ok(verdict=verdict) -> VideoRelateResult:
            return VideoRelateResult(success=verdict, video_id="v1")

        out = server.tool_errors(VideoRelateResult)(ok)()
        assert isinstance(out, VideoRelateResult), verdict


def test_a_status_answer_of_false_is_not_a_failure():
    """`authorized: false` answers the question the tool was asked. Sniffing for
    a boolean named like an outcome would turn every unauthorized check into an
    error — which is why the field is declared, not guessed."""
    def not_authorized() -> AuthStatusResult:
        return AuthStatusResult(authorized=False, expired=True)

    out = server.tool_errors(AuthStatusResult)(not_authorized)()
    assert isinstance(out, AuthStatusResult)
    assert out.ok is True


def test_every_model_with_a_verdict_field_declares_it():
    """A result that carries the platform's verdict must name it, or a failure
    there is invisible. Models that only report state declare nothing."""
    import alibaba_seller_mcp.models as m

    verdict_like = {"biz_success", "success"}
    for name in dir(m):
        cls = getattr(m, name)
        if not (isinstance(cls, type) and issubclass(cls, Result) and cls is not Result):
            continue
        carried = verdict_like & set(cls.model_fields)
        if carried:
            assert cls.OUTCOME_FIELD in carried, (
                f"{name} has {sorted(carried)} but OUTCOME_FIELD={cls.OUTCOME_FIELD!r}; "
                "a falsy verdict there would be reported as success"
            )


def test_the_declared_field_is_a_real_field():
    import alibaba_seller_mcp.models as m

    for name in dir(m):
        cls = getattr(m, name)
        if isinstance(cls, type) and issubclass(cls, Result) and cls.OUTCOME_FIELD:
            assert cls.OUTCOME_FIELD in cls.model_fields, f"{name}.OUTCOME_FIELD is not a field"
