"""Install a built wheel in a fresh venv and smoke-test its local stdio MCP."""

from __future__ import annotations

import argparse
import json
import os
import selectors
import subprocess
import sys
import tempfile
import venv
from pathlib import Path
from typing import Any

HOSTED_ONLY_TOOLS = {
    "asogenic_clear_asc_credential",
    "asogenic_get_asc_credential_status",
    "asogenic_get_server_status",
    "asogenic_rotate_api_key",
    "asogenic_revoke_tenant",
    "asogenic_get_activity_log",
    "asogenic_submit_feedback",
}
REQUIRED_LOCAL_TOOLS = {
    "asogenic_auth_status",
    "asogenic_list_apps",
    "asogenic_get_release_readiness",
}
FORBIDDEN_IMPORTS = ("asogenic.hosted_runtime", "asogenic.oauth", "asogenic.tenancy", "asogenic.server")


class SmokeError(RuntimeError):
    """Raised when the installed wheel fails the boundary or stdio smoke check."""


def _receive_json(process: subprocess.Popen[str], selector: selectors.BaseSelector) -> dict[str, Any]:
    events = selector.select(timeout=20)
    if not events or process.stdout is None:
        raise SmokeError("timed out waiting for the stdio MCP response")
    line = process.stdout.readline()
    if not line:
        stderr = process.stderr.read() if process.stderr is not None else ""
        raise SmokeError(f"stdio MCP exited before responding: {stderr[-2000:]}")
    try:
        message = json.loads(line)
    except json.JSONDecodeError as exc:
        raise SmokeError(f"stdio MCP emitted a non-JSON protocol line: {line[:200]!r}") from exc
    if not isinstance(message, dict):
        raise SmokeError("stdio MCP response must be a JSON object")
    return message


def _stdio_inventory(command: list[str]) -> set[str]:
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        env={**os.environ, "ASC_KEY_ID": "", "ASC_ISSUER_ID": "", "ASC_P8_PATH": ""},
    )
    try:
        if process.stdin is None or process.stdout is None:
            raise SmokeError("could not open stdio MCP pipes")
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)
        process.stdin.write(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-03-26",
                        "capabilities": {},
                        "clientInfo": {"name": "asogenic-public-wheel-smoke", "version": "0"},
                    },
                }
            )
            + "\n"
        )
        process.stdin.flush()
        initialized = _receive_json(process, selector)
        if initialized.get("id") != 1 or "result" not in initialized:
            raise SmokeError(f"unexpected initialize response: {initialized}")

        process.stdin.write(
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n"
        )
        process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}) + "\n")
        process.stdin.flush()
        listed = _receive_json(process, selector)
        if listed.get("id") != 2 or "result" not in listed:
            raise SmokeError(f"unexpected tools/list response: {listed}")
        tools = listed["result"].get("tools")
        if not isinstance(tools, list):
            raise SmokeError("tools/list response has no tools array")
        names = {
            tool["name"]
            for tool in tools
            if isinstance(tool, dict) and isinstance(tool.get("name"), str)
        }
        missing = REQUIRED_LOCAL_TOOLS - names
        leaked = HOSTED_ONLY_TOOLS & names
        if missing:
            raise SmokeError(f"required local tools are missing: {sorted(missing)}")
        if leaked:
            raise SmokeError(f"hosted-only tools leaked into local catalog: {sorted(leaked)}")
        return names
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def smoke_wheel(wheel: Path) -> set[str]:
    if not wheel.is_file() or wheel.suffix != ".whl":
        raise SmokeError(f"wheel path does not name an existing .whl file: {wheel}")
    with tempfile.TemporaryDirectory(prefix="asogenic-wheel-smoke-") as temporary:
        root = Path(temporary)
        environment = root / "venv"
        venv.EnvBuilder(with_pip=True).create(environment)
        python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        executable = environment / ("Scripts/asogenic-local.exe" if os.name == "nt" else "bin/asogenic-local")
        installed = subprocess.run(
            [str(python), "-m", "pip", "install", str(wheel.resolve())],
            text=True,
            capture_output=True,
            check=False,
        )
        if installed.returncode:
            raise SmokeError(f"wheel installation failed:\n{installed.stdout}\n{installed.stderr}")
        import_check = subprocess.run(
            [
                str(python),
                "-c",
                (
                    "import importlib.util; import asogenic.local_server; "
                    "bad = [name for name in "
                    f"{FORBIDDEN_IMPORTS!r} "
                    "if importlib.util.find_spec(name) is not None]; "
                    "assert not bad, f'hosted imports available in wheel: {bad}'"
                ),
            ],
            cwd=root,
            text=True,
            capture_output=True,
            check=False,
        )
        if import_check.returncode:
            raise SmokeError(f"installed wheel boundary check failed:\n{import_check.stderr}")
        if not executable.is_file():
            raise SmokeError("wheel did not install the asogenic-local entry point")
        return _stdio_inventory([str(executable)])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel", type=Path)
    args = parser.parse_args(argv)
    try:
        names = smoke_wheel(args.wheel)
    except (OSError, SmokeError, subprocess.SubprocessError) as exc:
        print(f"Installed-wheel smoke failed: {exc}", file=sys.stderr)
        return 1
    print(f"Installed-wheel local stdio smoke passed ({len(names)} tools listed; no tool invoked)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
