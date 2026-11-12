"""Static extractor for vLLM (``vllm serve`` flags, env vars, metrics).

vLLM derives most of its argparse arguments from config dataclasses:
``get_kwargs(SomeConfig)`` turns each annotated field (type, default and
attribute docstring) into ``add_argument`` keyword arguments.  This module
re-implements that derivation on the AST, without importing vLLM.
"""

from __future__ import annotations

import ast
import re
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
    Hint,
    all_fields,
    analyse_annotation,
    collect_classes,
    field_default,
    option_from_add_argument,
)

REPO_URL = "https://github.com/vllm-project/vllm"
ARG_UTILS = "vllm/engine/arg_utils.py"
ENVS = "vllm/envs.py"
METRIC_FALLBACK_FILES = (
    "vllm/v1/metrics/loggers.py",
    "vllm/v1/spec_decode/metrics.py",
    "vllm/v1/metrics/perf.py",
)
BASE_LABELS = ["model_name", "engine"]
DYNAMIC = "*"


class _Context:
    def __init__(self, repo: Repo) -> None:
        self.repo = repo
        self.scope = Scope()
        self.classes: dict[str, ClassInfo] = {}
        self.trees: dict[str, ast.Module] = {}
        self.options: dict[str, dict[str, Any]] = {}
        self.files: list[str] = []
        self.imports: dict[str, str] = {}
        utils = "vllm/utils/argparse_utils.py"
        # Newer releases register an action that pairs --enable-x with --disable-x.
        self.enable_disable = repo.exists(utils) and "EnableDisableAction" in repo.read(utils)

    def load(self, path: str) -> ast.Module:
        if path not in self.trees:
            tree = ast.parse(self.repo.read(path))
            self.trees[path] = tree
            self.scope.add_module(tree)
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                    for alias in node.names:
                        self.imports.setdefault(alias.asname or alias.name, node.module)
            for name, info in collect_classes(tree, path).items():
                self.classes.setdefault(name, info)
            self.files.append(path)
        return self.trees[path]

    def ensure_names(self, node: ast.AST) -> None:
        """Load the defining module of type aliases imported from elsewhere in vLLM."""
        for name in {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}:
            if name in self.scope.names or name in self.classes or name not in self.imports:
                continue
            self._follow(name, self.imports[name], 0)

    def _module_path(self, module: str) -> str | None:
        base = module.replace(".", "/")
        for path in (f"{base}.py", f"{base}/__init__.py"):
            if self.repo.exists(path):
                return path
        return None

    def _follow(self, name: str, module: str, depth: int) -> None:
        """Follow re-exports until the module that assigns ``name`` is loaded."""
        if depth > 4 or not module.startswith("vllm"):
            return
        path = self._module_path(module)
        if path is None:
            return
        tree = self.trees.get(path) or ast.parse(self.repo.read(path))
        defined = any(
            isinstance(t, ast.Name) and t.id == name
            for stmt in tree.body
            for t in [*getattr(stmt, "targets", []), getattr(stmt, "target", None)]
        )
        if defined:
            if path not in self.trees:
                self.load(path)
                self.files.remove(path)
            value = self.scope.names.get(name)
            if value is not None:
                self.ensure_names(value)
            return
        package = module if path.endswith("__init__.py") else module.rsplit(".", 1)[0]
        stars: list[str] = []
        for stmt in ast.walk(tree):
            if not isinstance(stmt, ast.ImportFrom):
                continue
            if stmt.level:
                parts = package.split(".")
                parts = parts[: len(parts) - (stmt.level - 1)]
                target = ".".join([*parts, stmt.module] if stmt.module else parts)
            else:
                target = stmt.module or ""
            if any(alias.name == "*" for alias in stmt.names):
                stars.append(target)
            if any((alias.asname or alias.name) == name for alias in stmt.names):
                self._follow(name, target, depth + 1)
                return
        for target in stars:  # ``from .types import *``
            self._follow(name, target, depth + 1)
            if name in self.scope.names:
                return

    def add(self, rec: dict[str, Any] | None) -> None:
        if rec is None:
            return
        self.options.setdefault(rec["name"], rec)


