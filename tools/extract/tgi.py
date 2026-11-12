"""Static extractor for Hugging Face Text Generation Inference.

Launcher options come from the ``clap`` derive struct ``Args`` in
``launcher/src/main.rs``; metrics from the ``metrics::*!`` macros in the
router and backends.
"""

from __future__ import annotations

import re
from typing import Any

from .common import Repo, clean_help, looks_deprecated, source

REPO_URL = "https://github.com/huggingface/text-generation-inference"
LAUNCHER = "launcher/src/main.rs"

_NUMERIC = {
    "usize": "int",
    "u8": "int",
    "u16": "int",
    "u32": "int",
    "u64": "int",
    "i32": "int",
    "i64": "int",
    "isize": "int",
    "f32": "float",
    "f64": "float",
    "bool": "bool",
    "String": "str",
    "PathBuf": "str",
}


def _block(text: str, start: int) -> tuple[int, int]:
    """Return (body_start, body_end) of the brace block opening at/after start."""
    open_idx = text.index("{", start)
    depth = 0
    i = open_idx
    while i < len(text):
        ch = text[i]
        if ch == '"':
            i += 1
            while i < len(text) and text[i] != '"':
                i += 2 if text[i] == "\\" else 1
        elif text.startswith("//", i):
            i = text.find("\n", i)
            continue
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return open_idx + 1, i
        i += 1
    raise ValueError("unbalanced braces")


def _kebab(name: str) -> str:
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "-", name).lower()


def parse_enums(text: str) -> dict[str, list[str]]:
    enums: dict[str, list[str]] = {}
    for match in re.finditer(r"#\[derive\(([^)]*)\)\]\s*(?:#\[[^\]]*\]\s*)*(?:pub\s+)?enum\s+(\w+)", text):
        if "ValueEnum" not in match.group(1):
            continue
        start, end = _block(text, match.end())
        values: list[str] = []
        pending: str | None = None
        for raw in text[start:end].splitlines():
            line = raw.strip()
            if not line or line.startswith("//"):
                continue
            named = re.match(r'#\[(?:clap|value)\(.*?name\s*=\s*"([^"]+)"', line)
            if named:
                pending = named.group(1)
                continue
            if line.startswith("#["):
                continue
            variant = re.match(r"(\w+)\s*,?$", line)
            if variant:
                values.append(pending or _kebab(variant.group(1)))
                pending = None
        enums[match.group(2)] = values
    return enums


def parse_args_struct(text: str, path: str, struct: str = "Args") -> list[dict[str, Any]]:
    match = re.search(rf"^(?:pub\s+)?struct\s+{struct}\s*\{{", text, re.MULTILINE)
    if match is None:
        return []
    enums = parse_enums(text)
    start, end = _block(text, match.start())
    base_line = text.count("\n", 0, start) + 1
    options: list[dict[str, Any]] = []
    doc: list[str] = []
    attr = ""
    in_attr = False
    for offset, raw in enumerate(text[start:end].splitlines()):
        line = raw.strip()
        if in_attr:
            attr += " " + line
            if line.endswith(")]"):
                in_attr = False
            continue
        if line.startswith("///"):
            doc.append(line[3:].strip())
            continue
        if line.startswith("#["):
            if re.match(r"#\[(clap|arg)\(", line):
                attr += " " + line
                in_attr = not line.endswith(")]")
            continue
        if line.startswith("//") or not line:
            continue
        field = re.match(r"(?:pub\s+)?(\w+)\s*:\s*(.+?),?$", line)
        if field is None:
            continue
        name, rtype = field.group(1), field.group(2).strip()
        is_flag = bool(re.search(r"\blong\b", attr))
        if is_flag:
            inner = re.sub(r"^Option<(.+)>$", r"\1", rtype)
            is_list = inner.startswith("Vec<")
            inner = re.sub(r"^Vec<(.+)>$", r"\1", inner)
            long_name = re.search(r'long\s*=\s*"([^"]+)"', attr)
            flag = "--" + (long_name.group(1) if long_name else name.replace("_", "-"))
            rec: dict[str, Any] = {
                "name": flag,
                "kind": "cli",
                "aliases": ["--" + a for a in re.findall(r'alias\s*=\s*"([^"]+)"', attr)],
                "type": _NUMERIC.get(inner, "str"),
                "help": clean_help(" ".join(doc)),
                "deprecated": False,
            }
            short = re.search(r"short\s*=\s*'(.)'", attr)
            if short:
                rec["aliases"].append("-" + short.group(1))
            if is_list:
                rec["type"] = f"list[{rec['type']}]"
            if inner in enums:
                rec["choices"] = enums[inner]
            elif rtype == "Option<bool>":
                rec["choices"] = ["true", "false"]
            default = re.search(r'default_value(?:_t)?\s*=\s*"([^"]*)"', attr)
            if default:
                rec["default"] = _coerce(default.group(1), rec["type"])
            elif rtype.startswith("Option<"):
                rec["default"] = None
            elif inner == "bool":
                rec["default"] = False
            env = re.search(r'\benv\s*=\s*"([^"]+)"', attr)
            if env:
                rec["env"] = env.group(1)
            elif re.search(r"\benv\b", attr):
                rec["env"] = name.upper()
            if looks_deprecated(rec["help"]) or re.search(r"\bdeprecated\b", rec["help"][:200], re.I):
                rec["deprecated"] = True
            rec["source"] = source(path, base_line + offset)
            options.append(rec)
        doc = []
        attr = ""
    return options


