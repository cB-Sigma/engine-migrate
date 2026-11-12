"""Static extractor for SGLang (``python -m sglang.launch_server``).

SGLang declares server arguments as ``A[T, "help"]`` / ``A[T, Arg(...)]``
annotated fields (``A`` is ``typing.Annotated``) that
``add_cli_args_from_dataclass`` turns into argparse arguments, plus a few
manual ``parser.add_argument`` calls.  Up to v0.5.19 the fields live on
``ServerArgs`` itself; from v0.5.20 they live in ``arg_groups/fields/*.py``.
"""

from __future__ import annotations

import ast
from typing import Any

from .common import (
    UNRESOLVED,
    Repo,
    Scope,
    call_name,
    clean_help,
    json_default,
    kwarg,
    looks_deprecated,
    normalise_type,
    source,
    static_str,
    static_value,
    type_name,
)
from .pyargs import (
    ClassInfo,
    Field,
    analyse_annotation,
    collect_classes,
    field_default,
    option_from_add_argument,
)
from .vllm import extract_python_metrics

REPO_URL = "https://github.com/sgl-project/sglang"
SERVER_ARGS = "python/sglang/srt/server_args.py"
GROUPS = "python/sglang/srt/arg_groups"
REGISTRIES = {
    "--reasoning-parser": [
        ("python/sglang/srt/parser/reasoning_parser_names.py", "REASONING_PARSER_NAMES"),
        ("python/sglang/srt/parser/reasoning_parser.py", "DetectorMap"),
    ],
    "--tool-call-parser": [
        ("python/sglang/srt/function_call/parser_names.py", "TOOL_CALL_PARSER_NAMES"),
        ("python/sglang/srt/function_call/function_call_parser.py", "ToolCallParserEnum"),
    ],
}


def _annotated_parts(node: ast.expr) -> tuple[ast.expr, list[ast.expr]] | None:
    if not isinstance(node, ast.Subscript):
        return None
    head = node.value.id if isinstance(node.value, ast.Name) else getattr(node.value, "attr", "")
    if head not in {"A", "Annotated"} or not isinstance(node.slice, ast.Tuple):
        return None
    elts = node.slice.elts
    return elts[0], list(elts[1:])


def _option_from_field(field: Field, scope: Scope, classes: dict[str, ClassInfo]) -> dict[str, Any] | None:
    parts = _annotated_parts(field.annotation)
    if parts is None:
        return None
    inner, metas = parts
    arg_call: ast.Call | None = None
    help_text: str | None = None
    for meta in metas:
        if isinstance(meta, ast.Call) and call_name(meta) == "Arg":
            arg_call = meta
            break
        text = static_str(meta, scope)
        if text is not None:
            help_text = text
            break
    if arg_call is None and help_text is None:
        return None

    def meta_value(name: str) -> Any:
        if arg_call is None or kwarg(arg_call, name) is None:
            return None
        return static_value(kwarg(arg_call, name), scope)

    if meta_value("no_cli") is True:
        return None
    if arg_call is not None:
        help_text = static_str(kwarg(arg_call, "help"), scope) or ""
    cli_name = meta_value("cli_name")
    name = cli_name if isinstance(cli_name, str) else "--" + field.name.replace("_", "-")
    aliases = meta_value("aliases")
    rec: dict[str, Any] = {
        "name": name,
        "kind": "cli",
        "aliases": [a for a in aliases if isinstance(a, str)] if isinstance(aliases, list) else [],
        "type": "str",
        "help": clean_help(help_text),
        "deprecated": False,
        "config_field": field.name,
    }
    resolved, value, expr = field_default(field, scope)
    if resolved:
        rec["default"] = value
    elif expr:
        rec["default_expr"] = expr
    elif field.default is None:
        rec["required"] = True

    hint = analyse_annotation(inner, scope, classes)
    action_node = kwarg(arg_call, "action") if arg_call is not None else None
    parser_node = kwarg(arg_call, "type_parser") if arg_call is not None else None
    if action_node is not None:
        action = static_value(action_node, scope)
        action_src = ast.unparse(action_node)
        if action in {"store_true", "store_false"} or "StoreTrue" in action_src:
            rec["type"] = "bool"
        elif action == "append":
            rec["type"] = "list[str]"
        else:
            rec["action"] = action_src
            rec["type"] = _scalar_type(hint.atoms)
        if "Deprecated" in action_src:
            rec["deprecated"] = True
    elif "literal" in hint.atoms and "list" not in hint.atoms:
        literals = hint.literals or []
        rec["type"] = "int" if literals and all(isinstance(v, int) for v in literals) else "str"
        rec["choices"] = list(literals)
    elif "list" in hint.atoms and parser_node is None:
        elem = hint.elem.atoms if hint.elem is not None else {"str"}
        rec["type"] = f"list[{_scalar_type(elem)}]"
    elif hint.atoms - {"none"} == {"bool"}:
        rec["type"] = "bool"
    elif parser_node is not None:
        rec["type"] = normalise_type(type_name(parser_node))
        rec["type_parser"] = type_name(parser_node)
    else:
        rec["type"] = _scalar_type(hint.atoms)
    if arg_call is not None and kwarg(arg_call, "choices") is not None:
        choices = static_value(kwarg(arg_call, "choices"), scope)
        if choices is not UNRESOLVED and isinstance(choices, list):
            rec["choices"] = [json_default(c) for c in choices]
        else:
            rec["choices_expr"] = ast.unparse(kwarg(arg_call, "choices"))  # type: ignore[arg-type]
    if looks_deprecated(rec["help"]):
        rec["deprecated"] = True
    rec["source"] = source(field.path, field.line)
    return rec


