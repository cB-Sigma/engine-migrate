"""Plain-text, Markdown and JSON renderers for the CLI."""

from __future__ import annotations

import json
import textwrap
from collections.abc import Sequence
from typing import Any

from . import db
from .diffing import Change, ConfigImpact, VersionDiff
from .lint import LintResult
from .translate import STATUSES, Translation

WIDTH = 100
FORMATS = ("table", "json", "markdown")


def to_json(data: Any) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False)


def _wrap(text: str, indent: str, width: int = WIDTH) -> list[str]:
    if not text:
        return []
    return textwrap.wrap(
        text,
        width=width,
        initial_indent=indent,
        subsequent_indent=indent,
        break_long_words=False,
        break_on_hyphens=False,
    )


def table(headers: Sequence[str], rows: Sequence[Sequence[str]], max_last: int = 60) -> str:
    """A fixed-width text table; the last column wraps."""
    if not rows:
        return "(none)"
    widths = [len(h) for h in headers]
    for row in rows:
        for idx, cell in enumerate(row[:-1]):
            widths[idx] = max(widths[idx], len(cell))
    lines = ["  ".join(h.ljust(widths[i]) for i, h in enumerate(headers)).rstrip()]
    lines.append("  ".join("-" * widths[i] for i in range(len(headers) - 1)) + "  " + "-" * min(max_last, 40))
    pad = sum(widths[:-1]) + 2 * (len(headers) - 1)
    for row in rows:
        head = "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row[:-1]))
        last = textwrap.wrap(row[-1], width=max_last, break_long_words=False, break_on_hyphens=False) or [""]
        lines.append(f"{head}  {last[0]}".rstrip())
        lines += [" " * pad + extra for extra in last[1:]]
    return "\n".join(lines)


def md_table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    def esc(cell: str) -> str:
        return cell.replace("|", "\\|").replace("\n", " ")

    out = ["| " + " | ".join(headers) + " |", "|" + "|".join(" --- " for _ in headers) + "|"]
    out += ["| " + " | ".join(esc(c) for c in row) + " |" for row in rows]
    return "\n".join(out)


def _code(text: str) -> str:
    return f"`{text}`" if text else ""


# ---------------------------------------------------------------------------
# translate
# ---------------------------------------------------------------------------


def _summary_line(result: Translation) -> str:
    counts = result.counts()
    return ", ".join(f"{counts[s]} {s.lower()}" for s in STATUSES)


def translation_text(result: Translation, output_only: bool = False) -> str:
    if output_only:
        return result.output
    lines = [
        f"# {result.source.label} -> {result.target.label}  (input read as: {result.shape})",
        "",
        result.output or "(no option could be translated)",
        "",
        f"## Report: {_summary_line(result)}",
        "",
    ]
    pad = " " * 15
    for item in result.items:
        arrow = f"  ->  {item.target}" if item.target else ""
        lines.append(f"{item.status:<14} {item.source}{arrow}")
        lines += _wrap(item.explanation, pad)
        for note in item.notes:
            lines += _wrap(f"note: {note}", pad)
        if item.source_ref:
            lines.append(f"{pad}source: {item.source_ref}")
        if item.target_ref:
            lines.append(f"{pad}target: {item.target_ref}")
    if result.advisories:
        lines += ["", "## Also check", ""]
        for advisory in result.advisories:
            wrapped = _wrap(advisory, "  ")
            wrapped[0] = "- " + wrapped[0][2:]
            lines += wrapped
    return "\n".join(lines)


def translation_markdown(result: Translation) -> str:
    fence = {"kubernetes": "yaml", "yaml": "yaml", "env": "sh"}.get(result.shape, "sh")
    rows = []
    for item in result.items:
        why = " ".join([item.explanation, *(f"Note: {n}" for n in item.notes)]).strip()
        refs = "<br>".join(_code(r) for r in (item.source_ref, item.target_ref) if r)
        rows.append([item.status, _code(item.source), _code(item.target), why, refs])
    parts = [
        f"### {result.source.label} → {result.target.label}",
        "",
        f"```{fence}",
        result.output,
        "```",
        "",
        f"**{_summary_line(result)}**",
        "",
        md_table(["Status", "Input", "Output", "Why", "Source"], rows),
    ]
    if result.advisories:
        parts += ["", "Also check:", ""] + [f"- {a}" for a in result.advisories]
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# diff
# ---------------------------------------------------------------------------

