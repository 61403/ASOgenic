"""Behavior checks for the public, credential-free local MCP boundary."""

from __future__ import annotations

import json
import os
import selectors
import subprocess
import sys
from pathlib import Path
from typing import Any


def _receive_json(process: subprocess.Popen[str], selector: selectors.BaseSelector) -> dict[str, Any]:
    events = selector.select(timeout=20)
    if not events or process.stdout is None:
        raise AssertionError("timed out waiting for a stdio MCP response")
    line = process.stdout.readline()
    if not line:
        stderr = process.stderr.read() if process.stderr is not None else ""
        raise AssertionError(f"stdio MCP exited before responding: {stderr[-2000:]}")
    return json.loads(line)


def _request(
    process: subprocess.Popen[str],
    selector: selectors.BaseSelector,
    request: dict[str, Any],
) -> dict[str, Any]:
    assert process.stdin is not None
    process.stdin.write(json.dumps(request) + "\n")
    process.stdin.flush()
    response = _receive_json(process, selector)
    assert response.get("id") == request.get("id")
    return response


def test_local_stdio_inventory_and_missing_credential_status(tmp_path: Path) -> None:
    """The public wheel boundary works without Apple credentials or hosted modules."""
    environment = {
        **os.environ,
        "ASC_KEY_ID": "",
        "ASC_ISSUER_ID": "",
        "ASC_P8_PATH": "",
        "ASOGENIC_LOCAL_STATE_DIR": str(tmp_path / "asogenic-state"),
    }
    process = subprocess.Popen(
        [sys.executable, "-m", "asogenic.local_server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        env=environment,
    )
    selector = selectors.DefaultSelector()
    assert process.stdout is not None
    selector.register(process.stdout, selectors.EVENT_READ)
    try:
        initialized = _request(
            process,
            selector,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "pytest", "version": "1"},
                },
            },
        )
        assert initialized["result"]["serverInfo"]["name"] == "asogenic"

        assert process.stdin is not None
        process.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
        process.stdin.flush()

        tools = _request(process, selector, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        names = {tool["name"] for tool in tools["result"]["tools"]}
        assert {"asogenic_auth_status", "asogenic_list_apps", "asogenic_get_release_readiness"} <= names
        assert "asogenic_rotate_api_key" not in names

        auth = _request(
            process,
            selector,
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "asogenic_auth_status", "arguments": {}},
            },
        )
        result = auth["result"]
        assert result["isError"] is False
        assert result["structuredContent"] == {
            "asc_configured": False,
            "asc_valid": False,
            "credential_source": None,
        }
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
