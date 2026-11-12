"""Static diff of two versions of one engine."""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from typing import Any

from . import db, shapes
from .shapes import ParsedConfig, Setting

RENAME_THRESHOLD = 0.82


@dataclass
class Change:
    """One difference between two versions."""

    category: str  # options | metrics
    kind: str  # added | removed | renamed | changed | deprecated
    name: str
    detail: str = ""
    new_name: str = ""
    evidence: str = ""  # for renames: alias | replacement | field | help-similarity
    inferred: bool = False
    score: float | None = None
    option_kind: str = ""
    ref: str = ""
    fields: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "category": self.category,
            "kind": self.kind,
            "name": self.name,
            "detail": self.detail,
            "ref": self.ref,
        }
        if self.option_kind:
            out["option_kind"] = self.option_kind
        if self.new_name:
            out["new_name"] = self.new_name
            out["evidence"] = self.evidence
            out["inferred"] = self.inferred
        if self.score is not None:
            out["score"] = round(self.score, 2)
        if self.fields:
            out["fields"] = self.fields
        return out


@dataclass
class VersionDiff:
    old: db.EngineVersion
    new: db.EngineVersion
    changes: list[Change]

    def select(self, category: str, kind: str | None = None) -> list[Change]:
        return [c for c in self.changes if c.category == category and (kind is None or c.kind == kind)]

    def summary(self) -> dict[str, dict[str, int]]:
        out: dict[str, dict[str, int]] = {}
        for change in self.changes:
            bucket = out.setdefault(change.category, {})
            bucket[change.kind] = bucket.get(change.kind, 0) + 1
        return out

    def as_dict(self) -> dict[str, Any]:
        return {
            "engine": self.old.engine,
            "from": {"version": self.old.version, "tag": self.old.tag},
            "to": {"version": self.new.version, "tag": self.new.tag},
            "summary": self.summary(),
            "changes": [c.as_dict() for c in self.changes],
        }


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", text.lower()).strip()


def similarity(a: str, b: str) -> float:
    """Similarity of two help strings in [0, 1]."""
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return 0.0
    return difflib.SequenceMatcher(None, na, nb, autojunk=False).ratio()


def _show(value: Any) -> str:
    return "unset" if value is None else repr(value)


def _key(option: db.Option) -> tuple[str, str]:
    return option.kind, option.name


def _option_changes(old: db.Option, new: db.Option) -> tuple[list[str], dict[str, Any]]:
    details: list[str] = []
    fields: dict[str, Any] = {}
    if old.has_default and new.has_default and old.default != new.default:
        details.append(f"default {_show(old.default)} -> {_show(new.default)}")
        fields["default"] = [old.default, new.default]
    if old.type != new.type:
        details.append(f"type {old.type} -> {new.type}")
        fields["type"] = [old.type, new.type]
    # Only when both snapshots list choices: a missing list can also mean that
    # static extraction could not resolve it in one of the versions.
    if old.choices is not None and new.choices is not None:
        before = {str(c) for c in old.choices or ()}
        after = {str(c) for c in new.choices or ()}
        gone, came = sorted(before - after), sorted(after - before)
        if gone:
            details.append("choices removed: " + ", ".join(gone))
            fields["choices_removed"] = gone
        if came:
            details.append("choices added: " + ", ".join(came))
            fields["choices_added"] = came
    for label, before_t, after_t in (
        ("aliases", old.aliases, new.aliases),
        ("negations", old.negations, new.negations),
    ):
        gone = sorted(set(before_t) - set(after_t))
        came = sorted(set(after_t) - set(before_t))
        if gone:
            details.append(f"{label} removed: " + ", ".join(gone))
            fields[f"{label}_removed"] = gone
        if came:
            details.append(f"{label} added: " + ", ".join(came))
            fields[f"{label}_added"] = came
    if old.env != new.env and (old.env or new.env):
        details.append(f"env {old.env or 'none'} -> {new.env or 'none'}")
        fields["env"] = [old.env, new.env]
    return details, fields