def _find_cli_args(repo: Repo) -> str:
    candidates = [
        p
        for p in repo.ls("vllm/entrypoints")
        if p.endswith("/cli_args.py") and "/cli/" not in p and "benchmark" not in p
    ]
    for path in candidates:
        if "def make_arg_parser" in repo.read(path):
            return path
    raise RuntimeError(f"no cli_args.py with make_arg_parser at {repo.tag}")


def _elem_type(hint: Hint | None) -> str:
    if hint is None:
        return "str"
    for atom in ("int", "float"):
        if atom in hint.atoms and "str" not in hint.atoms:
            return atom
    return "str"


def _base_from_field(ctx: _Context, field: Field) -> dict[str, Any]:
    """Mirror of vLLM's ``_compute_kwargs`` for a single field."""
    ctx.ensure_names(field.annotation)
    hint = analyse_annotation(field.annotation, ctx.scope, ctx.classes)
    atoms = hint.atoms
    rec: dict[str, Any] = {"help": clean_help(field.doc), "type": "str"}
    resolved, value, expr = field_default(field, ctx.scope)
    if resolved:
        rec["default"] = value
    elif expr:
        rec["default_expr"] = expr
    if "record" in atoms:
        rec["type"] = "json"
    elif atoms == {"bool", "str", "none"}:
        rec["type"] = "str"
        rec["optional_value"] = True
    elif "bool" in atoms:
        rec["type"] = "bool"
        rec["negatable"] = True
    elif "literal" in atoms:
        literals = hint.literals or []
        rec["choices"] = sorted(literals, key=str)
        rec["type"] = "int" if literals and all(isinstance(v, int) for v in literals) else "str"
        if "str" in atoms:
            rec["choices_open"] = True
    elif "tuple" in atoms or "list" in atoms:
        rec["type"] = f"list[{_elem_type(hint.elem)}]"
        if hint.elem is not None and hint.elem.literals:
            rec["choices"] = sorted(hint.elem.literals, key=str)
    elif "int" in atoms:
        rec["type"] = "int"
    elif "float" in atoms:
        rec["type"] = "float"
    elif "dict" in atoms:
        rec["type"] = "json"
    return rec


def _apply_overrides(ctx: _Context, rec: dict[str, Any], overrides: dict[str, ast.expr | None]) -> None:
    for key, node in overrides.items():
        if node is None:  # ``del kwargs[field][key]``
            if key == "nargs" and str(rec.get("type", "")).startswith("list["):
                rec["type"] = "json"
            continue
        if key == "type":
            scalar = normalise_type(type_name(node))
            # A custom parser on a ``nargs="+"`` field still takes several values.
            rec["type"] = f"list[{scalar}]" if str(rec.get("type", "")).startswith("list[") else scalar
            rec.pop("negatable", None)
        elif key == "default":
            value = static_value(node, ctx.scope)
            rec.pop("default", None)
            rec.pop("default_expr", None)
            if value is UNRESOLVED:
                rec["default_expr"] = ast.unparse(node)
            else:
                rec["default"] = json_default(value)
        elif key == "help":
            text = static_str(node, ctx.scope)
            if text is not None:
                rec["help"] = clean_help(text)
        elif key == "choices":
            value = static_value(node, ctx.scope)
            if value is not UNRESOLVED:
                rec["choices"] = value
        elif key == "flags":
            value = static_value(node, ctx.scope)
            if isinstance(value, list):
                rec["aliases"] = [str(v) for v in value]
        elif key == "action":
            value = static_value(node, ctx.scope)
            if value == "append":
                rec["type"] = "list[str]"
            elif value == "store_true":
                rec["type"] = "bool"
            rec.pop("negatable", None) if value in {"append", "store_true"} else None
        elif key == "deprecated" and static_value(node, ctx.scope) is True:
            rec["deprecated"] = True


