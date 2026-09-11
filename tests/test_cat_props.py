"""icbuCatProp auto-fill from schema (options/required) + manifest product_attributes."""

from alibaba_seller_mcp.alibaba.products import _build_cat_props, missing_required_cat_props
from alibaba_seller_mcp.alibaba.schema import parse_item_schema

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
    assert out["p-3"] == "C01B"


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
