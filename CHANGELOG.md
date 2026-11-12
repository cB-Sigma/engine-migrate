# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project
uses [Semantic Versioning](https://semver.org/). Database additions (new
engine versions) are listed under "Database".

## [Unreleased]

## [0.1.0] - 2026-11-12

### Added

- `translate`: command lines, YAML config files, env files, `docker run`
  lines and Kubernetes container specs between vLLM, SGLang, llama.cpp, TGI
  and Ollama, with an `EXACT` / `APPROXIMATE` / `NO EQUIVALENT` / `UNKNOWN`
  report that cites the engine source line of every option.
- `diff`: options and metrics added, removed, renamed, deprecated or changed
  between two versions of one engine; `--config` restricts the report to one
  configuration and suggests a rewritten config.
- `lint-metrics`: Grafana dashboards and Prometheus rule files checked
  against the metrics of an engine version, with CI exit codes.
- `flags`, `metrics`, `versions` to browse the database; `--demo`;
  table, JSON and Markdown output.
- Static extractors for all five engines and a validator that measures them
  against each engine's own generated reference.
- Mapping table with 42 concepts.

### Database

- vLLM 0.28.0 - 0.33.0, SGLang 0.5.18 - 0.5.23, llama.cpp b9000 - b12323
  (six builds), TGI 3.0.2 - 3.3.7 (four releases), Ollama 0.33.0 - 0.40.16
  (five releases).

[Unreleased]: https://github.com/cb-Sigma/engine-migrate/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/cb-Sigma/engine-migrate/releases/tag/v0.1.0
