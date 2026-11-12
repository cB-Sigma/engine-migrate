"""Extract metric names (and their label matchers) from PromQL expressions.

This is a tolerant scanner, not a full PromQL parser: it is meant to find
every metric selector in real dashboards and rules, including Grafana
template variables, without rejecting expressions it does not fully
understand.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

KEYWORDS = {
    "by", "without", "on", "ignoring", "group_left", "group_right", "bool", "offset",
    "and", "or", "unless", "atan2", "inf", "nan", "start", "end", "step",
}  # fmt: skip

AGGREGATORS = {
    "sum", "min", "max", "avg", "group", "stddev", "stdvar", "count", "count_values",
    "bottomk", "topk", "quantile", "limitk", "limit_ratio",
}  # fmt: skip

_LABEL_LIST_AFTER = {"by", "without", "on", "ignoring", "group_left", "group_right"}

HISTOGRAM_SUFFIXES = ("_bucket", "_sum", "_count")
COUNTER_SUFFIXES = ("_total", "_created")

_IDENT = re.compile(r"[A-Za-z_:][A-Za-z0-9_:]*")
_NUMBER = re.compile(r"(?:0x[0-9a-fA-F]+|\d+\.?\d*(?:[eE][-+]?\d+)?|\.\d+)(?:[a-zA-Z]+\d*[a-zA-Z]*)*")
_GRAFANA_VAR = re.compile(r"\$\{[^}]+\}|\$__?[A-Za-z_][A-Za-z0-9_]*|\$[A-Za-z_][A-Za-z0-9_]*|\[\[[^\]]+\]\]")
_MATCHER = re.compile(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*(=~|!~|!=|=)\s*")


@dataclass(frozen=True)
class Selector:
    """A metric selector found in an expression."""

    metric: str
    labels: tuple[str, ...]


def _skip_string(text: str, i: int) -> int:
    quote = text[i]
    i += 1
    while i < len(text):
        if text[i] == "\\" and quote != "`":
            i += 2
            continue
        if text[i] == quote:
            return i + 1
        i += 1
    return i


def _matching(text: str, i: int, open_ch: str, close_ch: str) -> int:
    """Index of the bracket closing the one at ``i`` (or len(text))."""
    depth = 0
    while i < len(text):
        ch = text[i]
        if ch in "\"'`":
            i = _skip_string(text, i)
            continue
        if ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return len(text)


def _parse_matchers(body: str) -> tuple[str | None, tuple[str, ...]]:
    """Label names inside ``{...}`` and the value of a ``__name__="x"`` matcher."""
    labels: list[str] = []
    name: str | None = None
    i = 0
    while i < len(body):
        match = _MATCHER.match(body, i)
        if match is None:
            i += 1
            continue
        label, op = match.group(1), match.group(2)
        i = match.end()
        value = ""
        if i < len(body) and body[i] in "\"'`":
            end = _skip_string(body, i)
            value = body[i + 1 : end - 1]
            i = end
        if label == "__name__":
            if op == "=" and re.fullmatch(_IDENT, value):
                name = value
        elif label not in labels:
            labels.append(label)
    return name, tuple(labels)


def extract_selectors(expr: str) -> list[Selector]:
    """All metric selectors referenced by a PromQL expression."""
    text = _GRAFANA_VAR.sub("1", expr)
    out: list[Selector] = []
    i = 0
    prev_word = ""
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "#":
            end = text.find("\n", i)
            i = n if end == -1 else end
            continue
        if ch in "\"'`":
            i = _skip_string(text, i)
            continue
        if ch == "[":
            i = _matching(text, i, "[", "]") + 1
            continue
        if ch == "{":
            end = _matching(text, i, "{", "}")
            name, labels = _parse_matchers(text[i + 1 : end])
            if name is not None:
                out.append(Selector(name, labels))
            i = end + 1
            prev_word = ""
            continue
        if ch == "(" and prev_word in _LABEL_LIST_AFTER:
            i = _matching(text, i, "(", ")") + 1
            prev_word = ""
            continue
        number = _NUMBER.match(text, i)
        if number and (i == 0 or not (text[i - 1].isalnum() or text[i - 1] in "_:")):
            i = number.end()
            prev_word = ""
            continue
        ident = _IDENT.match(text, i)
        if ident is None:
            if not ch.isspace():
                prev_word = ""
            i += 1
            continue
        word = ident.group(0)
        j = ident.end()
        k = j
        while k < n and text[k].isspace():
            k += 1
        nxt = text[k] if k < n else ""
        low = word.lower()
        if low in KEYWORDS:
            prev_word = low
            i = j
            continue
        if nxt == "(" or (low in AGGREGATORS and text[k : k + 2].lower() in {"by", "wi"}):
            prev_word = low if low in _LABEL_LIST_AFTER else ""
            i = j
            continue
        matched: tuple[str, ...] = ()
        if nxt == "{":
            end = _matching(text, k, "{", "}")
            _, matched = _parse_matchers(text[k + 1 : end])
            j = end + 1
        out.append(Selector(word, matched))
        prev_word = ""
        i = j
    return out


def metric_names(expr: str) -> list[str]:
    """Distinct metric names in order of first appearance."""
    seen: list[str] = []
    for selector in extract_selectors(expr):
        if selector.metric not in seen:
            seen.append(selector.metric)
    return seen
