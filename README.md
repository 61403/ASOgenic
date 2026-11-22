# ASOgenic

ASOgenic is an open-source, local Model Context Protocol (MCP) server for App
Store Connect and App Store Optimization workflows. It runs as a local stdio
process under your AI client and uses only the App Store Connect credentials
you provide on your machine.

It supports practical work such as inspecting apps, researching listings,
preparing metadata, managing releases, working with screenshots and previews,
and checking post-launch readiness.

> ASOgenic is not affiliated with Apple. Review every proposed App Store
> change before it is submitted or published.

## What this repository contains

This is the canonical source for the shareable ASOgenic core: the local MCP
runtime, public tool catalog, App Store Connect client, and deterministic ASO
logic. It deliberately excludes a hosted dashboard, OAuth, tenant storage,
billing, private operations, and customer information.

Community issues and pull requests belong here. The separate private hosted
adapter consumes tagged releases of this package and must never become a
forked copy of the public core.

## Requirements

- Python 3.11 or later
- An App Store Connect API key with access appropriate for the actions you
  intend to take
- Your key ID, issuer ID, and the path to the downloaded `.p8` private-key
  file

Keep the `.p8` file outside this repository. Do not commit it, paste it into
an issue, or share it with an AI agent.

## Install from source

```sh
git clone https://github.com/61403/ASOgenic.git
cd ASOgenic
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install .
```

On Windows, activate with `.venv\\Scripts\\Activate.ps1`. The executable is
then `.venv\\Scripts\\asogenic-local.exe` instead of
`.venv/bin/asogenic-local`.

## Configure App Store Connect access

Set these environment variables in your shell or in your AI client's MCP
configuration. Use absolute paths.

```sh
export ASC_KEY_ID="YOUR_KEY_ID"
export ASC_ISSUER_ID="YOUR_ISSUER_ID"
export ASC_P8_PATH="$HOME/.config/asogenic/AuthKey_YOUR_KEY_ID.p8"
```

Start the server manually to confirm it can launch:

```sh
.venv/bin/asogenic-local
```

The server communicates over standard input/output, so a successful manual
launch may appear to wait silently. Stop it with `Ctrl+C`; your AI client
starts it automatically after configuration.

### About the local signing secret

ASOgenic automatically generates and securely persists a random local
pool-signing secret on first run at
`~/.config/asogenic/pool-signing-secret`. You do not need to create, copy, or
share it. For controlled automation only, you may set
`ASOGENIC_POOL_SECRET`; otherwise leave it alone.

## Connect an AI agent

Use the absolute path to `asogenic-local` and pass the three App Store Connect
environment variables. Do not put real credentials in a configuration file that
you commit or share.

Replace these placeholders before use:

```text
/absolute/path/to/ASOgenic/.venv/bin/asogenic-local
/absolute/path/to/AuthKey_YOUR_KEY_ID.p8
YOUR_KEY_ID
YOUR_ISSUER_ID
```

### Codex

Codex CLI and Codex desktop share local MCP configuration on supported
installations. Add the server with:

```sh
codex mcp add asogenic \
  --env ASC_KEY_ID=YOUR_KEY_ID \
  --env ASC_ISSUER_ID=YOUR_ISSUER_ID \
  --env ASC_P8_PATH=/absolute/path/to/AuthKey_YOUR_KEY_ID.p8 \
  -- /absolute/path/to/ASOgenic/.venv/bin/asogenic-local
```

Check the result with `codex mcp list`.

### Claude Code

```sh
claude mcp add --scope user \
  -e ASC_KEY_ID=YOUR_KEY_ID \
  -e ASC_ISSUER_ID=YOUR_ISSUER_ID \
  -e ASC_P8_PATH=/absolute/path/to/AuthKey_YOUR_KEY_ID.p8 \
  asogenic -- /absolute/path/to/ASOgenic/.venv/bin/asogenic-local
```

Run `claude mcp list` or use `/mcp` in Claude Code to verify the connection.

### Claude Desktop, Cursor, and Windsurf

Add this entry using each client's MCP settings UI. Claude Desktop also
supports its local MCP configuration file; Cursor reads `~/.cursor/mcp.json`;
and Windsurf exposes the same fields through its MCP Servers settings.

```json
{
  "mcpServers": {
    "asogenic": {
      "type": "stdio",
      "command": "/absolute/path/to/ASOgenic/.venv/bin/asogenic-local",
      "env": {
        "ASC_KEY_ID": "YOUR_KEY_ID",
        "ASC_ISSUER_ID": "YOUR_ISSUER_ID",
        "ASC_P8_PATH": "/absolute/path/to/AuthKey_YOUR_KEY_ID.p8"
      }
    }
  }
}
```

For Claude Desktop on macOS, use **Settings → Developer → Edit Config** and
merge the `asogenic` object into `mcpServers`. Fully restart the client after
editing its configuration.

### VS Code and GitHub Copilot

In VS Code, open **Chat: Open Customizations**, select **MCP Servers**, and add
a **Command (stdio)** server. Enter the executable path and the three
environment variables above. Choose a personal configuration for your own
machine, rather than committing credentials to a workspace file.

For GitHub Copilot CLI, use:

```sh
copilot mcp add asogenic \
  --env ASC_KEY_ID=YOUR_KEY_ID \
  --env ASC_ISSUER_ID=YOUR_ISSUER_ID \
  --env ASC_P8_PATH=/absolute/path/to/AuthKey_YOUR_KEY_ID.p8 \
  -- /absolute/path/to/ASOgenic/.venv/bin/asogenic-local
```

Verify it with `copilot mcp list`.

### Gemini CLI

```sh
gemini mcp add asogenic /absolute/path/to/ASOgenic/.venv/bin/asogenic-local \
  --env ASC_KEY_ID=YOUR_KEY_ID \
  --env ASC_ISSUER_ID=YOUR_ISSUER_ID \
  --env ASC_P8_PATH=/absolute/path/to/AuthKey_YOUR_KEY_ID.p8
```

For another MCP-compatible client, create a local **stdio** server entry with
the same executable and environment. Consult that client's current MCP setup
guide if its configuration format differs.

## First use

Ask your agent to call `asogenic_auth_status` first. Once it reports the
expected App Store Connect account, begin with a read-only request, such as
listing apps or inspecting one app's current metadata. Give the agent a clear
instruction whenever a task might change App Store Connect state.

## Development and release checks

```sh
python -m pip install -e .
python scripts/verify_public_artifact.py verify . --manifest release/public-artifact.toml
python scripts/verify_public_artifact.py build-check . --manifest release/public-artifact.toml
```

The pull-request workflow also builds the wheel and source distribution,
compares them against the reviewed allowlist, and smoke-tests the installed
local MCP server.

## Community

Feedback, ideas, bugs, and improvements are welcome:

- Read [CONTRIBUTING.md](CONTRIBUTING.md) before opening a pull request.
- Use [SUPPORT.md](SUPPORT.md) to choose the right channel for questions,
  bugs, feature ideas, and security reports.
- Follow the [Code of Conduct](CODE_OF_CONDUCT.md) in all project spaces.
- Never post credentials, private keys, customer data, or sensitive App Store
  information. See [SECURITY.md](SECURITY.md) for private reporting.

## License

MIT. See [LICENSE](LICENSE).