def _coerce(text: str, kind: str) -> Any:
    try:
        if kind == "int":
            return int(text)
        if kind == "float":
            return float(text)
    except ValueError:
        return text
    if kind == "bool":
        return text == "true"
    return text


_MACRO = re.compile(r"metrics::(counter|gauge|histogram)!\(\s*\"([a-z0-9_]+)\"((?:\s*,\s*\"[a-z_]+\"\s*=>[^,)]+)*)")
_DESCRIBE = re.compile(
    r"metrics::describe_(counter|gauge|histogram)!\(\s*\"([a-z0-9_]+)\"\s*,\s*(?:metrics::Unit::\w+\s*,\s*)?"
    r"((?:\"(?:[^\"\\]|\\.)*\"\s*)+)"
)


def parse_metrics(text: str, path: str, into: dict[str, dict[str, Any]]) -> bool:
    found = False
    for match in _DESCRIBE.finditer(text):
        rec = into.setdefault(
            match.group(2),
            {"name": match.group(2), "type": match.group(1), "labels": [], "help": ""},
        )
        rec["help"] = clean_help("".join(re.findall(r'"((?:[^"\\]|\\.)*)"', match.group(3))))
        rec["source"] = source(path, text.count("\n", 0, match.start()) + 1)
        found = True
    for match in _MACRO.finditer(text):
        rec = into.setdefault(
            match.group(2),
            {
                "name": match.group(2),
                "type": match.group(1),
                "labels": [],
                "help": "",
                "source": source(path, text.count("\n", 0, match.start()) + 1),
            },
        )
        for label in re.findall(r'"([a-z_]+)"\s*=>', match.group(3)):
            if label not in rec["labels"]:
                rec["labels"].append(label)
        found = True
    return found


def extract(repo: Repo) -> dict[str, Any]:
    text = repo.read(LAUNCHER)
    options = parse_args_struct(text, LAUNCHER)
    env_records = [
        {
            "name": o["env"],
            "kind": "env",
            "aliases": [],
            "type": o["type"],
            "help": f"Environment form of {o['name']}.",
            "deprecated": o["deprecated"],
            "flag": o["name"],
            "source": o["source"],
        }
        for o in options
        if "env" in o
    ]
    metrics: dict[str, dict[str, Any]] = {}
    sources = [LAUNCHER]
    for path in repo.ls():
        if not re.match(r"(router|backends/[\w-]+)/src/.*\.rs$", path):
            continue
        body = repo.read(path)
        if "metrics::" in body and parse_metrics(body, path, metrics):
            sources.append(path)
    for rec in metrics.values():
        rec["labels"] = sorted(rec["labels"])
    return {
        "options": sorted(options, key=lambda o: o["name"]) + sorted(env_records, key=lambda o: o["name"]),
        "metrics": sorted(metrics.values(), key=lambda m: m["name"]),
        "sources": sorted(sources),
        "notes": [
            "Options are those of text-generation-launcher (the Docker image entrypoint).",
            "Metrics are collected from every router/backend source file, so the list is the "
            "union over backends; a given backend exposes a subset.",
        ],
    }
