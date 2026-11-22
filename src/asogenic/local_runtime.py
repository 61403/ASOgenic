"""Single-user runtime used by local stdio installations.

This module intentionally depends only on public core modules. Hosted tenant
storage, identity, and credential-vault code belongs in the private adapter.
"""

from __future__ import annotations

import logging
import os
import secrets
import string
import time
from pathlib import Path
from typing import Any

from asogenic.config import load_asc_credentials
from asogenic.session import SessionStore

LOCAL_TENANT_ID = "local"
_LOCAL_STATE_DIR_ENV = "ASOGENIC_LOCAL_STATE_DIR"
_POOL_SECRET_FILE = "pool-signing-secret"
_POOL_SECRET_HEX_LENGTH = 64
_POOL_SECRET_READ_RETRIES = 50
_POOL_SECRET_READ_RETRY_SECONDS = 0.01

logger = logging.getLogger("asogenic.local_runtime")


class LocalRuntime:
    """Local credentials, state, and process setup for stdio installations."""

    def pool_signing_secret_path(self) -> Path:
        """Return the owner-local path for the persistent pool-signing secret.

        ``ASOGENIC_LOCAL_STATE_DIR`` exists for portable or sandboxed installations;
        ordinary installations use the same ``~/.config/asogenic`` home as the
        documented local credential file.
        """
        configured_dir = os.environ.get(_LOCAL_STATE_DIR_ENV)
        state_dir = Path(configured_dir).expanduser() if configured_dir else Path.home() / ".config" / "asogenic"
        return state_dir / _POOL_SECRET_FILE

    @staticmethod
    def _read_pool_signing_secret(secret_path: Path) -> str | None:
        """Return a complete persisted secret, never a partially written value."""
        try:
            secret = secret_path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return None
        if len(secret) == _POOL_SECRET_HEX_LENGTH and all(char in string.hexdigits for char in secret):
            return secret
        return None

    def _wait_for_pool_signing_secret(self, secret_path: Path) -> str | None:
        """Wait for a concurrent local process to finish creating the secret file."""
        for _ in range(_POOL_SECRET_READ_RETRIES):
            secret = self._read_pool_signing_secret(secret_path)
            if secret:
                return secret
            time.sleep(_POOL_SECRET_READ_RETRY_SECONDS)
        return None

    def ensure_pool_signing_secret(self) -> str:
        """Load or create the local HMAC secret without exposing it to the user.

        An explicit environment value remains authoritative for managed and
        reproducible deployments. Otherwise the local stdio launcher creates a
        256-bit value once and persists it with owner-only permissions so signed
        research pools remain valid across restarts.
        """
        configured = os.environ.get("ASOGENIC_POOL_SECRET")
        if configured:
            return configured

        secret_path = self.pool_signing_secret_path()
        try:
            secret_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            secret_path.parent.chmod(0o700)

            secret = self._read_pool_signing_secret(secret_path)

            if not secret:
                generated = secrets.token_hex(32)
                try:
                    fd = os.open(
                        secret_path,
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                        0o600,
                    )
                except FileExistsError:
                    secret = self._wait_for_pool_signing_secret(secret_path)
                    if not secret:
                        raise OSError(
                            f"local pool-signing secret at {secret_path} is incomplete or invalid"
                        )
                else:
                    with os.fdopen(fd, "w", encoding="utf-8") as secret_file:
                        secret_file.write(f"{generated}\n")
                        secret_file.flush()
                        os.fsync(secret_file.fileno())
                    secret = generated
                    logger.info("created persistent local pool-signing secret")

            secret_path.chmod(0o600)
        except OSError as error:
            # Pool signing still protects a running local session when a locked-down
            # filesystem prevents persistence. Preserve the previous behavior in
            # that exceptional case rather than preventing the MCP from starting.
            secret = secrets.token_hex(32)
            logger.warning(
                "could not persist the local pool-signing secret (%s); "
                "pools will be valid only until this process exits",
                error,
            )

        os.environ["ASOGENIC_POOL_SECRET"] = secret
        return secret

    def resolve_tenant_id(self) -> str:
        return LOCAL_TENANT_ID

    @property
    def allows_local_files(self) -> bool:
        return True

    def get_tenant_store(self) -> Any:
        raise RuntimeError("tenant storage is available only in the hosted runtime")

    def build_session_store(self, tenant_id: str) -> SessionStore:
        return SessionStore()

    def load_credentials(self, tenant_id: str) -> Any:
        return load_asc_credentials()

    def missing_credentials_message(self, tenant_id: str) -> str:
        return (
            "no ASC credentials configured for local/stdio mode. Set ASC_KEY_ID, "
            "ASC_ISSUER_ID, and ASC_P8_PATH (or ASC_P8_PEM) in the MCP client's local env block."
        )
