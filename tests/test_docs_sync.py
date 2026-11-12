"""README and docs blocks must match what the tool prints today."""

from __future__ import annotations

import re

import pytest
from tools import gen_docs

from .conftest import ROOT


@pytest.mark.parametrize("name", gen_docs.FILES)
def test_generated_blocks_are_current(name: str) -> None:
    text = (ROOT / name).read_text(encoding="utf-8")
    assert gen_docs.render(text) == text, f"{name} is stale: run `python -m tools.gen_docs`"


def test_readme_shows_real_translate_and_diff_output() -> None:
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    commands = [m.group(2) for m in gen_docs.COMMAND.finditer(text)]
    assert any(c.startswith("translate ") for c in commands)
    assert any(c.startswith("diff ") for c in commands)
    assert any(c.startswith("lint-metrics ") for c in commands)
    assert "<!-- generated:coverage -->" in text


def test_no_unpublished_install_instructions() -> None:
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "pip install git+https://github.com/cb-Sigma/engine-migrate" in text
    assert not re.search(r"pip install engine-migrate\b", text)
    assert "img.shields.io/pypi" not in text
