"""Command line interface."""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Sequence
from importlib import resources
from pathlib import Path

from . import __version__, db, diffing, lint, render, translate
from .errors import EngineMigrateError

EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_USAGE = 2

DEMO_FILES = ("vllm-serve.sh", "sglang-launch.sh", "grafana-dashboard.json", "alert-rules.yaml")


def _read_config(value: str) -> str:
    """``value`` is a file path, ``-`` for stdin, or the configuration itself."""
    if value == "-":
        return sys.stdin.read()
    looks_inline = "\n" in value or re.search(r"\s--?\w", value) is not None
    if not looks_inline:
        path = Path(value)
        if path.is_file():
            return path.read_text(encoding="utf-8")
        raise EngineMigrateError(f"no such file: {value}")
    try:
        if Path(value).is_file():
            return Path(value).read_text(encoding="utf-8")
    except OSError:
        pass
    return value


def _demo_path(name: str) -> Path:
    return Path(str(resources.files("engine_migrate").joinpath("data").joinpath("demo").joinpath(name)))


def _add_format(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--format", "-f", choices=render.FORMATS, default="table", help="output format (default: table)"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="engine-migrate",
        description=(
            "Translate LLM serving configurations between inference engines, diff engine "
            "versions, and lint dashboards against an engine's metrics - all from a database "
            "extracted statically from engine source at release tags."
        ),
    )
    parser.add_argument("--version", action="version", version=f"engine-migrate {__version__}")
    parser.add_argument("--demo", action="store_true", help="run translate, diff and lint-metrics on bundled examples")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    p = sub.add_parser("translate", help="translate a configuration to another engine")
    p.add_argument("--from", dest="src", required=True, metavar="ENGINE", help="source engine")
    p.add_argument("--to", dest="dst", required=True, metavar="ENGINE", help="target engine")
    p.add_argument("--from-version", metavar="VERSION", help="source version (default: newest in the database)")
    p.add_argument("--to-version", metavar="VERSION", help="target version (default: newest in the database)")
    p.add_argument(
        "--shape",
        choices=("auto", "command", "docker", "kubernetes", "yaml", "env"),
        default="auto",
        help="how to read the input (default: auto-detect)",
    )
    p.add_argument("--output-only", action="store_true", help="print only the translated configuration")
    p.add_argument("--strict", action="store_true", help="exit 1 if any option is NO EQUIVALENT or UNKNOWN")
    p.add_argument("config", help="file, '-' for stdin, or the command line itself")
    _add_format(p)

    p = sub.add_parser("diff", help="what changed between two versions of an engine")
    p.add_argument("engine")
    p.add_argument("from_version", metavar="FROM")
    p.add_argument("to_version", metavar="TO")
    p.add_argument("--config", help="only report what affects this configuration, and suggest a rewrite")
    p.add_argument("--shape", choices=("auto", "command", "docker", "kubernetes", "yaml", "env"), default="auto")
    p.add_argument("--only", choices=("options", "metrics"), help="restrict to options or metrics")
    p.add_argument(
        "--kind",
        action="append",
        choices=("cli", "env", "param", "modelfile"),
        help="option kinds to include (default: cli, param, modelfile; repeatable)",
    )
    p.add_argument(
        "--exit-code",
        action="store_true",
        help="exit 1 if there are differences (with --config: if the configuration is affected)",
    )
    _add_format(p)

    p = sub.add_parser("lint-metrics", help="check dashboards and alert rules against a version's metrics")
    p.add_argument("--engine", required=True)
    p.add_argument("--to", dest="to_version", metavar="VERSION", help="version to check against (default: newest)")
    p.add_argument(
        "--from",
        dest="from_version",
        metavar="VERSION",
        help="version the files were written for (enables rename and removal detection)",
    )
    p.add_argument("--strict", action="store_true", help="treat warnings as errors for the exit code")
    p.add_argument("files", nargs="+", type=Path, help="Grafana dashboard JSON and Prometheus rule YAML files")
    _add_format(p)

    for name, noun in (("flags", "options"), ("metrics", "metrics")):
        p = sub.add_parser(name, help=f"browse the {noun} of an engine version")
        p.add_argument("engine")
        p.add_argument("version", nargs="?", help="default: newest in the database")
        p.add_argument("--grep", metavar="TEXT", help="only entries whose name or help contains TEXT")
        if name == "flags":
            p.add_argument(
                "--kind",
                action="append",
                choices=("cli", "env", "param", "modelfile"),
                help="option kinds to show (default: all; repeatable)",
            )
            p.add_argument("--deprecated", action="store_true", help="only deprecated options")
        _add_format(p)

    p = sub.add_parser("versions", help="list engines and versions in the database")
    _add_format(p)
    return parser


def _shape(value: str) -> str | None:
    return None if value == "auto" else value


def cmd_translate(args: argparse.Namespace) -> int:
    result = translate.translate(
        _read_config(args.config), args.src, args.dst, args.from_version, args.to_version, _shape(args.shape)
    )
    if args.format == "json":
        print(render.to_json(result.as_dict()))
    elif args.format == "markdown":
        print(render.translation_markdown(result))
    else:
        print(render.translation_text(result, args.output_only))
    counts = result.counts()
    if args.strict and (counts[translate.NO_EQUIVALENT] or counts[translate.UNKNOWN]):
        return EXIT_FINDINGS
    return EXIT_OK


