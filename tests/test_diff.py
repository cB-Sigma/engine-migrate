from __future__ import annotations

import dataclasses
import json
from typing import Any

import pytest

from engine_migrate import db, diffing

from .conftest import EXAMPLES, check_golden, run_cli


def _version(
    base: db.EngineVersion, version: str, options: list[dict[str, Any]], metrics: list[dict[str, Any]]
) -> db.EngineVersion:
    opts = tuple(db._option({"kind": "cli", "source": {"path": "x.py", "line": 1}, **o}) for o in options)
    mets = tuple(db._metric({"source": {"path": "m.py", "line": 1}, **m}) for m in metrics)
    by_spelling: dict[str, db.Option] = {}
    for o in opts:
        for s in o.spellings:
            by_spelling.setdefault(s, o)
    return dataclasses.replace(
        base,
        version=version,
        tag="v" + version,
        options=opts,
        metrics=mets,
        _by_spelling=by_spelling,
        _metrics_by_name={m.name: m for m in mets},
    )


BASE = db.load("vllm", "0.33.0")


def test_synthetic_changes_are_classified() -> None:
    old = _version(
        BASE,
        "1.0",
        [
            {"name": "--keep", "type": "int", "default": 1, "help": "kept"},
            {"name": "--gone", "type": "str", "help": "this option simply disappears from the program"},
            {"name": "--old-name", "type": "int", "help": "Maximum number of sequences decoded in one batch."},
            {"name": "--short", "aliases": [], "type": "str", "help": "an option that gets a longer primary name"},
            {"name": "--dep", "type": "bool", "help": "soon deprecated"},
            {"name": "--choice", "type": "str", "choices": ["a", "b"], "help": "pick"},
        ],
        [
            {"name": "x:requests", "type": "counter", "labels": ["model"], "help": "Total number of requests handled."},
            {"name": "x:latency_ms", "type": "histogram", "labels": ["model"], "help": "Histogram of request latency."},
            {"name": "x:queue", "type": "gauge", "labels": ["model", "pool"], "help": "Queue length"},
        ],
    )
    new = _version(
        BASE,
        "2.0",
        [
            {"name": "--keep", "type": "int", "default": 2, "help": "kept"},
            {"name": "--new-name", "type": "int", "help": "Maximum number of sequences decoded in a single batch."},
            {"name": "--long-name", "aliases": ["--short"], "type": "str", "help": "completely reworded"},
            {"name": "--dep", "type": "bool", "deprecated": True, "replaced_by": "--keep", "help": "Deprecated."},
            {"name": "--choice", "type": "str", "choices": ["a", "c"], "help": "pick"},
            {"name": "--fresh", "type": "str", "help": "brand new"},
        ],
        [
            {"name": "x:requests", "type": "counter", "labels": ["model"], "help": "Total number of requests handled."},
            {
                "name": "x:latency_seconds",
                "type": "histogram",
                "labels": ["model"],
                "help": "Histogram of request latency.",
            },
            {"name": "x:queue", "type": "gauge", "labels": ["model"], "help": "Queue length"},
        ],
    )
    vdiff = diffing.diff_versions(old, new)
    by = {(c.category, c.kind, c.name): c for c in vdiff.changes}
    assert ("options", "removed", "--gone") in by
    assert ("options", "added", "--fresh") in by
    inferred = by[("options", "renamed", "--old-name")]
    assert inferred.new_name == "--new-name"
    assert inferred.inferred
    assert inferred.evidence == "help-similarity"
    assert "inferred" in inferred.detail
    alias = by[("options", "renamed", "--short")]
    assert alias.new_name == "--long-name"
    assert not alias.inferred
    assert alias.evidence == "alias"
    assert by[("options", "changed", "--keep")].fields["default"] == [1, 2]
    assert by[("options", "deprecated", "--dep")].detail == "use --keep"
    assert by[("options", "changed", "--choice")].fields == {"choices_removed": ["b"], "choices_added": ["c"]}
    metric = by[("metrics", "renamed", "x:latency_ms")]
    assert metric.new_name == "x:latency_seconds"
    assert metric.inferred
    assert by[("metrics", "changed", "x:queue")].fields == {"labels_removed": ["pool"]}
    assert vdiff.summary() == {
        "options": {"removed": 1, "renamed": 2, "deprecated": 1, "changed": 2, "added": 1},
        "metrics": {"renamed": 1, "changed": 1},
    }

    impact = diffing.config_impact("vllm serve m --short x --old-name 3 --gone y --choice b --dep", vdiff)
    kinds = {f.option: (f.severity, f.kind) for f in impact.findings}
    assert kinds["--short"] == ("info", "renamed")
    assert kinds["--old-name"] == ("error", "renamed")
    assert kinds["--gone"] == ("error", "removed")
    assert kinds["--choice"] == ("error", "value-removed")
    assert kinds["--dep"] == ("warning", "deprecated")
    # Inferred renames are reported but never applied automatically.
    assert "--new-name" not in impact.rewritten
    assert "--old-name" not in impact.rewritten
    assert "--gone" not in impact.rewritten
    assert [c.name for c in impact.unset_default_changes] == ["--keep"]
    assert impact.affected