def _subscript_chain(node: ast.AST) -> tuple[str, list[Any]] | None:
    """``name["a"]["b"]`` -> ("name", ["a", "b"])."""
    keys: list[Any] = []
    while isinstance(node, ast.Subscript):
        if not isinstance(node.slice, ast.Constant):
            return None
        keys.insert(0, node.slice.value)
        node = node.value
    if isinstance(node, ast.Name) and keys:
        return node.id, keys
    return None


def _ordered_nodes(func: ast.AST) -> list[ast.Assign | ast.Call | ast.Delete]:
    nodes = [n for n in ast.walk(func) if isinstance(n, (ast.Assign, ast.Call, ast.Delete))]
    return sorted(nodes, key=lambda n: (n.lineno, n.col_offset))


def _process_function(ctx: _Context, func: ast.FunctionDef, path: str, seen: set[str]) -> None:
    var_cls: dict[str, str] = {}
    overrides: dict[tuple[str, str], dict[str, ast.expr | None]] = {}
    field_alias: dict[str, tuple[str, str]] = {}
    for node in _ordered_nodes(func):
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            source_chain = _subscript_chain(node.value)
            if isinstance(target, ast.Name) and source_chain and len(source_chain[1]) == 1:
                # ``x_kwargs = group_kwargs["field"]``
                field_alias[target.id] = (source_chain[0], source_chain[1][0])
            alias_chain = _subscript_chain(target)
            if alias_chain and len(alias_chain[1]) == 1 and alias_chain[0] in field_alias:
                overrides.setdefault(field_alias[alias_chain[0]], {})[alias_chain[1][0]] = node.value
            if isinstance(target, ast.Name) and call_name(node.value) == "get_kwargs":
                call = node.value
                assert isinstance(call, ast.Call)
                if call.args and isinstance(call.args[0], ast.Name):
                    var_cls[target.id] = call.args[0].id
            chain = _subscript_chain(target)
            if chain and len(chain[1]) == 2:
                overrides.setdefault((chain[0], chain[1][0]), {})[chain[1][1]] = node.value
        elif isinstance(node, ast.Delete):
            for target in node.targets:
                chain = _subscript_chain(target)
                if chain and len(chain[1]) == 2:
                    overrides.setdefault((chain[0], chain[1][0]), {})[chain[1][1]] = None
        elif isinstance(node, ast.Call):
            name = call_name(node)
            if name == "add_argument":
                _handle_add_argument(ctx, node, path, var_cls, overrides, field_alias)
            elif name == "add_cli_args" and isinstance(node.func, ast.Attribute):
                owner = node.func.value
                if isinstance(owner, ast.Name):
                    _process_class(ctx, owner.id, seen)


def _handle_add_argument(
    ctx: _Context,
    call: ast.Call,
    path: str,
    var_cls: dict[str, str],
    overrides: dict[tuple[str, str], dict[str, ast.expr | None]],
    field_alias: dict[str, tuple[str, str]],
) -> None:
    base: dict[str, Any] | None = None
    inline: dict[str, ast.expr | None] = {}
    spreads: list[ast.expr] = []
    for kw in call.keywords:
        if kw.arg is not None:
            continue
        if isinstance(kw.value, ast.Dict):
            # ``**{**kwargs["field"], "default": None}``
            for key, value in zip(kw.value.keys, kw.value.values, strict=True):
                if key is None:
                    spreads.append(value)
                elif isinstance(key, ast.Constant) and isinstance(key.value, str):
                    inline[key.value] = value
        else:
            spreads.append(kw.value)
    for spread in spreads:
        chain = _subscript_chain(spread)
        if isinstance(spread, ast.Name) and spread.id in field_alias:
            chain = (field_alias[spread.id][0], [field_alias[spread.id][1]])
        if not chain or len(chain[1]) != 1:
            continue
        var, (field_name,) = chain[0], chain[1]
        cls = ctx.classes.get(var_cls.get(var, ""))
        if cls is None:
            continue
        fields = all_fields(cls, ctx.classes)
        if field_name not in fields:
            continue
        base = _base_from_field(ctx, fields[field_name])
        base["config_field"] = f"{cls.name}.{field_name}"
        _apply_overrides(ctx, base, overrides.get((var, field_name), {}))
    if base is not None and inline:
        _apply_overrides(ctx, base, inline)
        help_node = inline.get("help")
        if isinstance(help_node, ast.Name) and "DEPRECAT" in help_node.id.upper():
            base["deprecated"] = True
    rec = option_from_add_argument(call, ctx.scope, path, base=base)
    if rec is not None and base is not None and base.get("deprecated"):
        rec["deprecated"] = True
    _finish(rec, ctx.enable_disable)
    ctx.add(rec)


