import json

from alibaba_seller_mcp.files.readers import read_prices
from alibaba_seller_mcp.storage import UsageStore
from alibaba_seller_mcp.usage.tracker import UsageTracker, estimate_cost


def test_estimate_cost_opus5():
    # 1M input @ $5 + 1M output @ $25 = $30
    assert estimate_cost("claude-opus-5", 1_000_000, 1_000_000) == 30.0
    # cache read bills at 0.1x input
    assert estimate_cost("claude-opus-5", 0, 0, cache_read_tokens=1_000_000) == 0.5
    assert estimate_cost("unknown-model", 100, 100) is None


def test_tracker_records_and_aggregates(tmp_path):
    tracker = UsageTracker(UsageStore(tmp_path / "usage.jsonl"))
    tracker.record("claude-opus-5", {"input_tokens": 1000, "output_tokens": 500}, label="social_content")
    tracker.record("claude-opus-5", {"input_tokens": 2000, "output_tokens": 800}, label="social_content")

    stats = tracker.stats(group_by="model")
    assert stats["call_count"] == 2
    assert stats["totals"]["input_tokens"] == 3000
    assert stats["totals"]["output_tokens"] == 1300
    assert stats["totals"]["estimated_cost_usd"] > 0
    assert "claude-opus-5" in stats["groups"]


def test_read_prices_csv(tmp_path):
    p = tmp_path / "prices.csv"
    p.write_text("sku,price,MOQ,currency\nA-1,12.5,100,USD\nA-2,9.99,50,USD\n", encoding="utf-8")
    rows = read_prices(str(p))
    assert len(rows) == 2
    assert rows[0]["sku"] == "A-1"
    assert rows[0]["price"] == 12.5
    assert rows[0]["min_order_quantity"] == "100"
    assert rows[0]["currency"] == "USD"


def test_read_prices_json(tmp_path):
    p = tmp_path / "prices.json"
    p.write_text(json.dumps([{"sku": "B-1", "amount": 20, "qty": 5}]), encoding="utf-8")
    rows = read_prices(str(p))
    assert rows[0]["sku"] == "B-1"
    assert rows[0]["price"] == 20.0
    assert rows[0]["quantity"] == 5
