"""Build the engine-migrate database from reference clones.

Usage::

    python -m tools.extract.run --ref /home/user/ref            # everything pinned in PINS
    python -m tools.extract.run --ref ~/ref --engine vllm --tag v0.33.0
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import llamacpp, ollama, sglang, tgi, vllm
from .common import SCHEMA_VERSION, Repo

ROOT = Path(__file__).resolve().parents[2]
DB_DIR = ROOT / "src" / "engine_migrate" / "data" / "db"

Extractor = Callable[[Repo], dict[str, Any]]

ENGINES: dict[str, dict[str, Any]] = {
    "vllm": {"clone": "vllm", "repo": vllm.REPO_URL, "extract": vllm.extract},
    "sglang": {"clone": "sglang", "repo": sglang.REPO_URL, "extract": sglang.extract},
    "llamacpp": {"clone": "llama.cpp", "repo": llamacpp.REPO_URL, "extract": llamacpp.extract},
    "tgi": {"clone": "text-generation-inference", "repo": tgi.REPO_URL, "extract": tgi.extract},
    "ollama": {"clone": "ollama", "repo": ollama.REPO_URL, "extract": ollama.extract},
}

# Tags the shipped database is built from.  Add a tag here to add a version.
PINS: dict[str, list[str]] = {
    "vllm": ["v0.28.0", "v0.29.0", "v0.30.0", "v0.31.0", "v0.32.0", "v0.33.0"],
    "sglang": ["v0.5.18", "v0.5.19", "v0.5.20", "v0.5.21", "v0.5.22", "v0.5.23"],
    "llamacpp": ["b9000", "b10000", "b11000", "b11500", "b12000", "b12323"],
    "tgi": ["v3.0.2", "v3.1.1", "v3.2.3", "v3.3.7"],
    "ollama": ["v0.33.0", "v0.34.0", "v0.35.0", "v0.40.0", "v0.40.16"],
}


def version_of(tag: str) -> str:
    return tag[1:] if tag.startswith("v") and tag[1:2].isdigit() else tag


def build(engine: str, tag: str, ref: Path) -> dict[str, Any]:
    spec = ENGINES[engine]
    repo = Repo(ref / spec["clone"], tag)
    data = spec["extract"](repo)
    return {
        "schema": SCHEMA_VERSION,
        "engine": engine,
        "version": version_of(tag),
        "provenance": {
            "repo": spec["repo"],
            "tag": tag,
            "commit": repo.commit(),
            "tag_date": repo.date(),
            "method": "static source extraction",
            "sources": data["sources"],
        },
        "notes": data.get("notes", []),
        "options": data["options"],
        "metrics": data["metrics"],
    }


def write(doc: dict[str, Any], out_dir: Path) -> Path:
    path: Path = out_dir / str(doc["engine"]) / f"{doc['version']}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def write_index(out_dir: Path) -> None:
    index: dict[str, list[dict[str, Any]]] = {}
    for engine in ENGINES:
        entries = []
        for path in (out_dir / engine).glob("*.json"):
            doc = json.loads(path.read_text(encoding="utf-8"))
            kinds: dict[str, int] = {}
            for opt in doc["options"]:
                kinds[opt["kind"]] = kinds.get(opt["kind"], 0) + 1
            entries.append(
                {
                    "version": doc["version"],
                    "tag": doc["provenance"]["tag"],
                    "tag_date": doc["provenance"]["tag_date"],
                    "options": kinds,
                    "metrics": len(doc["metrics"]),
                }
            )
        entries.sort(key=lambda e: (e["tag_date"], e["version"]))
        index[engine] = entries
    (out_dir / "index.json").write_text(json.dumps(index, indent=1, sort_keys=True) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--ref", type=Path, required=True, help="directory holding the reference clones")
    parser.add_argument("--engine", choices=sorted(ENGINES), action="append")
    parser.add_argument("--tag", action="append", help="tag to extract (default: all pinned tags)")
    parser.add_argument("--out", type=Path, default=DB_DIR)
    args = parser.parse_args(argv)
    for engine in args.engine or sorted(ENGINES):
        for tag in args.tag or PINS[engine]:
            doc = build(engine, tag, args.ref)
            path = write(doc, args.out)
            kinds: dict[str, int] = {}
            for opt in doc["options"]:
                kinds[opt["kind"]] = kinds.get(opt["kind"], 0) + 1
            print(f"{engine:9s} {tag:10s} options={kinds} metrics={len(doc['metrics'])} -> {path.name}")
    write_index(args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
