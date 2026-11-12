"""Shared helpers for the static extractors.

Everything here reads engine source through ``git show <tag>:<path>`` on a
blob-less reference clone, so no engine code is ever imported or executed.
"""

from __future__ import annotations

import ast
import dataclasses
import functools
import re
import subprocess
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1


class SourceError(RuntimeError):
    """Raised when a path cannot be read from the reference clone."""


@dataclasses.dataclass(frozen=True)
class Repo:
    """A reference clone pinned at one tag."""

    path: Path
    tag: str

    def _git(self, *args: str) -> str:
        proc = subprocess.run(
            ["git", "-C", str(self.path), *args],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            raise SourceError(proc.stderr.strip() or f"git {' '.join(args)} failed")
        return proc.stdout

    @functools.cache  # noqa: B019 - Repo is frozen and short-lived
    def read(self, relpath: str) -> str:
        return self._git("show", f"{self.tag}:{relpath}")

    def exists(self, relpath: str) -> bool:
        try:
            self.read(relpath)
        except SourceError:
            return False
        return True

    @functools.cache  # noqa: B019
    def ls(self, prefix: str = "") -> tuple[str, ...]:
        args = ["ls-tree", "-r", "--name-only", self.tag]
        if prefix:
            args += ["--", prefix]
        return tuple(self._git(*args).splitlines())

    def commit(self) -> str:
        return self._git("rev-list", "-n", "1", self.tag).strip()

    def date(self) -> str:
        return self._git("log", "-1", "--format=%cs", self.tag).strip()


def source(path: str, line: int) -> dict[str, Any]:
    return {"path": path, "line": line}


def clean_help(text: str | None) -> str:
    """Collapse whitespace the way a terminal reader would see the text."""
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


_DEPRECATED_START = re.compile(r"^[\[(*\s]*deprecated\b", re.IGNORECASE)
_DEPRECATED_SELF = re.compile(
    r"\b(this (flag|argument|option|parameter|variable) (is|has been) deprecated"
    r"|is deprecated and will be removed"
    r"|\(deprecated[:,)\s])",
    re.IGNORECASE,
)


def looks_deprecated(help_text: str) -> bool:
    """Heuristic used when the source has no structured deprecation marker."""
    return bool(_DEPRECATED_START.search(help_text) or _DEPRECATED_SELF.search(help_text))


# ---------------------------------------------------------------------------
# Python AST helpers
# ---------------------------------------------------------------------------

UNRESOLVED = object()


class Scope:
    """Module-level constants collected from one or more parsed files."""

    def __init__(self) -> None:
        self.names: dict[str, ast.expr] = {}

    def add_module(self, tree: ast.Module) -> None:
        for node in tree.body:
            self._add_stmt(node)

    def _add_stmt(self, node: ast.stmt) -> None:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    self.names.setdefault(target.id, node.value)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.value is not None:
                self.names.setdefault(node.target.id, node.value)
        elif isinstance(node, (ast.If, ast.Try)):
            for child in node.body:
                self._add_stmt(child)


def static_value(node: ast.AST | None, scope: Scope | None = None, depth: int = 0) -> Any:
    """Evaluate ``node`` if it is a compile-time constant, else ``UNRESOLVED``."""
    if node is None or depth > 8:
        return UNRESOLVED
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        items = [static_value(e, scope, depth + 1) for e in node.elts]
        if any(i is UNRESOLVED for i in items):
            return UNRESOLVED
        return items
    if isinstance(node, ast.Dict):
        out: dict[Any, Any] = {}
        for k, v in zip(node.keys, node.values, strict=True):
            kk = static_value(k, scope, depth + 1)
            vv = static_value(v, scope, depth + 1)
            if kk is UNRESOLVED or vv is UNRESOLVED or not isinstance(kk, (str, int)):
                return UNRESOLVED
            out[kk] = vv
        return out
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        val = static_value(node.operand, scope, depth + 1)
        return -val if isinstance(val, (int, float)) else UNRESOLVED
    if isinstance(node, ast.BinOp):
        left = static_value(node.left, scope, depth + 1)
        right = static_value(node.right, scope, depth + 1)
        if left is UNRESOLVED or right is UNRESOLVED:
            return UNRESOLVED
        try:
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Pow):
                return left**right
            if isinstance(node.op, ast.FloorDiv):
                return left // right
            if isinstance(node.op, ast.LShift):
                return left << right
        except Exception:
            return UNRESOLVED
        return UNRESOLVED
    if isinstance(node, ast.JoinedStr):
        text = static_str(node, scope, depth + 1)
        return text if text is not None else UNRESOLVED
    if isinstance(node, ast.Name) and scope is not None and node.id in scope.names:
        return static_value(scope.names[node.id], scope, depth + 1)
    if isinstance(node, ast.Call):
        fn = node.func
        fname = fn.id if isinstance(fn, ast.Name) else fn.attr if isinstance(fn, ast.Attribute) else ""
        if fname in {"list", "tuple", "sorted", "set", "frozenset", "int", "float", "str"} and len(node.args) == 1:
            inner = static_value(node.args[0], scope, depth + 1)
            if inner is UNRESOLVED:
                return UNRESOLVED
            try:
                if fname == "sorted":
                    return sorted(inner)
                if fname in {"int", "float", "str"}:
                    return {"int": int, "float": float, "str": str}[fname](inner)
            except Exception:
                return UNRESOLVED
            return list(inner) if isinstance(inner, (list, tuple, set)) else UNRESOLVED
        if fname in {"list", "dict", "set", "tuple"} and not node.args and not node.keywords:
            return {} if fname == "dict" else []
        if fname == "get_args" and len(node.args) == 1:
            lit = literal_values(node.args[0], scope)
            return lit if lit is not None else UNRESOLVED
    return UNRESOLVED


