"""The curated concept table that links options of different engines."""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import cache
from importlib import resources
from typing import Any

import yaml

from . import db


@dataclass(frozen=True)
class ValueSpec:
    """How a binding writes its value."""

    kind: str = "scalar"  # scalar | flag | list | lora
    invert: bool = False
    style: str = "nargs"  # nargs | comma | single | comma-paths
    map: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class Binding:
    """One engine option that expresses a concept."""

    engine: str
    option: str
    semantics: str
    meaning: str
    value: ValueSpec = field(default_factory=ValueSpec)
    prefer: str | None = None
    since: str | None = None
    until: str | None = None
    advice: str | None = None
    positional: bool = False

    def active_in(self, version: str) -> bool:
        """Whether the row claims the option exists in ``version``."""
        order = db.versions(self.engine)
        idx = order.index(version)
        if self.since is not None and idx < order.index(self.since):
            return False
        return not (self.until is not None and idx > order.index(self.until))


@dataclass(frozen=True)
class Concept:
    id: str
    title: str
    summary: str
    bindings: dict[str, tuple[Binding, ...]]
    absent: dict[str, str]
    caveat: str | None = None
    compare_defaults: bool = False
    advise_when_unset: bool = False
    portable: bool = True


def _value(raw: dict[str, Any] | None) -> ValueSpec:
    raw = raw or {}
    return ValueSpec(
        kind=str(raw.get("kind", "scalar")),
        invert=bool(raw.get("invert", False)),
        style=str(raw.get("style", "nargs")),
        map=tuple((str(k), str(v)) for k, v in (raw.get("map") or {}).items()),
    )


def parse_concepts(doc: dict[str, Any]) -> tuple[Concept, ...]:
    concepts: list[Concept] = []
    for raw in doc["concepts"]:
        bindings: dict[str, tuple[Binding, ...]] = {}
        for engine, rows in (raw.get("bindings") or {}).items():
            bindings[engine] = tuple(
                Binding(
                    engine=engine,
                    option=row["option"],
                    semantics=row["semantics"],
                    meaning=" ".join(str(row["meaning"]).split()),
                    value=_value(row.get("value")),
                    prefer=row.get("prefer"),
                    since=row.get("since"),
                    until=row.get("until"),
                    advice=row.get("advice"),
                    positional=bool(row.get("positional", False)),
                )
                for row in rows
            )
        concepts.append(
            Concept(
                id=raw["id"],
                title=raw["title"],
                summary=raw.get("summary", ""),
                bindings=bindings,
                absent={k: " ".join(str(v).split()) for k, v in (raw.get("absent") or {}).items()},
                caveat=raw.get("caveat"),
                compare_defaults=bool(raw.get("compare_defaults", False)),
                advise_when_unset=bool(raw.get("advise_when_unset", False)),
                portable=bool(raw.get("portable", True)),
            )
        )
    return tuple(concepts)


@cache
def concepts() -> tuple[Concept, ...]:
    """Load the bundled mapping table."""
    root = resources.files("engine_migrate").joinpath("data").joinpath("mappings")
    text = root.joinpath("concepts.yaml").read_text(encoding="utf-8")
    return parse_concepts(yaml.safe_load(text))


def find_binding(
    ev: db.EngineVersion, option: db.Option, table: tuple[Concept, ...] | None = None
) -> tuple[Concept, Binding] | None:
    """The concept row that covers ``option`` in this engine version."""
    for concept in table if table is not None else concepts():
        for binding in concept.bindings.get(ev.engine, ()):
            if not binding.active_in(ev.version):
                continue
            target = ev.find(binding.option)
            if target is not None and target.name == option.name and target.kind == option.kind:
                return concept, binding
    return None


def integrity_errors(table: tuple[Concept, ...] | None = None) -> list[str]:
    """Rows that name an option the database lacks for a claimed version."""
    problems: list[str] = []
    seen: set[str] = set()
    for concept in table if table is not None else concepts():
        if concept.id in seen:
            problems.append(f"{concept.id}: duplicate concept id")
        seen.add(concept.id)
        for engine, rows in concept.bindings.items():
            if engine not in db.index():
                problems.append(f"{concept.id}: unknown engine {engine!r}")
                continue
            known = db.versions(engine)
            for row in rows:
                for bound in (row.since, row.until):
                    if bound is not None and bound not in known:
                        problems.append(f"{concept.id}: {engine} {row.option}: unknown version {bound!r}")
                if any(b is not None and b not in known for b in (row.since, row.until)):
                    continue
                for version in known:
                    if not row.active_in(version):
                        continue
                    if db.load(engine, version).find(row.option) is None:
                        problems.append(
                            f"{concept.id}: {engine} {row.option} is not in the database for {engine} {version}"
                        )
        for engine in concept.absent:
            if engine not in db.index():
                problems.append(f"{concept.id}: absent note for unknown engine {engine!r}")
    return problems