def _match_renames(removed: list[db.Option], added: list[db.Option]) -> list[tuple[db.Option, db.Option, str, float]]:
    """Pair removed options with added ones; returns (old, new, evidence, score)."""
    pairs: list[tuple[db.Option, db.Option, str, float]] = []
    free = list(added)

    def take(old: db.Option, new: db.Option, evidence: str, score: float) -> None:
        pairs.append((old, new, evidence, score))
        free.remove(new)

    remaining: list[db.Option] = []
    for old in removed:
        hit = next((n for n in free if n.kind == old.kind and old.name in n.spellings), None)
        if hit is not None:
            take(old, hit, "alias", 1.0)
            continue
        hit = next(
            (n for n in free if n.kind == old.kind and (n.name in old.spellings or set(old.aliases) & set(n.aliases))),
            None,
        )
        if hit is not None:
            take(old, hit, "alias", 1.0)
            continue
        remaining.append(old)
    # Environment forms of flags follow their flag (same flag, or a flag renamed above).
    flag_renames = {o.name: n.name for o, n, _, _ in pairs}
    removed, remaining = remaining, []
    for old in removed:
        flag = old.raw.get("flag")
        hit = None
        if isinstance(flag, str):
            wanted = {flag, flag_renames.get(flag, flag)}
            hit = next((n for n in free if n.kind == old.kind and n.raw.get("flag") in wanted), None)
        if hit is not None:
            take(old, hit, "flag", 1.0)
        else:
            remaining.append(old)
    removed, remaining = remaining, []
    for old in removed:
        field_name = old.raw.get("config_field")
        hit = next(
            (n for n in free if n.kind == old.kind and field_name and n.raw.get("config_field") == field_name),
            None,
        )
        if hit is not None:
            take(old, hit, "field", 1.0)
        else:
            remaining.append(old)
    # Help-text similarity: greedy on the best score, labelled as inferred.
    scored: list[tuple[float, db.Option, db.Option]] = []
    for old in remaining:
        if "flag" in old.raw:
            continue  # generated help text, similarity would be meaningless
        for new in free:
            if new.kind != old.kind or "flag" in new.raw:
                continue
            score = similarity(old.help, new.help)
            if score >= RENAME_THRESHOLD and len(_norm(old.help)) >= 12:
                scored.append((score, old, new))
    scored.sort(key=lambda t: (-t[0], t[1].name, t[2].name))
    used_old: set[str] = set()
    for score, old, new in scored:
        if old.name in used_old or new not in free:
            continue
        used_old.add(old.name)
        take(old, new, "help-similarity", score)
    return pairs


_SIMPLE_REPLACEMENT = re.compile(r"^(--[A-Za-z0-9][A-Za-z0-9-]*)(?:=([A-Za-z0-9_.-]+))?$")


def _replacement(option: db.Option, new: db.EngineVersion) -> tuple[str, str | None] | None:
    """A replacement named by the engine's own deprecation metadata, if unambiguous."""
    if not option.replaced_by:
        return None
    match = _SIMPLE_REPLACEMENT.match(option.replaced_by.strip())
    if match is None:
        return None
    target = new.find(match.group(1))
    if target is None:
        return None
    value = match.group(2)
    if value is None and option.type != target.type:
        return None  # e.g. a switch replaced by an option that needs a value
    if value is not None and option.type != "bool":
        return None
    return match.group(1), value


