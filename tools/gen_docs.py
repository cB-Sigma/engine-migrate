"""Regenerate the generated parts of README.md and docs/coverage.md.

Usage::

    python -m tools.gen_docs            # rewrite in place
    python -m tools.gen_docs --check    # exit 1 if anything is out of date

Two kinds of blocks are kept in sync:

* ``<!-- engine-migrate: ARGS -->`` followed by a fenced block: the block is
  the output of ``engine-migrate ARGS`` run from the repository root;
* ``<!-- generated:NAME -->`` ... ``<!-- /generated:NAME -->``: tables built
  from the database index and docs/extraction-validation.json.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import re
import shlex
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from engine_migrate import db  # noqa: E402
from engine_migrate.cli import main as cli_main  # noqa: E402

COMMAND = re.compile(r"(<!-- engine-migrate: (.+?) -->\n```(\w*)\n)(.*?)(^```$)", re.DOTALL | re.MULTILINE)
GENERATED = re.compile(r"(<!-- generated:([\w-]+) -->\n)(.*?)(^<!-- /generated:\2 -->)", re.DOTALL | re.MULTILINE)

METHODS = {
    "vllm": "Python `ast`: argparse calls + config dataclasses (`get_kwargs`), `envs.py`, metric constructors",
    "sglang": "Python `ast`: `A[T, Arg(...)]` annotated fields + argparse calls, metric constructors",
    "llamacpp": "C++ scan of `add_opt(common_arg(...))` in `common/arg.cpp`, defaults from `common/common.h`, metric tables in the server",
    "tgi": "Rust scan of the `clap` struct `Args` in `launcher/src/main.rs`, `metrics::*!` macros",
    "ollama": "Go scan of `envconfig/config.go`, `api/types.go`, `parser/parser.go`",
}
REFERENCES = {
    "vllm": "CLI reference on docs.vllm.ai for each tag (built by vLLM from the live argparse parser)",
    "sglang": "`server_arguments.mdx` in the repo; complete from 0.5.22 on, where SGLang's own test also keeps a list of undocumented flags",
    "llamacpp": "help tables in `tools/server/README.md` (written by `llama-gen-docs`)",
    "tgi": "`docs/source/reference/launcher.md` (a `--help` dump)",
    "ollama": "env vars referenced by command help in `cmd/cmd.go` (weak: no generated reference exists)",
}


def run_cli(args: str) -> str:
    buffer = io.StringIO()
    cwd = os.getcwd()
    os.chdir(ROOT)
    try:
        with contextlib.redirect_stdout(buffer):
            cli_main(shlex.split(args))
    finally:
        os.chdir(cwd)
    return buffer.getvalue().rstrip("\n")


def _validation() -> dict[str, dict[str, Any]]:
    data: dict[str, dict[str, Any]] = json.loads(
        (ROOT / "docs" / "extraction-validation.json").read_text(encoding="utf-8")
    )
    return data


def _aggregate(engine: str) -> dict[str, tuple[int, int]]:
    totals: dict[str, tuple[int, int]] = {}
    for result in _validation()[engine].values():
        for check in result.get("checks", []):
            name = check["check"].split(" (")[0]
            agree, total = totals.get(name, (0, 0))
            totals[name] = (agree + check["agree"], total + check["total"])
    return totals


def _pct(agree: int, total: int) -> str:
    if total == 0:
        return "n/a"
    value = 100.0 * agree / total
    text = "100%" if agree == total else f"{min(value, 99.9):.1f}%"
    return f"{agree}/{total} ({text})"


def _range(values: list[int]) -> str:
    return str(values[0]) if min(values) == max(values) else f"{min(values)}-{max(values)}"


SHORT = {
    "reference flags found by extraction": "flags found",
    "documented flags found by extraction": "documented flags found",
    "extracted flags present in reference": "no spurious flags",
    "aliases and negations identical": "aliases/negations",
    "defaults identical": "defaults",
    "choices identical": "choices",
    "env var identical": "env names",
    "rendered help incl. defaults identical": "rendered help incl. defaults",
    "referenced env vars found by extraction": "env vars found",
    "metric names in the engine's own docs/dashboards that resolve": "metric names in the engine's docs/dashboards",
}


def coverage_table() -> str:
    lines = [
        "| Engine | Versions in the database | Flags | Env vars / params | Metrics | Extraction method | Measured agreement with the engine's own reference |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for engine in ("vllm", "sglang", "llamacpp", "tgi", "ollama"):
        entries = db.index()[engine]
        versions = ", ".join(e["version"] for e in entries)
        flags = _range([e["options"].get("cli", 0) for e in entries])
        other = _range([sum(v for k, v in e["options"].items() if k != "cli") for e in entries])
        metrics = _range([e["metrics"] for e in entries])
        agreement = "; ".join(
            f"{SHORT[name]} {_pct(*score)}" for name, score in _aggregate(engine).items() if name in SHORT
        )
        lines.append(
            f"| {db.ENGINE_TITLES[engine]} | {versions} | {flags} | {other} | {metrics} | {METHODS[engine]} | "
            f"{agreement}. Reference: {REFERENCES[engine]} |"
        )
    return "\n".join(lines)


def per_version_table() -> str:
    lines = [
        "| Engine | Version | Tag | Tag date | Flags | Env vars / params | Metrics | Checks (agree/total) |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    report = _validation()
    for engine in ("vllm", "sglang", "llamacpp", "tgi", "ollama"):
        for entry in db.index()[engine]:
            result = report[engine][entry["version"]]
            checks = "<br>".join(
                f"{SHORT.get(c['check'].split(' (')[0], c['check'])}: {c['agree']}/{c['total']}"
                for c in result.get("checks", [])
            )
            other = sum(v for k, v in entry["options"].items() if k != "cli")
            lines.append(
                f"| {db.ENGINE_TITLES[engine]} | {entry['version']} | `{entry['tag']}` | {entry['tag_date']} | "
                f"{entry['options'].get('cli', 0)} | {other} | {entry['metrics']} | {checks} |"
            )
    return "\n".join(lines)


def known_gaps() -> str:
    out: list[str] = []
    report = _validation()
    for engine in ("vllm", "sglang", "llamacpp", "tgi", "ollama"):
        for version, result in report[engine].items():
            items: list[str] = []
            if result.get("missing"):
                items.append("in the reference but not extracted: " + ", ".join(f"`{m}`" for m in result["missing"]))
            if result.get("extra"):
                items.append("extracted but not in the reference: " + ", ".join(f"`{m}`" for m in result["extra"]))
            for mismatch in result.get("mismatches", [])[:12]:
                items.append(mismatch.replace("|", "\\|"))
            missing_metrics = (result.get("metrics") or {}).get("missing") or {}
            if missing_metrics:
                items.append(
                    "metric names in the engine's docs that the source at this tag does not define: "
                    + ", ".join(f"`{m}`" for m in missing_metrics)
                )
            if items:
                out.append(f"- **{db.ENGINE_TITLES[engine]} {version}**")
                out += [f"  - {item}" for item in items]
    return "\n".join(out)


GENERATORS = {"coverage": coverage_table, "coverage-detail": per_version_table, "known-gaps": known_gaps}


def render(text: str) -> str:
    text = COMMAND.sub(lambda m: m.group(1) + run_cli(m.group(2)) + "\n" + m.group(5), text)
    return GENERATED.sub(lambda m: m.group(1) + GENERATORS[m.group(2)]() + "\n" + m.group(4), text)


FILES = ("README.md", "docs/coverage.md", "docs/usage.md")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    stale = []
    for name in FILES:
        path = ROOT / name
        before = path.read_text(encoding="utf-8")
        after = render(before)
        if after != before:
            stale.append(name)
            if not args.check:
                path.write_text(after, encoding="utf-8")
    if stale:
        print(("out of date: " if args.check else "updated: ") + ", ".join(stale))
    return 1 if (stale and args.check) else 0


if __name__ == "__main__":
    sys.exit(main())
