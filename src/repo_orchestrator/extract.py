"""Narrow, deterministic extractors over *untrusted text* (docs/architecture.md → Security boundaries).

Nothing here executes, imports or evaluates analyzed content. Every extractor handles one documented format and
fails with an explicit :class:`ExtractionError` (a stable ``cause`` code) rather than guessing: a policy turns
that into ``cannot_evaluate``, never into a pass.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import unquote

import yaml

from .models import Cause

EXTRACTOR_VERSION = "1"
MAX_QUOTE = 800
MAX_YAML_EXPANDED_NODES = 200_000


class ExtractionError(Exception):
    def __init__(self, cause: str, detail: str) -> None:
        super().__init__(f"{cause}: {detail}")
        self.cause = cause
        self.detail = detail


@dataclass(frozen=True)
class Extracted:
    value: Any
    quote: str
    line_start: int
    line_end: int
    extractor: str
    detail: dict[str, Any] = field(default_factory=dict)


def decode(data: bytes) -> str:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ExtractionError(Cause.PARSE_ERROR, f"not valid UTF-8 ({exc.reason})") from exc
    return text.removeprefix("﻿")


def _line_at(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1


def _quote(text: str) -> str:
    return text[:MAX_QUOTE]


def _ext(name: str) -> str:
    return f"{name}@{EXTRACTOR_VERSION}"


# --------------------------------------------------------------------------------------------------
# JavaScript (read as text; never executed)
# --------------------------------------------------------------------------------------------------


def _skip_string(t: str, i: int) -> int:
    quote = t[i]
    i += 1
    while i < len(t):
        c = t[i]
        if c == "\\":
            i += 2
            continue
        if quote == "`" and c == "$" and t[i + 1 : i + 2] == "{":
            i = _skip_balanced(t, i + 1)
            continue
        if c == quote:
            return i + 1
        i += 1
    raise ExtractionError(Cause.PARSE_ERROR, "unterminated string literal")


_PAIRS = {"{": "}", "(": ")", "[": "]"}


def _skip_trivia(t: str, i: int) -> int | None:
    """If a comment starts at i, return the index after it."""
    if t.startswith("//", i):
        j = t.find("\n", i)
        return len(t) if j < 0 else j
    if t.startswith("/*", i):
        j = t.find("*/", i + 2)
        if j < 0:
            raise ExtractionError(Cause.PARSE_ERROR, "unterminated comment")
        return j + 2
    return None


def _skip_balanced(t: str, i: int) -> int:
    stack = [_PAIRS[t[i]]]
    i += 1
    while i < len(t) and stack:
        c = t[i]
        if c in "'\"`":
            i = _skip_string(t, i)
            continue
        if c == "/":
            nxt = _skip_trivia(t, i)
            if nxt is not None:
                i = nxt
                continue
        if c in _PAIRS:
            stack.append(_PAIRS[c])
        elif c in ")]}":
            if c != stack[-1]:
                raise ExtractionError(Cause.PARSE_ERROR, "mismatched brackets")
            stack.pop()
        i += 1
    if stack:
        raise ExtractionError(Cause.PARSE_ERROR, "unbalanced brackets")
    return i


def _top_level_entries(t: str, open_idx: int) -> tuple[list[str], int]:
    """Split the object literal opening at ``open_idx`` into its top-level entries; return them and the end index."""
    end = _skip_balanced(t, open_idx)
    entries: list[str] = []
    start = open_idx + 1
    i = start
    while i < end - 1:
        c = t[i]
        if c in "'\"`":
            i = _skip_string(t, i)
            continue
        if c == "/":
            nxt = _skip_trivia(t, i)
            if nxt is not None:
                i = nxt
                continue
        if c in _PAIRS:
            i = _skip_balanced(t, i)
            continue
        if c == ",":
            entries.append(t[start:i])
            start = i + 1
        i += 1
    entries.append(t[start : end - 1])
    return [e for e in entries if _strip_leading_comments(e).strip()], end


_LEADING_COMMENT = re.compile(r"^\s*(?://[^\n]*(?:\n|$)|/\*.*?\*/)", re.DOTALL)
_ENTRY_KEY = re.compile(
    r"""^(?:(?P<ident>[A-Za-z_$][\w$]*)|'(?P<sq>[^']*)'|"(?P<dq>[^"]*)")\s*(?::(?P<value>.*))?$""", re.DOTALL
)


