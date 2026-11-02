from __future__ import annotations

import pytest

from repo_orchestrator import extract
from repo_orchestrator.extract import ExtractionError
from repo_orchestrator.models import Cause

JS = """
// comment with { brace and 'quote
export const VERSION = 3; // trailing
export function build() {
  const noise = { not: 'this' };
  return {
    schema_version: VERSION, // inline, comment
    "quoted-key": `tpl ${'}'} , still`,
    window: { from: 1, to: 2 },
    status,
    /* block, comment */ list: [1, 2, { a: 1 }],
  };
}
"""


def test_js_object_keys_reads_top_level_keys_only_and_survives_strings_comments_and_nesting():
    ex = extract.js_object_keys(JS, r"export function build", r"return\s*\{")
    assert ex.value == ("schema_version", "quoted-key", "window", "status", "list")
    assert ex.quote.startswith("{") and ex.quote.endswith("}") and ex.line_start < ex.line_end


@pytest.mark.parametrize(
    ("text", "cause"),
    [
        ("function f() { return { ...base, a: 1 } }", Cause.UNSUPPORTED_SYNTAX),
        ("function f() { return { [k]: 1 } }", Cause.UNSUPPORTED_SYNTAX),
        ("function f() { return { m() { return 1 } } }", Cause.UNSUPPORTED_SYNTAX),
        ("function f() { return { a: 'unterminated } }", Cause.PARSE_ERROR),
        ("function f() { return { a: (1 } }", Cause.PARSE_ERROR),
        ("function g() {}", Cause.LOCATOR_NO_MATCH),
    ],
)
def test_js_object_keys_fails_explicitly_rather_than_guessing(text, cause):
    with pytest.raises(ExtractionError) as err:
        extract.js_object_keys(text, r"function [fg]", r"return\s*\{")
    assert err.value.cause == cause


def test_js_anchor_must_match_exactly_once():
    with pytest.raises(ExtractionError) as err:
        extract.js_object_keys("function f(){return{a:1}}\nfunction f(){return{b:1}}", r"function f", r"return\s*\{")
    assert err.value.cause == Cause.LOCATOR_AMBIGUOUS


def test_js_object_value_returns_the_raw_expression():
    assert extract.js_object_value(JS, r"export function build", r"return\s*\{", "schema_version").value == "VERSION"
    with pytest.raises(ExtractionError):
        extract.js_object_value(JS, r"export function build", r"return\s*\{", "nope")


def test_js_const_int_and_string():
    assert extract.js_const_int(JS, "VERSION").value == 3
    assert extract.js_const_string("export const M = 'llava:7b' // x", "M").value == "llava:7b"
    assert extract.js_const_string('const M = "a:b"', "M").value == "a:b"
    for text in ("const X = 1;\nconst X = 2;", "const X = compute();", "let X = 1;"):
        with pytest.raises(ExtractionError):
            extract.js_const_int(text, "X")


def test_js_int_literal_only():
    assert extract.int_of_js(" 7 ") == 7
    with pytest.raises(ExtractionError):
        extract.int_of_js("'7'")


def test_yaml_path_quote_is_a_verbatim_slice_and_lines_are_exact():
    text = "a:\n  b:\n    c:\n      - x\n      - y\n    d: 1\n"
    doc = extract.yaml_single(text)
    value, quote, start, end = doc.get("a.b.c")
    assert value == ["x", "y"] and (start, end) == (3, 5) and quote in text
    with pytest.raises(ExtractionError) as err:
        doc.get("a.zzz")
    assert err.value.cause == Cause.LOCATOR_NO_MATCH


def test_yaml_find_keys_walks_nested_documents_in_source_order():
    docs = extract.yaml_docs("a:\n  k: '1'\n---\nlist:\n  - {k: '2'}\n")
    found = [v for d in docs for v, *_ in d.find_keys("k")]
    assert found == ["1", "2"]


def test_yaml_is_loaded_safely_and_bounded():
    with pytest.raises(ExtractionError) as err:
        extract.yaml_single("a: !!python/object/apply:os.system ['true']").get("a")
    assert err.value.cause == Cause.PARSE_ERROR
    bomb = "a: &a [x,x,x,x,x,x,x,x,x]\n" + "".join(
        f"{chr(98 + i)}: &{chr(98 + i)} [" + ",".join([f"*{chr(97 + i)}"] * 9) + "]\n" for i in range(9)
    )
    with pytest.raises(ExtractionError) as bomb_err:
        extract.yaml_single(bomb)
    assert "alias expansion" in bomb_err.value.detail
    with pytest.raises(ExtractionError):
        extract.yaml_single("a: [unclosed")
    with pytest.raises(ExtractionError):
        extract.yaml_single("a: 1\n---\nb: 2\n")


def test_json_loads_rejects_garbage_and_deep_nesting():
    assert extract.json_loads('{"a": 1}') == {"a": 1}
    for bad in ("{", "[" * 100000):
        with pytest.raises(ExtractionError):
            extract.json_loads(bad)