def _scalar_type(atoms: set[str]) -> str:
    for atom in ("int", "float"):
        if atom in atoms and "str" not in atoms:
            return atom
    if "dict" in atoms:
        return "json"
    return "str"


def _registry(repo: Repo, flag: str) -> tuple[list[str], str] | None:
    """Statically read a parser-name registry (dict or list literal)."""
    for path, symbol in REGISTRIES.get(flag, []):
        if not repo.exists(path):
            continue
        for node in ast.walk(ast.parse(repo.read(path))):
            target: ast.expr | None = None
            value: ast.expr | None = None
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                target, value = node.targets[0], node.value
            elif isinstance(node, ast.AnnAssign):
                target, value = node.target, node.value
            if not (isinstance(target, ast.Name) and target.id == symbol and value is not None):
                continue
            if isinstance(value, ast.Dict):
                keys = [k.value for k in value.keys if isinstance(k, ast.Constant)]
            else:
                evaluated = static_value(value)
                keys = evaluated if isinstance(evaluated, list) else []
            names = [k for k in keys if isinstance(k, str)]
            if names:
                return names, path
    return None


def _field_files(repo: Repo) -> list[str]:
    files = [SERVER_ARGS]
    files += [p for p in repo.ls(GROUPS) if p.startswith(f"{GROUPS}/fields/") and p.endswith(".py")]
    return files


def extract(repo: Repo) -> dict[str, Any]:
    scope = Scope()
    classes: dict[str, ClassInfo] = {}
    trees: dict[str, ast.Module] = {}
    support = [f"{GROUPS}/choices.py", "python/sglang/srt/utils/common.py"]
    field_files = _field_files(repo)
    for path in [*field_files, *support]:
        if not repo.exists(path):
            continue
        tree = ast.parse(repo.read(path))
        trees[path] = tree
        scope.add_module(tree)
        if path in field_files:
            for name, info in collect_classes(tree, path).items():
                classes.setdefault(name, info)

    options: dict[str, dict[str, Any]] = {}
    for info in classes.values():
        if info.path != SERVER_ARGS or info.name == "ServerArgs":
            for field in info.fields.values():
                rec = _option_from_field(field, scope, classes)
                if rec is not None:
                    options.setdefault(rec["name"], rec)

    server_args = classes.get("ServerArgs")
    class_defaults: dict[str, ast.expr | None] = {}
    for info in classes.values():
        for fname, field in info.fields.items():
            class_defaults.setdefault(fname, field.default)
    if server_args is not None:
        for node in server_args.node.body:
            if isinstance(node, ast.FunctionDef) and node.name == "add_cli_args":
                for call in [n for n in ast.walk(node) if isinstance(n, ast.Call)]:
                    if call_name(call) != "add_argument":
                        continue
                    rec = option_from_add_argument(call, scope, SERVER_ARGS, class_defaults=class_defaults)
                    if rec is None:
                        continue
                    if "choices_expr" in rec:
                        found = _registry(repo, rec["name"])
                        if found is not None:
                            names, reg_path = found
                            prefix = (
                                ["auto"] if '"auto"' in rec["choices_expr"] or "'auto'" in rec["choices_expr"] else []
                            )
                            rec["choices"] = prefix + names
                            rec["choices_source"] = reg_path
                            rec["choices_open"] = True
                    # Manual registrations win over auto-derived ones.
                    options[rec["name"]] = rec

    metric_candidates = [
        p
        for p in repo.ls("python/sglang/srt")
        if p.endswith(".py")
        and ("/observability/" in p or "metric" in p.rsplit("/", 1)[-1])
        and "/debug_utils/" not in p
    ]
    metrics, metric_sources = extract_python_metrics(repo, metric_candidates, [], ("sglang:",))
    used = sorted({o["source"]["path"] for o in options.values()} | set(metric_sources))
    return {
        "options": sorted(options.values(), key=lambda o: o["name"]),
        "metrics": metrics,
        "sources": used,
        "notes": [
            "CLI flags are those of `python -m sglang.launch_server` (ServerArgs).",
            "Most SGLang metrics take their base label names from a dict built at runtime; "
            "such metrics carry labels_dynamic=true and only the statically visible extra "
            "labels are listed.",
            "The SGLang model gateway (Rust) has its own flags and metrics and is not covered.",
        ],
    }
