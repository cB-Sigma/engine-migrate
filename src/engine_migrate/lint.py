"""Check dashboards and alert rules against the metrics of an engine version."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from . import db, diffing, promql
from .errors import ConfigParseError

# Prefix that marks a metric as belonging to an engine.
PREFIXES = {"vllm": "vllm:", "sglang": "sglang:", "llamacpp": "llamacpp:", "tgi": "tgi_"}

# Labels that Prometheus or the scrape config attach, never the engine.
TARGET_LABELS = {
    "job", "instance", "namespace", "pod", "container", "service", "endpoint", "cluster",
    "node", "le", "quantile", "__name__", "app", "region", "zone", "env", "deployment",
}  # fmt: skip

ERROR = "error"
WARNING = "warning"


@dataclass(frozen=True)
class Expression:
    """A PromQL expression and where it was found."""

    file: str
    location: str
    expr: str


@dataclass
class Problem:
    file: str
    location: str
    metric: str
    severity: str
    kind: str  # removed | renamed | not-in-version | unknown | label | type
    message: str
    suggestion: str = ""
    ref: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "file": self.file,
            "location": self.location,
            "metric": self.metric,
            "severity": self.severity,
            "kind": self.kind,
            "message": self.message,
            "suggestion": self.suggestion,
            "ref": self.ref,
        }


@dataclass
class LintResult:
    engine: str
    to_version: str
    from_version: str | None
    files: int
    expressions: int
    references: int
    problems: list[Problem] = field(default_factory=list)

    def count(self, severity: str) -> int:
        return sum(1 for p in self.problems if p.severity == severity)

    def exit_code(self, strict: bool = False) -> int:
        if self.count(ERROR) or (strict and self.count(WARNING)):
            return 1
        return 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "engine": self.engine,
            "to": self.to_version,
            "from": self.from_version,
            "files": self.files,
            "expressions": self.expressions,
            "metric_references": self.references,
            "errors": self.count(ERROR),
            "warnings": self.count(WARNING),
            "problems": [p.as_dict() for p in self.problems],
        }


# ---------------------------------------------------------------------------
# Reading dashboards and rule files
# ---------------------------------------------------------------------------


def _walk_dashboard(node: Any, file: str, trail: str, out: list[Expression]) -> None:
    if isinstance(node, dict):
        title = node.get("title")
        here = f"{trail} > {title}" if isinstance(title, str) and title and "targets" in node else trail
        if isinstance(title, str) and title and "panels" in node and "targets" not in node:
            here = f"{trail} > {title}" if trail else title
        for key, value in node.items():
            if key in {"expr", "expression"} and isinstance(value, str) and value.strip():
                ref = node.get("refId")
                out.append(Expression(file, f"{here or 'dashboard'}{f' [{ref}]' if ref else ''}", value))
            elif (
                key in {"query", "definition"}
                and isinstance(value, str)
                and "(" in value
                and here.endswith("templating")
            ):
                out.append(Expression(file, f"variable {node.get('name', '?')}", _variable_query(value)))
            elif key == "templating":
                _walk_dashboard(value, file, "templating", out)
            else:
                _walk_dashboard(value, file, here, out)
    elif isinstance(node, list):
        for item in node:
            _walk_dashboard(item, file, trail, out)


def _variable_query(query: str) -> str:
    """``label_values(metric{...}, label)`` -> the selector part."""
    inner = query.strip()
    for fn in ("label_values", "query_result", "metrics", "label_names"):
        if inner.startswith(fn + "("):
            body = inner[len(fn) + 1 : inner.rfind(")")]
            if fn == "label_values":
                depth = 0
                for idx in range(len(body) - 1, -1, -1):
                    ch = body[idx]
                    if ch in ")}":
                        depth += 1
                    elif ch in "({":
                        depth -= 1
                    elif ch == "," and depth == 0:
                        return body[:idx]
                return "" if promql._IDENT.fullmatch(body.strip()) else body
            return body
    return inner


def _walk_rules(doc: Any, file: str, out: list[Expression]) -> None:
    if isinstance(doc, dict):
        groups = doc.get("groups")
        if isinstance(groups, list):
            for group in groups:
                if not isinstance(group, dict):
                    continue
                for rule in group.get("rules") or []:
                    if isinstance(rule, dict) and isinstance(rule.get("expr"), (str, int, float)):
                        name = rule.get("alert") or rule.get("record") or "rule"
                        out.append(Expression(file, f"{group.get('name', 'group')} / {name}", str(rule["expr"])))
            return
        for value in doc.values():  # PrometheusRule CRD: spec.groups, or nested lists
            _walk_rules(value, file, out)
    elif isinstance(doc, list):
        for item in doc:
            _walk_rules(item, file, out)


def read_expressions(path: Path) -> list[Expression]:
    """PromQL expressions of a Grafana dashboard JSON or a Prometheus rule file."""
    text = path.read_text(encoding="utf-8")
    out: list[Expression] = []
    name = str(path)
    if path.suffix.lower() == ".json":
        try:
            doc = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ConfigParseError(f"{path}: invalid JSON: {exc}") from exc
        _walk_dashboard(doc.get("dashboard", doc) if isinstance(doc, dict) else doc, name, "", out)
    elif path.suffix.lower() in {".yaml", ".yml"}:
        try:
            docs = list(yaml.safe_load_all(text))
        except yaml.YAMLError as exc:
            raise ConfigParseError(f"{path}: invalid YAML: {exc}") from exc
        for doc in docs:
            _walk_rules(doc, name, out)
    else:
        for lineno, line in enumerate(text.splitlines(), start=1):
            if line.strip() and not line.lstrip().startswith("#"):
                out.append(Expression(name, f"line {lineno}", line))
    return out


# ---------------------------------------------------------------------------
# Checking
# ---------------------------------------------------------------------------


def resolve_metric(ev: db.EngineVersion, name: str) -> tuple[db.Metric, str] | None:
    """Find the database metric behind an exposed series name.

    Returns the metric and the suffix that was stripped (``""``, ``_bucket``,
    ``_total`` ...), or None when no metric of ``ev`` can produce the series.
    """
    metric = ev.metric(name)
    if metric is not None:
        return metric, ""
    for suffix in promql.HISTOGRAM_SUFFIXES:
        if name.endswith(suffix):
            base = ev.metric(name[: -len(suffix)])
            if base is not None and base.type in {"histogram", "summary"}:
                if suffix == "_bucket" and base.type == "summary":
                    continue
                return base, suffix
    for suffix in promql.COUNTER_SUFFIXES:
        if name.endswith(suffix):
            base = ev.metric(name[: -len(suffix)])
            if base is not None and base.type == "counter":
                return base, suffix
    return None


def _base_candidates(name: str) -> list[tuple[str, str]]:
    out = [(name, "")]
    for suffix in (*promql.HISTOGRAM_SUFFIXES, *promql.COUNTER_SUFFIXES):
        if name.endswith(suffix):
            out.append((name[: -len(suffix)], suffix))
    return out


def lint_expressions(
    expressions: list[Expression],
    engine: str,
    to_version: str | None,
    from_version: str | None = None,
) -> LintResult:
    new = db.load(engine, to_version)
    old = db.load(engine, from_version) if from_version else None
    prefix = PREFIXES.get(new.engine)
    result = LintResult(
        new.engine, new.version, old.version if old else None, len({e.file for e in expressions}), len(expressions), 0
    )
    if prefix is None:
        return result
    renames: dict[str, diffing.Change] = {}
    if old is not None:
        for change in diffing.diff_versions(old, new).select("metrics", "renamed"):
            renames[change.name] = change
    seen: set[tuple[str, str, str, str]] = set()
    for expression in expressions:
        for selector in promql.extract_selectors(expression.expr):
            name = selector.metric
            if not name.startswith(prefix):
                continue
            result.references += 1
            hit = resolve_metric(new, name)
            if hit is None:
                problem = _missing(expression, name, new, old, renames)
                key = (problem.file, problem.location, problem.metric, problem.kind)
                if key not in seen:
                    seen.add(key)
                    result.problems.append(problem)
                continue
            metric, _ = hit
            for label in selector.labels:
                if label in TARGET_LABELS or label in metric.labels:
                    continue
                was_there = False
                if old is not None:
                    before = resolve_metric(old, name)
                    was_there = before is not None and label in before[0].labels
                if metric.labels_dynamic and not was_there:
                    continue
                message = (
                    f"label {label!r} was removed from {metric.name} in {new.version}"
                    if was_there
                    else f"label {label!r} is not a label of {metric.name} in {new.label} (labels: {', '.join(metric.labels) or 'none'})"
                )
                key = (expression.file, expression.location, name, "label:" + label)
                if key in seen:
                    continue
                seen.add(key)
                result.problems.append(
                    Problem(
                        expression.file,
                        expression.location,
                        name,
                        ERROR if was_there else WARNING,
                        "label",
                        message,
                        ref=new.cite(metric),
                    )
                )
    return result


def _missing(
    expression: Expression,
    name: str,
    new: db.EngineVersion,
    old: db.EngineVersion | None,
    renames: dict[str, diffing.Change],
) -> Problem:
    if old is not None:
        before = resolve_metric(old, name)
        if before is not None:
            metric, suffix = before
            rename = renames.get(metric.name)
            if rename is not None:
                return Problem(
                    expression.file,
                    expression.location,
                    name,
                    ERROR,
                    "renamed",
                    f"{metric.name} is gone in {new.version}; probably renamed to {rename.new_name} ({rename.detail})",
                    rename.new_name + suffix,
                    rename.ref,
                )
            same_name = new.metric(metric.name)
            if same_name is not None:
                return Problem(
                    expression.file,
                    expression.location,
                    name,
                    ERROR,
                    "type",
                    f"{metric.name} changed type from {metric.type} to {same_name.type} in {new.version}, "
                    f"so the series {name} no longer exists",
                    ref=new.cite(same_name),
                )
            return Problem(
                expression.file,
                expression.location,
                name,
                ERROR,
                "removed",
                f"{metric.name} exists in {old.version} but not in {new.version}",
                ref=old.cite(metric),
            )
    for base, suffix in _base_candidates(name):
        plain = new.metric(base)
        if plain is not None:
            return Problem(
                expression.file,
                expression.location,
                name,
                ERROR,
                "type",
                f"{base} is a {plain.type} in {new.label}; it has no {suffix} series",
                ref=new.cite(plain),
            )
    elsewhere = [v for v in db.versions(new.engine) if resolve_metric(db.load(new.engine, v), name) is not None]
    if elsewhere:
        span = elsewhere[0] if len(elsewhere) == 1 else f"{elsewhere[0]} to {elsewhere[-1]}"
        return Problem(
            expression.file,
            expression.location,
            name,
            ERROR,
            "not-in-version",
            f"not defined in {new.label}; it is defined in {new.engine} {span}",
        )
    return Problem(
        expression.file,
        expression.location,
        name,
        ERROR,
        "unknown",
        f"not defined in any {new.engine} version in the database",
    )


def lint_files(paths: list[Path], engine: str, to_version: str | None, from_version: str | None = None) -> LintResult:
    expressions: list[Expression] = []
    for path in paths:
        expressions += read_expressions(path)
    result = lint_expressions(expressions, engine, to_version, from_version)
    result.files = len(paths)
    return result
