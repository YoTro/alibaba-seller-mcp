"""Two guardrails against quiet waste: typos in a brief, and runaway AI calls."""

from types import SimpleNamespace

import pytest

from alibaba_seller_mcp.ai.base import ClaudeGenerator
from alibaba_seller_mcp.alibaba.schema import parse_item_schema
from alibaba_seller_mcp.config import Config
from alibaba_seller_mcp.listing import PublishFromBrief
from alibaba_seller_mcp.storage import UsageStore
from alibaba_seller_mcp.usage.tracker import UsageTracker

SCHEMA = """<itemSchema>
 <field id="productTitle" type="input"/><field id="scPrice" type="singleCheck"/>
 <field id="icbuCatProp" type="complex"><fields>
   <field id="p-1" name="place of origin" type="singleCheck">
     <options><option displayName="China" value="100"/></options></field>
   <field id="p-9" name="Pest Control Type" type="singleCheck">
     <options><option displayName="Mosquito Killer" value="12"/></options></field>
 </fields></field>
 <field id="saleProp" type="complex"><fields>
   <field id="p-2" name="Color" type="multiCheck">
     <options><option displayName="White" value="5"/></options></field>
 </fields></field></itemSchema>"""


class _Stub:
    def get_schema_fields(self, *a, **k):
        return parse_item_schema(SCHEMA)

    def publish_product(self, m, t, *, draft, schema_fields=None):
        return {"product_id": "1", "biz_success": True, "missing_required": [], "response": {}}


def _run(**extra):
    brief = {"brief": "x", "category_id": 1, "ai": False, "content": {"title": "T"},
             "price": {"tiers": [[1, 9.9]]}, "lead_time": [[100, 7]],
             "images": {"main": [{"file_id": "1", "url": "u"}] * 4}, **extra}
    return PublishFromBrief(_Stub()).run(brief, token="t", draft=True).warnings


# ── brief typos ─────────────────────────────────────────────────────────────
def test_unknown_top_level_keys_are_reported_with_a_suggestion():
    warns = " | ".join(_run(prices={"tiers": [[1, 1]]}, lead_tiem=[[1, 1]], detial_spec={}))
    assert "prices (did you mean 'price'?)" in warns
    assert "lead_tiem (did you mean 'lead_time'?)" in warns
    assert "detial_spec (did you mean 'detail_spec'?)" in warns


def test_facts_keys_matching_no_attribute_are_reported():
    """The real case: `Type` looks right but this category calls it
    `Pest Control Type`, so the value was dropped and the draft published anyway."""
    warns = " | ".join(_run(facts={"place_of_origin": "China", "Type": "Mosquito Killer",
                                   "Colour": "White"}))
    assert "Type" in warns
    assert "Colour (did you mean 'Color'?)" in warns
    assert "place_of_origin" not in warns          # an alias, not a typo


def test_a_clean_brief_gets_no_key_warnings():
    warns = " | ".join(_run(facts={"place_of_origin": "China", "Pest Control Type": "Mosquito Killer",
                                   "Color": "White"}))
    assert "unknown brief key" not in warns
    assert "matching no attribute" not in warns


def test_sales_property_names_count_as_known_facts():
    """Color lives under saleProp, not icbuCatProp, but putting it in `facts` is
    supported (the flow moves it) — so it must not be flagged as a typo."""
    assert not [w for w in _run(facts={"Color": "White"}) if "matching no attribute" in w]


# ── AI token budget ─────────────────────────────────────────────────────────
class _FakeMessages:
    def __init__(self):
        self.calls = 0

    def create(self, **kw):
        self.calls += 1
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text="{}")],
            usage=SimpleNamespace(input_tokens=400, output_tokens=100,
                                  cache_read_input_tokens=0, cache_creation_input_tokens=0),
        )


def _gen(tmp_path, budget):
    cfg = Config(app_key="k", app_secret="s", state_dir=tmp_path, ai_token_budget=budget)
    tracker = UsageTracker(UsageStore(tmp_path / "usage.jsonl"))
    msgs = _FakeMessages()
    return ClaudeGenerator(cfg, tracker, client=SimpleNamespace(messages=msgs)), msgs, tracker


def _call(gen):
    return gen._complete(system="s", messages=[{"role": "user", "content": "x"}],
                         max_tokens=10, label="test")


def test_a_runaway_loop_stops_at_the_budget(tmp_path):
    gen, msgs, tracker = _gen(tmp_path, budget=1200)      # 500 tokens per call
    for _ in range(3):                                     # 500, 1000, 1500 -> over
        _call(gen)
    with pytest.raises(RuntimeError, match="token budget"):
        _call(gen)
    assert msgs.calls == 3
    assert tracker.session_tokens == 1500


def test_the_budget_is_soft_not_a_hard_ceiling(tmp_path):
    """Checked before each call, so the last call is allowed to finish rather
    than aborting work halfway. Documented, not accidental."""
    gen, _, tracker = _gen(tmp_path, budget=100)
    _call(gen)                                             # first call always runs
    assert tracker.session_tokens == 500 > 100
    with pytest.raises(RuntimeError):
        _call(gen)


def test_no_budget_means_no_limit(tmp_path):
    gen, msgs, _ = _gen(tmp_path, budget=0)
    for _ in range(5):
        _call(gen)
    assert msgs.calls == 5


def test_the_budget_message_says_how_to_proceed(tmp_path):
    gen, _, _ = _gen(tmp_path, budget=1)
    _call(gen)
    with pytest.raises(RuntimeError) as e:
        _call(gen)
    assert "ALIBABA_MCP_AI_TOKEN_BUDGET" in str(e.value)
    assert "ai: false" in str(e.value)


def test_a_bad_budget_value_is_rejected_at_startup(monkeypatch):
    from alibaba_seller_mcp.config import load_config

    monkeypatch.setenv("ALIBABA_MCP_AI_TOKEN_BUDGET", "lots")
    monkeypatch.setenv("ALIBABA_APP_KEY", "k")
    monkeypatch.setenv("ALIBABA_APP_SECRET", "s")
    with pytest.raises(RuntimeError, match="whole number of tokens"):
        load_config()
