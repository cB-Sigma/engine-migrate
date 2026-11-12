"""Static readers for Python ``argparse`` calls and config dataclasses."""

from __future__ import annotations

import ast
import dataclasses
from typing import Any

from .common import (
    UNRESOLVED,
    Scope,
    clean_help,
    json_default,
    kwarg,
    literal_values,
    looks_deprecated,
    normalise_type,
    source,
    static_str,
    static_value,
    type_name,
)


@dataclasses.dataclass
class Field:
    """One annotated attribute of a config class."""

    name: str
    annotation: ast.expr
    default: ast.expr | None
    doc: str
    line: int
    path: str


@dataclasses.dataclass
class ClassInfo:
    name: str
    bases: list[str]
    fields: dict[str, Field]
    node: ast.ClassDef
    path: str


def collect_classes(tree: ast.Module, path: str) -> dict[str, ClassInfo]:
    """Collect annotated fields and their attribute docstrings per class."""
    out: dict[str, ClassInfo] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        fields: dict[str, Field] = {}
        body = node.body
        for idx, stmt in enumerate(body):
            if not (isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)):
                continue
            doc = ""
            if idx + 1 < len(body):
                nxt = body[idx + 1]
                if (
                    isinstance(nxt, ast.Expr)
                    and isinstance(nxt.value, ast.Constant)
                    and isinstance(nxt.value.value, str)
                ):
                    doc = nxt.value.value
            fields[stmt.target.id] = Field(stmt.target.id, stmt.annotation, stmt.value, doc, stmt.lineno, path)
        bases = [b.id if isinstance(b, ast.Name) else getattr(b, "attr", "") for b in node.bases]
        out.setdefault(node.name, ClassInfo(node.name, bases, fields, node, path))
    return out


def all_fields(cls: ClassInfo, classes: dict[str, ClassInfo]) -> dict[str, Field]:
    """Fields including inherited ones, base classes first."""
    merged: dict[str, Field] = {}
    for base in cls.bases:
        if base in classes and base != cls.name:
            merged.update(all_fields(classes[base], classes))
    merged.update(cls.fields)
    return merged


# ---------------------------------------------------------------------------
# Annotation analysis
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class Hint:
    atoms: set[str] = dataclasses.field(default_factory=set)
    literals: list[Any] | None = None
    elem: Hint | None = None
    record: str | None = None


def analyse_annotation(
    node: ast.AST | None,
    scope: Scope,
    classes: dict[str, ClassInfo],
    depth: int = 0,
) -> Hint:
    """Flatten a type annotation into the atoms argparse generation cares about."""
    hint = Hint()
    if node is None or depth > 10:
        hint.atoms.add("other")
        return hint
    if isinstance(node, ast.Constant):
        if node.value is None:
            hint.atoms.add("none")
            return hint
        if isinstance(node.value, str):
            try:
                return analyse_annotation(ast.parse(node.value, mode="eval").body, scope, classes, depth + 1)
            except SyntaxError:
                hint.atoms.add("other")
                return hint
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        for side in (node.left, node.right):
            _merge(hint, analyse_annotation(side, scope, classes, depth + 1))
        return hint
    if isinstance(node, ast.Subscript):
        head = node.value.id if isinstance(node.value, ast.Name) else getattr(node.value, "attr", "")
        args = node.slice.elts if isinstance(node.slice, ast.Tuple) else [node.slice]
        if head in {"Optional"}:
            hint.atoms.add("none")
            _merge(hint, analyse_annotation(args[0], scope, classes, depth + 1))
            return hint
        if head in {"Union"}:
            for arg in args:
                _merge(hint, analyse_annotation(arg, scope, classes, depth + 1))
            return hint
        if head in {"Annotated", "A", "Final", "ClassVar"}:
            return analyse_annotation(args[0], scope, classes, depth + 1)
        if head == "Literal":
            vals = literal_values(node, scope)
            hint.atoms.add("literal")
            hint.literals = vals or []
            return hint
        if head.lower() in {"list", "tuple", "set", "sequence", "frozenset"}:
            kind = "tuple" if head.lower() == "tuple" else "list"
            hint.atoms.add(kind)
            real = [a for a in args if not (isinstance(a, ast.Constant) and a.value is Ellipsis)]
            if real:
                hint.elem = analyse_annotation(real[0], scope, classes, depth + 1)
            return hint
        if head.lower() in {"dict", "mapping"}:
            hint.atoms.add("dict")
            return hint
        if head in {"type", "Callable"}:
            hint.atoms.add("other")
            return hint
    name = node.id if isinstance(node, ast.Name) else getattr(node, "attr", "")
    if name in {"bool", "int", "float", "str", "dict", "list"}:
        hint.atoms.add(name)
        return hint
    if name in {"None", "NoneType"}:
        hint.atoms.add("none")
        return hint
    if name in {"Any"}:
        hint.atoms.add("other")
        return hint
    if name in classes and classes[name].fields:
        hint.atoms.add("record")
        hint.record = name
        return hint
    if name in scope.names:
        return analyse_annotation(scope.names[name], scope, classes, depth + 1)
    hint.atoms.add("other")
    return hint


def _merge(into: Hint, other: Hint) -> None:
    into.atoms |= other.atoms
    if other.literals is not None:
        into.literals = (into.literals or []) + other.literals
    into.elem = into.elem or other.elem
    into.record = into.record or other.record


# ---------------------------------------------------------------------------
# argparse.add_argument
# ---------------------------------------------------------------------------

_BOOL_ACTIONS = {"store_true", "store_false"}