def negations_for(name: str, enable_disable: bool) -> list[str]:
    """Negative spellings argparse registers for a boolean optional flag."""
    plain = "--no-" + name[2:]
    if enable_disable:
        for prefix, inverse in (("--enable-", "--disable-"), ("--disable-", "--enable-")):
            if name.startswith(prefix):
                return [inverse + name[len(prefix) :], plain]
    return [plain]


def _finish(rec: dict[str, Any] | None, enable_disable: bool = False) -> None:
    if rec is None:
        return
    negatable = rec.pop("negatable", False) or "negations" in rec
    if negatable and rec["name"].startswith("--"):
        rec["negations"] = negations_for(rec["name"], enable_disable)
    if looks_deprecated(rec.get("help", "")):
        rec["deprecated"] = True


def _process_class(ctx: _Context, cls_name: str, seen: set[str]) -> None:
    if cls_name in seen or cls_name not in ctx.classes:
        return
    seen.add(cls_name)
    cls = ctx.classes[cls_name]
    method = next(
        (n for n in cls.node.body if isinstance(n, ast.FunctionDef) and n.name == "add_cli_args"),
        None,
    )
    if method is None:
        for base in cls.bases:
            if base in ctx.classes:
                seen.discard(base)
                _process_frontend_or_method(ctx, cls, ctx.classes[base], seen)
                return
        return
    _process_frontend_or_method(ctx, cls, cls, seen)


def _process_frontend_or_method(ctx: _Context, cls: ClassInfo, owner: ClassInfo, seen: set[str]) -> None:
    method = next(
        (n for n in owner.node.body if isinstance(n, ast.FunctionDef) and n.name == "add_cli_args"),
        None,
    )
    if method is None:
        for base in owner.bases:
            if base in ctx.classes:
                _process_frontend_or_method(ctx, cls, ctx.classes[base], seen)
                return
        return
    src = ast.unparse(method)
    if "get_kwargs(cls)" in src:
        _process_frontend(ctx, cls)
    else:
        _process_function(ctx, method, owner.path, seen)


def _class_chain(ctx: _Context, cls: ClassInfo) -> list[ClassInfo]:
    chain: list[ClassInfo] = []
    for base in cls.bases:
        if base in ctx.classes:
            chain.extend(_class_chain(ctx, ctx.classes[base]))
    chain.append(cls)
    return chain


def _process_frontend(ctx: _Context, cls: ClassInfo) -> None:
    """Classes whose ``add_cli_args`` registers one flag per dataclass field."""
    overrides: dict[str, dict[str, ast.expr | None]] = {}
    for klass in _class_chain(ctx, cls):
        for node in klass.node.body:
            if not (isinstance(node, ast.FunctionDef) and node.name == "_customize_cli_kwargs"):
                continue
            for stmt in _ordered_nodes(node):
                targets: list[tuple[ast.AST, ast.expr | None]] = []
                if isinstance(stmt, ast.Assign):
                    targets = [(t, stmt.value) for t in stmt.targets]
                elif isinstance(stmt, ast.Delete):
                    targets = [(t, None) for t in stmt.targets]
                for target, value in targets:
                    chain = _subscript_chain(target)
                    if chain and len(chain[1]) == 2:
                        overrides.setdefault(chain[1][0], {})[chain[1][1]] = value
    for name, field in all_fields(cls, ctx.classes).items():
        if name.startswith("_"):
            continue
        rec = _base_from_field(ctx, field)
        _apply_overrides(ctx, rec, overrides.get(name, {}))
        flag = "--" + name.replace("_", "-")
        full: dict[str, Any] = {
            "name": flag,
            "kind": "cli",
            "aliases": rec.pop("aliases", []),
            "deprecated": False,
        }
        full.update(rec)
        full["config_field"] = f"{cls.name}.{name}"
        full["source"] = source(field.path, field.line)
        _finish(full, ctx.enable_disable)
        ctx.add(full)