def _strip_leading_comments(entry: str) -> str:
    while (m := _LEADING_COMMENT.match(entry)) is not None:
        entry = entry[m.end() :]
    return entry


def _entry_key_value(entry: str) -> tuple[str, str]:
    body = _strip_leading_comments(entry).strip()
    if body.startswith("..."):
        raise ExtractionError(Cause.UNSUPPORTED_SYNTAX, "spread in object literal")
    m = _ENTRY_KEY.match(body)
    if m is None:
        raise ExtractionError(Cause.UNSUPPORTED_SYNTAX, f"unsupported object entry: {body[:40]!r}")
    key = m.group("ident") or m.group("sq") or m.group("dq") or ""
    value = m.group("value")
    return key, (value.strip() if value is not None else key)


def _literal(text: str, after: str, literal: str) -> tuple[int, int, str]:
    """Locate the object literal matched by ``literal`` after the single match of ``after``."""
    live = mask_comments(text)
    anchors = list(re.finditer(after, live, re.MULTILINE))
    if len(anchors) != 1:
        raise ExtractionError(
            Cause.LOCATOR_NO_MATCH if not anchors else Cause.LOCATOR_AMBIGUOUS,
            f"anchor {after!r} matched {len(anchors)} times",
        )
    m = re.compile(literal).search(live, anchors[0].end())
    if m is None or not text[: m.end()].endswith("{"):
        raise ExtractionError(Cause.LOCATOR_NO_MATCH, f"object literal {literal!r} not found after anchor")
    open_idx = m.end() - 1
    end = _skip_balanced(text, open_idx)
    return open_idx, end, text[open_idx:end]


def js_object_keys(text: str, after: str, literal: str) -> Extracted:
    open_idx, _, quote = _literal(text, after, literal)
    entries, end = _top_level_entries(text, open_idx)
    keys = tuple(_entry_key_value(e)[0] for e in entries)
    return Extracted(keys, _quote(quote), _line_at(text, open_idx), _line_at(text, end - 1), _ext("js_object_keys"))


def js_object_value(text: str, after: str, literal: str, key: str) -> Extracted:
    open_idx, _, quote = _literal(text, after, literal)
    entries, end = _top_level_entries(text, open_idx)
    pairs = [_entry_key_value(e) for e in entries]
    values = [v for k, v in pairs if k == key]
    if len(values) != 1:
        raise ExtractionError(
            Cause.LOCATOR_NO_MATCH if not values else Cause.LOCATOR_AMBIGUOUS, f"key {key!r} found {len(values)} times"
        )
    return Extracted(
        values[0], _quote(quote), _line_at(text, open_idx), _line_at(text, end - 1), _ext("js_object_value")
    )


def mask_comments(t: str) -> str:
    """Blank out ``//`` and ``/* */`` comments (keeping newlines and every offset) so patterns never match commented code.

    Strings are skipped, so ``"http://x"`` is untouched. Anything the scanner cannot follow is left as it is.
    """
    out = list(t)
    i = 0
    while i < len(t):
        c = t[i]
        if c in "'\"`":
            try:
                i = _skip_string(t, i)
            except ExtractionError:
                i += 1
            continue
        if c == "/":
            try:
                end = _skip_trivia(t, i)
            except ExtractionError:
                end = None
            if end is not None:
                for k in range(i, end):
                    if t[k] != "\n":
                        out[k] = " "
                i = end
                continue
        i += 1
    return "".join(out)


def _const(text: str, name: str, value_pattern: str, label: str) -> Extracted:
    live = mask_comments(text)
    pattern = re.compile(
        rf"^[ \t]*(?:export[ \t]+)?const[ \t]+{re.escape(name)}[ \t]*=[ \t]*{value_pattern}[ \t]*;?[ \t]*(?://.*)?$",
        re.MULTILINE,
    )
    matches = list(pattern.finditer(live))
    if len(matches) != 1:
        raise ExtractionError(
            Cause.LOCATOR_NO_MATCH if not matches else Cause.LOCATOR_AMBIGUOUS,
            f"const {name} ({label}) matched {len(matches)} times",
        )
    m = matches[0]
    line = _line_at(text, m.start())
    return Extracted(m.group("v"), text[m.start() : m.end()].strip(), line, line, _ext(f"js_const_{label}"))


def js_const_int(text: str, name: str) -> Extracted:
    found = _const(text, name, r"(?P<v>\d+)", "int")
    return Extracted(int(found.value), found.quote, found.line_start, found.line_end, found.extractor)


