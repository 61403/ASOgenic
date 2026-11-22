# Contributing to ASOgenic

Thanks for helping make local App Store workflows more useful and reliable.
This project is the local ASOgenic MCP server: a stdio runtime, App Store
Connect client, ASO workflows, and public tool catalog.

Start with [SUPPORT.md](SUPPORT.md) to choose the right place for a question,
bug, feature idea, or security report. By participating, you agree to follow
the [Code of Conduct](CODE_OF_CONDUCT.md).

## Good contributions

This repository welcomes improvements to local MCP tools, schemas, App Store
Connect workflows, ASO logic, documentation, and tests. Keep contributions
usable from a local stdio MCP server and free of deployment-specific
configuration.

For the managed ASOgenic experience, visit [asogenic.com](https://asogenic.com/).

## Before you open a pull request

1. Search existing issues and discussions. Open an issue first for behavior,
   public API, or workflow changes that need design discussion.
2. Fork the repository and branch from `main` using a concise, descriptive
   branch name.
3. Keep the change focused and explain the user-visible behavior it changes.
4. Add or update focused tests when code behavior changes. Changes to a tool
   schema, description, or annotation must update the reviewed catalog contract
   in the same pull request.
5. Never include App Store Connect keys, API tokens, non-public listings,
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

## Release artifact review

Every pull request is checked against `release/public-artifact.toml`. Add a
file only when it has been reviewed for provenance, licensing, and secrets. Do
not weaken the allowlist just to include unreviewed material.

## Review expectations

Maintainers aim to acknowledge good-faith contributions promptly. A pull
request may need revision for correctness, API compatibility, safety,
documentation, or release-artifact review. We value small, well-explained
changes and respectful technical discussion.
