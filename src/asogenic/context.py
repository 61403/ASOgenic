"""Runtime-scoped state shared by the tool layer.

Local installations use :class:`LocalRuntime` by default. Private hosted entry
points can explicitly install the adapter from ``asogenic.hosted_runtime``.
Hosted code is loaded lazily so importing the local context does not import
tenant storage or OAuth modules.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol, TypeVar

from asogenic.aso.research_service import ASOResearchService
from asogenic.errors import asc_auth_error
from asogenic.local_runtime import LOCAL_TENANT_ID, LocalRuntime
from asogenic.models.keywords import KeywordPool
from asogenic.services.apple_search_hints_client import AppleSearchHintsClient
from asogenic.services.asc_auth import ASCTokenCache
from asogenic.services.asc_client import ASCClient
from asogenic.services.itunes_search_client import ITunesSearchClient
from asogenic.services.keyword_pool_cache import KeywordPoolCacheService
from asogenic.session import SessionStore, WorkspaceSession

if TYPE_CHECKING:
    from asogenic.tenancy.storage import TenantStore

T = TypeVar("T")

logger = logging.getLogger("asogenic.context")
__all__ = ["LOCAL_TENANT_ID", "LocalRuntime"]


class Runtime(Protocol):
    """Services the shared tool layer needs from its execution environment."""

    def resolve_tenant_id(self) -> str: ...
    def get_tenant_store(self) -> TenantStore: ...
    def build_session_store(self, tenant_id: str) -> SessionStore: ...
    def load_credentials(self, tenant_id: str) -> Any: ...
    def missing_credentials_message(self, tenant_id: str) -> str: ...
    @property
    def allows_local_files(self) -> bool: ...


def __getattr__(name: str) -> Any:
    """Keep the historical hosted-runtime import lazy for private compatibility callers."""
    if name == "HostedRuntime":
        from asogenic.hosted_runtime import HostedRuntime

        return HostedRuntime
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


_runtime: Runtime = LocalRuntime()


def configure_runtime(runtime: Runtime) -> None:
    """Set the process runtime. Hosted entrypoints must explicitly install HostedRuntime."""
    global _runtime
    _runtime = runtime


def get_runtime() -> Runtime:
    return _runtime


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _is_hosted() -> bool:
    """Whether the private hosted adapter has been explicitly installed."""
    return bool(getattr(_runtime, "is_hosted_runtime", False))


def resolve_tenant_id() -> str:
    """The calling tenant for this request: the tenant_id claim on a verified platform API key
    (hosted/HTTP mode), or LOCAL_TENANT_ID when no auth provider is configured (stdio/local
    mode — the original single-developer deployment).

    In hosted mode this fails closed: a request that reached a tool without a verified tenant
    claim is an auth failure, not a fall-through to the unmetered "local" identity (which would
    bypass quota and read ASC creds from the process env). fastmcp's RequireAuthMiddleware
    already rejects such a request before the tool body — this is defence in depth against that
    guarantee ever regressing."""
    return _runtime.resolve_tenant_id()


def get_tenant_store() -> TenantStore:
    return _runtime.get_tenant_store()


def set_tenant_store(store: TenantStore | None) -> None:
    """Test-only: inject an isolated TenantStore (e.g. an in-memory/temp-file one)."""
    if store is None:
        configure_runtime(LocalRuntime())
    else:
        from asogenic.hosted_runtime import HostedRuntime

        runtime = _runtime
        if isinstance(runtime, HostedRuntime):
            runtime._store = store
        else:
            configure_runtime(HostedRuntime(store))


class _PerTenant:
    """A tiny per-tenant registry: builds and caches one instance of T per tenant id, via a
    caller-supplied factory. Not a full LRU/eviction story — fine for the process-lifetime
    in-memory scope this replaces; a hosted deployment with many tenants and long uptimes should
    graduate this to a bounded cache or external store in a later iteration."""

    def __init__(self) -> None:
        self._by_tenant: dict[str, object] = {}

    def get_or_create(self, tenant_id: str, factory: Callable[[], T]) -> T:
        instance = self._by_tenant.get(tenant_id)
        if instance is None:
            instance = factory()
            self._by_tenant[tenant_id] = instance
        return instance  # type: ignore[return-value]

    def set(self, tenant_id: str, instance: object) -> None:
        self._by_tenant[tenant_id] = instance

    def delete(self, tenant_id: str) -> None:
        self._by_tenant.pop(tenant_id, None)

    def peek(self, tenant_id: str) -> object | None:
        """The cached instance for this tenant, or None — without running the factory."""
        return self._by_tenant.get(tenant_id)

    def reset(self) -> None:
        self._by_tenant = {}


_session_stores = _PerTenant()
_pool_caches = _PerTenant()
_asc_clients = _PerTenant()
_research_services = _PerTenant()


def _build_session_store_for_tenant(tenant_id: str) -> SessionStore:
    # Local/stdio stays pure in-memory (no SQLite touched) — persistence is additive for hosted
    # tenants only, matching the same LOCAL_TENANT_ID split used for ASC credentials above.
    return _runtime.build_session_store(tenant_id)


class _SessionStoreProxy:
    """Delegates to the calling tenant's own SessionStore, resolved per call — so `.get(...)`
    from tenant A can never see a session created under `.get_or_create(...)` by tenant B."""

    def _store(self) -> SessionStore:
        tenant_id = resolve_tenant_id()
        return _session_stores.get_or_create(tenant_id, lambda: _build_session_store_for_tenant(tenant_id))

    def get_or_create(self, key: str) -> WorkspaceSession:
        return self._store().get_or_create(key)

    def get(self, key: str) -> WorkspaceSession | None:
        return self._store().get(key)

    def flush(self, key: str) -> None:
        """Persist the calling tenant's current in-memory state for one session_key. Call this
        after any in-place mutation of a WorkspaceSession/LocalizationRecord obtained through
        get()/get_or_create() — see SessionStore's docstring for why this isn't automatic."""
        self._store().flush(key)


class _PoolCacheProxy:
    """Delegates to the calling tenant's own KeywordPoolCacheService."""

    def _cache(self) -> KeywordPoolCacheService:
        tenant_id = resolve_tenant_id()
        return _pool_caches.get_or_create(
            tenant_id, lambda: KeywordPoolCacheService(date_provider=_utc_now)
        )

    def load(self, *, bundle_id: str, locale_id: str, request_fingerprint: str = "legacy") -> KeywordPool | None:
        return self._cache().load(bundle_id=bundle_id, locale_id=locale_id, request_fingerprint=request_fingerprint)

    def save(self, pool: KeywordPool, *, bundle_id: str, request_fingerprint: str = "legacy") -> None:
        self._cache().save(pool, bundle_id=bundle_id, request_fingerprint=request_fingerprint)

    def delete(self, *, bundle_id: str, locale_id: str) -> None:
        self._cache().delete(bundle_id=bundle_id, locale_id=locale_id)