def diff_versions(old: db.EngineVersion, new: db.EngineVersion) -> VersionDiff:
    """Compare two database snapshots of the same engine."""
    changes: list[Change] = []
    old_opts = {_key(o): o for o in old.options}
    new_opts = {_key(o): o for o in new.options}
    removed = [o for k, o in old_opts.items() if k not in new_opts]
    added = [o for k, o in new_opts.items() if k not in old_opts]
    renames = _match_renames(removed, added)
    renamed_old = {_key(o) for o, _, _, _ in renames}
    renamed_new = {_key(n) for _, n, _, _ in renames}
    for o, n, evidence, score in renames:
        details, fields = _option_changes(o, n)
        keeps = o.name in n.spellings
        detail = "old spelling still accepted as an alias" if keeps else "old spelling no longer accepted"
        if evidence == "field":
            detail += f"; same config field {o.raw.get('config_field')}"
        if evidence == "flag":
            detail = f"environment form of {n.raw.get('flag')}"
        if evidence == "help-similarity":
            detail = f"inferred from help text ({score:.0%} similar); " + detail
        extra = [d for d in details if not d.startswith("aliases")]
        if extra:
            detail += "; " + "; ".join(extra)
        changes.append(
            Change(
                "options",
                "renamed",
                o.name,
                detail,
                n.name,
                evidence,
                evidence == "help-similarity",
                score if evidence == "help-similarity" else None,
                o.kind,
                new.cite(n),
                fields,
            )
        )
    for o in removed:
        if _key(o) in renamed_old:
            continue
        detail = f"replaced by {o.replaced_by}" if o.replaced_by else ""
        if o.deprecated:
            detail = (detail + "; " if detail else "") + f"was deprecated in {old.version}"
        fields = {}
        replacement = _replacement(o, new)
        if replacement is not None:
            fields["replacement"] = {"option": replacement[0], "value": replacement[1]}
        changes.append(Change("options", "removed", o.name, detail, option_kind=o.kind, ref=old.cite(o), fields=fields))
    for n in added:
        if _key(n) in renamed_new:
            continue
        detail = "already deprecated" if n.deprecated else ""
        changes.append(Change("options", "added", n.name, detail, option_kind=n.kind, ref=new.cite(n)))
    for key, o in old_opts.items():
        n2 = new_opts.get(key)
        if n2 is None:
            continue
        details, fields = _option_changes(o, n2)
        if details:
            changes.append(
                Change(
                    "options",
                    "changed",
                    o.name,
                    "; ".join(details),
                    option_kind=o.kind,
                    ref=new.cite(n2),
                    fields=fields,
                )
            )
        if n2.deprecated and not o.deprecated:
            hint = f"use {n2.replaced_by}" if n2.replaced_by else n2.help[:160]
            changes.append(Change("options", "deprecated", o.name, hint, option_kind=o.kind, ref=new.cite(n2)))

    old_m = {m.name: m for m in old.metrics}
    new_m = {m.name: m for m in new.metrics}
    gone = [m for name, m in old_m.items() if name not in new_m]
    came = [m for name, m in new_m.items() if name not in old_m]
    metric_pairs: list[tuple[db.Metric, db.Metric, float]] = []
    scored_m = sorted(
        (
            (similarity(a.help, b.help), a, b)
            for a in gone
            for b in came
            if a.type == b.type and len(_norm(a.help)) >= 12
        ),
        key=lambda t: (-t[0], t[1].name, t[2].name),
    )
    taken_a: set[str] = set()
    taken_b: set[str] = set()
    for score, a, b in scored_m:
        if score < RENAME_THRESHOLD or a.name in taken_a or b.name in taken_b:
            continue
        taken_a.add(a.name)
        taken_b.add(b.name)
        metric_pairs.append((a, b, score))
    for a, b, score in metric_pairs:
        detail = f"inferred from help text ({score:.0%} similar)"
        if set(a.labels) != set(b.labels):
            detail += f"; labels {sorted(a.labels)} -> {sorted(b.labels)}"
        changes.append(
            Change("metrics", "renamed", a.name, detail, b.name, "help-similarity", True, score, ref=new.cite(b))
        )
    for a in gone:
        if a.name not in taken_a:
            changes.append(Change("metrics", "removed", a.name, f"{a.type}", ref=old.cite(a)))
    for b in came:
        if b.name not in taken_b:
            changes.append(Change("metrics", "added", b.name, f"{b.type}", ref=new.cite(b)))
    for name, a in old_m.items():
        b2 = new_m.get(name)
        if b2 is None:
            continue
        details = []
        fields2: dict[str, Any] = {}
        if a.type != b2.type:
            details.append(f"type {a.type} -> {b2.type}")
            fields2["type"] = [a.type, b2.type]
        lost = sorted(set(a.labels) - set(b2.labels))
        gained = sorted(set(b2.labels) - set(a.labels))
        if lost:
            details.append("labels removed: " + ", ".join(lost))
            fields2["labels_removed"] = lost
        if gained:
            details.append("labels added: " + ", ".join(gained))
            fields2["labels_added"] = gained
        if details:
            changes.append(Change("metrics", "changed", name, "; ".join(details), ref=new.cite(b2), fields=fields2))
    order = {"removed": 0, "renamed": 1, "deprecated": 2, "changed": 3, "added": 4}
    changes.sort(key=lambda c: (c.category != "options", order[c.kind], c.option_kind, c.name))
    return VersionDiff(old, new, changes)


def diff(engine: str, old_version: str, new_version: str) -> VersionDiff:
    return diff_versions(db.load(engine, old_version), db.load(engine, new_version))


# ---------------------------------------------------------------------------
# Config-aware diff
# ---------------------------------------------------------------------------


@dataclass
class Finding:
    """How an upgrade touches one option of a concrete configuration."""

    option: str
    severity: str  # error | warning | info
    kind: str
    message: str
    ref: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "option": self.option,
            "severity": self.severity,
            "kind": self.kind,
            "message": self.message,
            "ref": self.ref,
        }


