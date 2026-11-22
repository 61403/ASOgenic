"""Offline contracts for App Store Connect HTTP requests and error mapping."""

from __future__ import annotations

import json

import httpx
import pytest

from asogenic.errors import ASOgenicError
from asogenic.services.asc_client import ASCClient


class _TokenCache:
    def __init__(self) -> None:
        self.invalidated = False

    def get_token(self) -> str:
        return "test-token"

    def invalidate(self) -> None:
        self.invalidated = True


class _Throttler:
    current_requests_per_minute = 60

    def wait_for_slot(self) -> None:
        pass

    def report_success(self) -> None:
        pass

    def report_throttled(self) -> None:
        pass


def test_generic_resource_methods_send_the_asc_jsonapi_contract() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"type": "apps", "id": "1"}]})
        if request.method == "POST":
            return httpx.Response(201, json={"data": {"type": "betaGroups", "id": "2"}})
        if request.method == "PATCH":
            return httpx.Response(200, json={"data": {"type": "betaGroups", "id": "2"}})
        return httpx.Response(204)

    client = ASCClient(
        _TokenCache(),  # type: ignore[arg-type]
        transport=httpx.MockTransport(handler),
        throttler=_Throttler(),  # type: ignore[arg-type]
    )
    try:
        assert client.list_resource("/v1/apps") == [{"type": "apps", "id": "1"}]
        assert client.create_resource(
            "betaGroups",
            attributes={"name": "MCP test"},
            relationships={"app": {"data": {"type": "apps", "id": "1"}}},
        )["data"]["id"] == "2"
        client.update_resource("betaGroups", "2", attributes={"name": "Updated"})
        client.delete_resource("betaGroups", "2")
    finally:
        client.close()

    assert [request.method for request in requests] == ["GET", "POST", "PATCH", "DELETE"]
    assert all(request.headers["Authorization"] == "Bearer test-token" for request in requests)
    assert requests[0].url.path == "/v1/apps"
    assert json.loads(requests[1].content) == {
        "data": {
            "type": "betaGroups",
            "attributes": {"name": "MCP test"},
            "relationships": {"app": {"data": {"type": "apps", "id": "1"}}},
        }
    }
    assert json.loads(requests[2].content) == {
        "data": {"type": "betaGroups", "id": "2", "attributes": {"name": "Updated"}}
    }


def test_unauthorized_response_invalidates_cached_token() -> None:
    token_cache = _TokenCache()
    client = ASCClient(
        token_cache,  # type: ignore[arg-type]
        transport=httpx.MockTransport(lambda request: httpx.Response(401, json={"errors": []})),
        throttler=_Throttler(),  # type: ignore[arg-type]
    )
    try:
        with pytest.raises(ASOgenicError, match="unauthorized") as raised:
            client.get_resource("/v1/apps/1")
    finally:
        client.close()

    assert raised.value.code == "ASC_AUTH_FAILED"
    assert token_cache.invalidated is True