def js_const_string(text: str, name: str) -> Extracted:
    return _const(text, name, r"""(?P<q>['"])(?P<v>[^'"\\\n]*)(?P=q)""", "string")


def int_of_js(raw: str) -> int:
    if not re.fullmatch(r"\d+", raw.strip()):
        raise ExtractionError(Cause.PARSE_ERROR, f"not an integer literal: {raw[:40]!r}")
    return int(raw.strip())


# --------------------------------------------------------------------------------------------------
# YAML (safe composition, bounded alias expansion)
# --------------------------------------------------------------------------------------------------


class YamlDoc:
    """One composed YAML document; node marks give exact line spans for evidence."""

    def __init__(self, text: str, loader: yaml.SafeLoader, root: yaml.Node) -> None:
        self._text = text
        self._lines = text.splitlines()
        self._loader = loader
        self.root = root

    def construct(self, node: yaml.Node) -> Any:
        try:
            return self._loader.construct_object(node, deep=True)
        except yaml.YAMLError as exc:  # e.g. a python/* tag: SafeLoader refuses to build it
            raise ExtractionError(
                Cause.PARSE_ERROR, f"unsafe or invalid YAML value: {str(exc).splitlines()[0][:100]}"
            ) from exc

    def span(self, first: yaml.Node, last: yaml.Node | None = None) -> tuple[str, int, int]:
        start_line = first.start_mark.line
        end_line = max(_last_line(last or first, set()), start_line)
        quote = "\n".join(self._lines[start_line : end_line + 1])
        return _quote(quote), start_line + 1, end_line + 1

    def get(self, dotted: str) -> tuple[Any, str, int, int]:
        node: yaml.Node = self.root
        key_node: yaml.Node | None = None
        for part in dotted.split("."):
            if not isinstance(node, yaml.MappingNode):
                raise ExtractionError(Cause.LOCATOR_NO_MATCH, f"{part!r}: parent is not a mapping in {dotted!r}")
            hits = [(k, v) for k, v in node.value if isinstance(k, yaml.ScalarNode) and k.value == part]
            if len(hits) != 1:
                raise ExtractionError(
                    Cause.LOCATOR_NO_MATCH if not hits else Cause.LOCATOR_AMBIGUOUS,
                    f"key {part!r} found {len(hits)} times in {dotted!r}",
                )
            key_node, node = hits[0]
        assert key_node is not None
        quote, start, end = self.span(key_node, node)
        return self.construct(node), quote, start, end

    def find_keys(self, name: str) -> Iterator[tuple[Any, str, int, int]]:
        """Every value stored under a mapping key ``name``, in source order.

        An anchored mapping reused through an alias is visited at **each** use site (an aliased claim is a claim per use);
        cycles are cut by an ancestor guard, and the total work is bounded by the expansion limit checked at load.
        """
        found: list[tuple[int, int, Any, str, int, int]] = []
        stack: list[tuple[yaml.Node, frozenset[int]]] = [(self.root, frozenset())]
        while stack:
            node, ancestors = stack.pop()
            if id(node) in ancestors:
                continue
            below = ancestors | {id(node)}
            if isinstance(node, yaml.MappingNode):
                for k, v in effective_pairs(node):
                    if isinstance(k, yaml.ScalarNode) and k.value == name:
                        quote, s, e = self.span(k, v)
                        found.append((k.start_mark.line, k.start_mark.column, self.construct(v), quote, s, e))
                    stack.append((v, below))
            elif isinstance(node, yaml.SequenceNode):
                stack.extend((child, below) for child in node.value)
        for _, _, value, quote, s, e in sorted(found, key=lambda f: (f[0], f[1])):
            yield value, quote, s, e


_MERGE_TAG = "tag:yaml.org,2002:merge"


def effective_pairs(node: yaml.MappingNode, guard: frozenset[int] = frozenset()) -> list[tuple[yaml.Node, yaml.Node]]:
    """Key/value pairs of a mapping as YAML defines them: ``<<`` merges are expanded and explicit keys win."""
    explicit = [(k, v) for k, v in node.value if k.tag != _MERGE_TAG]
    seen = {k.value for k, _ in explicit if isinstance(k, yaml.ScalarNode)}
    out = list(explicit)
    for k, v in node.value:
        if k.tag != _MERGE_TAG:
            continue
        sources = v.value if isinstance(v, yaml.SequenceNode) else [v]  # earlier sources take precedence
        for src in sources:
            if not isinstance(src, yaml.MappingNode) or id(src) in guard:
                continue
            for mk, mv in effective_pairs(src, guard | {id(node), id(src)}):
                if isinstance(mk, yaml.ScalarNode) and mk.value in seen:
                    continue
                if isinstance(mk, yaml.ScalarNode):
                    seen.add(mk.value)
                out.append((mk, mv))
    return out