def test_decode_rejects_invalid_utf8_and_strips_bom():
    assert extract.decode(b"\xef\xbb\xbfabc") == "abc"
    with pytest.raises(ExtractionError):
        extract.decode(b"\xff\xfe\x00")


def test_md_line_value_requires_exactly_one_match():
    text = "| [`a`](x) | 17 widgets, 12 extracted |\n| [`b`](y) | 3 widgets |\n"
    assert extract.md_line_value(text, r"^\| \[`a`\]", r"(\d+) widgets").value == "17"
    with pytest.raises(ExtractionError) as none:
        extract.md_line_value(text, r"^\| \[`c`\]", r"(\d+) widgets")
    with pytest.raises(ExtractionError) as many:
        extract.md_line_value(text, r"widgets", r"(\d+) widgets")
    assert (none.value.cause, many.value.cause) == (Cause.LOCATOR_NO_MATCH, Cause.LOCATOR_AMBIGUOUS)
    assert extract.to_int("2,400") == 2400
    with pytest.raises(ExtractionError):
        extract.to_int("12a")


def test_md_h3_under_h2_ignores_fenced_code_and_stops_at_the_next_h2():
    text = "## Other\n### no\n## Target\n### one\n```\n### fenced\n```\n### two\n#### deep\n## Next\n### three\n"
    ex = extract.md_h3_under_h2(text, "Target")
    assert ex.value == 2 and ex.detail["headings"] == ["one", "two"]
    with pytest.raises(ExtractionError):
        extract.md_h3_under_h2(text, "Missing")


def test_md_table_finds_the_first_table_under_a_heading():
    text = "## Intro\n\n| A | B |\n|---|---|\n| x | y |\n\n## Current adopters\n\ntext\n\n| Repo | Skills |\n|---|---|\n| `r` | `s1`, `s2` |\n| `q` | — |\n"
    ex = extract.md_table(text, "Current adopters")
    assert ex.value == [{"Repo": "`r`", "Skills": "`s1`, `s2`"}, {"Repo": "`q`", "Skills": "—"}]
    with pytest.raises(ExtractionError):
        extract.md_table("## H\n\nno table\n## Next\n| a |\n|---|\n| b |\n", "H")


def test_github_links_extracts_blob_and_tree_links_and_unquotes():
    text = "[a](https://github.com/o/r/blob/main/docs/a%20b.md#x) and https://github.com/o/r/tree/abc/dir/ then https://github.com/o/other/blob/main/z"
    links = extract.github_links(text, "o/r")
    assert [(link.kind, link.ref, link.path, link.line) for link in links] == [
        ("blob", "main", "docs/a b.md", 1),
        ("tree", "abc", "dir", 1),
    ]


def test_commented_out_constants_and_literals_are_not_live_code():
    commented = "/* export const V = 1; */\n// export const V = 2;\nexport const V = 3;\n"
    assert extract.js_const_int(commented, "V").value == 3
    only_comment = "/*\nexport const V = 1;\n*/\nexport const V = compute();\n"
    with pytest.raises(ExtractionError) as err:
        extract.js_const_int(only_comment, "V")
    assert err.value.cause == Cause.LOCATOR_NO_MATCH
    in_string = "const URL = 'http://x'; // note\nexport const V = 4;"
    assert extract.js_const_int(in_string, "V").value == 4
    ex = extract.js_const_int("// c\nexport const V = 5; // t", "V")
    assert ex.quote == "export const V = 5; // t" and ex.line_start == 2
    assert extract.js_const_string("// const M = 'old'\nconst M = 'new'", "M").value == "new"
    mask = extract.mask_comments("a // b\nc /* d\ne */ f")
    assert mask.count("\n") == 2 and len(mask) == len("a // b\nc /* d\ne */ f") and "b" not in mask
    stale_anchor = "// export function build() { return { old: 1 } }\nexport function build() { return { live: 1 } }"
    assert extract.js_object_keys(stale_anchor, r"export function build", r"return\s*\{").value == ("live",)


@pytest.mark.parametrize(
    "text",
    [
        '{"a": 1,\n "target": {"x": [1, 2]},\n "z": 2}',
        '{"a":1,"target":{"x":[1,2]},"z":2}',
        '{"target":{"x":[1,2]}}',
        '{ "a": {"target": 0}, "target" :\n  {"x": [1, 2]} }',
    ],
)
def test_json_top_level_key_is_independent_of_formatting_and_ignores_nested_keys(text):
    ex = extract.json_top_level_key(text, "target")
    assert ex.quote in text and ex.quote.replace(" ", "").replace("\n", "").startswith('"target":{"x":[1,2]}')
    assert 1 <= ex.line_start <= ex.line_end


def test_json_top_level_key_scalars_and_misses():
    assert extract.json_top_level_key('{"n":12,"s":"a\\"b","t":true}', "s").quote == '"s":"a\\"b"'
    assert extract.json_top_level_key('{"n":12,"t":true}', "t").quote == '"t":true'
    with pytest.raises(ExtractionError):
        extract.json_top_level_key('{"a": {"target": 1}}', "target")
