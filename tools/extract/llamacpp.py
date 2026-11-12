"""Static extractor for llama.cpp's ``llama-server``.

Options are parsed from the ``add_opt(common_arg(...))`` blocks in
``common/arg.cpp``.  Help strings built with ``string_format`` are rendered
with the struct defaults found in ``common/common.h`` where the expression
is simple enough to evaluate statically.
"""

from __future__ import annotations

import re
from typing import Any

from .common import Repo, clean_help, looks_deprecated, source

REPO_URL = "https://github.com/ggml-org/llama.cpp"
ARG_CPP = "common/arg.cpp"
COMMON_H = "common/common.h"
SERVER = "LLAMA_EXAMPLE_SERVER"
COMMON = "LLAMA_EXAMPLE_COMMON"


class Unresolved(Exception):
    """A C++ expression that cannot be evaluated statically."""


# ---------------------------------------------------------------------------
# Minimal C++ lexing helpers
# ---------------------------------------------------------------------------


def _skip_string(text: str, i: int) -> int:
    """``i`` is at an opening quote; return the index just past the literal."""
    quote = text[i]
    if quote == '"' and i > 0 and text[i - 1] == "R":
        match = re.compile(r'"([^(]*)\(').match(text, i)
        if match:
            end = text.find(")" + match.group(1) + '"', match.end())
            if end != -1:
                return end + len(match.group(1)) + 2
    i += 1
    while i < len(text):
        if text[i] == "\\":
            i += 2
            continue
        if text[i] == quote:
            return i + 1
        i += 1
    return i


def split_args(text: str, start: int) -> tuple[list[str], int]:
    """Split a parenthesised argument list starting just after ``(``.

    Returns the top-level arguments and the index of the closing ``)``.
    """
    args: list[str] = []
    depth = 0
    i = start
    begin = start
    while i < len(text):
        ch = text[i]
        if ch in "\"'":
            i = _skip_string(text, i)
            continue
        if text.startswith("//", i):
            i = text.find("\n", i)
            if i == -1:
                break
            continue
        if text.startswith("/*", i):
            i = text.find("*/", i) + 2
            continue
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            if depth == 0:
                tail = text[begin:i].strip()
                if tail:
                    args.append(tail)
                return args, i
            depth -= 1
        elif ch == "," and depth == 0:
            args.append(text[begin:i].strip())
            begin = i + 1
        i += 1
    raise ValueError("unbalanced parentheses")


_ESCAPES = {"n": "\n", "t": "\t", '"': '"', "\\": "\\", "'": "'", "r": "\r", "0": "\0"}