@dataclass
class ConfigImpact:
    diff: VersionDiff
    shape: str
    findings: list[Finding]
    unset_default_changes: list[Change]
    rewritten: str
    rewrite_notes: list[str]

    @property
    def affected(self) -> bool:
        return any(f.severity in {"error", "warning"} for f in self.findings)

    def as_dict(self) -> dict[str, Any]:
        return {
            "engine": self.diff.old.engine,
            "from": self.diff.old.version,
            "to": self.diff.new.version,
            "shape": self.shape,
            "findings": [f.as_dict() for f in self.findings],
            "unset_default_changes": [c.as_dict() for c in self.unset_default_changes],
            "rewritten": self.rewritten,
            "rewrite_notes": self.rewrite_notes,
        }


def _rewrite(setting: Setting, key: str) -> Setting:
    return Setting(key, list(setting.values), setting.origin, setting.enabled, None)


def config_impact(text: str, vdiff: VersionDiff, shape: str | None = None) -> ConfigImpact:
    """Restrict ``vdiff`` to what touches the configuration in ``text``."""
    old, new = vdiff.old, vdiff.new
    parsed: ParsedConfig = shapes.parse(text, old, shape)
    by_name = {(c.option_kind, c.name): c for c in vdiff.select("options") if c.kind != "deprecated"}
    deprecated = {(c.option_kind, c.name): c for c in vdiff.select("options", "deprecated")}
    findings: list[Finding] = []
    rewritten: list[Setting] = []
    notes: list[str] = []
    set_names: set[tuple[str, str]] = set()
    for setting in parsed.settings:
        if setting.origin == "positional" and not (old.engine == "vllm" and setting.key == "--model"):
            rewritten.append(setting)
            continue
        option = old.find(setting.key)
        if option is None:
            if new.find(setting.key) is None:
                findings.append(
                    Finding(setting.key, "info", "unknown", f"not in the database for {old.label} or {new.label}")
                )
            else:
                findings.append(
                    Finding(setting.key, "info", "unknown", f"not an option of {old.label}, but exists in {new.label}")
                )
            rewritten.append(setting)
            continue
        key = (option.kind, option.name)
        set_names.add(key)
        change = by_name.get(key)
        target = setting
        if change is not None and change.kind == "removed":
            findings.append(
                Finding(
                    setting.key,
                    "error",
                    "removed",
                    f"removed in {new.version}" + (f" ({change.detail})" if change.detail else ""),
                    change.ref,
                )
            )
            replacement = change.fields.get("replacement")
            usable = replacement is not None and (setting.key == option.name or setting.key in option.aliases)
            if usable and replacement is not None:
                values = [replacement["value"]] if replacement["value"] is not None else list(setting.values)
                rewritten.append(
                    Setting(replacement["option"], values, setting.origin, None if values else setting.enabled)
                )
                notes.append(
                    f"{setting.key} was rewritten to {replacement['option']}"
                    + (f" {replacement['value']}" if replacement["value"] else "")
                    + f", the replacement named by {new.engine}'s own deprecation notice."
                )
            else:
                notes.append(f"{setting.key} was dropped from the rewritten config (removed in {new.version}).")
            continue
        if change is not None and change.kind == "renamed":
            new_opt = new.find(change.new_name)
            still_ok = new_opt is not None and setting.key in new_opt.spellings
            if change.inferred:
                findings.append(
                    Finding(
                        setting.key,
                        "error",
                        "renamed",
                        f"gone in {new.version}; probably renamed to {change.new_name} ({change.detail}) - not rewritten automatically",
                        change.ref,
                    )
                )
                notes.append(f"{setting.key} was dropped; check whether {change.new_name} is its replacement.")
                continue
            if still_ok:
                findings.append(
                    Finding(
                        setting.key,
                        "info",
                        "renamed",
                        f"primary name is now {change.new_name}; this spelling is still accepted",
                        change.ref,
                    )
                )
            else:
                findings.append(
                    Finding(
                        setting.key, "error", "renamed", f"renamed to {change.new_name} ({change.detail})", change.ref
                    )
                )
                if setting.key == option.name or setting.key in option.aliases:
                    target = _rewrite(setting, change.new_name)
                else:
                    notes.append(f"{setting.key} is a negative spelling; rewrite it by hand for {change.new_name}.")
        elif new.find(setting.key) is None or new.find(setting.key) is not new.find(option.name):
            # The option survives, but this spelling (an alias or negation) does not.
            survivor = new.find(option.name)
            if survivor is not None:
                replacement = None
                if setting.key in option.negations and survivor.negations:
                    replacement = survivor.negations[0]
                elif setting.key in option.aliases:
                    replacement = survivor.name
                findings.append(
                    Finding(
                        setting.key,
                        "error",
                        "spelling-removed",
                        f"this spelling of {option.name} is not accepted in {new.version}"
                        + (f"; use {replacement}" if replacement else ""),
                        new.cite(survivor),
                    )
                )
                if replacement:
                    target = _rewrite(setting, replacement)
        if change is not None and change.kind == "changed":
            relevant = dict(change.fields)
            if "default" in relevant:
                relevant.pop("default")  # the config sets the option, so the default is moot
            removed_choices = set(relevant.get("choices_removed", []))
            bad = [v for v in setting.values if v in removed_choices]
            if bad:
                findings.append(
                    Finding(
                        setting.key,
                        "error",
                        "value-removed",
                        f"value {bad[0]!r} is no longer a listed choice in {new.version}",
                        change.ref,
                    )
                )
                notes.append(f"{setting.key} {bad[0]} was kept as written; pick a value that {new.label} accepts.")
            if "type" in relevant:
                findings.append(
                    Finding(
                        setting.key,
                        "warning",
                        "type-changed",
                        f"type {relevant['type'][0]} -> {relevant['type'][1]}",
                        change.ref,
                    )
                )
        dep = deprecated.get(key)
        if dep is not None:
            findings.append(
                Finding(setting.key, "warning", "deprecated", f"deprecated in {new.version}: {dep.detail}", dep.ref)
            )
        elif option.deprecated and (survivor2 := new.find(option.name)) is not None and survivor2.deprecated:
            findings.append(
                Finding(
                    setting.key,
                    "warning",
                    "deprecated",
                    f"already deprecated in {old.version} and still is",
                    new.cite(survivor2),
                )
            )
        rewritten.append(target)
    unset = [
        c
        for c in vdiff.select("options", "changed")
        if "default" in c.fields and (c.option_kind, c.name) not in set_names and c.option_kind == "cli"
    ]
    if parsed.shape == "command":
        prefix = parsed.prefix or shapes.LAUNCHERS[new.engine]
        positional = [v for s in rewritten if s.origin == "positional" for v in (s.values or [s.key])]
        env = [f"{s.written or s.key}={' '.join(s.values)}" for s in rewritten if s.origin == "env"]
        body = shapes._format_command(
            prefix + positional, shapes.emit_argv([s for s in rewritten if s.origin == "arg"])
        )
        output = (" ".join(env) + " " + body) if env else body
    else:
        output = _reemit(parsed, rewritten, old, new)
    return ConfigImpact(vdiff, parsed.shape, findings, unset, output, notes)


