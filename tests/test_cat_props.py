"""icbuCatProp auto-fill from schema (options/required) + manifest product_attributes."""

from alibaba_seller_mcp.alibaba.values import (
    _build_cat_props, _build_sale_props, build_ladder_period, missing_required, missing_required_cat_props,
)
from alibaba_seller_mcp.alibaba.schema import build_value_xml, parse_item_schema

SALE_SCHEMA = """
<itemSchema>
  <field id="saleProp" name="Sales Property" type="complex"><fields>
    <field id="p-191288010" name="color" type="multiCheck">
      <rules><rule name="requiredRule" value="true"/><rule name="valueAttributeRule" value="inputValue"/></rules>
      <options><option displayName="Orange" value="3558409"/><option displayName="Black" value="3327837"/></options>
    </field>
    <field id="p-191286172" name="Size" type="multiCheck"><rules/>
      <options><option displayName="one size" value="4348586"/></options>
    </field>
  </fields></field>
  <field id="ladderPeriod" name="Shipping" type="complex"><fields>
    <field id="ladderPeriod_0" type="complex"><fields>
      <field id="quantity" type="input"/><field id="day" type="input"/></fields></field>
    <field id="ladderPeriod_1" type="complex"><fields>
      <field id="quantity" type="input"/><field id="day" type="input"/></fields></field>
  </fields></field>
</itemSchema>
"""


def _sale_fields():
    return parse_item_schema(SALE_SCHEMA)


def test_build_sale_props_option_and_custom_values():
    sale = next(f for f in _sale_fields() if f.id == "saleProp")
    out = _build_sale_props(sale, {"Color": ["orange", "Coral"], "size": "one size"})
    color = out["p-191288010"]
    assert color[0] == {"__value__": "3558409", "__attrs__": {"inputValue": "Orange"}}   # option → code + display name
    assert color[1]["__attrs__"]["inputValue"] == "Coral" and int(color[1]["__value__"]) < 0  # custom → negative id
    assert out["p-191286172"] == [{"__value__": "4348586", "__attrs__": {"inputValue": "one size"}}]


def test_custom_sale_value_avoids_the_other_option_id():
    schema = SALE_SCHEMA.replace(
        '<option displayName="Black" value="3327837"/>',
        '<option displayName="Black" value="3327837"/><option displayName="other" value="-1"/>',
    )
    sale = next(f for f in parse_item_schema(schema) if f.id == "saleProp")
    out = _build_sale_props(sale, {"Color": ["Coral", "Teal"]})
    codes = [v["__value__"] for v in out["p-191288010"]]
    names = [v["__attrs__"]["inputValue"] for v in out["p-191288010"]]
    assert "-1" not in codes and len(set(codes)) == 2         # skips the id the "other" option owns
    assert names == ["Coral", "Teal"]                          # the seller's text, not "other"


def test_sale_props_and_ladder_period_serialize():
    fields = _sale_fields()
    values = {
        "saleProp": _build_sale_props(next(f for f in fields if f.id == "saleProp"), {"Color": "Orange"}),
        "ladderPeriod": build_ladder_period([[1000, 15], [100, 7]]),
    }
    xml = build_value_xml(fields, values)
    assert '<field id="p-191288010" type="multiCheck"><values><value inputValue="Orange">3558409</value></values>' in xml
    # tiers sorted by quantity, nested complex → quantity/day inputs
    assert xml.index('<value>100</value>') < xml.index('<value>1000</value>')
    assert '<field id="day" type="input"><value>7</value>' in xml


def test_build_ladder_period_defaults_and_caps():
    default = build_ladder_period(None)
    assert default == {"ladderPeriod_0": {"quantity": 1000, "day": 15}}
    four = build_ladder_period([[10, 3], [20, 5], [30, 7], [40, 9]])
    assert list(four) == ["ladderPeriod_0", "ladderPeriod_1", "ladderPeriod_2"]


def test_missing_required_includes_sale_props_and_ladder_period():
    fields = _sale_fields()
    assert missing_required(fields, {}) == ["saleProp: color", "ladderPeriod (lead_time)"]
    filled = {"saleProp": {"p-191288010": []}, "ladderPeriod": build_ladder_period(None)}
    assert missing_required(fields, filled) == []

# A minimal icbuCatProp schema: a required single-select (with options), a required
# multi-select (with options), and an optional free-text input.
ICBU_SCHEMA = """
<itemSchema>
  <field id="icbuCatProp" name="Product feature" type="complex"><fields>
    <field id="p-1" name="Place of Origin" type="singleCheck">
      <rules><rule name="requiredRule" value="true"/></rules>
      <options><option displayName="China" value="100"/><option displayName="Vietnam" value="200"/></options>
    </field>
    <field id="p-20662" name="Feature" type="multiCheck">
      <rules><rule name="requiredRule" value="true"/></rules>
      <options><option displayName="Eco-Friendly" value="9"/><option displayName="Durable" value="10"/></options>
    </field>
    <field id="p-3" name="Model Number" type="input"><rules/></field>
  </fields></field>
</itemSchema>
"""


def _icbu():
    return next(f for f in parse_item_schema(ICBU_SCHEMA) if f.id == "icbuCatProp")


def test_build_cat_props_by_name_and_id_resolves_options():
    attrs = {
        "Place of Origin": "China",            # by name, displayName -> value 100
        "20662": ["Eco-Friendly", "Durable"],  # by bare attr id, multi -> [9, 10]
        "p-3": "C01B",                          # by field id, free text
    }
    out = _build_cat_props(_icbu(), attrs)
    assert out["p-1"] == "100"
    assert out["p-20662"] == ["9", "10"]
    # free text rides in inputValue with a negative placeholder value
    assert out["p-3"] == {"__value__": "-1", "__attrs__": {"inputValue": "C01B"}}


def test_build_cat_props_passes_through_unknown_option_value():
    # a value already given as the option code is kept; unknown text passes through
    out = _build_cat_props(_icbu(), {"Place of Origin": "200"})
    assert out["p-1"] == "200"


def test_missing_required_reports_unfilled():
    fields = parse_item_schema(ICBU_SCHEMA)
    # nothing filled -> both required attrs missing
    assert set(missing_required_cat_props(fields, {})) == {"Place of Origin", "Feature"}
    # fill one -> only the other remains
    filled = {"icbuCatProp": {"p-1": "100"}}
    assert missing_required_cat_props(fields, filled) == ["Feature"]
