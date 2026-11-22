"""Small, client-visible release contract for deployment verification."""

from __future__ import annotations

import os

BUILD_ID = os.environ.get("ASOGENIC_BUILD_ID", "workspace")
SERVER_VERSION = os.environ.get("ASOGENIC_SERVER_VERSION", "1.5.3")
POOL_SIGNATURE_VERSION = "pool-json-v1"
CACHE_KEY_VERSION = "research-request-v2"
CONTRACT_VERSION = "aso-contract-v1"


def info() -> dict[str, str]:
    return {
        "server_version": SERVER_VERSION,
        "server_build": BUILD_ID,
        "contract_version": CONTRACT_VERSION,
        "pool_signature_version": POOL_SIGNATURE_VERSION,
        "cache_key_version": CACHE_KEY_VERSION,
    }
