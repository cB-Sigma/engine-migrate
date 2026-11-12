"""Measure extraction accuracy against references produced by the engines.

Each check compares the committed database with a reference that the engine
project itself generates from the *running* program:

* vLLM      - the versioned CLI reference on docs.vllm.ai (built from argparse)
* llama.cpp - the help tables in tools/server/README.md (written by llama-gen-docs)
* TGI       - docs/source/reference/launcher.md (a ``--help`` dump)
* SGLang    - docs/.../server_arguments.mdx (+ the "undocumented flags" list
              that SGLang's own test keeps complete from v0.5.23 on)
* Ollama    - env vars referenced by the ``ollama serve`` help in cmd/cmd.go

Usage::

    python -m tools.extract.validate --ref /home/user/ref [--cache DIR] [--offline]
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import urllib.request
from pathlib import Path
from typing import Any

from .common import Repo, SourceError
from .run import DB_DIR, ENGINES, PINS, ROOT, version_of

OUT = ROOT / "docs" / "extraction-validation.json"


def _norm(text: Any) -> str:
    text = html.unescape(str(text))
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("%%", "%")
    return re.sub(r"[\s`*\"'\\]+", "", text).lower()


def _db(engine: str, tag: str) -> dict[str, Any]:
    doc: dict[str, Any] = json.loads((DB_DIR / engine / f"{version_of(tag)}.json").read_text(encoding="utf-8"))
    return doc


def _cli(doc: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {o["name"]: o for o in doc["options"] if o["kind"] == "cli"}


def _spellings(opt: dict[str, Any]) -> set[str]:
    return {opt["name"], *opt.get("aliases", []), *opt.get("negations", [])}


def _score(name: str, agree: int, total: int) -> dict[str, Any]:
    return {"check": name, "agree": agree, "total": total}


def _set_scores(ours: set[str], theirs: set[str]) -> list[dict[str, Any]]:
    return [
        _score("reference flags found by extraction (recall)", len(ours & theirs), len(theirs)),
        _score("extracted flags present in reference (precision)", len(ours & theirs), len(ours)),
    ]


# -- vLLM -------------------------------------------------------------------


def _fetch(url: str, cache: Path, offline: bool) -> str | None:
    target = cache / re.sub(r"[^\w.-]+", "_", url)
    if target.exists():
        return target.read_text(encoding="utf-8")
    if offline:
        return None
    request = urllib.request.Request(url, headers={"User-Agent": "engine-migrate-validate"})
    try:
        with urllib.request.urlopen(request, timeout=60) as resp:
            body = str(resp.read().decode("utf-8", "replace"))
    except OSError:
        return None
    cache.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")
    return body


def parse_vllm_docs(page: str) -> dict[str, dict[str, Any]]:
    entries: dict[str, dict[str, Any]] = {}
    parts = re.split(r'<h4 id=(?:"[^"]*"|[^>\s]*)>', page)[1:]
    for part in parts:
        head, _, rest = part.partition("</h4>")
        names = [html.unescape(n) for n in re.findall(r"<code>([^<]+)</code>", head)]
        if not names or not names[0].startswith("-"):
            continue
        body = rest.split("</dl>", 1)[0]
        entry: dict[str, Any] = {"spellings": set(names)}
        choices = re.search(r"<dd>Possible choices: (.*?)</dd>", body, re.S)
        if choices:
            listed = [html.unescape(c) for c in re.findall(r"<code>([^<]*)</code>", choices.group(1))]
            entry["choices"] = [c for c in listed if c != "None"]
        default = re.search(r"<dd>Default: <code>(.*?)</code></dd>", body, re.S)
        if default:
            entry["default"] = html.unescape(default.group(1))
        entries[names[0]] = entry
    return entries


def _py_repr(value: Any) -> str:
    return repr(value)


def check_vllm(tag: str, ref: Path, cache: Path, offline: bool) -> dict[str, Any]:
    url = f"https://docs.vllm.ai/en/{tag}/cli/serve/"
    page = _fetch(url, cache, offline)
    if page is None:
        return {"reference": url, "skipped": "reference page not available"}
    theirs = parse_vllm_docs(page)
    ours = _cli(_db("vllm", tag))
    checks = _set_scores(set(ours), set(theirs))
    spell_ok = spell_total = default_ok = default_total = choice_ok = choice_total = 0
    mismatches: list[str] = []
    for name, entry in theirs.items():
        if name not in ours:
            continue
        opt = ours[name]
        spell_total += 1
        visible = {
            s
            for s in _spellings(opt)
            if not (
                s.startswith("--no-")
                and any(x.startswith(("--disable-", "--enable-")) for x in opt.get("negations", []))
            )
        }
        if visible == entry["spellings"]:
            spell_ok += 1
        else:
            mismatches.append(f"{name}: spellings {sorted(visible)} != {sorted(entry['spellings'])}")
        if "default" in entry and "default" in opt:
            default_total += 1
            if (
                _norm(_py_repr(opt["default"])) == _norm(entry["default"])
                or _norm(opt["default"]) == _norm(entry["default"])
                or (opt["default"] == [] and entry["default"] == "set()")  # sets are stored as lists
            ):
                default_ok += 1
            else:
                mismatches.append(f"{name}: default {opt['default']!r} != {entry['default']!r}")
        if "choices" in entry:
            choice_total += 1
            if sorted(map(str, opt.get("choices", []))) == sorted(entry["choices"]):
                choice_ok += 1
            else:
                mismatches.append(f"{name}: choices differ")
    checks += [
        _score("aliases and negations identical", spell_ok, spell_total),
        _score("defaults identical (where both sides state one)", default_ok, default_total),
        _score("choices identical (where reference lists them)", choice_ok, choice_total),
    ]
    missing = sorted(set(theirs) - set(ours))
    extra = sorted(set(ours) - set(theirs))
    return {"reference": url, "checks": checks, "missing": missing, "extra": extra, "mismatches": mismatches[:40]}


# -- llama.cpp --------------------------------------------------------------


def parse_llamacpp_readme(text: str) -> dict[str, dict[str, Any]]:
    entries: dict[str, dict[str, Any]] = {}
    for line in text.splitlines():
        match = re.match(r"^\| `(-[^`]+)` \| (.*) \|$", line)
        if match is None:
            continue
        spec, desc = match.group(1), match.group(2)
        names = [n.strip().split(" ")[0] for n in spec.split(",") if n.strip().startswith("-")]
        long_names = [n for n in names if n.startswith("--")]
        env = re.search(r"\(env: (\w+)\)", desc)
        text_only = re.sub(r"<br/>\(env: \w+\)", "", desc)
        key = long_names[0] if long_names else names[0]
        entries[key] = {"spellings": set(names), "env": env.group(1) if env else None, "help": text_only}
    return entries


def check_llamacpp(tag: str, ref: Path, cache: Path, offline: bool) -> dict[str, Any]:
    repo = Repo(ref / ENGINES["llamacpp"]["clone"], tag)
    path = next((p for p in ("tools/server/README.md", "examples/server/README.md") if repo.exists(p)), None)
    if path is None:
        return {"skipped": "no server README at this tag"}
    theirs = parse_llamacpp_readme(repo.read(path))
    ours = _cli(_db("llamacpp", tag))
    by_spelling = {s: o for o in ours.values() for s in _spellings(o)}
    matched = {k: by_spelling[k] for k in theirs if k in by_spelling}
    checks = [
        _score("reference flags found by extraction (recall)", len(matched), len(theirs)),
        _score(
            "extracted flags present in reference (precision)",
            sum(1 for o in ours.values() if _spellings(o) & {s for e in theirs.values() for s in e["spellings"]}),
            len(ours),
        ),
    ]
    spell_ok = env_ok = help_ok = help_total = 0
    mismatches: list[str] = []
    for key, entry in theirs.items():
        opt = matched.get(key)
        if opt is None:
            continue
        if _spellings(opt) == entry["spellings"]:
            spell_ok += 1
        else:
            mismatches.append(f"{key}: spellings {sorted(_spellings(opt))} != {sorted(entry['spellings'])}")
        if opt.get("env") == entry["env"]:
            env_ok += 1
        else:
            mismatches.append(f"{key}: env {opt.get('env')} != {entry['env']}")
        if not opt.get("help_partial"):
            help_total += 1
            if _norm(opt["help"]) == _norm(entry["help"].replace("\\|", "|")):
                help_ok += 1
            else:
                mismatches.append(f"{key}: help text differs")
    checks += [
        _score("aliases and negations identical", spell_ok, len(matched)),
        _score("env var identical", env_ok, len(matched)),
        _score("rendered help incl. defaults identical (fully rendered entries)", help_ok, help_total),
    ]
    return {
        "reference": f"{path} at {tag}",
        "note": "The README is refreshed manually from llama-gen-docs output and can lag arg.cpp.",
        "checks": checks,
        "missing": sorted(set(theirs) - set(matched)),
        "mismatches": mismatches[:40],
    }


# -- TGI --------------------------------------------------------------------


def parse_tgi_launcher_md(text: str) -> dict[str, dict[str, Any]]:
    entries: dict[str, dict[str, Any]] = {}
    for block in re.split(r"^## ", text, flags=re.MULTILINE)[1:]:
        flag = re.search(r"^\s+(?:-\w, )?(--[\w-]+)", block, re.MULTILINE)
        if flag is None:
            continue
        entry: dict[str, Any] = {}
        env = re.search(r"\[env: (\w+)=", block)
        if env:
            entry["env"] = env.group(1)
        default = re.search(r"\[default: ([^\]]*)\]", block)
        if default:
            entry["default"] = default.group(1)
        values = (
            re.findall(r"^\s+- ([\w.-]+)(?::|$)", block.split("Possible values:", 1)[1], re.MULTILINE)
            if "Possible values:" in block
            else []
        )
        inline = re.search(r"\[possible values: ([^\]]*)\]", block)
        if inline:
            values = [v.strip() for v in inline.group(1).split(",")]
        if values:
            entry["choices"] = values
        entries[flag.group(1)] = entry
    return entries


def check_tgi(tag: str, ref: Path, cache: Path, offline: bool) -> dict[str, Any]:
    repo = Repo(ref / ENGINES["tgi"]["clone"], tag)
    path = "docs/source/reference/launcher.md"
    if not repo.exists(path):
        return {"skipped": "no launcher.md at this tag"}
    theirs = parse_tgi_launcher_md(repo.read(path))
    theirs = {k: v for k, v in theirs.items() if k not in {"--help", "--version"}}
    ours = _cli(_db("tgi", tag))
    checks = _set_scores(set(ours), set(theirs))
    env_ok = env_total = default_ok = default_total = choice_ok = choice_total = 0
    mismatches: list[str] = []
    for name, entry in theirs.items():
        opt = ours.get(name)
        if opt is None:
            continue
        env_total += 1
        if opt.get("env") == entry.get("env"):
            env_ok += 1
        else:
            mismatches.append(f"{name}: env {opt.get('env')} != {entry.get('env')}")
        if "default" in entry:
            default_total += 1
            if _norm(opt.get("default")) == _norm(entry["default"]) or (
                isinstance(opt.get("default"), float) and float(entry["default"]) == opt["default"]
            ):
                default_ok += 1
            else:
                mismatches.append(f"{name}: default {opt.get('default')!r} != {entry['default']!r}")
        if "choices" in entry:
            choice_total += 1
            if sorted(opt.get("choices", [])) == sorted(entry["choices"]):
                choice_ok += 1
            else:
                mismatches.append(f"{name}: choices {opt.get('choices')} != {entry['choices']}")
    checks += [
        _score("env var identical", env_ok, env_total),
        _score("defaults identical (where reference states one)", default_ok, default_total),
        _score("choices identical (where reference lists them)", choice_ok, choice_total),
    ]
    return {
        "reference": f"{path} at {tag}",
        "checks": checks,
        "missing": sorted(set(theirs) - set(ours)),
        "extra": sorted(set(ours) - set(theirs)),
        "mismatches": mismatches[:40],
    }


# -- SGLang -----------------------------------------------------------------


def check_sglang(tag: str, ref: Path, cache: Path, offline: bool) -> dict[str, Any]:
    repo = Repo(ref / ENGINES["sglang"]["clone"], tag)
    base = "docs/docs/advanced_features"
    page = next(
        (p for p in (f"{base}/server_arguments.mdx", "docs/advanced_features/server_arguments.md") if repo.exists(p)),
        None,
    )
    if page is None:
        return {"skipped": "no server_arguments page at this tag"}
    text = repo.read(page)
    documented: set[str] = set()
    rows: dict[str, str] = {}
    cells = re.findall(r"<td[^>]*>(.*?)</td>", text, re.S)
    for idx, cell in enumerate(cells):
        flags = re.findall(r"`(--[\w-]+)`", cell)
        if flags and cell.strip().startswith("`--"):
            documented.update(flags)
            if idx + 1 < len(cells):
                rows[flags[0]] = cells[idx + 1]
    for line in text.splitlines():
        if line.startswith("| `--"):
            documented.update(re.findall(r"`(--[\w-]+)`", line.split("|")[1]))
    ours = _cli(_db("sglang", tag))
    spellings = {s for o in ours.values() for s in _spellings(o)}
    result: dict[str, Any] = {"reference": f"{page} at {tag}"}
    checks = [_score("documented flags found by extraction (recall)", len(documented & spellings), len(documented))]
    undocumented_path = f"{base}/server_arguments_undocumented.txt"
    if repo.exists(undocumented_path):
        listed = {line.strip() for line in repo.read(undocumented_path).splitlines() if line.strip().startswith("--")}
        complete = documented | listed
        result["reference"] += f" + {undocumented_path}"
        checks = [
            _score("reference flags found by extraction (recall)", len(complete & spellings), len(complete)),
            _score(
                "extracted flags present in reference (precision)",
                sum(1 for o in ours.values() if _spellings(o) & complete),
                len(ours),
            ),
        ]
        result["missing"] = sorted(complete - spellings)
        result["extra"] = sorted(o["name"] for o in ours.values() if not (_spellings(o) & complete))
    else:
        result["note"] = "The page is hand-maintained and incomplete at this tag, so only recall is measured."
        result["missing"] = sorted(documented - spellings)
    help_ok = help_total = 0
    for flag, cell in rows.items():
        opt = ours.get(flag)
        if opt is None or "{" in opt["help"]:
            continue
        help_total += 1
        if _norm(cell) == _norm(opt["help"]):
            help_ok += 1
    checks.append(_score("help text identical to the documented description", help_ok, help_total))
    result["checks"] = checks
    return result


# -- Ollama -----------------------------------------------------------------


def check_ollama(tag: str, ref: Path, cache: Path, offline: bool) -> dict[str, Any]:
    repo = Repo(ref / ENGINES["ollama"]["clone"], tag)
    try:
        text = repo.read("cmd/cmd.go")
    except SourceError:
        return {"skipped": "cmd/cmd.go not found"}
    referenced = set(re.findall(r'envVars\["(\w+)"\]', text))
    ours = {o["name"] for o in _db("ollama", tag)["options"] if o["kind"] == "env"}
    return {
        "reference": f"env vars referenced by command help in cmd/cmd.go at {tag}",
        "note": "Weak check: Ollama has no generated option reference.",
        "checks": [_score("referenced env vars found by extraction (recall)", len(referenced & ours), len(referenced))],
        "missing": sorted(referenced - ours),
    }


# -- metrics ----------------------------------------------------------------

METRIC_PREFIX = {"vllm": "vllm:", "sglang": "sglang:", "llamacpp": "llamacpp:", "tgi": "tgi_"}
_METRIC_DOCS = re.compile(
    r"(^|/)(docs?|examples?|assets|tools/server)/.*(metrics[^/]*\.(md|mdx)|grafana[^/]*\.json|dashboard[^/]*\.json)$"
    r"|^tools/server/README\.md$|^examples/server/README\.md$",
    re.IGNORECASE,
)


def check_metrics(engine: str, tag: str, ref: Path) -> dict[str, Any] | None:
    """Do the metric names in the engine's own docs and dashboards resolve?"""
    prefix = METRIC_PREFIX.get(engine)
    if prefix is None:
        return None
    sys.path.insert(0, str(ROOT / "src"))
    from engine_migrate import db as runtime_db
    from engine_migrate import lint

    repo = Repo(ref / ENGINES[engine]["clone"], tag)
    skip = ("gateway", "router", "diffusion", "docs/design/")  # other components / historical design notes
    files = [p for p in repo.ls() if _METRIC_DOCS.search(p) and not any(word in p for word in skip)]
    names: dict[str, str] = {}
    for path in files:
        try:
            text = repo.read(path)
        except SourceError:
            continue
        for match in re.finditer(re.escape(prefix) + r"[a-z0-9_]*[a-z0-9]", text):
            if text[match.end() : match.end() + 2] in {"_*", "*"}:
                continue  # prose such as ``vllm:kv_block_*_seconds``
            names.setdefault(match.group(0), path)
    if not names:
        return None
    ev = runtime_db.load(engine, version_of(tag))
    missing = {n: p for n, p in names.items() if lint.resolve_metric(ev, n) is None}
    return {
        "files": sorted(set(names.values())),
        "check": _score(
            "metric names in the engine's own docs/dashboards that resolve", len(names) - len(missing), len(names)
        ),
        "missing": dict(sorted(missing.items())),
    }


CHECKS = {
    "vllm": check_vllm,
    "llamacpp": check_llamacpp,
    "tgi": check_tgi,
    "sglang": check_sglang,
    "ollama": check_ollama,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--ref", type=Path, required=True)
    parser.add_argument("--cache", type=Path, default=Path(".validate-cache"))
    parser.add_argument("--offline", action="store_true", help="do not fetch docs.vllm.ai")
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args(argv)
    report: dict[str, dict[str, Any]] = {}
    for engine, tags in PINS.items():
        for tag in tags:
            result = CHECKS[engine](tag, args.ref, args.cache, args.offline)
            metrics = check_metrics(engine, tag, args.ref)
            if metrics is not None:
                result["metrics"] = metrics
                result.setdefault("checks", []).append(metrics["check"])
            report.setdefault(engine, {})[version_of(tag)] = result
            summary = "; ".join(
                f"{c['agree']}/{c['total']} {c['check'].split(' (')[0]}" for c in result.get("checks", [])
            )
            print(f"{engine:9s} {tag:10s} {summary or result.get('skipped')}")
    args.out.write_text(json.dumps(report, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
