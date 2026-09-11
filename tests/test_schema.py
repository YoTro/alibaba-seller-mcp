import xml.etree.ElementTree as ET

from alibaba_seller_mcp.alibaba.schema import (
    build_value_xml,
    describe_schema,
    parse_item_schema,
    required_field_ids,
)

SAMPLE = """
<itemSchema>
  <field id="productTitle" name="Product name" type="input">
    <rules><rule name="requiredRule" value="true"/><rule name="maxLengthRule" value="128"/></rules>
  </field>
  <field id="scPrice" name="Price setting" type="singleCheck">
    <rules><rule name="requiredRule" value="true"/></rules>
    <options><option displayName="Tiered" value="1"/><option displayName="SKU" value="3"/></options>
  </field>
  <field id="scImages" name="Product images" type="complex">
    <fields>
      <field id="scImages_0" type="input"/>
      <field id="scImages_1" type="input"/>
    </fields>
  </field>
  <field id="paymentMethod" type="complex">
    <fields>
      <field id="predefined_method" type="multiCheck"/>
      <field id="self_defined_0" type="input"/>
    </fields>
  </field>
</itemSchema>
"""


def test_parse_and_required():
    fields = parse_item_schema(SAMPLE)
    assert required_field_ids(fields) == ["productTitle", "scPrice"]
    by_id = {f.id: f for f in fields}
    assert by_id["scPrice"].options == [
        {"value": "1", "displayName": "Tiered"},
        {"value": "3", "displayName": "SKU"},
    ]
    assert [c.id for c in by_id["scImages"].children] == ["scImages_0", "scImages_1"]
    assert by_id["productTitle"].max_length == 128


def test_build_value_xml_matches_doc_shape():
    fields = parse_item_schema(SAMPLE)
    values = {
        "productTitle": "My Title",
        "scPrice": "1",
        "scImages": {"scImages_0": "http://img/1.jpg", "scImages_1": "http://img/2.jpg"},
        "paymentMethod": {"predefined_method": ["L/C", "D/A"], "self_defined_0": "dddd"},
    }
    xml = build_value_xml(fields, values)
    root = ET.fromstring(xml)
    assert root.tag == "itemSchema"

    title = root.find("./field[@id='productTitle']")
    assert title.get("type") == "input"
    assert title.find("value").text == "My Title"

    # complex -> complex-value with nested input fields
    sc0 = root.find("./field[@id='scImages']/complex-value/field[@id='scImages_0']/value")
    assert sc0.text == "http://img/1.jpg"

    # multiCheck -> <values><value>..</value></values>
    vals = root.findall("./field[@id='paymentMethod']/complex-value/field[@id='predefined_method']/values/value")
    assert [v.text for v in vals] == ["L/C", "D/A"]


def test_build_value_xml_attributed_value():
    # image fields need <value fileId="…">url</value>
    fields = parse_item_schema(SAMPLE)
    values = {
        "scImages": {"scImages_0": {"__value__": "http://img/1.jpg", "__attrs__": {"fileId": "999"}}}
    }
    xml = build_value_xml(fields, values)
    root = ET.fromstring(xml)
    val = root.find("./field[@id='scImages']/complex-value/field[@id='scImages_0']/value")
    assert val.get("fileId") == "999"
    assert val.text == "http://img/1.jpg"


def test_multicomplex_uses_plural_complex_values():
    # multiComplex items are wrapped in <complex-values> (plural), one per item;
    # complex uses singular <complex-value>. (Confirmed from schema.render doc.)
    schema = (
        '<itemSchema><field id="faqs" type="multiComplex"><fields>'
        '<field id="question" type="input"/><field id="answers" type="input"/>'
        "</fields></field></itemSchema>"
    )
    fields = parse_item_schema(schema)
    xml = build_value_xml(fields, {"faqs": [{"question": "Q1", "answers": "A1"}, {"question": "Q2", "answers": "A2"}]})
    root = ET.fromstring(xml)
    items = root.findall("./field[@id='faqs']/complex-values")
    assert len(items) == 2
    assert items[0].find("./field[@id='question']/value").text == "Q1"
    # no singular complex-value under a multiComplex field
    assert root.find("./field[@id='faqs']/complex-value") is None


def test_describe_schema_drops_labels():
    xml = '<itemSchema><field id="info" type="label"/><field id="t" type="input"/></itemSchema>'
    desc = describe_schema(xml)
    ids = [f["id"] for f in desc["fields"]]
    assert "info" not in ids and "t" in ids