def _last_line(node: yaml.Node, seen: set[int]) -> int:
    """Zero-based last source line of a node. Block collections end where the next token starts, so use their children."""
    if isinstance(node, yaml.ScalarNode):
        return node.end_mark.line - (1 if node.end_mark.column == 0 else 0)
    if id(node) in seen:
        return node.start_mark.line
    seen.add(id(node))
    if getattr(node, "flow_style", False):
        return int(node.end_mark.line)
    if isinstance(node, yaml.MappingNode):
        children = [c for pair in node.value for c in pair]
    elif isinstance(node, yaml.SequenceNode):
        children = list(node.value)
    else:
        children = []
    return max([_last_line(c, seen) for c in children], default=node.start_mark.line)


def _expanded_size(node: yaml.Node, memo: dict[int, int]) -> int:
    nid = id(node)
    if nid in memo:
        return memo[nid]
    memo[nid] = 1  # cycles count once
    size = 1
    if isinstance(node, yaml.MappingNode):
        size += sum(_expanded_size(k, memo) + _expanded_size(v, memo) for k, v in node.value)
    elif isinstance(node, yaml.SequenceNode):
        size += sum(_expanded_size(c, memo) for c in node.value)
    memo[nid] = size
    return size


def yaml_docs(text: str) -> list[YamlDoc]:
    loader = yaml.SafeLoader(text)
    docs: list[YamlDoc] = []
    try:
        while loader.check_node():
            root = loader.get_node()
            assert root is not None
            if _expanded_size(root, {}) > MAX_YAML_EXPANDED_NODES:
                raise ExtractionError(Cause.PARSE_ERROR, "YAML alias expansion exceeds the node limit")
            docs.append(YamlDoc(text, loader, root))
        return docs
    except yaml.YAMLError as exc:
        raise ExtractionError(Cause.PARSE_ERROR, f"invalid YAML: {str(exc).splitlines()[0][:120]}") from exc


def yaml_single(text: str) -> YamlDoc:
    docs = yaml_docs(text)
    if len(docs) != 1:
        raise ExtractionError(Cause.PARSE_ERROR, f"expected one YAML document, found {len(docs)}")
    return docs[0]


# --------------------------------------------------------------------------------------------------
# JSON
# --------------------------------------------------------------------------------------------------


def json_loads(text: str) -> Any:
    import json

    try:
        return json.loads(text)
    except (ValueError, RecursionError) as exc:
        raise ExtractionError(Cause.PARSE_ERROR, f"invalid JSON: {str(exc)[:120]}") from exc


def json_top_level_key(text: str, key: str) -> Extracted:
    """Exact source span of a top-level property of a JSON object, whatever the formatting (minified or indented)."""
    n, i, depth = len(text), 0, 0
    while i < n:
        c = text[i]
        if c == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            token, k = text[i : j + 1], j + 1
            while k < n and text[k] in " \t\r\n":
                k += 1
            if depth == 1 and k < n and text[k] == ":" and json_loads(token) == key:
                v = k + 1
                while v < n and text[v] in " \t\r\n":
                    v += 1
                if text[v : v + 1] == '"':
                    end = v + 1
                    while end < n and text[end] != '"':
                        end += 2 if text[end] == "\\" else 1
                    end += 1
                elif text[v : v + 1] in ("{", "["):
                    end = _skip_balanced(text, v)
                else:
                    m = re.compile(r"[^,}\]\s]+").match(text, v)
                    end = m.end() if m else v
                return Extracted(
                    key,
                    _quote(text[i:end]),
                    _line_at(text, i),
                    _line_at(text, max(end - 1, i)),
                    _ext("json_top_level_key"),
                )
            i = j + 1
            continue
        if c in "{[":
            depth += 1
        elif c in "}]":
            depth -= 1
        i += 1
    raise ExtractionError(Cause.LOCATOR_NO_MATCH, f"top-level key {key!r} not found")


# --------------------------------------------------------------------------------------------------
# Markdown
# --------------------------------------------------------------------------------------------------