def cmd_diff(args: argparse.Namespace) -> int:
    vdiff = diffing.diff(args.engine, args.from_version, args.to_version)
    if args.config:
        impact = diffing.config_impact(_read_config(args.config), vdiff, _shape(args.shape))
        if args.format == "json":
            print(render.to_json(impact.as_dict()))
        elif args.format == "markdown":
            print(render.impact_markdown(impact))
        else:
            print(render.impact_text(impact))
        return EXIT_FINDINGS if args.exit_code and impact.affected else EXIT_OK
    kinds = set(args.kind) if args.kind else {"cli", "param", "modelfile"}
    changes = render.filter_changes(vdiff, kinds, args.only)
    if args.format == "json":
        data = vdiff.as_dict()
        data["changes"] = [c.as_dict() for c in changes]
        print(render.to_json(data))
    elif args.format == "markdown":
        print(render.diff_markdown(vdiff, changes))
    else:
        print(render.diff_text(vdiff, changes))
    return EXIT_FINDINGS if args.exit_code and changes else EXIT_OK


def cmd_lint(args: argparse.Namespace) -> int:
    missing = [str(p) for p in args.files if not p.is_file()]
    if missing:
        raise EngineMigrateError("no such file: " + ", ".join(missing))
    result = lint.lint_files(list(args.files), args.engine, args.to_version, args.from_version)
    if args.format == "json":
        print(render.to_json(result.as_dict()))
    elif args.format == "markdown":
        print(render.lint_markdown(result))
    else:
        print(render.lint_text(result))
    return result.exit_code(args.strict)


def cmd_flags(args: argparse.Namespace) -> int:
    ev = db.load(args.engine, args.version)
    kinds = set(args.kind) if args.kind else None
    needle = (args.grep or "").lower()
    options = [
        o
        for o in ev.options
        if (kinds is None or o.kind in kinds)
        and (not args.deprecated or o.deprecated)
        and (not needle or needle in o.name.lower() or needle in o.help.lower() or any(needle in a for a in o.aliases))
    ]
    if args.format == "json":
        print(
            render.to_json(
                {"engine": ev.engine, "version": ev.version, "tag": ev.tag, "options": [o.raw for o in options]}
            )
        )
    elif args.format == "markdown":
        print(render.options_markdown(ev, options))
    else:
        print(render.options_text(ev, options))
    return EXIT_OK


def cmd_metrics(args: argparse.Namespace) -> int:
    ev = db.load(args.engine, args.version)
    needle = (args.grep or "").lower()
    metrics = [m for m in ev.metrics if not needle or needle in m.name.lower() or needle in m.help.lower()]
    if args.format == "json":
        data = [
            {
                "name": m.name,
                "type": m.type,
                "labels": list(m.labels),
                "labels_dynamic": m.labels_dynamic,
                "help": m.help,
                "source": {"path": m.source_path, "line": m.source_line},
            }
            for m in metrics
        ]
        print(render.to_json({"engine": ev.engine, "version": ev.version, "tag": ev.tag, "metrics": data}))
    elif args.format == "markdown":
        print(render.metrics_markdown(ev, metrics))
    else:
        print(render.metrics_text(ev, metrics))
        if not ev.metrics:
            print("\n" + " ".join(n for n in ev.notes if "metric" in n.lower()))
    return EXIT_OK


def cmd_versions(args: argparse.Namespace) -> int:
    rows = render.coverage_rows()
    if args.format == "json":
        print(render.to_json(db.index()))
    elif args.format == "markdown":
        print(render.md_table([h.capitalize() for h in render.COVERAGE_HEADERS], rows))
    else:
        print(render.table(render.COVERAGE_HEADERS, rows))
    return EXIT_OK


def cmd_demo() -> int:
    """Run the three main commands on the examples bundled with the package."""
    vllm_config = _demo_path("vllm-serve.sh").read_text(encoding="utf-8")
    sglang_config = _demo_path("sglang-launch.sh").read_text(encoding="utf-8")
    dashboards = [_demo_path("grafana-dashboard.json"), _demo_path("alert-rules.yaml")]

    print("$ engine-migrate translate --from vllm --to sglang vllm-serve.sh")
    print(render.translation_text(translate.translate(vllm_config, "vllm", "sglang", "0.33.0", "0.5.23")))
    print("\n$ engine-migrate diff sglang 0.5.18 0.5.23 --config sglang-launch.sh")
    print(render.impact_text(diffing.config_impact(sglang_config, diffing.diff("sglang", "0.5.18", "0.5.23"))))
    print("\n$ engine-migrate lint-metrics --engine vllm --to 0.31.0 grafana-dashboard.json alert-rules.yaml")
    result = lint.lint_files(dashboards, "vllm", "0.31.0")
    for problem in result.problems:
        problem.file = Path(problem.file).name
    print(render.lint_text(result))
    print(f"\nThe demo inputs are in {_demo_path('vllm-serve.sh').parent}")
    return EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handlers = {
        "translate": cmd_translate,
        "diff": cmd_diff,
        "lint-metrics": cmd_lint,
        "flags": cmd_flags,
        "metrics": cmd_metrics,
        "versions": cmd_versions,
    }
    try:
        if args.demo:
            return cmd_demo()
        if args.command is None:
            parser.print_help()
            return EXIT_USAGE
        return handlers[args.command](args)
    except EngineMigrateError as exc:
        print(f"engine-migrate: error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except BrokenPipeError:
        return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