# ---------------------------------------------------------------------------
# Environment variables
# ---------------------------------------------------------------------------


def _extract_env(repo: Repo) -> list[dict[str, Any]]:
    if not repo.exists(ENVS):
        return []
    text = repo.read(ENVS)
    lines = text.splitlines()
    tree = ast.parse(text)
    scope = Scope()
    scope.add_module(tree)
    table: ast.Dict | None = None
    for node in tree.body:
        value = getattr(node, "value", None)
        targets = [getattr(node, "target", None), *list(getattr(node, "targets", []))]
        if isinstance(value, ast.Dict) and any(
            isinstance(t, ast.Name) and t.id == "environment_variables" for t in targets
        ):
            table = value
    if table is None:
        return []
    out: list[dict[str, Any]] = []
    for key, value in zip(table.keys, table.values, strict=True):
        if not (isinstance(key, ast.Constant) and isinstance(key.value, str)):
            continue
        rec: dict[str, Any] = {
            "name": key.value,
            "kind": "env",
            "aliases": [],
            "type": "str",
            "help": _comment_above(lines, key.lineno),
            "deprecated": False,
        }
        for call in [n for n in ast.walk(value) if isinstance(n, ast.Call)]:
            fname = call_name(call)
            if fname in {"getenv", "get"} and len(call.args) >= 2:
                default = static_value(call.args[1], scope)
                if default is not UNRESOLVED:
                    rec["default"] = json_default(default)
                else:
                    rec["default_expr"] = ast.unparse(call.args[1])
                break
            if fname.startswith("env_with_choices") or fname.startswith("env_list_with_choices"):
                if len(call.args) >= 2:
                    default = static_value(call.args[1], scope)
                    if default is not UNRESOLVED:
                        rec["default"] = json_default(default)
                if len(call.args) >= 3:
                    choices = static_value(call.args[2], scope)
                    if isinstance(choices, list):
                        rec["choices"] = choices
                break
        src = ast.unparse(value)
        if "bool(" in src or "in ('1', 'true')" in src.lower():
            rec["type"] = "bool"
        elif re.search(r"\bint\(", src):
            rec["type"] = "int"
        elif re.search(r"\bfloat\(", src):
            rec["type"] = "float"
        if looks_deprecated(rec["help"]):
            rec["deprecated"] = True
        rec["source"] = source(ENVS, key.lineno)
        out.append(rec)
    return out


def _comment_above(lines: list[str], lineno: int) -> str:
    parts: list[str] = []
    idx = lineno - 2
    while idx >= 0 and lines[idx].strip().startswith("#"):
        text = lines[idx].strip().lstrip("#").strip()
        if text.startswith("===") or text.endswith("==="):
            break
        parts.insert(0, text)
        idx -= 1
    return clean_help(" ".join(parts))


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

_METRIC_FUNCS = {
    "_gauge_cls": "gauge",
    "_counter_cls": "counter",
    "_histogram_cls": "histogram",
    "Gauge": "gauge",
    "Counter": "counter",
    "Histogram": "histogram",
    "Summary": "summary",
    "Info": "info",
}


