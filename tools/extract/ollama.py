"""Static extractor for Ollama.

Ollama is configured through environment variables (``envconfig/config.go``),
Modelfile instructions (``parser/parser.go``) and model parameters
(``api/types.go``).  It does not expose Prometheus metrics.
"""

from __future__ import annotations

import re
from typing import Any

from .common import Repo, clean_help, looks_deprecated, source

REPO_URL = "https://github.com/ollama/ollama"
ENVCONFIG = "envconfig/config.go"
TYPES = "api/types.go"
PARSER = "parser/parser.go"

_ENTRY = re.compile(
    r'(?:"(?P<key>\w+)":\s*\{|ret\["(?P<key2>\w+)"\]\s*=\s*EnvVar\{)\s*"(?P<name>\w+)",\s*'
    r'(?P<expr>.+?),\s*"(?P<desc>(?:[^"\\]|\\.)*)"\}'
)
_GO_TYPES = {
    "int": "int",
    "int64": "int",
    "uint": "int",
    "uint64": "int",
    "float32": "float",
    "float64": "float",
    "bool": "bool",
    "string": "str",
    "[]string": "list[str]",
}


def _unescape(text: str) -> str:
    return text.replace('\\"', '"').replace("\\\\", "\\")


def parse_env(text: str) -> list[dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for lineno, line in enumerate(text.splitlines(), start=1):
        match = _ENTRY.search(line)
        if match is None:
            continue
        desc = _unescape(match.group("desc"))
        expr = match.group("expr")
        rec: dict[str, Any] = {
            "name": match.group("name"),
            "kind": "env",
            "aliases": [],
            "type": "str",
            "help": clean_help(desc),
            "deprecated": looks_deprecated(desc),
        }
        if re.match(r"(Bool|BoolWithDefault)\b", expr) or re.search(r"\((true|false)\)$", expr):
            rec["type"] = "bool"
        default = re.search(r'\(default:? "?([^")]+)"?\)', desc)
        if default:
            rec["default"] = default.group(1).strip()
        rec["getter"] = expr
        rec["source"] = source(ENVCONFIG, lineno)
        out.setdefault(rec["name"], rec)
    return list(out.values())


def _struct_fields(text: str, name: str) -> list[tuple[str, str, str, str, int]]:
    match = re.search(rf"^type {name} struct \{{\n(.*?)^\}}", text, re.MULTILINE | re.DOTALL)
    if match is None:
        return []
    base = text.count("\n", 0, match.start(1)) + 1
    fields = []
    for offset, line in enumerate(match.group(1).splitlines()):
        found = re.match(r'\s*(\w+)\s+(\*?[\w\[\]\.]+)\s+`json:"(\w+)[^`]*`\s*(?://\s*(.*))?$', line)
        if found:
            fields.append(
                (found.group(1), found.group(2).lstrip("*"), found.group(3), found.group(4) or "", base + offset)
            )
    return fields


def parse_params(text: str) -> list[dict[str, Any]]:
    defaults: dict[str, Any] = {}
    body = re.search(r"^func DefaultOptions\(\) Options \{\n(.*?)^\}", text, re.MULTILINE | re.DOTALL)
    if body:
        for field, value in re.findall(r"^\s*(\w+):\s*([-\w.\"]+),", body.group(1), re.MULTILINE):
            if re.fullmatch(r"-?\d+", value):
                defaults[field] = int(value)
            elif re.fullmatch(r"-?\d+\.\d+", value):
                defaults[field] = float(value)
            elif value in {"true", "false"}:
                defaults[field] = value == "true"
    out: list[dict[str, Any]] = []
    for struct in ("Runner", "Options"):
        for field, gotype, json_name, comment, line in _struct_fields(text, struct):
            rec: dict[str, Any] = {
                "name": json_name,
                "kind": "param",
                "aliases": [],
                "type": _GO_TYPES.get(gotype, "str"),
                "help": clean_help(comment),
                "deprecated": looks_deprecated(comment),
                "scope": "load" if struct == "Runner" else "request",
                "go_field": f"{struct}.{field}",
            }
            if field in defaults:
                rec["default"] = defaults[field]
            rec["source"] = source(TYPES, line)
            out.append(rec)
    return out


def parse_instructions(text: str) -> list[dict[str, Any]]:
    func = re.search(r"func isValidCommand\(cmd string\) bool \{(.*?)\n\}", text, re.DOTALL)
    if func is None:
        return []
    line = text.count("\n", 0, func.start()) + 1
    names: list[str] = []
    for case in re.findall(r"case ([^:]+):\s*\n\s*return true", func.group(1)):
        names += re.findall(r'"(\w+)"', case)
    return [
        {
            "name": name.upper(),
            "kind": "modelfile",
            "aliases": [],
            "type": "str",
            "help": "Modelfile instruction.",
            "deprecated": False,
            "source": source(PARSER, line),
        }
        for name in names
    ]


def extract(repo: Repo) -> dict[str, Any]:
    options = parse_env(repo.read(ENVCONFIG))
    sources = [ENVCONFIG]
    if repo.exists(TYPES):
        options += parse_params(repo.read(TYPES))
        sources.append(TYPES)
    if repo.exists(PARSER):
        options += parse_instructions(repo.read(PARSER))
        sources.append(PARSER)
    return {
        "options": sorted(options, key=lambda o: (o["kind"], o["name"])),
        "metrics": [],
        "sources": sorted(sources),
        "notes": [
            "Ollama has no serving command-line flags; it is configured with environment "
            "variables (kind=env), Modelfile instructions (kind=modelfile) and model "
            "parameters set via Modelfile PARAMETER or the API options field (kind=param).",
            "Ollama does not expose a Prometheus metrics endpoint, so no metrics are listed.",
            "Environment variable defaults are only recorded when the description states them.",
        ],
    }
