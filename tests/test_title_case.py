from alibaba_seller_mcp.text_format import (
    TITLE_MAX_LEN,
    clean_title,
    enforce_length,
    normalize_title,
    title_case,
    validate_title,
)


def test_major_words_capitalized_small_words_lower():
    out = title_case("insect killer for the home and garden with laser")
    # first word cap; major words cap; small words (for/the/and/with) lower
    assert out == "Insect Killer for the Home and Garden with Laser"


def test_first_word_small_is_capitalized():
    assert title_case("for home use").split()[0] == "For"


def test_long_prepositions_capitalized():
    # >=4 letter prepositions/conjunctions are major words
    assert title_case("made from steel over time") == "Made From Steel Over Time"


def test_brand_model_acronym_preserved():
    out = title_case("c01b salt blaster with USB and abs body", brands=["C01B"])
    assert "C01B" in out             # brand preserved via brands list
    assert "USB" in out              # acronym preserved
    # 'abs' is lowercase in input -> treated as normal word -> capitalized
    assert "Abs" in out or "ABS" in out


def test_digits_token_preserved():
    assert "RSS001B" in title_case("model RSS001B pest control")


def test_clean_title_strips_disallowed_keeps_allowed():
    out = clean_title("Salt Gun @ Home! (Best) $9 ~ Bug/Killer, Mosquito & Fly - Control.")
    # disallowed removed: @ ! ( ) $ ~ ; allowed kept: / , & - .
    for bad in "@!(){}$^~？！":
        assert bad not in out
    for good in ["/", ",", "&", "-", "."]:
        assert good in out
    assert "  " not in out  # whitespace collapsed


def test_enforce_length_word_boundary():
    long = "word " * 40  # 200 chars
    out = enforce_length(long.strip())
    assert len(out) <= TITLE_MAX_LEN
    assert not out.endswith("wor")  # trimmed at a word boundary, not mid-word


def test_normalize_title_full_pipeline():
    raw = "c01b salt blaster @ insect killer!! for the home & garden ~ mosquito control"
    out = normalize_title(raw, brands=["C01B"])
    assert out.startswith("C01B ")
    assert "@" not in out and "!" not in out and "~" not in out
    assert "&" in out
    assert len(out) <= TITLE_MAX_LEN
    assert " for the " in out  # small words lowercased


def test_validate_title_reports_issues():
    assert validate_title("Good Title with Salt Gun & Laser") == []
    issues = validate_title("Bad @ Title!" + " x" * 80)
    assert any("disallowed" in i for i in issues)
    assert any("exceeds" in i for i in issues)
