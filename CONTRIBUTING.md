# Contributing to ASOgenic

Thanks for helping make local App Store workflows more useful and reliable.
This repository is the canonical source for the shareable ASOgenic MCP core:
the local stdio runtime, App Store Connect client, deterministic ASO logic, and
public tool catalog.

Start with [SUPPORT.md](SUPPORT.md) to choose the right place for a question,
bug, feature idea, or security report. By participating, you agree to follow
the [Code of Conduct](CODE_OF_CONDUCT.md).

## Where a change belongs

| Change | Destination |
| --- | --- |
| Local MCP tools, schemas, ASC calls, ASO algorithms, local runtime | This repository |
| OAuth, hosted tenants, credential vaults, billing, dashboard, HTTP operations | Private hosted adapter |
| A core change that affects hosted users | This repository first, then an adapter dependency update |

Do not copy a fix into the private adapter and later export it here. That
would create two sources of truth and make community contributions disappear.

## Before you open a pull request

1. Search existing issues and discussions. Open an issue first for behavior,
   public API, or workflow changes that need design discussion.
2. Fork the repository and branch from `main` using a concise, descriptive
   branch name.
3. Keep the change focused and explain the user-visible behavior it changes.
4. Add or update focused tests when code behavior changes. Changes to a tool
   schema, description, or annotation must update the reviewed catalog contract
   in the same pull request.
5. Never include App Store Connect keys, API tokens, private listings,
   customer data, databases, logs, or screenshots containing private data.

## Local checks

Create a virtual environment, install the project, and run the release gate:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
python scripts/verify_public_artifact.py verify . --manifest release/public-artifact.toml
python scripts/verify_public_artifact.py build-check . --manifest release/public-artifact.toml
```

The pull-request workflow additionally builds the wheel and source
distribution, verifies their inventories, and starts the installed local MCP
wheel in a fresh environment. Please run focused tests relevant to your change
as well.

## Public/private boundary

Every pull request is checked against `release/public-artifact.toml`. Add a
file only when it has been reviewed for provenance, licensing, secrets, and the
public/private architecture boundary. Do not weaken the allowlist to export
private code or operational material.

Maintainers merge public-core PRs here, release a tagged version, then update
the private adapter to an exact package version. The adapter must pass its own
integration and boundary checks before deployment.

## Review expectations

Maintainers aim to acknowledge good-faith contributions promptly. A pull
request may need revision for correctness, API compatibility, safety,
documentation, or the public/private boundary. We value small, well-explained
changes and respectful technical discussion.