def metric_files(repo: Repo) -> list[str]:
    files: list[str] = []
    gen = "docs/mkdocs/gen_files/generate_metrics.py"
    if repo.exists(gen):
        # vLLM's own docs generator lists the files that define metrics.
        for node in ast.walk(ast.parse(repo.read(gen))):
            if isinstance(node, ast.Dict):
                for key, value in zip(node.keys, node.values, strict=True):
                    if isinstance(key, ast.Constant) and key.value == "path":
                        path = static_value(value)
                        if isinstance(path, str):
                            files.append(path)
    pattern = re.compile(r"(^|/)(metrics|stats|prometheus|loggers|perf)[^/]*\.py$|/metrics/[^/]+\.py$")
    for path in repo.ls("vllm"):
        if pattern.search(path) and "/benchmarks/" not in path and path not in files:
            files.append(path)
    for path in METRIC_FALLBACK_FILES:
        if path not in files and repo.exists(path):
            files.append(path)
    return [f for f in files if repo.exists(f)]


def extract_python_metrics(
    repo: Repo, files: list[str], base_labels: list[str], prefixes: tuple[str, ...]
) -> tuple[list[dict[str, Any]], list[str]]:
    """Find prometheus_client style metric constructors in ``files``."""
    metrics: dict[str, dict[str, Any]] = {}
    used: list[str] = []
    for path in files:
        text = repo.read(path)
        if not any(p in text for p in prefixes):
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        scope = Scope()
        scope.add_module(tree)
        found = False
        for func in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.Module))]:
            local = _local_assignments(func)
            for call in [n for n in ast.walk(func) if isinstance(n, ast.Call)]:
                kind = _METRIC_FUNCS.get(call_name(call))
                if kind is None:
                    continue
                name_node = kwarg(call, "name") or (call.args[0] if call.args else None)
                doc_node = kwarg(call, "documentation") or (call.args[1] if len(call.args) > 1 else None)
                labels_node = kwarg(call, "labelnames") or (call.args[2] if len(call.args) > 2 else None)
                labels = _labels(labels_node, local, scope, base_labels, call.lineno)
                pairs: list[tuple[str, str]] = []
                name = static_str(name_node, scope)
                if name and "{" not in name:
                    pairs.append((name, clean_help(static_str(doc_node, scope))))
                elif isinstance(name_node, ast.Name):
                    # ``for name, doc in specs: Counter(name=name, documentation=doc)``
                    pairs = _loop_values(func, name_node, doc_node, local, scope)
                for name, doc in pairs:
                    if not name.startswith(prefixes) or name in metrics:
                        continue
                    rec: dict[str, Any] = {
                        "name": name,
                        "type": kind,
                        "labels": sorted({lab for lab in labels if lab != DYNAMIC}, key=labels.index),
                        "help": doc,
                        "source": source(path, call.lineno),
                    }
                    if DYNAMIC in labels:
                        # Some label names are only known at runtime (passed in as a dict).
                        rec["labels_dynamic"] = True
                    metrics[name] = rec
                    found = True
        if found:
            used.append(path)
    return sorted(metrics.values(), key=lambda m: m["name"]), used


def _loop_values(
    func: ast.AST,
    name_node: ast.Name,
    doc_node: ast.expr | None,
    local: dict[str, list[tuple[int, ast.expr]]],
    scope: Scope,
) -> list[tuple[str, str]]:
    """Resolve metric names that come from iterating a literal list of tuples."""
    out: list[tuple[str, str]] = []
    loops: list[tuple[ast.expr, ast.expr]] = []
    for node in ast.walk(func):
        if isinstance(node, (ast.For, ast.AsyncFor)):
            loops.append((node.target, node.iter))
        elif isinstance(node, (ast.ListComp, ast.GeneratorExp, ast.SetComp, ast.DictComp)):
            loops += [(gen.target, gen.iter) for gen in node.generators]
    for target, iterable in loops:
        names = [
            t.id if isinstance(t, ast.Name) else ""
            for t in (target.elts if isinstance(target, ast.Tuple) else [target])
        ]
        if name_node.id not in names:
            continue
        name_idx = names.index(name_node.id)
        doc_idx = names.index(doc_node.id) if isinstance(doc_node, ast.Name) and doc_node.id in names else None
        sources: list[ast.expr] = [iterable]
        if isinstance(iterable, ast.Name):
            sources = [value for _, value in local.get(iterable.id, [])]
            if not sources and iterable.id in scope.names:
                sources = [scope.names[iterable.id]]
        for src in sources:
            value = static_value(src, scope)
            if not isinstance(value, list):
                continue
            for item in value:
                row = item if isinstance(item, list) else [item]
                if name_idx < len(row) and isinstance(row[name_idx], str):
                    doc = row[doc_idx] if doc_idx is not None and doc_idx < len(row) else ""
                    out.append((row[name_idx], clean_help(str(doc))))
    return out