_KIND_TITLES = {
    "removed": "removed",
    "renamed": "renamed",
    "deprecated": "newly deprecated",
    "changed": "changed",
    "added": "added",
}


def _change_row(change: Change) -> list[str]:
    name = change.name
    if change.kind == "renamed":
        name = f"{change.name} -> {change.new_name}"
        if change.inferred:
            name += " (inferred)"
    return [name, change.detail]


def filter_changes(vdiff: VersionDiff, kinds: set[str] | None, only: str | None) -> list[Change]:
    out = []
    for change in vdiff.changes:
        if only and change.category != only:
            continue
        if change.category == "options" and kinds is not None and change.option_kind not in kinds:
            continue
        out.append(change)
    return out


def diff_text(vdiff: VersionDiff, changes: list[Change]) -> str:
    lines = [
        f"# {vdiff.old.engine}: {vdiff.old.version} -> {vdiff.new.version}  ({vdiff.old.tag} .. {vdiff.new.tag})",
        "",
    ]
    for category, title in (("options", "Options"), ("metrics", "Metrics")):
        for kind, kind_title in _KIND_TITLES.items():
            group = [c for c in changes if c.category == category and c.kind == kind]
            if not group:
                continue
            lines += [f"## {title} {kind_title} ({len(group)})", ""]
            lines.append(table(["name", "detail"], [_change_row(c) for c in group]))
            lines.append("")
    if len(lines) == 2:
        lines.append("No differences in the selected categories.")
    return "\n".join(lines).rstrip()


def diff_markdown(vdiff: VersionDiff, changes: list[Change]) -> str:
    parts = [f"### {vdiff.old.engine} {vdiff.old.version} → {vdiff.new.version}", ""]
    for category, title in (("options", "Options"), ("metrics", "Metrics")):
        for kind, kind_title in _KIND_TITLES.items():
            group = [c for c in changes if c.category == category and c.kind == kind]
            if not group:
                continue
            parts += [f"**{title} {kind_title} ({len(group)})**", ""]
            parts.append(md_table(["Name", "Detail", "Source"], [[*_change_row(c), _code(c.ref)] for c in group]))
            parts.append("")
    if len(parts) == 2:
        parts.append("No differences in the selected categories.")
    return "\n".join(parts).rstrip()


def impact_text(impact: ConfigImpact) -> str:
    old, new = impact.diff.old, impact.diff.new
    lines = [
        f"# {old.engine}: {old.version} -> {new.version}, for this configuration  (input read as: {impact.shape})",
        "",
    ]
    if impact.findings:
        rows = [[f.severity.upper(), f.option, f.message] for f in impact.findings]
        lines.append(table(["severity", "option", "what changes"], rows, max_last=64))
    else:
        lines.append("None of the options set in this configuration changed.")
    if impact.unset_default_changes:
        lines += [
            "",
            f"## Defaults that changed for options this configuration does not set ({len(impact.unset_default_changes)})",
            "",
        ]
        lines.append(
            table(["option", "change"], [[c.name, c.detail] for c in impact.unset_default_changes], max_last=64)
        )
    lines += ["", f"## Suggested configuration for {new.label}", "", impact.rewritten]
    if impact.rewrite_notes:
        lines.append("")
        for note in impact.rewrite_notes:
            lines.append(f"- {note}")
    return "\n".join(lines)


def impact_markdown(impact: ConfigImpact) -> str:
    old, new = impact.diff.old, impact.diff.new
    parts = [f"### {old.engine} {old.version} → {new.version} for this configuration", ""]
    if impact.findings:
        parts.append(
            md_table(
                ["Severity", "Option", "What changes", "Source"],
                [[f.severity.upper(), _code(f.option), f.message, _code(f.ref)] for f in impact.findings],
            )
        )
    else:
        parts.append("None of the options set in this configuration changed.")
    if impact.unset_default_changes:
        parts += ["", "Defaults that changed for options this configuration does not set:", ""]
        parts.append(md_table(["Option", "Change"], [[_code(c.name), c.detail] for c in impact.unset_default_changes]))
    fence = {"kubernetes": "yaml", "yaml": "yaml"}.get(impact.shape, "sh")
    parts += ["", f"Suggested configuration for {new.label}:", "", f"```{fence}", impact.rewritten, "```"]
    parts += [f"- {n}" for n in impact.rewrite_notes]
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# lint
# ---------------------------------------------------------------------------


