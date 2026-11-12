"""Translate a configuration from one engine to another."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from . import db, mapping, shapes
from .mapping import Binding, Concept
from .shapes import ParsedConfig, Setting

EXACT = "EXACT"
APPROXIMATE = "APPROXIMATE"
NO_EQUIVALENT = "NO EQUIVALENT"
UNKNOWN = "UNKNOWN"
STATUSES = (EXACT, APPROXIMATE, NO_EQUIVALENT, UNKNOWN)


@dataclass
class Item:
    """The verdict for one input option."""

    source: str
    status: str
    target: str = ""
    concept: str = ""
    explanation: str = ""
    notes: list[str] = field(default_factory=list)
    source_ref: str = ""
    target_ref: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "status": self.status,
            "target": self.target,
            "concept": self.concept,
            "explanation": self.explanation,
            "notes": self.notes,
            "source_ref": self.source_ref,
            "target_ref": self.target_ref,
        }


@dataclass
class Translation:
    source: db.EngineVersion
    target: db.EngineVersion
    shape: str
    items: list[Item]
    settings: list[Setting]
    output: str
    advisories: list[str] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        return {status: sum(1 for i in self.items if i.status == status) for status in STATUSES}

    def as_dict(self) -> dict[str, Any]:
        return {
            "from": {"engine": self.source.engine, "version": self.source.version, "tag": self.source.tag},
            "to": {"engine": self.target.engine, "version": self.target.version, "tag": self.target.tag},
            "shape": self.shape,
            "output": self.output,
            "summary": self.counts(),
            "options": [i.as_dict() for i in self.items],
            "advisories": self.advisories,
        }


# ---------------------------------------------------------------------------
# Value handling
# ---------------------------------------------------------------------------

_LOCAL_PATH = re.compile(r"^(/|\./|\.\./|~|[A-Za-z]:\\)|\.(gguf|bin|safetensors|jinja|j2|txt|json)$")


def _looks_like(value: str, kind: str) -> bool:
    local = bool(_LOCAL_PATH.search(value))
    if kind in {"local_path", "file_path"}:
        return local
    if kind == "repo_id":
        return not local
    if kind == "inline":
        return not local
    return True


def _flag_state(setting: Setting, option: db.Option) -> bool:
    """Whether the option, as written, switches its feature on."""
    if setting.enabled is not None:
        state = setting.enabled
    elif setting.values and shapes.is_truthy(setting.values[0]) is not None:
        state = bool(shapes.is_truthy(setting.values[0]))
    else:
        state = True
    key = setting.key
    if key in option.negations:
        state = not state
    return state


def _lora_items(values: list[str], style: str) -> list[tuple[str | None, str, bool]]:
    """(name, path, was_json) triples from an adapter list."""
    raw: list[str] = []
    for value in values:
        raw += value.split(",") if style == "comma-paths" and not value.lstrip().startswith("{") else [value]
    out: list[tuple[str | None, str, bool]] = []
    for item in raw:
        item = item.strip()
        if not item:
            continue
        if item.startswith("{"):
            try:
                obj = json.loads(item)
            except json.JSONDecodeError:
                out.append((None, item, True))
                continue
            name = obj.get("name") or obj.get("lora_name")
            path = obj.get("path") or obj.get("lora_path") or ""
            out.append((str(name) if name else None, str(path), True))
        elif "=" in item and style != "comma-paths":
            name, _, path = item.partition("=")
            out.append((name, path, False))
        else:
            out.append((None, item, False))
    return out


@dataclass
class _Encoded:
    settings: list[Setting]
    notes: list[str]
    lossy: bool = False
    impossible: str | None = None


def _origin_for(option: db.Option) -> str:
    return {"cli": "arg", "env": "env", "param": "param", "modelfile": "modelfile"}.get(option.kind, "arg")


def _encode(
    setting: Setting,
    src_opt: db.Option,
    src: Binding,
    dst_opt: db.Option,
    dst: Binding,
    dst_ev: db.EngineVersion,
) -> _Encoded:
    origin = _origin_for(dst_opt)
    notes: list[str] = []
    kind = src.value.kind
    if kind != dst.value.kind:
        return _Encoded([], [], impossible="the two options take different kinds of value")

    if kind == "flag":
        want = _flag_state(setting, src_opt) != src.value.invert  # neutral: feature on?
        present = want != dst.value.invert  # should the target flag itself be "true"?
        if present:
            if dst.value.invert:
                return _Encoded([Setting(dst_opt.name, [], origin)], notes)
            return _Encoded([Setting(dst_opt.name, [], origin)], notes)
        if dst.value.invert:
            notes.append(f"nothing to emit: the feature is on in {dst_ev.label} unless {dst_opt.name} is given")
            return _Encoded([], notes)
        if dst_opt.negations:
            return _Encoded([Setting(dst_opt.negations[0], [], origin)], notes)
        if dst_opt.has_default and dst_opt.default in (False, None):
            notes.append(f"nothing to emit: {dst_opt.name} is off unless given")
            return _Encoded([], notes)
        return _Encoded([], [], impossible=f"{dst_opt.name} has no negative form in {dst_ev.label}")

    if kind == "lora":
        items = _lora_items(setting.values, src.value.style)
        lossy = False
        if dst.value.style == "comma-paths":
            if any(name for name, _, _ in items):
                notes.append("adapter names are dropped: the target option takes paths/ids only")
                lossy = True
            values = [",".join(path for _, path, _ in items)]
        else:
            values = []
            for name, path, was_json in items:
                if was_json:
                    notes.append("JSON adapter entries were rewritten as NAME=PATH; extra JSON fields are dropped")
                    lossy = True
                values.append(f"{name}={path}" if name else path)
            if any(name is None for name, _, _ in items) and dst_ev.engine == "vllm":
                notes.append("vLLM expects name=path items; give each adapter a name")
                lossy = True
        return _Encoded([Setting(dst_opt.name, values, origin)], list(dict.fromkeys(notes)), lossy)

    if kind == "list":
        values = list(setting.values)
        if src.value.style == "comma":
            values = [v for value in values for v in value.split(",") if v]
        lossy = False
        if dst.value.style == "single" and len(values) > 1:
            notes.append(f"only the first of {len(values)} values is kept: {dst_opt.name} takes a single value")
            values = values[:1]
            lossy = True
        elif dst.value.style == "comma":
            values = [",".join(values)]
        return _Encoded([Setting(dst_opt.name, values, origin)], notes, lossy)

    # scalar
    values = list(setting.values)
    if src.value.map:
        forward = dict(src.value.map)
        values = [forward.get(v, v) for v in values]
    if dst.value.map:
        backward = {neutral: engine for engine, neutral in dst.value.map}
        values = [backward.get(v, v) for v in values]
    if not values:
        return _Encoded([], [], impossible="the option was given without a value")
    lossy = False
    choices = dst_opt.choices
    if choices is not None:
        accepted = {str(c) for c in choices if c is not None}
        for value in values:
            if value not in accepted:
                shown = ", ".join(sorted(accepted)[:12]) + (", ..." if len(accepted) > 12 else "")
                qualifier = "a built-in" if dst_opt.choices_open else "an accepted"
                notes.append(f"{value!r} is not {qualifier} value of {dst_opt.name} in {dst_ev.label} ({shown})")
                lossy = True
    if dst_opt.type == "int" and any(not re.fullmatch(r"-?\d+", v) for v in values):
        notes.append(f"{values[0]!r} is not a plain integer; check that {dst_opt.name} accepts it")
        lossy = True
    if dst_opt.type == "float" and any(not re.fullmatch(r"-?\d*\.?\d+(e-?\d+)?", v) for v in values):
        notes.append(f"{values[0]!r} is not a plain number; check that {dst_opt.name} accepts it")
        lossy = True
    return _Encoded([Setting(dst_opt.name, values, origin)], notes, lossy)


# ---------------------------------------------------------------------------
# Translation
# ---------------------------------------------------------------------------


def _other_versions(engine: str, spelling: str) -> list[str]:
    return [v for v in db.versions(engine) if db.load(engine, v).find(spelling) is not None]


def _span(versions: list[str]) -> str:
    return versions[0] if len(versions) == 1 else f"{versions[0]} to {versions[-1]}"


def _pick_target(concept: Concept, dst_ev: db.EngineVersion, values: list[str]) -> tuple[Binding, db.Option] | None:
    candidates: list[tuple[Binding, db.Option]] = []
    for binding in concept.bindings.get(dst_ev.engine, ()):
        if not binding.active_in(dst_ev.version):
            continue
        option = dst_ev.find(binding.option)
        if option is not None:
            candidates.append((binding, option))
    if not candidates:
        return None
    probe = values[0] if values else ""
    for binding, option in candidates:
        if binding.prefer is None or _looks_like(probe, binding.prefer):
            return binding, option
    return candidates[0]


def _absent_text(concept: Concept, dst_ev: db.EngineVersion) -> str:
    rows = concept.bindings.get(dst_ev.engine, ())
    if rows:
        versions = [v for v in db.versions(dst_ev.engine) if any(r.active_in(v) for r in rows)]
        if versions:
            return f"{rows[0].option} exists in {dst_ev.engine} {_span(versions)}, but not in {dst_ev.version}."
    text = concept.absent.get(dst_ev.engine)
    if text:
        return text
    return f"No option of {dst_ev.label} is mapped to '{concept.title.lower()}'."


def translate_settings(
    parsed: ParsedConfig,
    src_ev: db.EngineVersion,
    dst_ev: db.EngineVersion,
    table: tuple[Concept, ...] | None = None,
) -> tuple[list[Item], list[Setting], list[str]]:
    table = table if table is not None else mapping.concepts()
    items: list[Item] = []
    out: list[Setting] = []
    advisories: list[str] = []
    used_concepts: set[str] = set()
    emitted: set[tuple[str, tuple[str, ...]]] = set()

    for setting in parsed.settings:
        item = Item(source=setting.display, status=UNKNOWN)
        items.append(item)
        if setting.origin == "positional" and setting.key != "--model":
            item.explanation = "Unexpected positional argument; it is not translated."
            continue
        option = src_ev.find(setting.key)
        if option is None:
            elsewhere = _other_versions(src_ev.engine, setting.key)
            if elsewhere:
                item.explanation = (
                    f"Not an option of {src_ev.label}; it exists in {src_ev.engine} {_span(elsewhere)}. "
                    "Pass the matching --from-version."
                )
            else:
                item.explanation = (
                    f"Not found in the {src_ev.label} database (a typo, a plugin option, "
                    "or one that static extraction cannot see)."
                )
            continue
        item.source_ref = src_ev.cite(option)
        if option.deprecated:
            item.notes.append(f"{option.name} is deprecated in {src_ev.label}")
        found = mapping.find_binding(src_ev, option, table)
        if found is None:
            item.explanation = f"{option.name} is a known {src_ev.label} option, but no mapping row covers it yet."
            continue
        concept, src_binding = found
        item.concept = concept.id
        used_concepts.add(concept.id)
        if not concept.portable:
            item.status = NO_EQUIVALENT
            item.explanation = (
                _absent_text(concept, dst_ev)
                if dst_ev.engine in concept.absent
                else ("The value is specific to the source engine and is not translated.")
            )
            continue
        picked = _pick_target(concept, dst_ev, setting.values)
        if picked is None:
            item.status = NO_EQUIVALENT
            item.explanation = _absent_text(concept, dst_ev)
            continue
        dst_binding, dst_opt = picked
        encoded = _encode(setting, option, src_binding, dst_opt, dst_binding, dst_ev)
        if encoded.impossible is not None:
            item.status = NO_EQUIVALENT
            item.explanation = f"{dst_opt.name} exists in {dst_ev.label}, but {encoded.impossible}."
            item.target_ref = dst_ev.cite(dst_opt)
            continue
        item.target_ref = dst_ev.cite(dst_opt)
        item.notes += encoded.notes
        if dst_opt.deprecated:
            item.notes.append(f"{dst_opt.name} is deprecated in {dst_ev.label}")
        if dst_binding.advice:
            item.notes.append(dst_binding.advice)
        if concept.caveat:
            item.notes.append(concept.caveat)
        same = src_binding.semantics == dst_binding.semantics
        item.status = EXACT if same and not encoded.lossy else APPROXIMATE
        if not same:
            item.explanation = f"{src_ev.engine}: {src_binding.meaning} {dst_ev.engine}: {dst_binding.meaning}"
        elif encoded.lossy:
            item.explanation = "Same meaning, but the value could not be carried over unchanged."
        new = [s for s in encoded.settings if (s.key, tuple(s.values)) not in emitted]
        for s in new:
            emitted.add((s.key, tuple(s.values)))
            if dst_binding.positional and s.origin == "arg":
                s.origin = "positional"
        out += new
        item.target = "; ".join(s.display for s in encoded.settings) or "(nothing emitted)"

    # Defaults that differ when the user left a concept unset.
    for concept in table:
        if concept.id in used_concepts:
            continue
        src_rows = [r for r in concept.bindings.get(src_ev.engine, ()) if r.active_in(src_ev.version)]
        dst_rows = [r for r in concept.bindings.get(dst_ev.engine, ()) if r.active_in(dst_ev.version)]
        if concept.compare_defaults and src_rows and dst_rows:
            a = src_ev.find(src_rows[0].option)
            b = dst_ev.find(dst_rows[0].option)
            if a and b and a.has_default and b.has_default and a.default != b.default:
                advisories.append(
                    f"{concept.title}: not set, and the defaults differ - {src_ev.label} {a.name} "
                    f"defaults to {a.default!r}, {dst_ev.label} {b.name} defaults to {b.default!r}."
                )
        if concept.advise_when_unset and dst_rows and not src_rows:
            b = dst_ev.find(dst_rows[0].option)
            if b is not None:
                advisories.append(
                    f"{concept.title}: {dst_ev.label} has a switch for this ({b.name}); "
                    f"{src_ev.engine} has none, so decide whether to add it."
                )
    return items, out, advisories


def translate(
    text: str,
    src_engine: str,
    dst_engine: str,
    src_version: str | None = None,
    dst_version: str | None = None,
    shape: str | None = None,
) -> Translation:
    """Translate configuration ``text`` and return the result with its report."""
    src_ev = db.load(src_engine, src_version)
    dst_ev = db.load(dst_engine, dst_version)
    parsed = shapes.parse(text, src_ev, shape)
    items, settings, advisories = translate_settings(parsed, src_ev, dst_ev)
    output, shape_notes = shapes.emit(parsed, settings, dst_ev, src_ev)
    return Translation(src_ev, dst_ev, parsed.shape, items, settings, output, shape_notes + advisories)