SESSION_STORE = _SessionStoreProxy()
POOL_CACHE = _PoolCacheProxy()


def _build_asc_client_for_tenant(tenant_id: str) -> ASCClient:
    # Process-wide ASC_* variables are valid only for local/stdio mode. Never let a
    # deployment-wide Apple account become the implicit credential for a hosted tenant.
    creds = _runtime.load_credentials(tenant_id)
    if creds is None:
        raise asc_auth_error(_runtime.missing_credentials_message(tenant_id))
    return ASCClient(ASCTokenCache(creds))


def get_asc_client() -> ASCClient:
    """Return the calling tenant's ASC client from its allowed credential source.

    Local/stdio uses the MCP client's environment; hosted/HTTP uses the dashboard owner's vault
    or a session import. Raises an auth error if no credential is configured for this tenant.
    """
    tenant_id = resolve_tenant_id()
    return _asc_clients.get_or_create(tenant_id, lambda: _build_asc_client_for_tenant(tenant_id))


def set_asc_client(client: ASCClient) -> None:
    """Install an explicitly-built client for the calling tenant only. Session-scoped — not
    persisted to disk or the tenant store, so it
    does not survive a process restart and never leaks to any other tenant."""
    _asc_clients.set(resolve_tenant_id(), client)


def clear_asc_client() -> None:
    """Drop the calling tenant's cached ASC client, including any decrypted credential it holds."""
    _asc_clients.delete(resolve_tenant_id())


def clear_tenant_state(tenant_id: str) -> None:
    """Evict all cached state for a revoked tenant, including decrypted ASC credentials."""
    _asc_clients.delete(tenant_id)
    _session_stores.delete(tenant_id)
    _pool_caches.delete(tenant_id)


def clear_tenant_asc_client(tenant_id: str) -> None:
    """Evict only the cached ASC client for an administratively updated tenant."""
    _asc_clients.delete(tenant_id)


def clear_credential_scope_asc_clients(tenant_id: str) -> None:
    """Evict cached decrypted clients for every OAuth/key tenant sharing one account credential."""
    for scoped_tenant_id in get_tenant_store().credential_scope_tenant_ids(tenant_id):
        _asc_clients.delete(scoped_tenant_id)


def reset_asc_client() -> None:
    """Test-only: force get_asc_client() to rebuild the calling tenant's client from scratch."""
    _asc_clients.reset()


def reset_session_store() -> None:
    """Test-only: clear every tenant's workspace sessions."""
    _session_stores.reset()


def reset_pool_cache() -> None:
    """Test-only: clear every tenant's keyword pool cache."""
    _pool_caches.reset()


def get_research_service() -> ASOResearchService:
    """The shared ASOResearchService. Research tools call public Apple endpoints and need no ASC
    credential, so one instance is reused across tenants (no tenant secret to isolate here)."""
    return _research_services.get_or_create(
        "shared",
        lambda: ASOResearchService(
            hints_client=AppleSearchHintsClient(),
            search_client=ITunesSearchClient(),
            date_provider=_utc_now,
        ),
    )


def set_research_service(service: ASOResearchService) -> None:
    """Test-only: inject a research service backed by fake network clients."""
    _research_services.set("shared", service)


def reset_research_service() -> None:
    _research_services.reset()
