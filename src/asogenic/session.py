"""In-memory session cache mirroring the Swift WorkspaceCacheService. Source:
docs/interfaces/data-model.md #Cache / snapshot structures.

Keyed by {bundle_id}-{platform}-{version_id}. Holds per-locale LocalizationRecords so a
disconnected client can resume without re-fetching, and approvals can be tracked between
asogenic_approve_locale and asogenic_publish. Never records intent — the client can always
reconstruct any state by re-calling the fetch tools.

Optionally backed by SQLite (tenancy/storage.py's session_state table) for hosted tenants, so an
approved-but-unpublished locale survives a process restart instead of silently vanishing — see
`SessionStore.__init__` and `.flush()`. Local/stdio use stays pure in-memory, matching the
existing single-developer deployment exactly (no new file touched, no behavior change).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from asogenic.models.metadata import LocalizationRecord

if TYPE_CHECKING:
    from asogenic.tenancy.storage import TenantStore


def session_key(*, bundle_id: str, platform: str, version_id: str | None) -> str:
    return f"{bundle_id}-{platform}-{version_id or 'none'}"


class WorkspaceSession:
    """Records for every locale in one app/platform/version workspace."""

    def __init__(self) -> None:
        self.records: dict[str, LocalizationRecord] = {}
        # locale_id -> content hash of the last fully-successful publish, so a retried
        # asogenic_publish call for identical content is a no-op instead of a second ASC write.
        self._published_content_hash: dict[str, str] = {}
        # App-level, not locale-level (there's one app behind every locale in this session) — set
        # via asogenic_intake_product_context. Free-form: what the app actually does, who it's
        # for, whether it needs a login to review, where its privacy policy lives, and so on. No
        # other tool can generate this; it exists so the generation tools have something real to
        # ground on instead of guessing from bare seed words.
        self.product_context: dict[str, Any] | None = None

    def put(self, record: LocalizationRecord) -> None:
        self.records[record.id] = record

    def get(self, locale_id: str) -> LocalizationRecord | None:
        return self.records.get(locale_id)

    def approve(self, locale_id: str) -> LocalizationRecord | None:
        record = self.records.get(locale_id)
        if record is not None:
            record.is_approved = True
        return record

    def already_published(self, locale_id: str, content_hash: str) -> bool:
        return self._published_content_hash.get(locale_id) == content_hash

    def mark_published(self, locale_id: str, content_hash: str) -> None:
        self._published_content_hash[locale_id] = content_hash

    def published_hashes(self) -> dict[str, str]:
        """A copy of locale_id -> last-published content hash, for serialization (session_serde.py)."""
        return dict(self._published_content_hash)

    def load_published_hashes(self, hashes: dict[str, str]) -> None:
        """Restore published-content hashes from a deserialized session (session_serde.py)."""
        self._published_content_hash.update(hashes)


class SessionStore:
    """The process-lifetime store of every open WorkspaceSession, keyed by session_key().

    With `tenant_id`/`store` given, this becomes read-through/write-through against SQLite: a
    miss in the in-memory cache falls back to a persisted copy before creating a fresh session,
    and `flush(key)` persists the current in-memory state. Mutation of a WorkspaceSession's
    records happens in place on plain dataclasses (record.is_approved = True, etc.), so callers
    that mutate must call `flush(key)` afterward — this store cannot auto-detect an in-place
    mutation the way a setter-based API could. See tools/publish.py and
    tools/auth_discovery.py's fetch_source for the three call sites that do this today.
    """

    def __init__(self, *, tenant_id: str | None = None, store: TenantStore | None = None) -> None:
        self._sessions: dict[str, WorkspaceSession] = {}
        self._tenant_id = tenant_id
        self._store = store

    def get_or_create(self, key: str) -> WorkspaceSession:
        session = self._sessions.get(key)
        if session is not None:
            return session
        loaded = self._load(key)
        session = loaded if loaded is not None else WorkspaceSession()
        self._sessions[key] = session
        return session

    def get(self, key: str) -> WorkspaceSession | None:
        session = self._sessions.get(key)
        if session is not None:
            return session
        loaded = self._load(key)
        if loaded is not None:
            self._sessions[key] = loaded
        return loaded

    def flush(self, key: str) -> None:
        """Persist the current in-memory state of one session to SQLite. No-op if this store
        isn't tenant-backed (local/stdio mode) or the session doesn't exist in memory."""
        if self._store is None or self._tenant_id is None:
            return
        session = self._sessions.get(key)
        if session is None:
            return
        from asogenic.tools.session_serde import session_to_dict

        self._store.save_session_state(self._tenant_id, key, session_to_dict(session))

    def _load(self, key: str) -> WorkspaceSession | None:
        if self._store is None or self._tenant_id is None:
            return None
        data = self._store.load_session_state(self._tenant_id, key)
        if data is None:
            return None
        from asogenic.tools.session_serde import session_from_dict

        return session_from_dict(data)