def _local_assignments(func: ast.AST) -> dict[str, list[tuple[int, ast.expr]]]:
    out: dict[str, list[tuple[int, ast.expr]]] = {}
    for node in ast.walk(func):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    out.setdefault(target.id, []).append((node.lineno, node.value))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value:
            out.setdefault(node.target.id, []).append((node.lineno, node.value))
    return out


def _labels(
    node: ast.expr | None,
    local: dict[str, list[tuple[int, ast.expr]]],
    scope: Scope,
    base_labels: list[str],
    lineno: int,
    depth: int = 0,
) -> list[str]:
    if node is None:
        return []
    if depth > 6:
        return [DYNAMIC]
    value = static_value(node, scope)
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return list(value)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _labels(node.left, local, scope, base_labels, lineno, depth + 1) + _labels(
            node.right, local, scope, base_labels, lineno, depth + 1
        )
    if isinstance(node, (ast.List, ast.Tuple)):
        out: list[str] = []
        for elt in node.elts:
            if isinstance(elt, ast.Starred):
                out += _labels(elt.value, local, scope, base_labels, lineno, depth + 1)
            else:
                text = static_str(elt, scope)
                if text:
                    out.append(text)
        return out
    if isinstance(node, ast.Name):
        prior = [v for (line, v) in local.get(node.id, []) if line <= lineno]
        if prior:
            return _labels(prior[-1], local, scope, base_labels, lineno, depth + 1)
        if "label" in node.id.lower():
            # A ``labelnames`` parameter handed down from the stat logger.
            return list(base_labels) if base_labels else [DYNAMIC]
    if isinstance(node, ast.Call) and call_name(node) in {"list", "tuple"} and node.args:
        return _labels(node.args[0], local, scope, base_labels, lineno, depth + 1)
    if isinstance(node, ast.Call) and call_name(node) == "keys":
        return [DYNAMIC]
    if isinstance(node, ast.Set):
        return [t for t in (static_str(e, scope) for e in node.elts) if t]
    return [DYNAMIC]


# ---------------------------------------------------------------------------


def extract(repo: Repo) -> dict[str, Any]:
    ctx = _Context(repo)
    for path in repo.ls("vllm/config"):
        if path.endswith(".py"):
            ctx.load(path)
    ctx.load(ARG_UTILS)
    cli_path = _find_cli_args(repo)
    tree = ctx.load(cli_path)
    seen: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "make_arg_parser":
            _process_function(ctx, node, cli_path, seen)
    options = sorted(ctx.options.values(), key=lambda o: o["name"])
    options += sorted(_extract_env(repo), key=lambda o: o["name"])
    files = metric_files(repo)
    metrics, metric_sources = extract_python_metrics(repo, files, BASE_LABELS, ("vllm:",))
    return {
        "options": options,
        "metrics": metrics,
        "sources": sorted({cli_path, ARG_UTILS, ENVS, *metric_sources}),
        "notes": [
            "CLI flags are those registered by make_arg_parser() for `vllm serve`.",
            "Flags added at runtime by platform plugins are not visible to static extraction.",
            "HTTP-level metrics emitted by prometheus-fastapi-instrumentator are not defined "
            "in vLLM source and are not listed.",
        ],
    }
