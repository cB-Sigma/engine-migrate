# Security

## Supported versions

Only the latest release (and `main`) receives fixes.

## What the tool does with your input

`engine-migrate` reads the files you pass, the JSON/YAML data bundled in the
package, and nothing else. It makes no network requests, does not execute
any engine, and loads YAML with `yaml.safe_load`. Configurations often
contain secrets (API keys, tokens): they are echoed in the output because
the output is your translated config, so treat reports like the config
itself.

The extraction tools under `tools/` are developer tools. They run `git` in
reference clones and parse source files as text; they never import or run
the code they read.

## Reporting a vulnerability

Please use GitHub's private
[security advisory form](https://github.com/cb-Sigma/engine-migrate/security/advisories/new)
rather than a public issue. You can expect an acknowledgement within a week.

A translation that is wrong in a security-relevant way (for example an
authentication or trust-remote-code option reported as `EXACT` when it is
not) is a correctness bug: a public issue is fine and welcome.
