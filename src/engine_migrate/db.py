"""Read-only access to the bundled, statically extracted engine database."""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from functools import cache
from importlib import resources
from typing import Any

from .errors import UnknownEngineError, UnknownVersionError

ENGINE_ALIASES = {
    "vllm": "vllm",
    "sglang": "sglang",
    "llamacpp": "llamacpp",
    "llama.cpp": "llamacpp",
    "llama-cpp": "llamacpp",
    "llama_cpp": "llamacpp",
    "llama-server": "llamacpp",
    "tgi": "tgi",
    "text-generation-inference": "tgi",
    "ollama": "ollama",
}

ENGINE_TITLES = {
    "vllm": "vLLM",
    "sglang": "SGLang",
    "llamacpp": "llama.cpp",
    "tgi": "TGI",
    "ollama": "Ollama",
}

_MISSING: Any = object()


@dataclass(frozen=True)
class Option:
    """A CLI flag, environment variable or config key of one engine version."""

    name: str
    kind: str
    type: str
    help: str
    aliases: tuple[str, ...]
    negations: tuple[str, ...]
    deprecated: bool
    source_path: str
    source_line: int
    raw: dict[str, Any] = field(compare=False, hash=False, repr=False)

    @property
    def has_default(self) -> bool:
        return "default" in self.raw

    @property
    def default(self) -> Any:
        return self.raw.get("default")

    @property
    def choices(self) -> tuple[Any, ...] | None:
        choices = self.raw.get("choices")
        return tuple(choices) if choices is not None else None

    @property
    def choices_open(self) -> bool:
        return bool(self.raw.get("choices_open"))

    @property
    def env(self) -> str | None:
        value = self.raw.get("env")
        return value if isinstance(value, str) else None

    @property
    def replaced_by(self) -> str | None:
        value = self.raw.get("replaced_by")
        return value if isinstance(value, str) else None

    @property
    def spellings(self) -> tuple[str, ...]:
        return (self.name, *self.aliases, *self.negations)

    @property
    def takes_value(self) -> bool:
        return self.type != "bool"

    @property
    def is_list(self) -> bool:
        return self.type.startswith("list[")


@dataclass(frozen=True)
class Metric:
    """A Prometheus metric defined by one engine version."""

    name: str
    type: str
    labels: tuple[str, ...]
    labels_dynamic: bool
    help: str
    source_path: str
    source_line: int


@dataclass(frozen=True)
class EngineVersion:
    """Everything known about one engine at one release tag."""

    engine: str
    version: str
    tag: str
    repo: str
    commit: str
    tag_date: str
    sources: tuple[str, ...]
    notes: tuple[str, ...]
    options: tuple[Option, ...]
    metrics: tuple[Metric, ...]
    _by_spelling: dict[str, Option] = field(compare=False, hash=False, repr=False)
    _metrics_by_name: dict[str, Metric] = field(compare=False, hash=False, repr=False)

    @property
    def label(self) -> str:
        return f"{self.engine} {self.version}"

    def find(self, spelling: str) -> Option | None:
        """Look an option up by any spelling (name, alias or negation)."""
        hit = self._by_spelling.get(spelling)
        if hit is None and spelling.startswith("--") and "_" in spelling:
            hit = self._by_spelling.get(spelling.replace("_", "-"))
        return hit

    def metric(self, name: str) -> Metric | None:
        return self._metrics_by_name.get(name)

    def cite(self, item: Option | Metric) -> str:
        """Human-readable provenance, e.g. ``vllm@v0.33.0:path.py:12``."""
        return f"{self.engine}@{self.tag}:{item.source_path}:{item.source_line}"

    def options_of(self, *kinds: str) -> Iterator[Option]:
        return (o for o in self.options if not kinds or o.kind in kinds)


def _option(raw: dict[str, Any]) -> Option:
    src = raw.get("source", {})
    return Option(
        name=raw["name"],
        kind=raw["kind"],
        type=str(raw.get("type", "str")),
        help=raw.get("help", ""),
        aliases=tuple(raw.get("aliases", ())),
        negations=tuple(raw.get("negations", ())),
        deprecated=bool(raw.get("deprecated")),
        source_path=src.get("path", ""),
        source_line=int(src.get("line", 0)),
        raw=raw,
    )


def _metric(raw: dict[str, Any]) -> Metric:
    src = raw.get("source", {})
    return Metric(
        name=raw["name"],
        type=raw.get("type", "untyped"),
        labels=tuple(raw.get("labels", ())),
        labels_dynamic=bool(raw.get("labels_dynamic")),
        help=raw.get("help", ""),
        source_path=src.get("path", ""),
        source_line=int(src.get("line", 0)),
    )


def _data_root() -> Any:
    return resources.files("engine_migrate").joinpath("data")


@cache
def index() -> dict[str, list[dict[str, Any]]]:
    """Engine -> versions in release order (oldest first)."""
    text = _data_root().joinpath("db").joinpath("index.json").read_text(encoding="utf-8")
    data: dict[str, list[dict[str, Any]]] = json.loads(text)
    return data


def engines() -> list[str]:
    return sorted(index())


def normalise_engine(name: str) -> str:
    key = ENGINE_ALIASES.get(name.strip().lower())
    if key is None or key not in index():
        known = ", ".join(engines())
        raise UnknownEngineError(f"unknown engine {name!r} (known: {known})")
    return key


def versions(engine: str) -> list[str]:
    return [entry["version"] for entry in index()[normalise_engine(engine)]]


def latest(engine: str) -> str:
    return versions(engine)[-1]


def resolve_version(engine: str, version: str | None) -> str:
    """Accept ``0.33.0``, ``v0.33.0``, a tag, or None/"latest"."""
    engine = normalise_engine(engine)
    known = versions(engine)
    if version is None or version == "latest":
        return known[-1]
    wanted = version.strip()
    for entry in index()[engine]:
        if wanted in {entry["version"], entry["tag"], "v" + entry["version"]}:
            return str(entry["version"])
    raise UnknownVersionError(f"{engine} {version!r} is not in the database (available: {', '.join(known)})")


@cache
def _load(engine: str, version: str) -> EngineVersion:
    text = _data_root().joinpath("db").joinpath(engine).joinpath(f"{version}.json").read_text(encoding="utf-8")
    doc = json.loads(text)
    options = tuple(_option(o) for o in doc["options"])
    metrics = tuple(_metric(m) for m in doc["metrics"])
    by_spelling: dict[str, Option] = {}
    for opt in options:
        by_spelling.setdefault(opt.name, opt)
    for opt in options:
        for spelling in (*opt.aliases, *opt.negations):
            by_spelling.setdefault(spelling, opt)
    prov = doc["provenance"]
    return EngineVersion(
        engine=engine,
        version=version,
        tag=prov["tag"],
        repo=prov["repo"],
        commit=prov["commit"],
        tag_date=prov["tag_date"],
        sources=tuple(prov["sources"]),
        notes=tuple(doc.get("notes", ())),
        options=options,
        metrics=metrics,
        _by_spelling=by_spelling,
        _metrics_by_name={m.name: m for m in metrics},
    )


def load(engine: str, version: str | None = None) -> EngineVersion:
    """Load one engine version from the bundled database."""
    engine = normalise_engine(engine)
    return _load(engine, resolve_version(engine, version))


def version_index(engine: str, version: str) -> int:
    return versions(engine).index(resolve_version(engine, version))