def test_identical_versions_have_no_changes() -> None:
    assert diffing.diff("vllm", "0.33.0", "0.33.0").changes == []


def test_real_changes_found_in_the_database() -> None:
    """Facts checked against the engine sources by hand."""
    vllm = diffing.diff("vllm", "0.32.0", "0.33.0")
    prefix = next(c for c in vllm.select("options", "changed") if c.name == "--enable-prefix-caching")
    assert prefix.fields["negations_added"] == ["--disable-prefix-caching"]
    sglang = diffing.diff("sglang", "0.5.18", "0.5.23")
    stream = next(c for c in sglang.select("options", "removed") if c.name == "--stream-output")
    assert stream.fields["replacement"] == {"option": "--incremental-streaming-output", "value": None}
    llama = diffing.diff("llamacpp", "b9000", "b12323")
    webui = next(c for c in llama.select("options", "renamed") if c.name == "--webui")
    assert (webui.new_name, webui.evidence, webui.inferred) == ("--ui", "alias", False)
    busy = next(c for c in llama.select("metrics", "changed") if c.name == "llamacpp:n_busy_slots_per_decode")
    assert busy.fields["type"] == ["counter", "gauge"]


def test_env_forms_follow_their_flag() -> None:
    llama = diffing.diff("llamacpp", "b9000", "b12323")
    env = [c for c in llama.select("options", "renamed") if c.option_kind == "env"]
    assert env
    assert all(c.evidence == "flag" and not c.inferred for c in env)


GOLDEN = [
    ("sglang", "0.5.18", "0.5.23", "sglang-launch.sh"),
    ("llamacpp", "b9000", "b12323", "llamacpp-server.sh"),
    ("vllm", "0.28.0", "0.33.0", "vllm-deployment.yaml"),
    ("vllm", "0.28.0", "0.33.0", "vllm-config.yaml"),
    ("vllm", "0.28.0", "0.33.0", "vllm-docker.sh"),
]


@pytest.mark.parametrize(("engine", "old", "new", "name"), GOLDEN)
def test_golden_config_impact(engine: str, old: str, new: str, name: str, capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run_cli(["diff", engine, old, new, "--config", str(EXAMPLES / name)], capsys)
    assert code == 0
    check_golden(f"diff-{engine}-{old}-{new}-{name}.txt", out)


def test_golden_full_diff(capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run_cli(["diff", "tgi", "3.0.2", "3.3.7"], capsys)
    assert code == 0
    check_golden("diff-tgi-3.0.2-3.3.7.txt", out)
    code, out = run_cli(["diff", "llamacpp", "b11500", "b12323", "--only", "metrics", "-f", "markdown"], capsys)
    check_golden("diff-llamacpp-metrics.md", out)


def test_same_engine_rewrite_keeps_image_and_launcher() -> None:
    vdiff = diffing.diff("vllm", "0.28.0", "0.33.0")
    docker = diffing.config_impact((EXAMPLES / "vllm-docker.sh").read_text(), vdiff)
    assert "vllm/vllm-openai:v0.33.0" in docker.rewritten
    assert "-e HF_TOKEN=$HF_TOKEN" in docker.rewritten
    k8s = diffing.config_impact((EXAMPLES / "vllm-deployment.yaml").read_text(), vdiff)
    assert "image: vllm/vllm-openai:v0.33.0" in k8s.rewritten
    assert "command:" not in k8s.rewritten
    assert "VLLM_LOGGING_LEVEL" in k8s.rewritten


def test_exit_code_and_json(capsys: pytest.CaptureFixture[str]) -> None:
    argv = ["diff", "sglang", "0.5.18", "0.5.23", "--config", str(EXAMPLES / "sglang-launch.sh")]
    code, out = run_cli([*argv, "--exit-code", "-f", "json"], capsys)
    assert code == 1
    data = json.loads(out)
    assert {f["kind"] for f in data["findings"]} == {"removed", "value-removed"}
    code, _ = run_cli(["diff", "vllm", "0.33.0", "0.33.0", "--exit-code"], capsys)
    assert code == 0
    code, _ = run_cli(["diff", "vllm", "0.1.0", "0.33.0"], capsys)
    assert code == 2