def md_line_value(text: str, line_regex: str, value_regex: str) -> Extracted:
    """The single ``value_regex`` match (group 1) among the lines selected by ``line_regex``."""
    lines = text.splitlines()
    selector, value = re.compile(line_regex), re.compile(value_regex)
    hits: list[tuple[int, str, str]] = []
    for number, line in enumerate(lines, start=1):
        if selector.search(line):
            hits.extend((number, line, m.group(1)) for m in value.finditer(line))
    if len(hits) != 1:
        raise ExtractionError(
            Cause.LOCATOR_NO_MATCH if not hits else Cause.LOCATOR_AMBIGUOUS,
            f"line {line_regex!r} / value {value_regex!r} matched {len(hits)} times",
        )
    number, line, raw = hits[0]
    return Extracted(raw, _quote(line), number, number, _ext("md_line_value"))


def to_int(raw: str) -> int:
    cleaned = raw.replace(",", "").strip()
    if not cleaned.isdigit():
        raise ExtractionError(Cause.PARSE_ERROR, f"not an integer: {raw[:40]!r}")
    return int(cleaned)


_FENCE = re.compile(r"^\s*(```|~~~)")
_HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$")


def _headings(text: str) -> Iterator[tuple[int, int, str]]:
    fenced = False
    for number, line in enumerate(text.splitlines(), start=1):
        if _FENCE.match(line):
            fenced = not fenced
            continue
        if not fenced and (m := _HEADING.match(line)):
            yield number, len(m.group(1)), m.group(2).strip()


def md_h3_under_h2(text: str, h2_title: str) -> Extracted:
    heads = list(_headings(text))
    starts = [i for i, (_, level, title) in enumerate(heads) if level == 2 and title == h2_title]
    if len(starts) != 1:
        raise ExtractionError(
            Cause.LOCATOR_NO_MATCH if not starts else Cause.LOCATOR_AMBIGUOUS,
            f"section '## {h2_title}' found {len(starts)} times",
        )
    first = starts[0]
    titles: list[str] = []
    for _, level, title in heads[first + 1 :]:
        if level <= 2:
            break
        if level == 3:
            titles.append(title)
    number = heads[first][0]
    return Extracted(len(titles), f"## {h2_title}", number, number, _ext("md_h3_under_h2"), {"headings": titles})


def md_table(text: str, heading: str) -> Extracted:
    """First pipe table after the heading titled ``heading`` (any level); value = list of row dicts."""
    lines = text.splitlines()
    heads = [(n, t) for n, _, t in _headings(text) if t == heading]
    if len(heads) != 1:
        raise ExtractionError(
            Cause.LOCATOR_NO_MATCH if not heads else Cause.LOCATOR_AMBIGUOUS,
            f"heading {heading!r} found {len(heads)} times",
        )
    i = heads[0][0]  # 0-based index of the line after the heading
    while i < len(lines) and not lines[i].lstrip().startswith("|"):
        if _HEADING.match(lines[i]):
            raise ExtractionError(Cause.LOCATOR_NO_MATCH, f"no table under {heading!r}")
        i += 1
    start = i
    while i < len(lines) and lines[i].lstrip().startswith("|"):
        i += 1
    block = lines[start:i]
    if len(block) < 3:
        raise ExtractionError(Cause.PARSE_ERROR, f"table under {heading!r} has no rows")

    def cells(row: str) -> list[str]:
        return [c.strip() for c in row.strip().strip("|").split("|")]

    header = cells(block[0])
    rows = [dict(zip(header, cells(r), strict=False)) for r in block[2:]]
    return Extracted(
        rows, _quote("\n".join(block)), start + 1, i, _ext("md_table"), {"header": header, "first_row_line": start + 3}
    )


@dataclass(frozen=True)
class GithubLink:
    kind: str  # blob | tree
    ref: str
    path: str
    line: int
    raw: str


def github_links(text: str, slug: str) -> list[GithubLink]:
    pattern = re.compile(
        rf"https://github\.com/{re.escape(slug)}/(?P<kind>blob|tree)/(?P<ref>[^/\s)\]>\"'`]+)/(?P<path>[^\s)\]>\"'`#?]+)"
    )
    links: list[GithubLink] = []
    for number, line in enumerate(text.splitlines(), start=1):
        for m in pattern.finditer(line):
            links.append(
                GithubLink(m.group("kind"), m.group("ref"), unquote(m.group("path")).rstrip("/"), number, m.group(0))
            )
    return links