def static_str(node: ast.AST | None, scope: Scope | None = None, depth: int = 0) -> str | None:
    """Render a string expression; unresolved pieces appear as ``{expr}``."""
    if node is None or depth > 8:
        return None
    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, str) else None
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant):
                parts.append(str(value.value))
            elif isinstance(value, ast.FormattedValue):
                inner = static_value(value.value, scope, depth + 1)
                if inner is UNRESOLVED:
                    parts.append("{" + ast.unparse(value.value) + "}")
                else:
                    parts.append(str(inner))
        return "".join(parts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = static_str(node.left, scope, depth + 1)
        right = static_str(node.right, scope, depth + 1)
        if left is None and right is None:
            return None
        return (left if left is not None else "{" + ast.unparse(node.left) + "}") + (
            right if right is not None else "{" + ast.unparse(node.right) + "}"
        )
    if isinstance(node, ast.Name) and scope is not None and node.id in scope.names:
        return static_str(scope.names[node.id], scope, depth + 1)
    # "...".format(...) / "...".strip(): keep the template, it is still useful.
    templated = isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    if templated and node.func.attr in {"format", "strip", "replace"}:  # type: ignore[attr-defined]
        return static_str(node.func.value, scope, depth + 1)  # type: ignore[attr-defined]
    return None


def literal_values(node: ast.AST | None, scope: Scope | None, depth: int = 0) -> list[Any] | None:
    """Values of a ``Literal[...]`` annotation (following aliases), else None."""
    if node is None or depth > 8:
        return None
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        try:
            node = ast.parse(node.value, mode="eval").body
        except SyntaxError:
            return None
    if isinstance(node, ast.Subscript) and _name(node.value) == "Literal":
        elts = node.slice.elts if isinstance(node.slice, ast.Tuple) else [node.slice]
        vals: list[Any] = []
        for elt in elts:
            nested = literal_values(elt, scope, depth + 1) if isinstance(elt, (ast.Name, ast.Subscript)) else None
            if nested is not None:  # Literal["auto", OtherLiteralAlias]
                vals.extend(nested)
                continue
            value = static_value(elt, scope)
            if value is UNRESOLVED:
                return None
            vals.append(value)
        return vals
    if isinstance(node, ast.Name) and scope is not None and node.id in scope.names:
        return literal_values(scope.names[node.id], scope, depth + 1)
    return None


def _name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def call_name(node: ast.AST) -> str:
    return _name(node.func) if isinstance(node, ast.Call) else ""


def kwarg(call: ast.Call, name: str) -> ast.expr | None:
    for kw in call.keywords:
        if kw.arg == name:
            return kw.value
    return None


def json_default(value: Any) -> Any:
    """Coerce a statically evaluated default into JSON-friendly data."""
    if isinstance(value, (set, frozenset, tuple)):
        return list(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return value


def type_name(node: ast.AST | None) -> str:
    """Name of an argparse ``type=`` callable as written in source."""
    if node is None:
        return ""
    if isinstance(node, ast.Call):
        inner = ", ".join(type_name(a) for a in node.args)
        return f"{_name(node.func)}({inner})"
    if isinstance(node, ast.Attribute):
        return ast.unparse(node)
    return _name(node) or ast.unparse(node)


_SIMPLE_TYPES = {
    "int": "int",
    "float": "float",
    "str": "str",
    "bool": "bool",
    "nullable_str": "str",
    "json.loads": "json",
    "human_readable_int": "int",
    "human_readable_int_or_auto": "int",
}


def normalise_type(raw: str) -> str:
    """Map a source-level type spelling onto the small database vocabulary."""
    raw = raw.strip()
    if raw in _SIMPLE_TYPES:
        return _SIMPLE_TYPES[raw]
    match = re.fullmatch(r"(optional_type|parse_type)\((.*)\)", raw)
    if match:
        return normalise_type(match.group(2))
    if "json" in raw.lower() or "dict" in raw.lower():
        return "json"
    return raw or "str"