def lint_text(result: LintResult) -> str:
    scope = f"{result.engine} {result.to_version}" + (
        f" (upgrading from {result.from_version})" if result.from_version else ""
    )
    lines = []
    for problem in result.problems:
        lines.append(f"{problem.file}: {problem.location}")
        lines.append(f"  {problem.severity.upper()} [{problem.kind}] {problem.metric}")
        lines += _wrap(problem.message, "    ")
        if problem.suggestion:
            lines.append(f"    suggestion: {problem.suggestion}")
        if problem.ref:
            lines.append(f"    source: {problem.ref}")
    if lines:
        lines.append("")
    lines.append(
        f"{result.files} file(s), {result.expressions} expression(s), {result.references} "
        f"{result.engine} metric reference(s) checked against {scope}: "
        f"{result.count('error')} error(s), {result.count('warning')} warning(s)"
    )
    return "\n".join(lines)


def lint_markdown(result: LintResult) -> str:
    rows = [
        [
            p.severity.upper(),
            p.kind,
            _code(p.metric),
            f"{p.file}: {p.location}",
            p.message + (f" Suggestion: {_code(p.suggestion)}" if p.suggestion else ""),
        ]
        for p in result.problems
    ]
    head = f"### Metric lint against {result.engine} {result.to_version}: {result.count('error')} error(s), {result.count('warning')} warning(s)"
    return (
        head
        + "\n\n"
        + (md_table(["Severity", "Kind", "Metric", "Where", "Problem"], rows) if rows else "No problems found.")
    )


# ---------------------------------------------------------------------------
# browse
# ---------------------------------------------------------------------------


def _default_text(option: db.Option) -> str:
    if option.has_default:
        return "unset" if option.default is None else str(option.default)
    return ""


def options_rows(options: Sequence[db.Option]) -> list[list[str]]:
    rows = []
    for o in options:
        names = ", ".join([o.name, *o.aliases])
        flags = " [deprecated]" if o.deprecated else ""
        rows.append([names + flags, o.type, _default_text(o), o.help])
    return rows


def options_text(ev: db.EngineVersion, options: Sequence[db.Option]) -> str:
    head = f"# {ev.label} ({ev.tag}): {len(options)} option(s)\n\n"
    return head + table(["name", "type", "default", "help"], options_rows(options), max_last=56)


def options_markdown(ev: db.EngineVersion, options: Sequence[db.Option]) -> str:
    rows = [
        [
            _code(r[0].replace(" [deprecated]", "")) + (" (deprecated)" if "[deprecated]" in r[0] else ""),
            r[1],
            _code(r[2]),
            r[3],
        ]
        for r in options_rows(options)
    ]
    return f"### {ev.label} options\n\n" + md_table(["Name", "Type", "Default", "Help"], rows)


def metrics_rows(metrics: Sequence[db.Metric]) -> list[list[str]]:
    return [[m.name, m.type, ", ".join(m.labels) + (" +runtime" if m.labels_dynamic else ""), m.help] for m in metrics]


def metrics_text(ev: db.EngineVersion, metrics: Sequence[db.Metric]) -> str:
    head = f"# {ev.label} ({ev.tag}): {len(metrics)} metric(s)\n\n"
    return head + table(["name", "type", "labels", "help"], metrics_rows(metrics), max_last=56)


def metrics_markdown(ev: db.EngineVersion, metrics: Sequence[db.Metric]) -> str:
    return f"### {ev.label} metrics\n\n" + md_table(
        ["Name", "Type", "Labels", "Help"], [[_code(r[0]), *r[1:]] for r in metrics_rows(metrics)]
    )


def coverage_rows() -> list[list[str]]:
    rows = []
    for engine in db.engines():
        for entry in db.index()[engine]:
            kinds = entry["options"]
            rows.append(
                [
                    db.ENGINE_TITLES[engine],
                    entry["version"],
                    entry["tag_date"],
                    str(kinds.get("cli", 0)),
                    str(sum(v for k, v in kinds.items() if k != "cli")),
                    str(entry["metrics"]),
                ]
            )
    return rows


COVERAGE_HEADERS = ["engine", "version", "tag date", "flags", "env/params", "metrics"]