def _reemit(parsed: ParsedConfig, settings: list[Setting], old: db.EngineVersion, new: db.EngineVersion) -> str:
    """Re-emit a same-engine config, keeping image, launcher and env spellings."""
    restored = [
        Setting(s.written, s.values, "env", s.enabled) if s.origin == "env" and s.written else s for s in settings
    ]
    if parsed.shape == "docker":
        env = parsed.envelope
        groups = [list(g) for g in env["options"]]
        groups += [["-e", f"{s.key}={' '.join(s.values)}"] for s in restored if s.origin == "env"]
        head = list(parsed.prefix) + [v for s in restored if s.origin == "positional" for v in s.values]
        groups.append([env["image"]])
        if head:
            groups.append(head)
        groups += shapes.emit_argv([s for s in restored if s.origin == "arg"])
        return shapes._format_command(list(env["head"]), groups)
    if parsed.shape == "kubernetes":
        text = shapes.emit_kubernetes(new.engine, restored, parsed.envelope, old)
        docs = parsed.envelope["docs"]
        original = [c for d in docs for c in shapes._find_containers(d)][parsed.envelope["index"]]
        text = text.replace(shapes.image_placeholder(new.engine), str(original.get("image")), 1)
        if "command" not in original:
            import yaml

            docs2 = list(yaml.safe_load_all(text))
            container = [c for d in docs2 for c in shapes._find_containers(d)][parsed.envelope["index"]]
            head = container.pop("command", [])
            extra = [t for t in head if t not in shapes.LAUNCHERS[new.engine]]
            container["args"] = extra + list(container.get("args") or [])
            text = yaml.safe_dump_all(docs2, sort_keys=False, default_flow_style=False, width=1000).rstrip("\n")
        return text
    if parsed.shape == "yaml":
        return shapes.emit_yaml(restored, new)
    return shapes.emit_env(restored)