def option_from_add_argument(
    call: ast.Call,
    scope: Scope,
    path: str,
    *,
    base: dict[str, Any] | None = None,
    class_defaults: dict[str, ast.expr | None] | None = None,
) -> dict[str, Any] | None:
    """Turn one ``parser.add_argument(...)`` call into a database record.

    ``base`` carries values derived elsewhere (for example from a config
    dataclass); explicit keyword arguments in the call override it.
    """
    flags: list[str] = []
    for arg in call.args:
        value = static_str(arg, scope)
        if value is None:
            continue
        flags.append(value)
    flags = [f for f in flags if f.startswith("-")]
    if not flags:
        return None
    long_flags = [f for f in flags if f.startswith("--")]
    name = long_flags[0] if long_flags else flags[0]
    rec: dict[str, Any] = {
        "name": name,
        "kind": "cli",
        "aliases": [f for f in flags if f != name],
        "type": "str",
        "help": "",
        "deprecated": False,
    }
    if base:
        rec.update(base)
        rec["name"] = name
        rec["aliases"] = [f for f in flags if f != name] + list(base.get("aliases", []))

    action_node = kwarg(call, "action")
    action = static_value(action_node, scope) if action_node is not None else None
    action_src = ast.unparse(action_node) if action_node is not None else ""
    if action in _BOOL_ACTIONS:
        rec["type"] = "bool"
        rec["default"] = action == "store_false"
    elif "BooleanOptionalAction" in action_src:
        rec["type"] = "bool"
        rec["negations"] = ["--no-" + name[2:]]
    elif action == "append":
        rec["type"] = "list[str]"
    elif action_node is not None and action is UNRESOLVED:
        rec["action"] = action_src
        if "StoreTrue" in action_src or "store_true" in action_src.lower():
            rec["type"] = "bool"
        if "Deprecated" in action_src:
            rec["deprecated"] = True

    type_node = kwarg(call, "type")
    if type_node is not None:
        base_type = normalise_type(type_name(type_node))
        rec["type"] = base_type
    nargs = static_value(kwarg(call, "nargs"), scope) if kwarg(call, "nargs") else None
    if nargs in {"+", "*"} and not str(rec["type"]).startswith("list"):
        rec["type"] = f"list[{rec['type']}]"

    choices_node = kwarg(call, "choices")
    if choices_node is not None:
        choices = static_value(choices_node, scope)
        if choices is not UNRESOLVED and isinstance(choices, (list, dict)):
            rec["choices"] = _sorted_choices(list(choices))
        else:
            rec["choices_expr"] = ast.unparse(choices_node)

    default_node = kwarg(call, "default")
    if default_node is not None:
        _set_default(rec, default_node, scope, class_defaults)

    help_node = kwarg(call, "help")
    if help_node is not None:
        text = static_str(help_node, scope)
        if text is not None:
            rec["help"] = clean_help(text.replace("%%", "%"))
        if isinstance(help_node, ast.Name) and "DEPRECAT" in help_node.id.upper():
            rec["deprecated"] = True
        if "SUPPRESS" in ast.unparse(help_node):
            rec["hidden"] = True
    dep = kwarg(call, "deprecated")
    if dep is not None and static_value(dep, scope) is True:
        rec["deprecated"] = True
    if looks_deprecated(rec.get("help", "")):
        rec["deprecated"] = True
    new_flag = kwarg(call, "new_flag")
    if new_flag is not None:
        replacement = static_str(new_flag, scope)
        if replacement:
            rec["replaced_by"] = replacement
    if kwarg(call, "required") is not None and static_value(kwarg(call, "required"), scope) is True:
        rec["required"] = True
    rec["source"] = source(path, call.lineno)
    return rec


def _sorted_choices(values: list[Any]) -> list[Any]:
    try:
        return sorted(values, key=lambda v: (v is None, str(v)))
    except TypeError:
        return values


def _set_default(
    rec: dict[str, Any],
    node: ast.expr,
    scope: Scope,
    class_defaults: dict[str, ast.expr | None] | None,
) -> None:
    value = static_value(node, scope)
    if value is UNRESOLVED and class_defaults is not None:
        # ``default=ServerArgs.port`` style references to a dataclass default.
        if isinstance(node, ast.Attribute) and node.attr in class_defaults:
            value = static_value(class_defaults[node.attr], scope)
        elif (
            isinstance(node, ast.Call)
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value in class_defaults
        ):
            value = static_value(class_defaults[node.args[0].value], scope)
    rec.pop("default", None)
    rec.pop("default_expr", None)
    if value is UNRESOLVED:
        rec["default_expr"] = ast.unparse(node)
    else:
        rec["default"] = json_default(value)


def field_default(field: Field, scope: Scope, extra: dict[str, ast.expr | None] | None = None) -> tuple[bool, Any, str]:
    """Return ``(resolved, value, expr)`` for a dataclass field default."""
    node = field.default
    if node is None:
        return False, None, ""
    value = static_value(node, scope)
    if value is UNRESOLVED and isinstance(node, ast.Call):
        fname = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
        if fname in {"field", "Field"}:
            if kwarg(node, "default") is not None:
                value = static_value(kwarg(node, "default"), scope)
            elif node.args and fname == "Field":
                value = static_value(node.args[0], scope)
            factory = kwarg(node, "default_factory")
            if value is UNRESOLVED and factory is not None:
                if isinstance(factory, ast.Lambda):
                    value = static_value(factory.body, scope)
                elif isinstance(factory, ast.Name) and factory.id in {"list", "dict", "set"}:
                    value = {} if factory.id == "dict" else []
    if value is UNRESOLVED and isinstance(node, ast.Attribute) and extra and node.attr in extra:
        value = static_value(extra[node.attr], scope)
    if value is UNRESOLVED:
        return False, None, ast.unparse(node)
    return True, json_default(value), ""
