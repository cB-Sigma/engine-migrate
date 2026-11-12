from __future__ import annotations

import copy
from importlib import resources
from typing import Any

import yaml

from engine_migrate import db, mapping


def _raw() -> dict[str, Any]:
    text = (
        resources.files("engine_migrate")
        .joinpath("data")
        .joinpath("mappings")
        .joinpath("concepts.yaml")
        .read_text("utf-8")
    )
    doc: dict[str, Any] = yaml.safe_load(text)
    return doc


def test_every_row_names_a_flag_the_database_contains() -> None:
    assert mapping.integrity_errors() == []


def test_a_row_with_an_unknown_flag_is_rejected() -> None:
    doc = copy.deepcopy(_raw())
    doc["concepts"][0]["bindings"]["vllm"][0]["option"] = "--no-such-flag-anywhere"
    errors = mapping.integrity_errors(mapping.parse_concepts(doc))
    assert len(errors) == len(db.versions("vllm"))
    assert all("--no-such-flag-anywhere" in e for e in errors)


def test_a_row_is_checked_per_version() -> None:
    """--log-level only exists from vLLM 0.31.0: dropping `since` must fail."""
    doc = copy.deepcopy(_raw())
    concept = next(c for c in doc["concepts"] if c["id"] == "logging.level")
    assert concept["bindings"]["vllm"][0].pop("since") == "0.31.0"
    errors = mapping.integrity_errors(mapping.parse_concepts(doc))
    assert errors
    assert all("--log-level" in e and "vllm" in e for e in errors)
    assert {e.rsplit(" ", 1)[1] for e in errors} == {"0.28.0", "0.29.0", "0.30.0"}


def test_unknown_since_version_is_rejected() -> None:
    doc = copy.deepcopy(_raw())
    doc["concepts"][0]["bindings"]["vllm"][0]["since"] = "0.0.1"
    assert any("unknown version" in e for e in mapping.integrity_errors(mapping.parse_concepts(doc)))


def test_rows_are_documented() -> None:
    ids = set()
    for concept in mapping.concepts():
        assert concept.id not in ids
        ids.add(concept.id)
        assert concept.title
        assert concept.bindings
        for rows in concept.bindings.values():
            for row in rows:
                assert row.semantics
                assert len(row.meaning) > 8
                assert row.value.kind in {"scalar", "flag", "list", "lora"}
