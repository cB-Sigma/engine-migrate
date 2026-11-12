from __future__ import annotations

import pytest

from engine_migrate import db
from engine_migrate.errors import UnknownEngineError, UnknownVersionError

ALL = [(engine, version) for engine in db.engines() for version in db.versions(engine)]


def test_engines_present() -> None:
    assert db.engines() == ["llamacpp", "ollama", "sglang", "tgi", "vllm"]


@pytest.mark.parametrize(("engine", "version"), ALL)
def test_every_entry_has_provenance(engine: str, version: str) -> None:
    ev = db.load(engine, version)
    assert ev.tag
    assert len(ev.commit) == 40
    assert ev.repo.startswith("https://github.com/")
    assert ev.options
    for option in ev.options:
        assert option.source_path, option.name
        assert option.source_line > 0, option.name
        assert option.kind in {"cli", "env", "param", "modelfile"}
    for metric in ev.metrics:
        assert metric.source_path, metric.name
        assert metric.source_line > 0, metric.name
        assert metric.type in {"counter", "gauge", "histogram", "summary", "info", "untyped"}


@pytest.mark.parametrize(("engine", "version"), ALL)
def test_index_matches_files(engine: str, version: str) -> None:
    ev = db.load(engine, version)
    entry = next(e for e in db.index()[engine] if e["version"] == version)
    assert entry["metrics"] == len(ev.metrics)
    assert sum(entry["options"].values()) == len(ev.options)
    assert entry["tag"] == ev.tag


def test_versions_are_in_release_order() -> None:
    for engine in db.engines():
        dates = [e["tag_date"] for e in db.index()[engine]]
        assert dates == sorted(dates)


def test_lookup_by_alias_and_negation() -> None:
    ev = db.load("vllm", "0.33.0")
    assert ev.find("-tp") is ev.find("--tensor-parallel-size")
    assert ev.find("--disable-prefix-caching") is ev.find("--enable-prefix-caching")
    assert ev.find("--tensor_parallel_size") is ev.find("--tensor-parallel-size")
    assert ev.find("--no-such-flag") is None


def test_version_spellings() -> None:
    assert db.resolve_version("vllm", "v0.33.0") == "0.33.0"
    assert db.resolve_version("vllm", None) == db.latest("vllm")
    assert db.resolve_version("llama.cpp", "b12323") == "b12323"
    with pytest.raises(UnknownVersionError):
        db.resolve_version("vllm", "9.9.9")
    with pytest.raises(UnknownEngineError):
        db.normalise_engine("triton")


def test_known_facts_from_source() -> None:
    """Spot checks that were verified by reading the engine source by hand."""
    assert db.load("vllm", "0.33.0").find("--port").default == 8000  # type: ignore[union-attr]
    assert db.load("sglang", "0.5.23").find("--port").default == 30000  # type: ignore[union-attr]
    assert db.load("tgi", "3.3.7").find("--port").default == 3000  # type: ignore[union-attr]
    metric = db.load("vllm", "0.33.0").metric("vllm:request_success")
    assert metric is not None
    assert metric.type == "counter"
    assert "finished_reason" in metric.labels
    # Names that only exist as loop variables in source must still be found.
    assert db.load("vllm", "0.33.0").metric("vllm:spec_decode_num_drafts") is not None
    assert db.load("ollama").metrics == ()