def _decode(body: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(body):
        if body[i] == "\\" and i + 1 < len(body):
            out.append(_ESCAPES.get(body[i + 1], body[i + 1]))
            i += 2
        else:
            out.append(body[i])
            i += 1
    return "".join(out)


def strip_comments(expr: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(expr):
        if expr[i] in "\"'":
            j = _skip_string(expr, i)
            out.append(expr[i:j])
            i = j
        elif expr.startswith("//", i):
            j = expr.find("\n", i)
            i = len(expr) if j == -1 else j
        elif expr.startswith("/*", i):
            i = expr.find("*/", i) + 2
        else:
            out.append(expr[i])
            i += 1
    return "".join(out).strip()


def string_literal(expr: str) -> str | None:
    """Value of one or more adjacent C string literals, else None."""
    expr = strip_comments(expr)
    parts: list[str] = []
    i = 0
    while i < len(expr):
        if expr[i].isspace():
            i += 1
            continue
        if expr[i] != '"':
            return None
        j = _skip_string(expr, i)
        parts.append(_decode(expr[i + 1 : j - 1]))
        i = j
    return "".join(parts) if parts else None


def init_list(expr: str) -> list[str] | None:
    expr = strip_comments(expr)
    if not (expr.startswith("{") and expr.endswith("}")):
        return None
    items, _ = split_args(expr + ")", 1) if False else _split_braces(expr)
    out: list[str] = []
    for item in items:
        value = string_literal(item)
        if value is None:
            return None
        out.append(value)
    return out


def _split_braces(expr: str) -> tuple[list[str], int]:
    inner = expr[1:-1]
    args, _ = split_args(inner + ")", 0)
    return args, len(expr)


# ---------------------------------------------------------------------------
# Struct defaults from common.h
# ---------------------------------------------------------------------------

_MEMBER = re.compile(
    r"^\s*(?:struct\s+|enum\s+|const\s+)*([\w:]+(?:<[^;=]*>)?)\s*[&*]?\s+(\w+)\s*(?:=\s*([^;]+?))?\s*;"
)


def parse_structs(text: str) -> dict[str, dict[str, tuple[str, str | None]]]:
    """``struct name -> member -> (type, default expression)``."""
    structs: dict[str, dict[str, tuple[str, str | None]]] = {}
    for match in re.finditer(r"^struct\s+(\w+)\s*\{", text, re.MULTILINE):
        depth = 0
        i = match.end() - 1
        body_start = i + 1
        end = None
        while i < len(text):
            ch = text[i]
            if ch in "\"'":
                i = _skip_string(text, i)
                continue
            if text.startswith("//", i):
                i = text.find("\n", i)
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = i
                    break
            i += 1
        if end is None:
            continue
        members: dict[str, tuple[str, str | None]] = {}
        level = 0
        for line in text[body_start:end].splitlines():
            code = strip_comments(line)
            if level == 0:
                found = _MEMBER.match(code)
                if found and found.group(1) not in {"return", "using", "typedef"}:
                    members.setdefault(found.group(2), (found.group(1), found.group(3)))
            level += code.count("{") - code.count("}")
        structs[match.group(1)] = members
    return structs


def _binary(op: str, a: Any, b: Any) -> Any:
    if op == "||":
        return bool(a) or bool(b)
    if op == "&&":
        return bool(a) and bool(b)
    if op == "==":
        return a == b
    if op == "!=":
        return a != b
    if op == "<=":
        return a <= b
    if op == ">=":
        return a >= b
    if op == "<":
        return a < b
    if op == ">":
        return a > b
    if op == "+":
        return a + b
    if op == "-":
        return a - b
    if op == "*":
        return a * b
    return a / b if isinstance(a, float) or isinstance(b, float) else a // b


class Evaluator:
    """Evaluates the small C++ expression subset used in help strings."""

    def __init__(
        self,
        structs: dict[str, dict[str, tuple[str, str | None]]],
        overrides: dict[str, str] | None = None,
    ) -> None:
        self.structs = structs
        self.overrides = overrides or {}

    def member(self, path: list[str]) -> Any:
        dotted = ".".join(path)
        if dotted in self.overrides:
            return self.eval(self.overrides[dotted])
        struct = "common_params"
        for idx, name in enumerate(path):
            members = self.structs.get(struct)
            if members is None or name not in members:
                raise Unresolved(".".join(path))
            mtype, default = members[name]
            if idx == len(path) - 1:
                if default is None:
                    raise Unresolved(".".join(path))
                return self.eval(default)
            struct = mtype.split("::")[-1]
        raise Unresolved(".".join(path))

    def eval(self, expr: str) -> Any:
        expr = strip_comments(expr).strip()
        while expr.startswith("(") and self._wrapped(expr):
            expr = expr[1:-1].strip()
        if not expr:
            raise Unresolved(expr)
        parts = self._split_top(expr, "?")
        if len(parts) > 1:
            cond = self.eval(parts[0])
            rest = "?".join(parts[1:])
            branches = self._split_colon(rest)
            return self.eval(branches[0] if cond else branches[1])
        for ops in (("||",), ("&&",), ("==", "!="), ("<=", ">=", "<", ">"), ("+", "-"), ("*", "/")):
            hit = self._split_binary(expr, ops)
            if hit is not None:
                left, op, right = hit
                a, b = self.eval(left), self.eval(right)
                try:
                    return _binary(op, a, b)
                except Exception as exc:
                    raise Unresolved(expr) from exc
        if expr.startswith("!"):
            return not self.eval(expr[1:])
        if expr.startswith("-"):
            return -self.eval(expr[1:])
        cast = re.match(r"^\((?:double|float|int|unsigned|size_t|int32_t|uint32_t|long)\)\s*(.+)$", expr, re.S)
        if cast:
            return self.eval(cast.group(1))
        literal = string_literal(expr)
        if literal is not None:
            return literal
        if expr in {"true", "false"}:
            return expr == "true"
        number = re.fullmatch(r"(\d+\.?\d*(?:[eE][-+]?\d+)?|\.\d+)([fFuUlL]*)", expr)
        if number:
            text = number.group(1)
            is_float = "." in text or "e" in text.lower() or "f" in number.group(2).lower()
            return float(text) if is_float else int(text)
        if re.fullmatch(r"0[xX][0-9a-fA-F]+", expr):
            return int(expr, 16)
        if expr.endswith(".c_str()"):
            return self.eval(expr[: -len(".c_str()")])
        if expr.endswith(".empty()"):
            value = self.eval(expr[: -len(".empty()")])
            return len(value) == 0
        if re.fullmatch(r"params(\.\w+)+", expr):
            return self.member(expr.split(".")[1:])
        if re.fullmatch(r"\w+\{\}|std::string\(\)|\{\}", expr):
            return ""
        raise Unresolved(expr)

    @staticmethod
    def _wrapped(expr: str) -> bool:
        try:
            _, end = split_args(expr, 1)
        except ValueError:
            return False
        return end == len(expr) - 1

    @staticmethod
    def _split_top(expr: str, sep: str) -> list[str]:
        parts: list[str] = []
        depth = 0
        i = 0
        begin = 0
        while i < len(expr):
            ch = expr[i]
            if ch in "\"'":
                i = _skip_string(expr, i)
                continue
            if ch in "([{":
                depth += 1
            elif ch in ")]}":
                depth -= 1
            elif depth == 0 and expr.startswith(sep, i):
                parts.append(expr[begin:i])
                begin = i + len(sep)
                i += len(sep)
                continue
            i += 1
        parts.append(expr[begin:])
        return parts

    def _split_colon(self, expr: str) -> list[str]:
        depth = 0
        nested = 0
        i = 0
        while i < len(expr):
            ch = expr[i]
            if ch in "\"'":
                i = _skip_string(expr, i)
                continue
            if ch in "([{":
                depth += 1
            elif ch in ")]}":
                depth -= 1
            elif depth == 0 and ch == "?":
                nested += 1
            elif depth == 0 and ch == ":" and not expr.startswith("::", i) and expr[i - 1] != ":":
                if nested == 0:
                    return [expr[:i], expr[i + 1 :]]
                nested -= 1
            i += 1
        raise Unresolved(expr)

    def _split_binary(self, expr: str, ops: tuple[str, ...]) -> tuple[str, str, str] | None:
        depth = 0
        i = len(expr) - 1
        spans: list[tuple[int, int]] = []
        j = 0
        while j < len(expr):
            if expr[j] in "\"'":
                k = _skip_string(expr, j)
                spans.append((j, k))
                j = k
            else:
                j += 1

        def in_string(pos: int) -> bool:
            return any(a <= pos < b for a, b in spans)

        while i > 0:
            ch = expr[i]
            if in_string(i):
                i -= 1
                continue
            if ch in ")]}":
                depth += 1
            elif ch in "([{":
                depth -= 1
            elif depth == 0:
                for op in ops:
                    start = i - len(op) + 1
                    if start > 0 and expr.startswith(op, start):
                        left = expr[:start].rstrip()
                        if not left or left[-1] in "+-*/<>=!&|?:(,":
                            continue
                        if op in {"<", ">"} and (expr[start - 1] in "-<>" or expr[i + 1 : i + 2] in {"=", "<", ">"}):
                            continue
                        return left, op, expr[i + 1 :].strip()
            i -= 1
        return None


_SPEC = re.compile(r"%(?:%|([-+ #0]*)(\d+)?(?:\.(\d+))?(hh|h|ll|l|z|j|t)?([diufFeEgGsxXc]))")


def render_format(fmt: str, values: list[Any]) -> str:
    """printf-style formatting; unresolved values are shown as ``<expr>``."""
    it = iter(values)

    def repl(match: re.Match[str]) -> str:
        if match.group(0) == "%%":
            return "%"
        try:
            value = next(it)
        except StopIteration:
            return match.group(0)
        if isinstance(value, _Raw):
            return f"<{value.expr}>"
        conv = match.group(5)
        spec = "%" + (match.group(1) or "") + (match.group(2) or "")
        if match.group(3) is not None:
            spec += "." + match.group(3)
        try:
            if conv in "diu":
                return (spec + "d") % int(value)
            if conv in "fFeEgG":
                return (spec + conv) % float(value)
            if conv in "xX":
                return (spec + conv) % int(value)
            return (spec + "s") % (value,)
        except (TypeError, ValueError):
            return f"<{value}>"

    return _SPEC.sub(repl, fmt)


class _Raw:
    def __init__(self, expr: str) -> None:
        self.expr = re.sub(r"\s+", " ", expr)


def render_help(expr: str, evaluator: Evaluator) -> tuple[str, bool]:
    """Return ``(help text, fully_resolved)``."""
    expr = strip_comments(expr)
    literal = string_literal(expr)
    if literal is not None:
        return literal, True
    if expr.startswith("string_format(") and expr.endswith(")"):
        try:
            args, end = split_args(expr, len("string_format("))
        except ValueError:
            return expr, False
        if end == len(expr) - 1 and args:
            fmt = string_literal(args[0])
            if fmt is not None:
                values: list[Any] = []
                ok = True
                for arg in args[1:]:
                    try:
                        values.append(evaluator.eval(arg))
                    except Unresolved:
                        values.append(_Raw(arg))
                        ok = False
                return render_format(fmt, values), ok
    # Fall back to the literal fragments so the text is still searchable.
    fragments = re.findall(r'"((?:[^"\\]|\\.)*)"', expr)
    return "".join(_decode(f) for f in fragments), False


# ---------------------------------------------------------------------------
# Options
# ---------------------------------------------------------------------------

_DEFAULT = re.compile(r"\(default: ([^)]*?)\)")


def _handler_type(lambda_src: str) -> str:
    head = lambda_src.split("{", 1)[0]
    if re.search(r",\s*int\s+\w+", head):
        return "int"
    if re.search(r",\s*bool\s+\w+", head):
        return "bool"
    if head.count("std::string") >= 2:
        return "pair"
    if "std::string" in head:
        return "str"
    return "flag"


def _coerce_default(text: str, kind: str) -> Any:
    text = text.strip()
    head = re.split(r"[,;]", text, maxsplit=1)[0].strip()
    if kind == "int" and re.fullmatch(r"-?\d+", head):
        return int(head)
    if kind in {"flag", "bool"} and head in {"enabled", "disabled", "true", "false"}:
        return head in {"enabled", "true"}
    if re.fullmatch(r"-?\d+", head):
        return int(head)
    if re.fullmatch(r"-?\d*\.\d+", head):
        return float(head)
    return head.strip("'\"")


def parse_options(text: str, evaluator: Evaluator) -> list[dict[str, Any]]:
    options: list[dict[str, Any]] = []
    marker = "add_opt(common_arg("
    pos = 0
    while True:
        idx = text.find(marker, pos)
        if idx == -1:
            break
        pos = idx + len(marker)
        line = text.count("\n", 0, idx) + 1
        try:
            args, close = split_args(text, pos)
            _, outer_close = split_args(text, idx + len("add_opt("))
        except ValueError:
            continue
        chain = text[close + 1 : outer_close]
        names = init_list(args[0]) if args else None
        if not names:
            continue
        rest = args[1:]
        negations: list[str] = []
        if rest and strip_comments(rest[0]).startswith("{"):
            negations = init_list(rest[0]) or []
            rest = rest[1:]
        lambda_idx = next((i for i, a in enumerate(rest) if strip_comments(a).startswith("[")), len(rest))
        before = rest[:lambda_idx]
        handler = rest[lambda_idx] if lambda_idx < len(rest) else ""
        if not before:
            continue
        hints = [string_literal(b) for b in before[:-1]]
        help_text, resolved = render_help(before[-1], evaluator)
        kind = _handler_type(handler)
        long_names = [n for n in names if n.startswith("--")]
        name = long_names[0] if long_names else names[0]
        rec: dict[str, Any] = {
            "name": name,
            "kind": "cli",
            "aliases": [n for n in names if n != name],
            "type": {"flag": "bool", "pair": "str"}.get(kind, kind),
            "help": clean_help(help_text),
            "deprecated": False,
        }
        if negations:
            rec["negations"] = negations
        hint = " ".join(h for h in hints if h)
        if hint:
            rec["value_hint"] = hint
            enum = re.fullmatch(r"[\[{<]([\w.|,-]+)[\]}>]", hint)
            if enum and re.search(r"[|,]", enum.group(1)):
                rec["choices"] = re.split(r"[|,]", enum.group(1))
        default = _DEFAULT.search(help_text)
        if default and "<" not in default.group(1):
            rec["default"] = _coerce_default(default.group(1), kind)
        if not resolved:
            rec["help_partial"] = True
        examples = re.search(r"\.set_examples\(\{([^}]*)\}\)", chain)
        rec["examples"] = sorted(e.strip() for e in examples.group(1).split(",") if e.strip()) if examples else [COMMON]
        excludes = re.search(r"\.set_excludes\(\{([^}]*)\}\)", chain)
        if excludes:
            rec["excludes"] = sorted(e.strip() for e in excludes.group(1).split(",") if e.strip())
        env = re.search(r'\.set_env\("([^"]+)"\)', chain)
        if env:
            rec["env"] = env.group(1)
        if ".set_sparam()" in chain or ".set_sampling()" in chain:
            rec["group"] = "sampling"
        elif ".set_spec()" in chain:
            rec["group"] = "speculative"
        elif examples:
            rec["group"] = "example-specific"
        else:
            rec["group"] = "common"
        if ".set_preset_only()" in chain:
            rec["preset_only"] = True
        if looks_deprecated(rec["help"]) or re.search(r"\bDEPRECATED\b", help_text):
            rec["deprecated"] = True
        rec["source"] = source(ARG_CPP, line)
        options.append(rec)
    return options


def applies_to_server(rec: dict[str, Any]) -> bool:
    examples = set(rec.get("examples", []))
    if SERVER in rec.get("excludes", []):
        return False
    return COMMON in examples or SERVER in examples


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def parse_metrics(text: str, path: str) -> list[dict[str, Any]]:
    metrics: dict[str, dict[str, Any]] = {}

    def add(name: str, kind: str, help_text: str, offset: int, labels: list[str] | None = None) -> None:
        full = "llamacpp:" + name
        metrics.setdefault(
            full,
            {
                "name": full,
                "type": kind,
                "labels": labels or [],
                "help": clean_help(help_text),
                "source": source(path, text.count("\n", 0, offset) + 1),
            },
        )

    # Style 1: json{{"counter", {{ {"name", ...}, {"help", ...} ...
    kinds = [(m.start(), m.group(1)) for m in re.finditer(r'\{"(counter|gauge)",\s*\{\{', text)]
    for match in re.finditer(r'\{"name",\s*"([a-z0-9_]+)"\},\s*\{"help",\s*"((?:[^"\\]|\\.)*)"\}', text):
        prior = [k for (off, k) in kinds if off < match.start()]
        if prior:
            add(match.group(1), prior[-1], _decode(match.group(2)), match.start())
    # Style 2: std::vector<metric_item> counters = { {"name", "help", value}, ... };
    for block in re.finditer(r"std::vector<\s*metric_item\s*>\s+(\w+)\s*=\s*\{", text):
        kind = "counter" if "counter" in block.group(1) else "gauge"
        try:
            _, end = split_args(text, block.end())
        except ValueError:
            continue
        body = text[block.end() : end]
        for item in re.finditer(r'\{\s*"([a-z0-9_]+)"\s*,\s*((?:"(?:[^"\\]|\\.)*"\s*)+),', body):
            add(item.group(1), kind, string_literal(item.group(2)) or "", block.end() + item.start())
    # Style 3: metrics written out literally with "# HELP llamacpp:<name> ..."
    for match in re.finditer(r'"# HELP llamacpp:([a-z0-9_]+)"\s*"\s*((?:[^"\\]|\\.)*)"', text):
        window = text[match.start() : match.start() + 800]
        kind_match = re.search(r"# TYPE llamacpp:" + match.group(1) + r" (\w+)", window)
        labels = re.findall(match.group(1) + r"\{(\w+)=", window)
        add(
            match.group(1),
            kind_match.group(1) if kind_match else "untyped",
            _decode(match.group(2)).strip(),
            match.start(),
            sorted(set(labels)),
        )
    return list(metrics.values())


def server_overrides(text: str) -> dict[str, str]:
    """``params.x = v;`` assignments made when the parser is built for the server."""
    out: dict[str, str] = {}
    for match in re.finditer(r"if \(ex == LLAMA_EXAMPLE_SERVER\) \{", text):
        try:
            _, end = split_args(text, match.end())
        except ValueError:
            continue
        body = text[match.end() : end]
        if "add_opt(" in body:
            continue
        for name, value in re.findall(r"^\s*params\.([\w.]+)\s*=\s*([^;]+);", body, re.MULTILINE):
            out[name] = value
    return out


def extract(repo: Repo) -> dict[str, Any]:
    structs = parse_structs(repo.read(COMMON_H)) if repo.exists(COMMON_H) else {}
    arg_cpp = repo.read(ARG_CPP)
    evaluator = Evaluator(structs, server_overrides(arg_cpp))
    options = [o for o in parse_options(arg_cpp, evaluator) if applies_to_server(o)]
    seen: dict[str, dict[str, Any]] = {}
    for opt in options:
        seen.setdefault(opt["name"], opt)
    env_records: list[dict[str, Any]] = []
    for opt in seen.values():
        if "env" in opt:
            env_records.append(
                {
                    "name": opt["env"],
                    "kind": "env",
                    "aliases": [],
                    "type": opt["type"],
                    "help": f"Environment form of {opt['name']}.",
                    "deprecated": opt["deprecated"],
                    "flag": opt["name"],
                    "source": opt["source"],
                }
            )
    server_dirs = [d for d in ("tools/server", "examples/server") if repo.ls(d)]
    metrics: dict[str, dict[str, Any]] = {}
    metric_sources: list[str] = []
    for directory in server_dirs:
        for path in repo.ls(directory):
            if not re.fullmatch(rf"{directory}/server[\w-]*\.(cpp|h|hpp)", path):
                continue
            text = repo.read(path)
            if "llamacpp:" not in text and "_total" not in text:
                continue
            found = parse_metrics(text, path)
            if found:
                metric_sources.append(path)
            for metric in found:
                metrics.setdefault(metric["name"], metric)
    return {
        "options": sorted(seen.values(), key=lambda o: o["name"]) + sorted(env_records, key=lambda o: o["name"]),
        "metrics": sorted(metrics.values(), key=lambda m: m["name"]),
        "sources": sorted({ARG_CPP, COMMON_H, *metric_sources}),
        "notes": [
            "Options are those whose examples include LLAMA_EXAMPLE_COMMON or "
            "LLAMA_EXAMPLE_SERVER and that do not exclude the server.",
            "Defaults are read from the '(default: ...)' part of the help text after "
            "rendering it with the struct defaults in common/common.h; options whose "
            "help could not be fully rendered carry help_partial=true.",
            "Options registered only under some build flags are included without the condition.",
        ],
    }
