"""Credential and config loading. No Keychain, no AI provider keys — see docs/mcp-server.md."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ASCCredentials:
    key_id: str
    issuer_id: str
    p8_pem: str


def load_asc_credentials() -> ASCCredentials | None:
    """Load ASC credentials from environment variables (see .env.example)."""
    key_id = os.environ.get("ASC_KEY_ID")
    issuer_id = os.environ.get("ASC_ISSUER_ID")
    p8_pem = os.environ.get("ASC_P8_PEM")

    if not p8_pem:
        p8_path = os.environ.get("ASC_P8_PATH")
        if p8_path:
            p8_pem = Path(p8_path).expanduser().read_text()

    if not (key_id and issuer_id and p8_pem):
        return None
    return ASCCredentials(key_id=key_id, issuer_id=issuer_id, p8_pem=p8_pem)
